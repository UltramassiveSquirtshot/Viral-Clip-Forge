"""
Subtitle-aware analysis for the ranker pipeline.

Parses .en.srt files (already downloaded by yt-dlp) to extract excitement signals
and natural clip boundaries. Used alongside FFmpeg signal analysis to produce
better timestamp suggestions for compilation videos.

No external dependencies — pure Python SRT parsing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..utils import get_logger

log = get_logger()

# Words/phrases that indicate exciting/interesting moments (case-insensitive).
# Matched as whole words or substrings within a subtitle line.
_EXCITEMENT_PATTERNS = [
    # Exclamations / reactions
    r"\boh my\b", r"\boh no\b", r"\boh yes\b", r"\bomg\b", r"\bwtf\b",
    r"\bwow\b", r"\bwhoa\b", r"\bno way\b",
    # High-energy descriptors
    r"\binsane\b", r"\bincredible\b", r"\bamazing\b", r"\bunbelievable\b",
    r"\bimpossible\b", r"\bluckiest\b", r"\bmiraculous\b",
    # Action/event words typical in compilations
    r"\bclose call\b", r"\bnarrow escape\b", r"\bbarely made it\b",
    r"\bsurvived\b", r"\bescaped\b", r"\bdodged\b",
    r"\bboom\b", r"\bcrash\b", r"\blucky\b", r"\bmiracle\b",
    # Rank/list markers (very common in Top-N compilations)
    r"\bnumber \d\b", r"\b#\d\b", r"\btop \d\b", r"\brank \d\b",
    r"\bfirst place\b", r"\bsecond place\b",
    # Momentum/transition phrases
    r"\blet's go\b", r"\bhere we go\b", r"\bjust in time\b",
]

_EXCITEMENT_RE = re.compile(
    "|".join(_EXCITEMENT_PATTERNS),
    re.IGNORECASE,
)

_SRT_TIMESTAMP_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
)


@dataclass
class SubtitleEntry:
    start_sec: float
    end_sec: float
    text: str

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec

    def excitement(self) -> float:
        """Return excitement score 0–1 based on keyword matches."""
        matches = len(_EXCITEMENT_RE.findall(self.text))
        # Saturate at 3 matches → score 1.0
        return min(1.0, matches / 3.0)


def _ts_to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(path: Path) -> list[SubtitleEntry]:
    """
    Parse a .srt file into a list of SubtitleEntry sorted by start time.
    Handles both SRT and VTT (which yt-dlp converts to SRT).
    Returns [] on any parse error.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        log.warning("[subtitle] Could not read %s: %s", path, exc)
        return []

    entries: list[SubtitleEntry] = []
    # Split into blocks by blank line
    blocks = re.split(r"\n\s*\n", raw.strip())
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue
        # Find timestamp line (may be first or second line)
        ts_line = None
        text_lines: list[str] = []
        for i, line in enumerate(lines):
            m = _SRT_TIMESTAMP_RE.match(line.strip())
            if m:
                ts_line = m
                text_lines = lines[i + 1:]
                break
        if ts_line is None:
            continue
        start = _ts_to_sec(*ts_line.group(1, 2, 3, 4))
        end = _ts_to_sec(*ts_line.group(5, 6, 7, 8))
        # Strip HTML tags and special chars from subtitle text
        text = " ".join(text_lines).strip()
        text = re.sub(r"<[^>]+>", "", text)        # remove <i>, <b>, etc.
        text = re.sub(r"\{[^}]+\}", "", text)       # remove {style} blocks
        text = text.strip()
        if text and end > start:
            entries.append(SubtitleEntry(start_sec=start, end_sec=end, text=text))

    entries.sort(key=lambda e: e.start_sec)
    log.info("[subtitle] Loaded %d entries from %s", len(entries), path.name)
    return entries


def find_natural_end(
    entries: list[SubtitleEntry],
    start_sec: float,
    min_dur: float = 4.0,
    max_dur: float = 15.0,
    pause_threshold: float = 0.8,
) -> float:
    """
    Given a clip start, find the natural end time by looking for the first
    subtitle pause > pause_threshold seconds after start + min_dur,
    without exceeding start + max_dur.

    Falls back to start + max_dur if no natural boundary is found,
    or to the end of the last subtitle before start + max_dur.
    """
    if not entries:
        return start_sec + min_dur

    earliest_end = start_sec + min_dur
    hard_end = start_sec + max_dur

    # Find entries that are active during the window
    window_entries = [e for e in entries if e.start_sec < hard_end and e.end_sec > start_sec]
    if not window_entries:
        return hard_end

    # Look for pause after earliest_end
    for i in range(len(window_entries) - 1):
        curr = window_entries[i]
        nxt = window_entries[i + 1]
        gap = nxt.start_sec - curr.end_sec
        if curr.end_sec >= earliest_end and gap >= pause_threshold:
            # End right after the current subtitle
            return min(curr.end_sec + 0.1, hard_end)

    # No natural pause — end after the last subtitle in the window
    last_in_window = max((e for e in window_entries if e.start_sec < hard_end),
                         key=lambda e: e.end_sec, default=None)
    if last_in_window and last_in_window.end_sec >= earliest_end:
        return min(last_in_window.end_sec + 0.1, hard_end)

    return hard_end


def score_subtitle_windows(
    entries: list[SubtitleEntry],
    video_duration: float,
    min_dur: float = 4.0,
    max_dur: float = 15.0,
) -> list[tuple[float, float, float]]:
    """
    Slide a window over subtitle entries and return a list of
    (start_sec, end_sec, excitement_score) tuples, one per subtitle entry
    that has non-zero excitement.

    Each window starts at a subtitle's start_sec and ends at find_natural_end().
    Score is the sum of excitement over all entries in the window, normalised 0–1.
    """
    if not entries:
        return []

    results: list[tuple[float, float, float]] = []
    for i, entry in enumerate(entries):
        start = entry.start_sec
        if start + min_dur > video_duration:
            break

        end = find_natural_end(entries, start, min_dur, max_dur)
        end = min(end, video_duration)

        # Score = weighted sum of excitement in window
        # Entries closer to the start of the window get full weight
        window = [e for e in entries if e.start_sec >= start and e.start_sec < end]
        if not window:
            continue

        raw_score = sum(e.excitement() for e in window)
        # Density bonus: more entries per second = more content
        density = len(window) / max(end - start, 1.0)
        density_bonus = min(1.0, density / 3.0) * 0.2

        # Normalize: 3 excited entries in window → raw_score=1.0
        norm_score = min(1.0, raw_score / 3.0) * 0.8 + density_bonus

        if norm_score > 0.05:  # skip near-zero windows
            results.append((start, end, norm_score))

    return results
