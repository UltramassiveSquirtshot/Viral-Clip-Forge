"""
Karaoke captions (.ass) for AI-Shorts.

Builds an Advanced SubStation Alpha file with word-by-word highlight ("bouncy"
bright/punchy style). Each caption line groups a few consecutive words; inside a
line the `\\k` tag advances the highlight per word, so the active word pops in a
saturated colour while the rest stay white.

ASS is b_urned by FFmpeg's `subtitles=` filter, which the project already uses
in clip_cutter.py. We render at the final 1080x1920 resolution so PlayRes matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class TimedWord:
    text: str
    start: float   # absolute seconds on the final timeline
    end: float


# ASS colours are &HAABBGGRR (alpha, blue, green, red), alpha 00 = opaque.
_PRIMARY = "&H00FFFFFF"      # inactive words: white
_HIGHLIGHT = "&H0000F2FF"    # active word: saturated yellow (R=255,G=242,B=0)
_OUTLINE = "&H00000000"      # black outline


def _ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def _group_words(words: list[TimedWord], per_line: int) -> list[list[TimedWord]]:
    return [words[i:i + per_line] for i in range(0, len(words), per_line)]


def build_ass(
    words: list[TimedWord],
    width: int,
    height: int,
    font: str = "Arial",
    words_per_line: int = 3,
    out_path: Path | None = None,
) -> str:
    """Render a karaoke ASS string (and optionally write it). Bright/punchy style:
    big bold text, thick outline, bottom-centred, active word highlighted."""
    font_size = int(height * 0.052)
    margin_v = int(height * 0.16)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,{font},{font_size},{_PRIMARY},{_HIGHLIGHT},{_OUTLINE},&H64000000,-1,0,0,0,100,100,0,0,1,6,3,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = [header]
    for group in _group_words(words, words_per_line):
        if not group:
            continue
        start = group[0].start
        end = group[-1].end
        # Build karaoke text: each word gets \k<centiseconds> = its own duration.
        # Active word turns SecondaryColour (highlight) during its \k span.
        parts = []
        for w in group:
            dur_cs = max(1, int(round((w.end - w.start) * 100)))
            parts.append(f"{{\\k{dur_cs}}}{_escape(w.text)} ")
        text = "".join(parts).rstrip()
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Pop,,0,0,0,,{text}"
        )

    ass = "\n".join(lines) + "\n"
    if out_path is not None:
        out_path.write_text(ass, encoding="utf-8")
    return ass
