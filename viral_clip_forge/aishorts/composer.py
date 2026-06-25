"""
FFmpeg composer for AI-Shorts.

Two-stage assembly:
  1. For each image, render a SILENT 1080x1920 Ken Burns segment lasting that
     image's on-screen duration (= delta to the next image's timestamp; the last
     image runs to the end of the audio). Bright/punchy = noticeable zoom drift,
     alternating in/out per image.
  2. concat the video segments, map a SINGLE pre-concatenated audio track (so
     image↔voice sync stays correct even if the user adds images at intermediate
     timestamps), burn the karaoke .ass captions, and draw a persistent bright
     title banner.

Reuses the ranker composer's _run_ffmpeg / _ff_path / _escape_drawtext patterns.
drawtext + subtitles work because this FFmpeg is built with libfreetype/libass.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..utils import get_logger
from .config import AiShortsConfig

log = get_logger()


@dataclass
class AiShortsResult:
    output_path: Path | None
    title: str
    n_images: int
    duration: float
    success: bool
    error: str | None


def _run_ffmpeg(cmd: list[str], timeout: int = 900) -> tuple[str, int]:
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
    )
    return result.stderr.decode("utf-8", errors="replace"), result.returncode


def _escape_drawtext(text: str) -> str:
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


def _probe_duration(cfg: AiShortsConfig, path: Path) -> float:
    try:
        r = subprocess.run(
            [str(cfg.ffprobe_bin), "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
        val = r.stdout.decode().strip()
        return float(val) if val else 0.0
    except Exception:
        return 0.0


def _concat_audio(cfg: AiShortsConfig, mp3s: list[Path], dest: Path) -> bool:
    """Concatenate beat MP3s into a single AAC m4a, re-encoded for clean joins."""
    cmd = [str(cfg.ffmpeg_bin)]
    for p in mp3s:
        cmd += ["-i", str(p)]
    n = len(mp3s)
    concat_inputs = "".join(f"[{i}:a:0]" for i in range(n))
    cmd += [
        "-filter_complex", f"{concat_inputs}concat=n={n}:v=0:a=1[aout]",
        "-map", "[aout]",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
        "-y", str(dest),
    ]
    stderr, rc = _run_ffmpeg(cmd)
    if rc != 0 or not dest.exists():
        log.error("[aishorts] Audio concat failed (rc=%d): %s", rc, stderr[-300:])
        return False
    return True


def _ken_burns_segment(cfg: AiShortsConfig, image: Path, dur: float,
                       dest: Path, zoom_in: bool) -> bool:
    """Render one silent Ken Burns video segment from a still image."""
    W, H, fps = cfg.width, cfg.height, cfg.fps
    frames = max(1, int(round(dur * fps)))
    step = cfg.ken_burns_zoom_step
    zmax = cfg.ken_burns_zoom_max
    if zoom_in:
        zexpr = f"min(zoom+{step},{zmax})"
    else:
        # start zoomed-in and drift out toward 1.0
        zexpr = f"if(eq(on,0),{zmax},max(zoom-{step},1.0))"

    # Pre-scale large so zoompan has resolution to crop into; then zoompan to
    # final size. Pad to avoid odd-dimension issues, center the pan.
    vf = (
        f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,"
        f"crop={W*2}:{H*2},"
        f"zoompan=z='{zexpr}':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={W}x{H}:fps={fps},setsar=1,format=yuv420p"
    )
    cmd = [
        str(cfg.ffmpeg_bin),
        "-loop", "1", "-i", str(image),
        "-t", f"{dur:.3f}",
        "-filter_complex", f"[0:v]{vf}[vout]",
        "-map", "[vout]",
        "-c:v", "libx264", "-profile:v", "main", "-crf", "20", "-preset", "fast",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-an", "-movflags", "+faststart",
        "-y", str(dest),
    ]
    stderr, rc = _run_ffmpeg(cmd)
    if rc != 0 or not dest.exists():
        log.error("[aishorts] Ken Burns segment failed for %s (rc=%d): %s",
                  image.name, rc, stderr[-300:])
        return False
    return True


def _title_drawtext(cfg: AiShortsConfig, title: str, in_pad: str, out_pad: str) -> str:
    """Persistent bright/punchy title banner near the top (yellow box, black text)."""
    font = _ff_path(cfg.font_path)
    W = cfg.width
    # Auto-size so long titles fit width.
    margin = 0.9
    glyph_ratio = 0.6
    n_chars = max(1, len(title))
    fit_fs = int((W * margin) / (n_chars * glyph_ratio))
    fs = max(40, min(int(W * 0.085), fit_fs))
    return (
        f"{in_pad}drawtext=fontfile='{font}':text='{_escape_drawtext(title.upper())}':"
        f"fontsize={fs}:fontcolor=black:borderw=2:bordercolor=white:"
        f"box=1:boxcolor=yellow@0.9:boxborderw=22:"
        f"x=(w-text_w)/2:y=110{out_pad}"
    )


def compose(
    cfg: AiShortsConfig,
    images: list[tuple[float, Path]],   # (timestamp_seconds, image_path), sorted
    audio_mp3s: list[Path],
    ass_path: Path | None,
    title: str,
    work_dir: Path,
) -> AiShortsResult:
    if not images:
        return AiShortsResult(None, title, 0, 0.0, False, "no images to compose")
    if not audio_mp3s:
        return AiShortsResult(None, title, 0, 0.0, False, "no audio to compose")

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Single audio track from all beat MP3s.
    audio_path = work_dir / "narration.m4a"
    if not _concat_audio(cfg, audio_mp3s, audio_path):
        return AiShortsResult(None, title, 0, 0.0, False, "audio concat failed")
    total_audio = _probe_duration(cfg, audio_path)
    if total_audio <= 0:
        return AiShortsResult(None, title, 0, 0.0, False, "audio has zero duration")

    # Per-image durations from timestamps; last image runs to end of audio.
    timestamps = [ts for ts, _ in images]
    durations: list[float] = []
    for i in range(len(images)):
        start = timestamps[i]
        end = timestamps[i + 1] if i + 1 < len(images) else total_audio
        durations.append(max(0.2, end - start))

    # Stage 1: render each Ken Burns segment.
    seg_dir = work_dir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    seg_paths: list[Path] = []
    for i, ((_, img), dur) in enumerate(zip(images, durations)):
        dest = seg_dir / f"seg{i:02d}.mp4"
        if _ken_burns_segment(cfg, img, dur, dest, zoom_in=(i % 2 == 0)):
            seg_paths.append(dest)
        else:
            return AiShortsResult(None, title, 0, 0.0, False, f"segment {i} failed")

    n = len(seg_paths)

    # Stage 2: concat video, attach audio, burn captions + title.
    out_name = f"aishorts_{datetime.now().strftime('%Y%m%d-%H%M%S')}.mp4"
    out_path = cfg.output_dir / out_name

    cmd: list[str] = [str(cfg.ffmpeg_bin)]
    for p in seg_paths:
        cmd += ["-i", str(p)]
    audio_idx = n
    cmd += ["-i", str(audio_path)]

    concat_inputs = "".join(f"[{i}:v:0]" for i in range(n))
    filtergraph = f"{concat_inputs}concat=n={n}:v=1:a=0[cv]"

    cur = "[cv]"
    # Burn karaoke captions if present.
    if ass_path is not None and ass_path.exists():
        ass_arg = _ff_path(str(ass_path))
        filtergraph += f";{cur}subtitles='{ass_arg}'[sv]"
        cur = "[sv]"
    # Title banner.
    if title.strip():
        filtergraph += ";" + _title_drawtext(cfg, title, cur, "[vout]")
        video_map = "[vout]"
    else:
        video_map = cur

    cmd += [
        "-filter_complex", filtergraph,
        "-map", video_map, "-map", f"{audio_idx}:a:0",
        "-c:v", "libx264", "-profile:v", "main", "-crf", "20", "-preset", "fast",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        "-y", str(out_path),
    ]

    log.info("[aishorts] Composing %d images → %s (%.1fs)", n, out_name, total_audio)
    stderr, rc = _run_ffmpeg(cmd)
    if rc != 0 or not out_path.exists():
        log.error("[aishorts] Compose failed (rc=%d): %s", rc, stderr[-400:])
        out_path.unlink(missing_ok=True)
        return AiShortsResult(None, title, n, total_audio, False, f"compose failed: {stderr[-200:]}")

    size_mb = out_path.stat().st_size / 1_048_576
    log.info("[aishorts] Composed %s (%.1f MB, %d images, %.1fs)", out_name, size_mb, n, total_audio)
    return AiShortsResult(out_path, title, n, total_audio, True, None)
