from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import AppConfig
from .state import (
    delete_clips_for_run,
    get_db_connection,
    get_pending_runs,
    set_approval_status,
    update_clip_path,
)
from .utils import get_logger

log = get_logger()


class ApprovalError(Exception):
    pass


def _manifests_dir(config: AppConfig) -> Path:
    return config.state_db_path.parent / "manifests"


def find_run_manifest(config: AppConfig, run_id: str) -> Path | None:
    """Locate the manifest.json belonging to a given run_id."""
    manifests_dir = _manifests_dir(config)
    if not manifests_dir.exists():
        return None
    for manifest_path in sorted(manifests_dir.glob("*/manifest.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("run_id") == run_id:
            return manifest_path
    return None


def _load_manifest(manifest_path: Path) -> dict:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _save_manifest(manifest_path: Path, manifest: dict) -> None:
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def _iter_clip_entries(manifest: dict):
    """Yield (video_id, clip_entry) for every clip recorded in the manifest."""
    for niche in manifest.get("niches", {}).values():
        for video_record in niche.get("results", []):
            for clip in video_record.get("clips", []):
                yield video_record, clip


def list_pending_runs(config: AppConfig) -> list[dict]:
    """Return a summary of every run currently awaiting approval."""
    manifests_dir = _manifests_dir(config)
    summaries: list[dict] = []
    if not manifests_dir.exists():
        return summaries

    for manifest_path in sorted(manifests_dir.glob("*/manifest.json")):
        try:
            manifest = _load_manifest(manifest_path)
        except (json.JSONDecodeError, OSError):
            continue

        if manifest.get("approval_status") != "pending":
            continue

        niches_summary: dict[str, list[dict]] = {}
        for niche_name, niche in manifest.get("niches", {}).items():
            videos = []
            for video_record in niche.get("results", []):
                if video_record.get("status") != "clipped":
                    continue
                clips = [
                    {
                        "index": c["index"],
                        "start_sec": c["start_sec"],
                        "end_sec": c["end_sec"],
                        "duration_sec": c.get("duration_sec"),
                        "combined_score": c.get("combined_score"),
                        "output_path": c.get("output_path"),
                        "cut_successful": c.get("cut_successful", False),
                    }
                    for c in video_record.get("clips", [])
                ]
                videos.append({
                    "video_id": video_record["video_id"],
                    "title": video_record["title"],
                    "channel": video_record["channel"],
                    "url": video_record["url"],
                    "view_count": video_record.get("view_count"),
                    "license": video_record.get("license"),
                    "clips": clips,
                })
            if videos:
                niches_summary[niche_name] = videos

        summaries.append({
            "run_id": manifest["run_id"],
            "run_date": manifest.get("run_date"),
            "manifest_path": str(manifest_path),
            "pending_dir": manifest.get("pending_dir"),
            "niches": niches_summary,
        })

    return summaries


def approve_run(config: AppConfig, run_id: str) -> dict:
    """Move a pending run's clips into the final clips directory and mark it approved."""
    manifest_path = find_run_manifest(config, run_id)
    if manifest_path is None:
        raise ApprovalError(f"No manifest found for run {run_id}")

    manifest = _load_manifest(manifest_path)
    if manifest.get("approval_status") != "pending":
        raise ApprovalError(
            f"Run {run_id} is not pending approval (current status: {manifest.get('approval_status')!r})"
        )

    pending_dir_str = manifest.get("pending_dir")
    if not pending_dir_str:
        raise ApprovalError(f"Run {run_id} has no pending_dir recorded")

    pending_dir = Path(pending_dir_str)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    conn = get_db_connection(config.state_db_path)
    moved: list[str] = []
    try:
        for _video_record, clip in _iter_clip_entries(manifest):
            if not clip.get("cut_successful") or not clip.get("output_path"):
                continue

            old_rel = Path(clip["output_path"])
            old_abs = pending_dir / old_rel.name
            new_abs = config.output_dir / old_rel.name

            if old_abs.exists():
                if new_abs.exists():
                    new_abs.unlink()
                shutil.move(str(old_abs), str(new_abs))
                moved.append(new_abs.name)
            elif not new_abs.exists():
                log.warning(f"[approve] Expected clip file missing: {old_abs}")

            clip_id = clip.get("clip_id")
            if clip_id:
                update_clip_path(conn, clip_id, str(new_abs))

            try:
                clip["output_path"] = str(new_abs.relative_to(config.output_dir.parent))
            except ValueError:
                clip["output_path"] = str(new_abs)

        manifest["approval_status"] = "approved"
        manifest["pending_dir"] = None
        _save_manifest(manifest_path, manifest)

        set_approval_status(conn, run_id, "approved")
    finally:
        conn.close()

    # Clean up the now-empty pending directory tree.
    if pending_dir.exists():
        try:
            shutil.rmtree(pending_dir)
        except OSError:
            pass
        parent = pending_dir.parent
        try:
            if parent.name == "pending" and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass

    return {"run_id": run_id, "moved_clips": moved, "output_dir": str(config.output_dir)}


def reject_run(config: AppConfig, run_id: str) -> dict:
    """Discard a pending run's clips and mark it rejected."""
    manifest_path = find_run_manifest(config, run_id)
    if manifest_path is None:
        raise ApprovalError(f"No manifest found for run {run_id}")

    manifest = _load_manifest(manifest_path)
    if manifest.get("approval_status") != "pending":
        raise ApprovalError(
            f"Run {run_id} is not pending approval (current status: {manifest.get('approval_status')!r})"
        )

    pending_dir_str = manifest.get("pending_dir")
    pending_dir = Path(pending_dir_str) if pending_dir_str else None

    video_ids = [
        video_record["video_id"]
        for video_record, _clip in _iter_clip_entries(manifest)
    ]
    # dedupe while preserving order
    video_ids = list(dict.fromkeys(video_ids))

    conn = get_db_connection(config.state_db_path)
    try:
        delete_clips_for_run(conn, video_ids)

        manifest["approval_status"] = "rejected"
        manifest["pending_dir"] = None
        for _video_record, clip in _iter_clip_entries(manifest):
            if clip.get("cut_successful"):
                clip["cut_successful"] = False
                clip["error"] = "rejected_by_user"
        _save_manifest(manifest_path, manifest)

        set_approval_status(conn, run_id, "rejected")
    finally:
        conn.close()

    deleted = False
    if pending_dir and pending_dir.exists():
        try:
            shutil.rmtree(pending_dir)
            deleted = True
        except OSError:
            pass
        parent = pending_dir.parent
        try:
            if parent.name == "pending" and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass

    return {"run_id": run_id, "deleted_pending_dir": deleted}
