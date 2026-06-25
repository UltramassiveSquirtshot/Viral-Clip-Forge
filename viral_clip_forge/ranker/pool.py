"""
Persistent candidate pool for the ranker pipeline.

Stores YouTube CC-BY video candidates per theme so they survive across sessions.
Each entry tracks view count, download state, and which time-ranges have already
been cut into ranking clips — so the same video can be reused in multiple
classifications without repeating the same footage.

File: data/ranker_pool.json
Shape:
  {
    "lucky escapes": {
      "VIDEO_ID": {
        "title": "...", "channel": "...", "views": 890000,
        "duration_sec": 480, "downloaded": false,
        "local_path": "downloads/ranker/VIDEO_ID.mp4",
        "use_count": 0, "used_ranges": [[start, end], ...]
      }
    }
  }
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def load_pool(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_pool(path: Path, pool: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(pool, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def add_candidates(pool: dict, theme: str, candidates: list[dict]) -> None:
    """Merge new candidates into the pool for a theme (no duplicates by video_id)."""
    bucket = pool.setdefault(theme, {})
    for c in candidates:
        vid = c["id"]
        if vid not in bucket:
            bucket[vid] = {
                "title": c.get("title", ""),
                "channel": c.get("channel", ""),
                "views": c.get("views", 0),
                "duration_sec": c.get("duration_sec", 0),
                "downloaded": False,
                "local_path": None,
                "use_count": 0,
                "used_ranges": [],
            }


def get_theme_candidates(pool: dict, theme: str) -> list[dict]:
    """Return candidates for a theme sorted by views desc, with video_id injected."""
    bucket = pool.get(theme, {})
    result = []
    for vid, info in bucket.items():
        entry = dict(info)
        entry["id"] = vid
        result.append(entry)
    result.sort(key=lambda x: x.get("views", 0), reverse=True)
    return result


def get_used_ranges(pool: dict, theme: str, video_id: str) -> list[list[float]]:
    return pool.get(theme, {}).get(video_id, {}).get("used_ranges", [])


def mark_downloaded(pool: dict, theme: str, video_id: str, local_path: str) -> None:
    if theme in pool and video_id in pool[theme]:
        pool[theme][video_id]["downloaded"] = True
        pool[theme][video_id]["local_path"] = local_path


def mark_used(pool: dict, theme: str, video_id: str, start_sec: float, end_sec: float) -> None:
    if theme in pool and video_id in pool[theme]:
        pool[theme][video_id]["use_count"] += 1
        pool[theme][video_id]["used_ranges"].append([start_sec, end_sec])


def ranges_overlap(a_start: float, a_end: float, used: list[list[float]], min_gap: float = 10.0) -> bool:
    """Return True if [a_start, a_end] overlaps with any used range (with a minimum gap buffer)."""
    for s, e in used:
        if a_start < (e + min_gap) and a_end > (s - min_gap):
            return True
    return False
