"""
Generates YouTube-optimized titles, descriptions, and tags for CC-BY clip uploads.
"""

from datetime import datetime


def _fmt(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def generate_title(video_title: str, start_sec: float, end_sec: float, clip_index: int) -> str:
    """Max 100 chars. Format: '<truncated title> [<start>-<end>]'"""
    timestamp = f"[{_fmt(start_sec)}-{_fmt(end_sec)}]"
    max_title_len = 97 - len(timestamp)
    truncated = video_title if len(video_title) <= max_title_len else video_title[:max_title_len - 3] + "..."
    return f"{truncated} {timestamp}"


def generate_description(
    video_title: str,
    channel_name: str,
    video_url: str,
    niche_name: str,
    start_sec: float,
    end_sec: float,
) -> str:
    timestamp_url = f"{video_url}&t={int(start_sec)}"
    return (
        f"Highlight clip from {_fmt(start_sec)} to {_fmt(end_sec)} of the original video below.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 ORIGINAL SOURCE (Creative Commons CC-BY)\n"
        f"Title: {video_title}\n"
        f"Channel: {channel_name}\n"
        f"URL: {timestamp_url}\n"
        f"License: Creative Commons Attribution (CC BY)\n"
        f"https://creativecommons.org/licenses/by/4.0/\n\n"
        f"This clip is a highlight extracted from the above CC-BY licensed video.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🤖 AI-assisted editing: FFmpeg scene detection + audio peak analysis"
    )


def generate_tags(niche_name: str, video_title: str) -> list[str]:
    base = ["creative commons", "cc by", "highlights", "clip"]
    niche_tags = {
        "tech": ["tech", "technology", "ai", "artificial intelligence", "programming", "software", "gadgets"],
        "finance": ["finance", "investing", "money", "economy", "stocks", "crypto"],
    }
    tags = base + niche_tags.get(niche_name.lower(), [niche_name.lower()])
    # Add first two words from video title as tags if they're meaningful
    words = [w.lower().strip(".,!?") for w in video_title.split()[:3] if len(w) > 3]
    tags += words
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    # YouTube tag limit: 500 chars total
    result: list[str] = []
    total = 0
    for t in unique:
        if total + len(t) + 1 > 500:
            break
        result.append(t)
        total += len(t) + 1
    return result
