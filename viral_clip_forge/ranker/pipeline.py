"""
Ranking-Shorts pipeline orchestration.

Flow:
  1. Read the next script from Drive (title + labels + per-rank search queries),
     removing it from the queue.
  2. Download one portrait stock clip per rank (Pexels).
  3. Fetch a royalty-free background track (Pixabay; optional).
  4. Compose the 9:16 ranking video (FFmpeg: concat + title/rank overlays + music).
  5. Reserve the next algorithm-safe upload slot and upload as a scheduled private
     YouTube video with AI disclosure on.
  6. Write a manifest (same shape /status and /next parse) and Telegram a summary.

Reuses: scheduler.next_upload_slots, youtube_uploader.upload_clip,
state.record_clip, notifier.send_telegram.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import AppConfig
from ..notifier import send_telegram
from ..scheduler import next_upload_slots
from ..state import get_db_connection, mark_video_processed, record_clip
from ..utils import ensure_dirs, generate_run_id, get_logger, setup_logging
from . import music_source, pexels_source, script_source
from .composer import compose
from .config import build_ranker_config

log = get_logger()


@dataclass
class RankerRunResult:
    run_id: str
    started_at: str
    finished_at: str
    status: str            # completed | partial | failed | empty
    title: str
    n_segments: int
    clips_produced: int
    uploads_scheduled: int
    output_path: Path | None
    youtube_url: str | None
    scheduled_publish_at: str | None
    manifest_path: Path | None
    errors: list[str] = field(default_factory=list)


def _try_upload(config: AppConfig, clip_path: Path, title: str, description: str,
                tags: list[str], publish_at, category_id: str) -> tuple[str | None, str | None]:
    try:
        from ..youtube_uploader import upload_clip
        yt_id = upload_clip(
            config, clip_path, title, description, tags, publish_at,
            category_id=category_id,
        )
        return yt_id, None
    except Exception as exc:
        return None, str(exc)


def _write_manifest(config: AppConfig, run_id: str, started_at: str, result: RankerRunResult,
                    theme: str) -> Path:
    """Manifest shaped so the Telegram /status and /next commands keep working."""
    clip_entry = {
        "clip_id": run_id,
        "index": 1,
        "start_sec": 0.0,
        "end_sec": float(result.n_segments) * 6.0,
        "duration_sec": float(result.n_segments) * 6.0,
        "output_path": str(result.output_path) if result.output_path else None,
        "cut_successful": bool(result.output_path),
        "youtube_video_id": (result.youtube_url or "").rsplit("/", 1)[-1] or None if result.youtube_url else None,
        "youtube_url": result.youtube_url,
        "scheduled_publish_at": result.scheduled_publish_at,
        "upload_status": "scheduled" if result.youtube_url else ("failed" if result.output_path else "not_attempted"),
    }
    manifest = {
        "run_id": run_id,
        "run_date": started_at,
        "pipeline_version": "ranker-1.0.0",
        "kind": "ranking_short",
        "status": result.status,
        "clips_produced": result.clips_produced,
        "uploads_scheduled": result.uploads_scheduled,
        "errors": result.errors,
        # niches/results structure reused by /status and /next parsers
        "niches": {
            "ranking": {
                "results": [
                    {
                        "video_id": run_id,
                        "title": result.title,
                        "channel": "Ranking Shorts",
                        "url": "",
                        "view_count": 0,
                        "status": "clipped" if result.output_path else "clip_failed",
                        "clips": [clip_entry],
                    }
                ]
            }
        },
    }
    manifest_dir = config.state_db_path.parent / "manifests" / started_at[:16].replace(":", "-")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def run_ranker_pipeline(config: AppConfig) -> RankerRunResult:
    run_id = generate_run_id()
    started_at = datetime.now(timezone.utc).isoformat()

    setup_logging(config.log_dir, run_id)
    log.info("=== Viral Clip Forge RANKER run %s ===", run_id)

    cfg = build_ranker_config(config)
    ensure_dirs(cfg.output_dir, cfg.download_dir, config.state_db_path.parent, config.log_dir)

    result = RankerRunResult(
        run_id=run_id, started_at=started_at, finished_at="", status="running",
        title="", n_segments=0, clips_produced=0, uploads_scheduled=0,
        output_path=None, youtube_url=None, scheduled_publish_at=None, manifest_path=None,
    )

    # 1. Script from Drive
    try:
        script = script_source.load_next_script(cfg)
    except RuntimeError as exc:
        # Token missing / setup needed — surface clearly.
        result.status = "failed"
        result.errors.append(str(exc))
        result.finished_at = datetime.now(timezone.utc).isoformat()
        log.error("[ranker] %s", exc)
        _notify(config, result)
        return result

    if script is None:
        result.status = "empty"
        result.finished_at = datetime.now(timezone.utc).isoformat()
        log.info("[ranker] No scripts queued — exiting cleanly.")
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            "🏁 <b>Ranker</b>: no scripts queued on Drive. "
            "Upload a <code>ranker_scripts.json</code> to queue a video.",
        )
        return result

    result.title = script.title
    log.info("[ranker] Script: '%s' (%d ranks) theme=%s", script.title, len(script.labels), script.theme)

    # 2. Footage
    clips = pexels_source.fetch_ranking_clips(cfg, script.search_queries)
    if not clips:
        result.status = "failed"
        result.errors.append("No stock footage downloaded for any rank")
        result.finished_at = datetime.now(timezone.utc).isoformat()
        log.error("[ranker] No footage downloaded — aborting.")
        _notify(config, result)
        return result

    # 3. Music (optional)
    music = music_source.fetch_background_music(cfg, script.theme)

    # 4. Compose
    comp = compose(cfg, clips, script.title, script.labels[: len(clips)], music)
    result.n_segments = comp.n_segments
    if not comp.success or not comp.output_path:
        result.status = "failed"
        result.errors.append(comp.error or "compose failed")
        result.finished_at = datetime.now(timezone.utc).isoformat()
        _notify(config, result)
        return result

    result.output_path = comp.output_path
    result.clips_produced = 1

    # Record in state DB (synthetic processed_videos row to satisfy the clip FK)
    try:
        conn = get_db_connection(config.state_db_path)
        mark_video_processed(conn, run_id, script.title, "ranking", "n/a", run_id, "clipped")
        record_clip(conn, clip_id=run_id, video_id=run_id, start_sec=0.0,
                    end_sec=float(comp.n_segments) * cfg.clip_seconds,
                    output_path=str(comp.output_path), combined_score=None)
        conn.close()
    except Exception as exc:
        log.warning("[ranker] Could not record clip in state DB: %s", exc)

    # 5. Schedule + upload
    publish_at = None
    try:
        slots = next_upload_slots(1, config.schedule_state_path)
        publish_at = slots[0] if slots else None
    except Exception as exc:
        log.warning("[ranker] Could not reserve upload slot: %s", exc)

    if publish_at:
        description = _build_description(script)
        tags = _build_tags(script)
        yt_id, err = _try_upload(
            config, comp.output_path, script.title, description, tags, publish_at,
            cfg.youtube_category_id,
        )
        if yt_id:
            result.youtube_url = f"https://youtu.be/{yt_id}"
            result.scheduled_publish_at = publish_at.isoformat()
            result.uploads_scheduled = 1
            log.info("[ranker] Scheduled %s at %s", result.youtube_url, result.scheduled_publish_at)
        else:
            result.errors.append(f"Upload failed: {err}")
            log.warning("[ranker] Upload failed: %s", err)

    result.status = "partial" if result.errors else "completed"
    result.finished_at = datetime.now(timezone.utc).isoformat()
    result.manifest_path = _write_manifest(config, run_id, started_at, result, script.theme)

    log.info("=== Ranker run %s %s | uploads=%d ===", run_id, result.status, result.uploads_scheduled)
    _notify(config, result)
    return result


def _build_description(script) -> str:
    lines = [f"Top {len(script.labels)} {script.theme}.", ""]
    for i, label in enumerate(script.labels):
        rank = len(script.labels) - i
        lines.append(f"#{rank} {label}")
    lines += ["", "Footage: Pexels (royalty-free). Music: Pixabay (royalty-free).",
              "#shorts #top5 #ranking"]
    return "\n".join(lines)


def _build_tags(script) -> list[str]:
    base = ["shorts", "top 5", "ranking", script.theme]
    base += [w.lower() for label in script.labels for w in label.split()][:10]
    # de-dup preserving order, cap
    seen, tags = set(), []
    for t in base:
        if t and t not in seen:
            seen.add(t)
            tags.append(t)
    return tags[:15]


def _notify(config: AppConfig, result: RankerRunResult) -> None:
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return
    if result.status in ("completed", "partial") and result.output_path:
        lines = [
            "🏆 <b>Ranking Short</b> — run complete",
            f"<b>{result.title}</b>  ·  {result.n_segments} ranks",
        ]
        if result.youtube_url:
            pub = (result.scheduled_publish_at or "")[:16].replace("T", " ")
            lines.append(f"📦 Scheduled → <a href=\"{result.youtube_url}\">YT</a> @ {pub}")
        else:
            lines.append("⚠️ Composed but not uploaded (run <code>python main.py --setup-youtube</code>)")
        if result.errors:
            lines.append(f"⚠️ {len(result.errors)} error(s) — check logs")
        text = "\n".join(lines)
    else:
        text = (
            f"❌ <b>Ranker</b> run {result.status}.\n"
            + ("\n".join(result.errors[:3]) if result.errors else "See logs.")
        )
    send_telegram(config.telegram_bot_token, config.telegram_chat_id, text)
