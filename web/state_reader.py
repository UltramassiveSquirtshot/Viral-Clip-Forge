"""
Read-only helpers for dashboard state.
All functions return plain dicts/lists — no Telegram strings.
Adapted from telegram_listener.py helpers.
"""

from __future__ import annotations

import ctypes
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent
_MANIFESTS_DIR = PROJECT_ROOT / "data" / "manifests"
_PIPELINE_LOCK = PROJECT_ROOT / "data" / "pipeline.lock"
_RANKER_LOCK = PROJECT_ROOT / "data" / "ranker.lock"
_ANALYTICS_LOCK = PROJECT_ROOT / "data" / "analytics.lock"
_RANKER_PENDING = PROJECT_ROOT / "data" / "ranker_pending.json"
_RANKER_POOL = PROJECT_ROOT / "data" / "ranker_pool.json"
_AISHORTS_PENDING = PROJECT_ROOT / "data" / "aishorts_pending.json"
_AISHORTS_LOCK = PROJECT_ROOT / "data" / "aishorts.lock"


def is_running(lock_path: Path) -> bool:
    if not lock_path.exists():
        return False
    try:
        pid = int(lock_path.read_text().strip())
        handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
        if handle == 0:
            lock_path.unlink(missing_ok=True)
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


def _read_manifest(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _iter_manifests(max_dirs: int = 20):
    if not _MANIFESTS_DIR.exists():
        return
    for d in sorted(_MANIFESTS_DIR.iterdir(), reverse=True)[:max_dirs]:
        m = d / "manifest.json"
        if m.exists():
            data = _read_manifest(m)
            if data:
                yield data


def get_pipeline_status() -> dict[str, Any]:
    running = is_running(_PIPELINE_LOCK)
    for data in _iter_manifests(1):
        return {
            "running": running,
            "last_run_date": data.get("run_date", "")[:10],
            "status": data.get("status", "?"),
            "clips_produced": data.get("clips_produced", 0),
            "uploads_scheduled": data.get("uploads_scheduled", 0),
            "niches": {
                niche: [
                    {
                        "title": v.get("title", ""),
                        "status": v.get("status", ""),
                        "clips": [
                            {
                                "scheduled_publish_at": c.get("scheduled_publish_at", ""),
                                "upload_status": c.get("upload_status", ""),
                            }
                            for c in v.get("clips", [])
                        ],
                    }
                    for v in nd.get("results", [])
                ]
                for niche, nd in data.get("niches", {}).items()
            },
        }
    return {"running": running, "last_run_date": None, "status": "no_runs", "clips_produced": 0, "uploads_scheduled": 0, "niches": {}}


def get_analytics_status() -> dict[str, Any]:
    running = is_running(_ANALYTICS_LOCK)
    reports_dir = PROJECT_ROOT / "data" / "analytics_reports"
    last_report = None
    if reports_dir.exists():
        md_files = sorted(reports_dir.glob("*_analytics.md"), reverse=True)
        if md_files:
            last_report = md_files[0].name[:10]
    return {"running": running, "last_report_date": last_report}


def get_next_slots(n: int = 10) -> list[dict[str, str]]:
    upcoming: list[dict[str, str]] = []
    for data in _iter_manifests(5):
        for niche, nd in data.get("niches", {}).items():
            for v in nd.get("results", []):
                for c in v.get("clips", []):
                    if c.get("upload_status") == "scheduled" and c.get("scheduled_publish_at"):
                        upcoming.append({
                            "publish_at": c["scheduled_publish_at"][:16],
                            "title": v.get("title", "")[:50],
                        })
    upcoming.sort(key=lambda x: x["publish_at"])
    return upcoming[:n]


def list_recent_runs(n: int = 10) -> list[dict[str, Any]]:
    runs = []
    for data in _iter_manifests(n):
        runs.append({
            "run_date": data.get("run_date", "")[:16],
            "status": data.get("status", "?"),
            "clips_produced": data.get("clips_produced", 0),
            "uploads_scheduled": data.get("uploads_scheduled", 0),
            "api_units_used": data.get("api_units_used", 0),
        })
    return runs


def get_ranker_pending() -> dict | None:
    if not _RANKER_PENDING.exists():
        return None
    try:
        return json.loads(_RANKER_PENDING.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_ranker_pool(theme: str | None = None) -> dict:
    if not _RANKER_POOL.exists():
        return {}
    try:
        pool = json.loads(_RANKER_POOL.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if theme:
        t = theme.lower()
        return {k: v for k, v in pool.items() if t in k.lower()}
    return pool


def get_ranker_status() -> dict[str, Any]:
    running = is_running(_RANKER_LOCK)
    pending = get_ranker_pending()
    return {
        "running": running,
        "pending": pending,
        "step": pending.get("step") if pending else None,
    }


# ---------------------------------------------------------------------------
# AI Shorts
# ---------------------------------------------------------------------------

def get_aishorts_pending() -> dict | None:
    if not _AISHORTS_PENDING.exists():
        return None
    try:
        return json.loads(_AISHORTS_PENDING.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_aishorts_status() -> dict[str, Any]:
    running = is_running(_AISHORTS_LOCK)
    pending = get_aishorts_pending()
    # Trim heavy fields (word timeline, mp3 paths) from the view payload.
    view = None
    if pending:
        view = {
            "run_id": pending.get("run_id"),
            "step": pending.get("step"),
            "title": pending.get("title"),
            "theme": pending.get("theme"),
            "hook": pending.get("hook"),
            "total_seconds": pending.get("total_seconds"),
            "drive_folder_url": pending.get("drive_folder_url"),
            "scenes": pending.get("scenes", []),
        }
    return {
        "running": running,
        "pending": view,
        "step": pending.get("step") if pending else None,
    }


def get_aishorts_image_count() -> int:
    """Count images already uploaded to the current run's Drive folder."""
    pending = get_aishorts_pending()
    if not pending or not pending.get("run_id"):
        return 0
    try:
        from viral_clip_forge.config import load_config
        from viral_clip_forge.aishorts.config import build_aishorts_config
        from viral_clip_forge.aishorts import gdrive_images
        cfg = build_aishorts_config(load_config())
        return gdrive_images.count_images(cfg, pending["run_id"])
    except Exception:
        return 0


# Hardcoded theme list — must stay in sync with ranker/pipeline.py _THEMES
RANKER_THEMES = [
    ("a", "Dashcam & Close Calls"),
    ("b", "Animal Attacks"),
    ("c", "Lucky Escapes"),
    ("d", "Engineering Fails"),
    ("e", "Natural Disasters"),
]
