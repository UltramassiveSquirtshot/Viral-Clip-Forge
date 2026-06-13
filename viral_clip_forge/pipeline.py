from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .clip_analyzer import find_best_clip_windows, detect_scene_changes, detect_audio_peaks
from .clip_cutter import cut_all_clips
from .config import AppConfig
from .content_generator import generate_title, generate_description, generate_tags
from .downloader import cleanup_stale_downloads, download_video
from .filter import FilterResult, ScoredCandidate, run_filter_pipeline
from .license_checker import LicenseStatus, check_video_license
from .scheduler import next_upload_slots
from .scraper import scrape_niche
from .state import (
    get_db_connection,
    get_processed_ids,
    get_today_api_units,
    mark_video_processed,
    record_clip,
    record_run_finish,
    record_run_start,
    update_run_api_units,
)
from .notifier import notify_run
from .utils import ensure_dirs, generate_run_id, get_logger, setup_logging

log = get_logger()


@dataclass
class PipelineResult:
    run_id: str
    started_at: str
    finished_at: str
    status: str
    niches_processed: list[str]
    api_units_used: int
    videos_found: int
    videos_processed: int
    videos_skipped_license: int
    clips_produced: int
    uploads_scheduled: int
    manifest_path: Path | None
    errors: list[str]
    niche_summaries: list[dict] = field(default_factory=list)


def _try_upload_clip(config, clip_path, title, description, tags, publish_at, thumbnail_url: str = "") -> tuple[str | None, str]:
    """Returns (youtube_video_id, error_message). One or the other will be None."""
    try:
        from .youtube_uploader import upload_clip
        yt_id = upload_clip(config, clip_path, title, description, tags, publish_at, thumbnail_url=thumbnail_url)
        return yt_id, None
    except RuntimeError as exc:
        # Token not set up yet — skip upload silently
        if "setup-youtube" in str(exc):
            return None, str(exc)
        return None, str(exc)
    except Exception as exc:
        return None, str(exc)


