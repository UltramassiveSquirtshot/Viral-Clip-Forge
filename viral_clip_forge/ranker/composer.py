"""
FFmpeg composer for ranking Shorts.

Two-stage assembly (most reliable for mixed-source stock clips):
  1. Normalize each clip → identical 1080x1920 / H.264 / 30fps / AAC 44.1k stereo,
     trimmed to clip_seconds (same scale+crop chain as the clip pipeline's
     cut_clip_shorts).
  2. concat the normalized segments, then drawtext the persistent title banner and
     one timed rank label per segment (enable='between(t,start,end)'), and amix
     a background music track if present.

drawtext is available because FFmpeg here is built with libfreetype + fontconfig.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..utils import get_logger
from .config import RankerConfig

log = get_logger()


@dataclass
class RankerResult:
    output_path: Path | None
    title: str
    labels: list[str]
    n_segments: int
    success: bool
    error: str | None


def _run_ffmpeg(cmd: list[str], timeout: int = 600) -> tuple[str, int]:
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
    )
    return result.stderr.decode("utf-8", errors="replace"), result.returncode


def _escape_drawtext(text: str) -> str:
    """Escape characters special to FFmpeg drawtext text values."""
    out = text.replace("\\", "\\\\")
    out = out.replace(":", r"\:")
    out = out.replace("'", r"\'")
    out = out.replace("%", r"\%")
    return out


def _ff_path(p: str) -> str:
    """Normalize a Windows path for use inside an FFmpeg filter (forward slashes,
    escape the drive-letter colon)."""
    s = str(p).replace("\\", "/")
    s = s.replace(":", r"\:")
    return s


def _normalize_clip(cfg: RankerConfig, src: Path, dest: Path) -> bool:
    vf = (
        f"scale={cfg.width}:{cfg.height}:force_original_aspect_ratio=increase,"
        f"crop={cfg.width}:{cfg.height},fps=30,setsar=1"
    )
    cmd = [
        str(cfg.ffmpeg_bin),
        "-i", str(src),
        "-t", f"{cfg.clip_seconds:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-profile:v", "main", "-crf", "20", "-preset", "fast",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
        # If a source has no audio, synthesize silence so concat has a uniform stream.
        "-af", "apad",
        "-shortest",
        "-movflags", "+faststart",
        "-y", str(dest),
    ]
    stderr, rc = _run_ffmpeg(cmd)
    if rc != 0 or not dest.exists():
        # Retry adding an explicit silent audio source for clips with no audio track.
        cmd2 = [
            str(cfg.ffmpeg_bin),
            "-i", str(src),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t", f"{cfg.clip_seconds:.3f}",
            "-vf", vf,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-profile:v", "main", "-crf", "20", "-preset", "fast",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart",
            "-y", str(dest),
        ]
        stderr, rc = _run_ffmpeg(cmd2)
        if rc != 0 or not dest.exists():
            log.error("[ranker] Normalize failed for %s: %s", src.name, stderr[-300:])
            return False
    return True


def _build_drawtext_filters(cfg: RankerConfig, title: str, labels: list[str], n: int) -> str:
    """Return the chain of drawtext filters (applied to the concatenated video)."""
    font = _ff_path(cfg.font_path)
    seg = cfg.clip_seconds
    W = cfg.width

    filters = []

    # Persistent title banner near the top. Auto-size so long titles fit the width:
    # estimate ~0.58*fontsize per glyph (Arial Bold avg), leave ~8% side margin.
    margin = 0.92
    max_text_w = W * margin
    glyph_ratio = 0.58
    n_chars = max(1, len(title))
    fit_fs = int(max_text_w / (n_chars * glyph_ratio))
    title_fs = max(34, min(int(W * 0.075), fit_fs))
    filters.append(
        f"drawtext=fontfile='{font}':text='{_escape_drawtext(title)}':"
        f"fontsize={title_fs}:fontcolor=white:borderw=4:bordercolor=black:"
        f"box=1:boxcolor=black@0.5:boxborderw=20:"
        f"x=(w-text_w)/2:y=120"
    )

    # Timed rank labels, list-style down the left. Countdown order: #5 first … #1 last.
    label_fs = max(40, int(W * 0.055))
    line_h = int(label_fs * 1.6)
    list_top = int(cfg.height * 0.30)
    for i, label in enumerate(labels[:n]):
        rank = n - i  # first segment is the highest number (#5), last is #1
        start = i * seg
        end = (i + 1) * seg
        y = list_top + i * line_h
        text = f"#{rank}  {label}"
        filters.append(
            f"drawtext=fontfile='{font}':text='{_escape_drawtext(text)}':"
            f"fontsize={label_fs}:fontcolor=yellow:borderw=4:bordercolor=black:"
            f"x=60:y={y}:enable='between(t\\,{start:.2f}\\,{end:.2f})'"
        )

    return ",".join(filters)


def compose(
    cfg: RankerConfig,
    clips: list[Path],
    title: str,
    labels: list[str],
    music: Path | None,
) -> RankerResult:
    if not clips:
        return RankerResult(None, title, labels, 0, False, "no clips to compose")

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    work = cfg.download_dir / "normalized"
    work.mkdir(parents=True, exist_ok=True)

    # Stage 1: normalize each clip.
    norm_paths: list[Path] = []
    for i, src in enumerate(clips, start=1):
        dest = work / f"norm{i:02d}.mp4"
        if _normalize_clip(cfg, src, dest):
            norm_paths.append(dest)

    if not norm_paths:
        return RankerResult(None, title, labels, 0, False, "all clips failed to normalize")

    n = len(norm_paths)

    # Stage 2: concat + overlays (+ music).
    out_name = f"ranking_{datetime.now().strftime('%Y%m%d-%H%M%S')}.mp4"
    out_path = cfg.output_dir / out_name

    cmd: list[str] = [str(cfg.ffmpeg_bin)]
    for p in norm_paths:
        cmd += ["-i", str(p)]
    if music:
        cmd += ["-i", str(music)]

    # concat filter over the video+audio of each normalized input
    concat_inputs = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
    drawtext_chain = _build_drawtext_filters(cfg, title, labels, n)

    filtergraph = (
        f"{concat_inputs}concat=n={n}:v=1:a=1[cv][ca];"
        f"[cv]{drawtext_chain}[vout]"
    )

    if music:
        music_idx = n  # music is the last input
        # Duck the music under the original audio; total duration follows the video.
        filtergraph += (
            f";[{music_idx}:a:0]volume=0.35[bg];"
            f"[ca][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = "[ca]"

    cmd += [
        "-filter_complex", filtergraph,
        "-map", "[vout]", "-map", audio_map,
        "-c:v", "libx264", "-profile:v", "main", "-crf", "20", "-preset", "fast",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
        "-movflags", "+faststart",
        "-y", str(out_path),
    ]

    log.info("[ranker] Composing %d segments → %s", n, out_name)
    stderr, rc = _run_ffmpeg(cmd)
    if rc != 0 or not out_path.exists():
        log.error("[ranker] Compose failed (rc=%d): %s", rc, stderr[-400:])
        out_path.unlink(missing_ok=True)
        return RankerResult(None, title, labels, n, False, f"compose failed: {stderr[-200:]}")

    size_mb = out_path.stat().st_size / 1_048_576
    log.info("[ranker] Composed %s (%.1f MB, %d segments)", out_name, size_mb, n)
    return RankerResult(out_path, title, labels, n, True, None)
