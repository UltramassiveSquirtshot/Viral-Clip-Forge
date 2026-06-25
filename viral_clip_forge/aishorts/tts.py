"""
edge-tts wrapper — synthesizes a narrator voice per beat and returns word-level
timing for karaoke captions.

edge-tts (>=7.x) needs `boundary="WordBoundary"` on Communicate to emit
per-word events; offset/duration come in 100-nanosecond ticks (1e7 ticks = 1s).
We synthesize one MP3 per beat so the timings are beat-relative; the caller
offsets them by the beat's cumulative start time on the final timeline.

Free, runs locally, no API key.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from ..utils import get_logger

log = get_logger()

_TICKS_PER_SECOND = 10_000_000  # edge-tts offsets/durations are in 100-ns ticks


@dataclass
class Word:
    text: str
    start: float   # seconds, relative to the start of this beat's audio
    end: float


@dataclass
class BeatAudio:
    index: int
    mp3_path: Path
    words: list[Word]
    duration: float   # seconds (max word end; 0 if no boundaries returned)


async def _synthesize_one(text: str, voice: str, mp3_path: Path) -> list[Word]:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    words: list[Word] = []
    with open(mp3_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / _TICKS_PER_SECOND
                dur = chunk["duration"] / _TICKS_PER_SECOND
                words.append(Word(text=chunk["text"], start=start, end=start + dur))
    return words


def synthesize_beats(
    voice: str, narrations: list[str], out_dir: Path
) -> list[BeatAudio]:
    """Synthesize one MP3 per narration. Returns BeatAudio with beat-relative words."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[BeatAudio] = []
    for i, text in enumerate(narrations):
        mp3_path = out_dir / f"beat{i:02d}.mp3"
        words = asyncio.run(_synthesize_one(text, voice, mp3_path))
        duration = max((w.end for w in words), default=0.0)
        results.append(BeatAudio(index=i, mp3_path=mp3_path, words=words, duration=duration))
        log.info("[aishorts] TTS beat %d: %.2fs, %d words → %s",
                 i, duration, len(words), mp3_path.name)
    return results


def probe_duration(ffprobe_bin: Path, path: Path) -> float:
    """Return the actual audio duration of an MP3 via ffprobe (more reliable than
    the last word boundary, which can fall slightly short of the audio tail)."""
    import subprocess
    try:
        r = subprocess.run(
            [str(ffprobe_bin), "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
        val = r.stdout.decode().strip()
        return float(val) if val else 0.0
    except Exception:
        return 0.0