def run_pipeline(config: AppConfig) -> PipelineResult:
    run_id = generate_run_id()
    started_at = datetime.now(timezone.utc).isoformat()

    setup_logging(config.log_dir, run_id)
    log.info(f"=== Viral Clip Forge run {run_id} ===")

    ensure_dirs(config.output_dir, config.download_dir, config.state_db_path.parent, config.log_dir)

    deleted = cleanup_stale_downloads(config.download_dir, max_age_hours=24)
    if deleted:
        log.info(f"Cleaned up {deleted} stale downloads")

    conn = get_db_connection(config.state_db_path)
    record_run_start(conn, run_id)

    seen_ids = get_processed_ids(conn)
    today_units = get_today_api_units(conn)
    run_units: list[int] = []

    result = PipelineResult(
        run_id=run_id,
        started_at=started_at,
        finished_at="",
        status="running",
        niches_processed=[],
        api_units_used=0,
        videos_found=0,
        videos_processed=0,
        videos_skipped_license=0,
        clips_produced=0,
        uploads_scheduled=0,
        manifest_path=None,
        errors=[],
    )

    manifest: dict = {
        "run_id": run_id,
        "run_date": started_at,
        "pipeline_version": "1.0.0",
        "api_units_used": 0,
        "status": "running",
        "niches": {},
        "errors": [],
        "uploads_scheduled": 0,
    }

    for niche_name, niche_cfg in config.niches.items():
        log.info(f"--- Processing niche: {niche_name} ---")
        niche_manifest: dict = {
            "videos_found": 0,
            "videos_after_filter": 0,
            "videos_selected": 0,
            "videos_clipped": 0,
            "videos_skipped": 0,
            "results": [],
        }

        try:
            candidates = scrape_niche(
                api_key=config.youtube_api_key,
                niche=niche_cfg,
                today_units=today_units,
                run_units=run_units,
            )
            niche_manifest["videos_found"] = len(candidates)
            result.videos_found += len(candidates)

            update_run_api_units(conn, run_id, sum(run_units))

            filter_result = run_filter_pipeline(
                candidates=candidates,
                seen_ids=seen_ids,
                min_views=config.min_views,
                max_duration_seconds=config.max_video_duration,
                top_n=3,
            )
            selected = filter_result.selected
            niche_manifest["videos_after_filter"] = filter_result.count_after_threshold
            niche_manifest["videos_selected"] = len(selected)
            niche_manifest["filter_stats"] = {
                "before_dedup": filter_result.count_before_dedup,
                "after_dedup": filter_result.count_after_dedup,
                "after_threshold": filter_result.count_after_threshold,
            }
            niche_manifest["sources"] = {
                "cc_search": sum(1 for c in candidates if c.source == "cc_search"),
                "trending": sum(1 for c in candidates if c.source == "trending"),
                "scrapetube": sum(1 for c in candidates if c.source == "scrapetube"),
            }
            score_lookup: dict[str, ScoredCandidate] = {
                s.candidate.video_id: s for s in filter_result.scored_all
            }
            niche_manifest["candidate_ranking"] = [
                {
                    "rank": rank + 1,
                    "video_id": s.candidate.video_id,
                    "title": s.candidate.title,
                    "source": s.candidate.source,
                    "view_count": s.candidate.view_count,
                    "composite_score": round(s.composite, 4),
                    "selected": rank < len(selected),
                }
                for rank, s in enumerate(filter_result.scored_all)
            ]

        except Exception as exc:
            msg = f"[{niche_name}] Scraping/filter failed: {exc}"
            log.error(msg)
            result.errors.append(msg)
            manifest["niches"][niche_name] = niche_manifest
            continue

        for video in selected:
            sc = score_lookup.get(video.video_id)
            video_record: dict = {
                "video_id": video.video_id,
                "title": video.title,
                "channel": video.channel_title,
                "url": f"https://www.youtube.com/watch?v={video.video_id}",
                "published_at": video.published_at,
                "view_count": video.view_count,
                "like_count": video.like_count,
                "comment_count": video.comment_count,
                "duration_seconds": video.duration_seconds,
                "source": video.source,
                "score_breakdown": {
                    "view_score": round(sc.view_score, 4),
                    "engagement_ratio": round(sc.engagement_ratio, 6),
                    "recency_bonus": round(sc.recency_bonus, 4),
                    "composite": round(sc.composite, 4),
                } if sc else None,
                "license": None,
                "license_confidence": None,
                "status": None,
                "clips": [],
            }

            try:
                license_result = check_video_license(video)
                video_record["license"] = license_result.final_status.value
                video_record["license_confidence"] = license_result.confidence

                if license_result.final_status != LicenseStatus.CC_BY:
                    status = "skipped_license" if license_result.final_status == LicenseStatus.STANDARD else "skipped_uncertain"
                    video_record["status"] = status
                    score_str = f" score={sc.composite:.4f}" if sc else ""
                    log.info(
                        f"[{niche_name}] REJECTED {video.video_id} '{video.title[:50]}' "
                        f"license={license_result.final_status.value} "
                        f"confidence={license_result.confidence} "
                        f"source={video.source}{score_str}"
                    )
                    mark_video_processed(conn, video.video_id, video.title, niche_name, license_result.final_status.value, run_id, status)
                    seen_ids.add(video.video_id)
                    niche_manifest["videos_skipped"] += 1
                    result.videos_skipped_license += 1
                    niche_manifest["results"].append(video_record)
                    continue

                log.info(
                    f"[{niche_name}] ACCEPTED {video.video_id} '{video.title[:50]}' "
                    f"license={license_result.final_status.value} "
                    f"confidence={license_result.confidence} "
                    f"source={video.source}"
                )

                dl_result = download_video(
                    video_id=video.video_id,
                    output_dir=config.download_dir,
                    ffmpeg_bin_dir=config.ffmpeg_bin,
                    max_height=1080,
                )

                if not dl_result.success or dl_result.output_path is None:
                    msg = f"[{niche_name}] Download failed for {video.video_id}: {dl_result.error}"
                    log.error(msg)
                    result.errors.append(msg)
                    video_record["status"] = "download_failed"
                    mark_video_processed(conn, video.video_id, video.title, niche_name, license_result.final_status.value, run_id, "download_failed")
                    seen_ids.add(video.video_id)
                    niche_manifest["results"].append(video_record)
                    continue

                source_path = dl_result.output_path
                video_duration = dl_result.duration_seconds or float(video.duration_seconds)

                scene_changes = detect_scene_changes(source_path, config.ffmpeg_bin, config.scene_threshold)
                audio_peaks = detect_audio_peaks(source_path, config.ffprobe_bin, peak_percentile=config.audio_peak_percentile)

                clip_windows = find_best_clip_windows(
                    scene_changes=scene_changes,
                    audio_peaks=audio_peaks,
                    video_duration=video_duration,
                    min_duration=config.min_clip_duration,
                    max_duration=config.max_clip_duration,
                    max_clips=config.max_clips_per_video,
                )

                cut_results = cut_all_clips(
                    source_path=source_path,
                    candidates=clip_windows,
                    output_dir=config.output_dir,
                    video_id=video.video_id,
                    ffmpeg_bin=config.ffmpeg_bin,
                    ffprobe_bin=config.ffprobe_bin,
                    max_clips=config.max_clips_per_video,
                )

                # Collect successful clips and reserve upload slots for all at once
                successful_cuts = [
                    (idx, cut) for idx, cut in enumerate(cut_results)
                    if cut.success and cut.output_path
                ]
                upload_slots = []
                if successful_cuts:
                    try:
                        upload_slots = next_upload_slots(
                            len(successful_cuts),
                            config.schedule_state_path,
                        )
                    except Exception as exc:
                        log.warning("Could not reserve upload slots: %s", exc)

                clips_manifest = []
                slot_idx = 0
                for idx, cut in enumerate(cut_results):
                    if cut.success and cut.output_path:
                        record_clip(
                            conn,
                            clip_id=cut.clip_id,
                            video_id=video.video_id,
                            start_sec=cut.start_sec,
                            end_sec=cut.end_sec,
                            output_path=str(cut.output_path),
                            combined_score=clip_windows[idx].combined_score if idx < len(clip_windows) else None,
                        )
                        result.clips_produced += 1

                        clip_entry: dict = {
                            "clip_id": cut.clip_id,
                            "index": idx + 1,
                            "start_sec": cut.start_sec,
                            "end_sec": cut.end_sec,
                            "duration_sec": cut.duration_sec,
                            "combined_score": clip_windows[idx].combined_score if idx < len(clip_windows) else None,
                            "output_path": str(cut.output_path.relative_to(source_path.parent.parent)) if cut.output_path else None,
                            "file_size_bytes": cut.file_size_bytes,
                            "cut_successful": True,
                            "re_encoded": cut.re_encoded,
                            "youtube_video_id": None,
                            "youtube_url": None,
                            "scheduled_publish_at": None,
                            "upload_status": "not_attempted",
                        }

                        # Upload to YouTube
                        publish_at = upload_slots[slot_idx] if slot_idx < len(upload_slots) else None
                        slot_idx += 1

                        if publish_at and cut.output_path:
                            title = generate_title(video.title, cut.start_sec, cut.end_sec, idx + 1)
                            description = generate_description(
                                video.title, video.channel_title,
                                f"https://www.youtube.com/watch?v={video.video_id}",
                                niche_name, cut.start_sec, cut.end_sec,
                            )
                            tags = generate_tags(niche_name, video.title)

                            yt_id, err = _try_upload_clip(
                                config, cut.output_path, title, description, tags, publish_at,
                                thumbnail_url=video.thumbnail_url,
                            )
                            if yt_id:
                                clip_entry["youtube_video_id"] = yt_id
                                clip_entry["youtube_url"] = f"https://youtu.be/{yt_id}"
                                clip_entry["scheduled_publish_at"] = publish_at.isoformat()
                                clip_entry["upload_status"] = "scheduled"
                                result.uploads_scheduled += 1
                                log.info("Scheduled https://youtu.be/%s at %s", yt_id, publish_at.isoformat())
                            else:
                                clip_entry["upload_status"] = "failed"
                                log.warning("Upload failed for clip %d of %s: %s", idx + 1, video.video_id, err)
                                result.errors.append(f"Upload failed [{video.video_id} clip {idx+1}]: {err}")
                        else:
                            clip_entry["upload_status"] = "no_slot"

                        clips_manifest.append(clip_entry)
                    else:
                        clips_manifest.append({
                            "index": idx + 1,
                            "start_sec": cut.start_sec,
                            "end_sec": cut.end_sec,
                            "cut_successful": False,
                            "error": cut.error,
                        })

                video_record["clips"] = clips_manifest
                video_record["status"] = "clipped" if any(c.get("cut_successful") for c in clips_manifest) else "clip_failed"

                mark_video_processed(conn, video.video_id, video.title, niche_name, license_result.final_status.value, run_id, video_record["status"])
                seen_ids.add(video.video_id)
                result.videos_processed += 1
                niche_manifest["videos_clipped"] += 1

            except Exception as exc:
                msg = f"[{niche_name}] Error processing {video.video_id}: {exc}"
                log.error(msg, exc_info=True)
                result.errors.append(msg)
                video_record["status"] = "error"
                try:
                    mark_video_processed(conn, video.video_id, video.title, niche_name, "unknown", run_id, "error")
                    seen_ids.add(video.video_id)
                except Exception:
                    pass

            niche_manifest["results"].append(video_record)

        manifest["niches"][niche_name] = niche_manifest
        result.niches_processed.append(niche_name)
        result.niche_summaries.append({
            "niche": niche_name,
            "sources": niche_manifest.get("sources", {}),
            "filter_stats": niche_manifest.get("filter_stats", {}),
            "videos_selected": niche_manifest["videos_selected"],
            "videos_clipped": niche_manifest["videos_clipped"],
            "videos_skipped": niche_manifest["videos_skipped"],
            "top_candidates": [
                {
                    "title": r["title"],
                    "views": r["view_count"],
                    "score": r["score_breakdown"]["composite"] if r.get("score_breakdown") else None,
                    "source": r.get("source", "unknown"),
                    "status": r["status"],
                    "license": r.get("license"),
                }
                for r in niche_manifest["results"]
            ],
        })

    result.api_units_used = sum(run_units)
    manifest["api_units_used"] = result.api_units_used
    manifest["uploads_scheduled"] = result.uploads_scheduled
    update_run_api_units(conn, run_id, result.api_units_used)

    if result.errors and result.videos_processed == 0:
        result.status = "failed"
    elif result.errors:
        result.status = "partial"
    else:
        result.status = "completed"

    result.finished_at = datetime.now(timezone.utc).isoformat()
    manifest["status"] = result.status
    manifest["errors"] = result.errors

    record_run_finish(
        conn, run_id, result.status, result.api_units_used, result.niches_processed, "not_required"
    )

    manifest_dir = config.state_db_path.parent / "manifests" / started_at[:16].replace(":", "-")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    result.manifest_path = manifest_path

    conn.close()

    log.info(
        f"=== Run {run_id} {result.status} | "
        f"clips={result.clips_produced} uploads_scheduled={result.uploads_scheduled} "
        f"api_units={result.api_units_used} errors={len(result.errors)} ==="
    )
    log.info(f"Manifest: {manifest_path}")

    notify_run(config.telegram_bot_token, config.telegram_chat_id, result)

    return result
