"""
AI-Shorts pipeline — two-step flow (web UI primary, Telegram = compact notify).

Step 1  --aishorts  (run_aishorts_init)
  → pop next script from Drive queue (ai_shorts_scripts.json)
  → synthesize voice per beat with edge-tts (MP3 + word boundaries)
  → build the real timeline (beat start times) and per-beat suggested image timestamp
  → verify total < 60s
  → create the run's Drive images folder (empty)
  → save data/aishorts_pending.json (step=pending_images)
  → Telegram: compact "script ready, N images, ~Xs, open the web UI" + Drive link

Step 2  user generates images on Leonardo, names each <timestamp>.png, uploads to Drive

Step 3  --aishorts-assemble RUN_ID  (run_aishorts_assemble)
  → load pending → download images (ordered by filename timestamp)
  → build karaoke .ass from saved word boundaries
  → compose (Ken Burns + concatenated narration + captions + title)
  → save in clips/ + upload to Drive finals/ → Telegram link → delete pending

Modifications ONLY in viral_clip_forge/aishorts/ — CC-BY and ranker flows untouched.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ..config import AppConfig
from ..notifier import send_telegram
from ..utils import ensure_dirs, get_logger, setup_logging
from . import gdrive_images, tts
from .captions import TimedWord, build_ass
from .composer import compose
from .config import AiShortsConfig, build_aishorts_config

log = get_logger()


# ---------------------------------------------------------------------------
# Pending state helpers (atomic write, like the ranker)
# ---------------------------------------------------------------------------

def _load_pending(cfg: AiShortsConfig) -> dict | None:
    if cfg.pending_path.exists():
        try:
            return json.loads(cfg.pending_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _save_pending(cfg: AiShortsConfig, data: dict) -> None:
    cfg.pending_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg.pending_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, cfg.pending_path)


# ---------------------------------------------------------------------------
# Drive upload (final video) — same pattern as ranker._upload_to_drive
# ---------------------------------------------------------------------------

def _upload_final(cfg: AiShortsConfig, file_path: Path) -> str:
    from googleapiclient.http import MediaFileUpload
    from ..ranker import gdrive
    from ..analytics.uploader_gdrive import _get_or_create_folder

    service = gdrive.get_service(cfg.gdrive_token_path, cfg.gdrive_client_secret_path)
    root = _get_or_create_folder(service, cfg.drive_root_folder)
    aishorts = _get_or_create_folder(service, cfg.drive_aishorts_folder, parent_id=root)
    finals = _get_or_create_folder(service, cfg.drive_finals_folder, parent_id=aishorts)

    media = MediaFileUpload(str(file_path), mimetype="video/mp4", resumable=False)
    metadata = {"name": file_path.name, "parents": [finals]}
    uploaded = service.files().create(body=metadata, media_body=media, fields="id").execute()
    file_id = uploaded["id"]
    service.permissions().create(fileId=file_id, body={"role": "reader", "type": "anyone"}).execute()
    url = f"https://drive.google.com/file/d/{file_id}/view"
    log.info("[aishorts] Uploaded final %s → %s", file_path.name, url)
    return url


# ---------------------------------------------------------------------------
# Step 1 — generate script & voice, suggest image timestamps
# ---------------------------------------------------------------------------

def run_aishorts_init(config: AppConfig) -> str:
    """Pop a script, synthesize voice, suggest image timestamps. Returns status."""
    setup_logging(config.log_dir, "aishorts-init")
    log.info("=== AI-Shorts INIT ===")

    cfg = build_aishorts_config(config)
    ensure_dirs(cfg.output_dir, cfg.work_dir, config.state_db_path.parent, config.log_dir)

    # Guard: don't clobber an in-progress session.
    if cfg.pending_path.exists():
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            "⚠️ <b>AI Shorts</b>: sessione già in corso.\n"
            "Carica le immagini e usa <code>/assemble</code>, "
            "oppure elimina <code>data/aishorts_pending.json</code> per ricominciare.",
        )
        return "blocked"

    from .script_source import load_next_script
    try:
        script, remaining = load_next_script(cfg)
    except Exception as exc:
        log.error("[aishorts] Could not load script: %s", exc)
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"❌ <b>AI Shorts</b>: errore lettura coda script su Drive: {exc}",
        )
        return "error"

    if script is None:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            "ℹ️ <b>AI Shorts</b>: nessuno script in coda su Drive "
            "(<code>ai_shorts_scripts.json</code>). Accodane uno e riprova.",
        )
        return "no_scripts"

    # Short, typeable run id (date + 4 hex) — used in `/assemble RUN_ID`.
    import uuid
    from datetime import datetime
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    run_dir = cfg.run_dir(run_id)
    audio_dir = run_dir / "audio"

    # Synthesize voice per beat.
    narrations = [b.narration for b in script.beats]
    try:
        beat_audios = tts.synthesize_beats(cfg.voice, narrations, audio_dir)
    except Exception as exc:
        log.error("[aishorts] TTS failed: %s", exc)
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"❌ <b>AI Shorts</b>: sintesi vocale fallita: {exc}",
        )
        return "error"

    # Real per-beat durations (ffprobe is more reliable than last word boundary).
    durations = []
    for ba in beat_audios:
        d = tts.probe_duration(cfg.ffprobe_bin, ba.mp3_path) or ba.duration
        durations.append(d)

    # Cumulative beat start times = suggested image timestamps (one per beat).
    starts: list[float] = []
    acc = 0.0
    for d in durations:
        starts.append(round(acc, 1))
        acc += d
    total = round(acc, 1)

    # Absolute word timeline for captions.
    words_abs: list[dict] = []
    for ba, start in zip(beat_audios, starts):
        for w in ba.words:
            words_abs.append({
                "text": w.text,
                "start": round(start + w.start, 3),
                "end": round(start + w.end, 3),
            })

    # Create the empty Drive images folder for this run.
    try:
        folder_url = gdrive_images.create_run_folder(cfg, run_id)
    except Exception as exc:
        log.error("[aishorts] Could not create Drive folder: %s", exc)
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"❌ <b>AI Shorts</b>: impossibile creare la cartella Drive del run: {exc}",
        )
        return "error"

    # Build scene list (one per beat) with the suggested filename.
    scenes = []
    for i, (beat, start) in enumerate(zip(script.beats, starts)):
        scenes.append({
            "index": i,
            "timestamp": start,
            "suggested_filename": f"{start}.png",
            "narration": beat.narration,
            "leonardo_prompt": beat.leonardo_prompt,
        })

    pending = {
        "run_id": run_id,
        "step": "pending_images",
        "title": script.title,
        "theme": script.theme,
        "hook": script.hook,
        "total_seconds": total,
        "drive_folder_url": folder_url,
        "scenes": scenes,
        "beat_mp3s": [str(ba.mp3_path) for ba in beat_audios],
        "words": words_abs,
        "remaining_in_queue": remaining,
    }
    _save_pending(cfg, pending)

    # Compact Telegram notification — details live in the web UI.
    over = total > cfg.warn_total_seconds
    lines = [
        "✅ <b>AI Shorts — script &amp; voce pronti</b>",
        f"🎬 {script.title}",
        f"🖼 {len(scenes)} immagini · ⏱ ~{total:.0f}s"
        + ("  ⚠️ &gt;58s, accorcia lo script" if over else ""),
        f"<a href=\"{folder_url}\">📁 Cartella Drive (carica qui le immagini)</a>",
        "",
        "Apri la <b>web UI → AI Shorts</b> per i prompt Leonardo e i nomi file.",
        f"Poi: <code>/assemble {run_id}</code>",
    ]
    send_telegram(config.telegram_bot_token, config.telegram_chat_id, "\n".join(lines))

    log.info("[aishorts] INIT done run=%s scenes=%d total=%.1fs", run_id, len(scenes), total)
    return "pending_images"


# ---------------------------------------------------------------------------
# Step 3 — assemble final video
# ---------------------------------------------------------------------------

def run_aishorts_assemble(config: AppConfig, run_id: str) -> str:
    """Download images, build captions, compose, upload. Returns status."""
    setup_logging(config.log_dir, "aishorts-assemble")
    log.info("=== AI-Shorts ASSEMBLE run=%s ===", run_id)

    cfg = build_aishorts_config(config)
    pending = _load_pending(cfg)

    if not pending or pending.get("step") != "pending_images":
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            "⚠️ <b>AI Shorts</b>: nessuna sessione in attesa di immagini.\n"
            "Avvia con <code>/aishorts</code>.",
        )
        return "error"

    run_id = (run_id or "").strip() or pending.get("run_id", "")
    if run_id != pending.get("run_id"):
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"⚠️ <b>AI Shorts</b>: RUN_ID <code>{run_id}</code> non corrisponde alla "
            f"sessione in corso (<code>{pending.get('run_id')}</code>).",
        )
        return "error"

    run_dir = cfg.run_dir(run_id)
    img_dir = run_dir / "images"

    send_telegram(
        config.telegram_bot_token, config.telegram_chat_id,
        f"⏳ <b>AI Shorts</b>: scarico immagini e monto <b>{pending.get('title', '')[:50]}</b>…",
    )

    # Download images ordered by timestamp in filename.
    try:
        images = gdrive_images.download_images(cfg, run_id, img_dir)
    except Exception as exc:
        log.error("[aishorts] Image download failed: %s", exc)
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"❌ <b>AI Shorts</b>: download immagini fallito: {exc}",
        )
        return "failed"

    if not images:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            "❌ <b>AI Shorts</b>: nessuna immagine valida nella cartella Drive.\n"
            "Carica file nominati col timestamp (es. <code>0.0.png</code>) e riprova.",
        )
        return "failed"

    image_pairs = [(im.timestamp, im.local_path) for im in images]
    audio_mp3s = [Path(p) for p in pending.get("beat_mp3s", []) if Path(p).exists()]
    if not audio_mp3s:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            "❌ <b>AI Shorts</b>: file audio del run mancanti. Riparti da <code>/aishorts</code>.",
        )
        return "failed"

    # Karaoke captions from saved absolute word timeline.
    ass_path = None
    words = pending.get("words", [])
    if words:
        timed = [TimedWord(text=w["text"], start=w["start"], end=w["end"]) for w in words]
        ass_path = run_dir / "captions.ass"
        font_name = Path(cfg.font_path).stem
        build_ass(timed, cfg.width, cfg.height, font=font_name, out_path=ass_path)

    # Compose.
    comp = compose(
        cfg,
        images=image_pairs,
        audio_mp3s=audio_mp3s,
        ass_path=ass_path,
        title=pending.get("title", ""),
        work_dir=run_dir / "compose",
    )
    if not comp.success or not comp.output_path:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"❌ <b>AI Shorts</b>: composizione fallita: {comp.error}",
        )
        return "failed"

    # Copy to clips/.
    ensure_dirs(cfg.output_dir)
    local_path = cfg.output_dir / comp.output_path.name
    if comp.output_path != local_path:
        shutil.copy2(comp.output_path, local_path)

    # Upload to Drive finals/.
    drive_url = None
    try:
        drive_url = _upload_final(cfg, local_path)
    except Exception as exc:
        log.error("[aishorts] Drive upload failed: %s", exc)

    # Done — clear pending.
    cfg.pending_path.unlink(missing_ok=True)

    size_mb = local_path.stat().st_size / 1_048_576
    lines = [
        f"✅ <b>AI Short pronto</b>  ({comp.n_images} immagini · {comp.duration:.0f}s · {size_mb:.1f} MB)",
        f"🎬 {pending.get('title', '')}",
    ]
    if drive_url:
        lines.append(f"<a href=\"{drive_url}\">▶ Apri su Drive</a>")
    else:
        lines.append(f"Salvato: <code>{local_path.name}</code>")
    lines.append("Carica su YouTube manualmente da Drive.")
    send_telegram(config.telegram_bot_token, config.telegram_chat_id, "\n".join(lines))

    log.info("=== AI-Shorts ASSEMBLE completed | images=%d ===", comp.n_images)
    return "completed"
