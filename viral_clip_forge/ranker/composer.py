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


def _has_audio(path: Path) -> bool:
    """Return True if the file has at least one audio stream."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "compact", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return bool(result.stdout.strip())


def _clip_duration(cfg: RankerConfig, src: Path) -> float:
    """Return actual duration of src via ffprobe; fall back to cfg.clip_seconds."""
    try:
        r = subprocess.run(
            [str(cfg.app.ffprobe_bin),
             "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
        val = r.stdout.decode().strip()
        dur = float(val) if val else 0.0
        return dur if dur > 0 else cfg.clip_seconds
    except Exception:
        return cfg.clip_seconds


def _normalize_clip(cfg: RankerConfig, src: Path, dest: Path) -> bool:
    duration = _clip_duration(cfg, src)
    vf = (
        f"scale={cfg.width}:{cfg.height}:force_original_aspect_ratio=increase,"
        f"crop={cfg.width}:{cfg.height},fps=30,setsar=1"
    )
    # Always use explicit anullsrc so the output is guaranteed to have an audio
    # stream even when the source clip has none. Pexels portrait clips often ship
    # without audio; -apad alone exits 0 but produces a video-only file.
    cmd = [
        str(cfg.ffmpeg_bin),
        "-i", str(src),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", f"{duration:.3f}",
        "-filter_complex",
        f"[0:v:0]{vf}[vout]",
        "-map", "[vout]", "-map", "1:a:0",
        "-c:v", "libx264", "-profile:v", "main", "-crf", "20", "-preset", "fast",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
        "-movflags", "+faststart",
        "-y", str(dest),
    ]
    stderr, rc = _run_ffmpeg(cmd)
    if rc != 0 or not dest.exists() or not _has_audio(dest):
        # Fallback: source may have its own audio — mix it with silence to guarantee
        # presence, then let amix produce the output audio stream.
        dest.unlink(missing_ok=True)
        cmd2 = [
            str(cfg.ffmpeg_bin),
            "-i", str(src),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t", f"{duration:.3f}",
            "-filter_complex",
            f"[0:v:0]{vf}[vout];[1:a:0]anull[aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-profile:v", "main", "-crf", "20", "-preset", "fast",
            "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
            "-movflags", "+faststart",
            "-y", str(dest),
        ]
        stderr, rc = _run_ffmpeg(cmd2)
        if rc != 0 or not dest.exists():
            log.error("[ranker] Normalize failed for %s: %s", src.name, stderr[-300:])
            return False
    return True


def _build_drawtext_subgraph(cfg: RankerConfig, title: str, labels: list[str], n: int,
                             in_pad: str, out_pad: str,
                             segment_durations: list[float] | None = None) -> str:
    """Return a filter_complex subgraph that chains all drawtext filters.

    Each filter gets its own intermediate pad label so FFmpeg's filter_complex
    parser can resolve the graph correctly. Comma-chaining only works in -vf,
    not inside filter_complex where explicit labels are required.

    segment_durations: actual duration of each clip (seconds). Falls back to
    cfg.clip_seconds for any missing entry.
    """
    font = _ff_path(cfg.font_path)
    W = cfg.width

    # Persistent title banner near the top. Auto-size so long titles fit the width.
    margin = 0.92
    max_text_w = W * margin
    glyph_ratio = 0.58
    n_chars = max(1, len(title))
    fit_fs = int(max_text_w / (n_chars * glyph_ratio))
    title_fs = max(34, min(int(W * 0.075), fit_fs))

    # Timed rank labels, list-style down the left. Countdown: #5 first … #1 last.
    label_fs = max(40, int(W * 0.055))
    line_h = int(label_fs * 1.6)
    list_top = int(cfg.height * 0.30)

    all_filters: list[str] = []
    # title drawtext
    all_filters.append(
        f"drawtext=fontfile='{font}':text='{_escape_drawtext(title)}':"
        f"fontsize={title_fs}:fontcolor=white:borderw=4:bordercolor=black:"
        f"box=1:boxcolor=black@0.5:boxborderw=20:"
        f"x=(w-text_w)/2:y=120"
    )
    # rank label drawtexts
    durations = segment_durations or []
    for i, label in enumerate(labels[:n]):
        rank = n - i
        seg_i = durations[i] if i < len(durations) else cfg.clip_seconds
        start = sum(durations[j] if j < len(durations) else cfg.clip_seconds for j in range(i))
        end = start + seg_i
        y = list_top + i * line_h
        text = f"#{rank}  {label}"
        all_filters.append(
            f"drawtext=fontfile='{font}':text='{_escape_drawtext(text)}':"
            f"fontsize={label_fs}:fontcolor=yellow:borderw=4:bordercolor=black:"
            f"x=60:y={y}:enable='between(t\\,{start:.2f}\\,{end:.2f})'"
        )

    # Wire them up with explicit pad labels between each stage.
    parts: list[str] = []
    cur_in = in_pad
    for idx, filt in enumerate(all_filters):
        cur_out = out_pad if idx == len(all_filters) - 1 else f"[dt{idx}]"
        parts.append(f"{cur_in}{filt}{cur_out}")
        cur_in = cur_out
    return ";".join(parts)


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
    norm_durations = [_clip_duration(cfg, p) for p in norm_paths]

    # Stage 2: concat + optional overlays (+ music).
    out_name = f"ranking_{datetime.now().strftime('%Y%m%d-%H%M%S')}.mp4"
    out_path = cfg.output_dir / out_name

    cmd: list[str] = [str(cfg.ffmpeg_bin)]
    for p in norm_paths:
        cmd += ["-i", str(p)]
    if music:
        cmd += ["-i", str(music)]

    # concat filter over the video+audio of each normalized input
    concat_inputs = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))

    # Skip drawtext when no labels/title provided (e.g. ranker v3 flow — user adds text in CapCut)
    if labels and title:
        drawtext_subgraph = _build_drawtext_subgraph(
            cfg, title, labels, n, "[cv]", "[vout]", segment_durations=norm_durations
        )
        filtergraph = (
            f"{concat_inputs}concat=n={n}:v=1:a=1[cv][ca];"
            f"{drawtext_subgraph}"
        )
        video_map = "[vout]"
    else:
        filtergraph = f"{concat_inputs}concat=n={n}:v=1:a=1[cv][ca]"
        video_map = "[cv]"

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
        "-map", video_map, "-map", audio_map,
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
