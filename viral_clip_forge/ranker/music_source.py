"""
Pixabay background-music source.

Fetches one royalty-free audio track to bake under the ranking video. Pixabay
audio is free for commercial use, no attribution required
(https://pixabay.com/service/license-summary/).

Returns None on any failure — the composer then proceeds with the clips' own
audio only, so a missing track never fails the run.

Note: Pixabay's audio API is part of their media API. If the audio endpoint is
unavailable for the account, return None gracefully.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from ..utils import get_logger
from .config import RankerConfig

log = get_logger()

_AUDIO_URL = "https://pixabay.com/api/audio/"
_USER_AGENT = "ViralClipForge-Ranker/1.0"

# Map a theme to a music mood query.
_MOOD_BY_HINT = {
    "satisfying": "calm ambient",
    "lucky": "epic suspense",
    "escape": "epic suspense",
    "animal": "uplifting acoustic",
    "nature": "calm ambient",
    "fail": "funny upbeat",
}


def _mood_query(theme: str) -> str:
    t = theme.lower()
    for hint, mood in _MOOD_BY_HINT.items():
        if hint in t:
            return mood
    return "upbeat background"


def _download(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
        return dest.exists() and dest.stat().st_size > 0
    except Exception as exc:
        log.warning("[ranker] Music download failed: %s", exc)
        dest.unlink(missing_ok=True)
        return False


def fetch_background_music(cfg: RankerConfig, theme: str) -> Path | None:
    if not cfg.app.pixabay_api_key:
        log.info("[ranker] PIXABAY_API_KEY not set — proceeding without background music")
        return None

    params = urllib.parse.urlencode({
        "key": cfg.app.pixabay_api_key,
        "q": _mood_query(theme),
        "per_page": 10,
    })
    try:
        req = urllib.request.Request(f"{_AUDIO_URL}?{params}", headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        log.warning("[ranker] Pixabay audio search failed: %s — proceeding without music", exc)
        return None

    hits = data.get("hits", [])
    if not hits:
        log.info("[ranker] No Pixabay tracks found for '%s' — proceeding without music", theme)
        return None

    # Pixabay audio hit exposes a download/preview URL; pick the first available field.
    track = hits[0]
    url = track.get("audio") or track.get("download") or track.get("previewURL")
    if not url:
        log.info("[ranker] Pixabay track has no downloadable URL — proceeding without music")
        return None

    cfg.music_dir.mkdir(parents=True, exist_ok=True)
    dest = cfg.music_dir / f"track_{track.get('id', 'x')}.mp3"
    if _download(url, dest):
        log.info("[ranker] Background music: %s", dest.name)
        return dest
    return None
