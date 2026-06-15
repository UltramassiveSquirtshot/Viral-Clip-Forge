"""
Pexels stock-footage source.

Downloads one PORTRAIT (9:16-ish) stock clip per rank, using the per-rank
`search_queries` from the script so each segment matches its label.

Pexels license: free for commercial use, no attribution required
(https://www.pexels.com/license/). The composite is still flagged as
AI/synthetic media on upload because it is machine-assembled.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from pathlib import Path

from ..utils import get_logger
from .config import RankerConfig

log = get_logger()

_SEARCH_URL = "https://api.pexels.com/videos/search"
_USER_AGENT = "ViralClipForge-Ranker/1.0"


def _api_get(url: str, api_key: str) -> dict:
    import json
    req = urllib.request.Request(url, headers={
        "Authorization": api_key,
        "User-Agent": _USER_AGENT,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _pick_portrait_file(video: dict, min_width: int) -> str | None:
    """Choose a downloadable .mp4 file: portrait, width >= min_width, smallest that qualifies."""
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4"]
    portrait = [
        f for f in files
        if f.get("width") and f.get("height") and f["height"] >= f["width"]
    ]
    pool = portrait or files
    # Prefer files at least min_width wide; among those pick the smallest (faster DL).
    big_enough = [f for f in pool if (f.get("width") or 0) >= min_width]
    candidates = big_enough or pool
    if not candidates:
        return None
    candidates.sort(key=lambda f: (f.get("width") or 0))
    return candidates[0].get("link")


def _download(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
        return dest.exists() and dest.stat().st_size > 0
    except Exception as exc:
        log.warning("[ranker] Download failed (%s): %s", url, exc)
        dest.unlink(missing_ok=True)
        return False


def _search_one(cfg: RankerConfig, query: str, used_ids: set[int]) -> tuple[int, str] | None:
    """Return (video_id, download_link) for the first suitable unused portrait clip."""
    if not cfg.app.pexels_api_key:
        raise RuntimeError("PEXELS_API_KEY is not set in .env")

    params = urllib.parse.urlencode({
        "query": query,
        "orientation": "portrait",
        "size": "medium",
        "per_page": 15,
    })
    try:
        data = _api_get(f"{_SEARCH_URL}?{params}", cfg.app.pexels_api_key)
    except Exception as exc:
        log.warning("[ranker] Pexels search failed for '%s': %s", query, exc)
        return None

    for video in data.get("videos", []):
        vid = video.get("id")
        if vid in used_ids:
            continue
        link = _pick_portrait_file(video, cfg.width)
        if link:
            return vid, link
    return None


def fetch_ranking_clips(cfg: RankerConfig, search_queries: list[str]) -> list[Path]:
    """
    Download one clip per query (rank order preserved). Falls back to the theme word
    if a query yields nothing. Ranks that find no footage are dropped (logged).
    """
    cfg.download_dir.mkdir(parents=True, exist_ok=True)
    used_ids: set[int] = set()
    paths: list[Path] = []

    for i, query in enumerate(search_queries, start=1):
        hit = _search_one(cfg, query, used_ids)
        if hit is None:
            log.warning("[ranker] No footage for rank %d query '%s'", i, query)
            continue
        vid, link = hit
        used_ids.add(vid)
        dest = cfg.download_dir / f"rank{i:02d}_{vid}.mp4"
        if _download(link, dest):
            paths.append(dest)
            log.info("[ranker] Downloaded rank %d footage: %s", i, dest.name)
        else:
            log.warning("[ranker] Failed to download rank %d footage for '%s'", i, query)

    return paths
