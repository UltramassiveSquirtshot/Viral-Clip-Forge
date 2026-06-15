from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from .clip_analyzer import ClipCandidate
from .utils import format_timestamp, get_logger

log = get_logger()


@dataclass
class CutResult:
    clip_id: str
    video_id: str
    output_path: Path | None
    start_sec: float
    end_sec: float
    duration_sec: float
    file_size_bytes: int
    success: bool
    error: str | None
    ffmpeg_returncode: int
    re_encoded: bool = False


def _run_ffmpeg(cmd: list[str], timeout: int = 300) -> tuple[str, int]:
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    stderr = result.stderr.decode("utf-8", errors="replace")
    return stderr, result.returncode


def _get_duration(path: Path, ffprobe_bin: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                str(ffprobe_bin),
                "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        val = result.stdout.decode().strip()
        return float(val) if val else None
    except Exception:
        return None


def cut_clip(
    source_path: Path,
    output_path: Path,
    start_sec: float,
    duration_sec: float,
    ffmpeg_bin: Path,
    ffprobe_bin: Path,
    encode: bool = False,
) -> tuple[int, str]:
    """Lossless stream copy (encode=False) or H.264 re-encode (encode=True), no aspect ratio change."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if encode:
        cmd = [
            str(ffmpeg_bin),
            "-ss", format_timestamp(start_sec),
            "-i", str(source_path),
            "-t", format_timestamp(duration_sec),
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ]
    else:
        cmd = [
            str(ffmpeg_bin),
            "-ss", format_timestamp(start_sec),
            "-i", str(source_path),
            "-t", format_timestamp(duration_sec),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-y",
            str(output_path),
        ]

    return _run_ffmpeg(cmd)


def cut_clip_shorts(
    source_path: Path,
    output_path: Path,
    start_sec: float,
    duration_sec: float,
    ffmpeg_bin: Path,
    subtitle_path: Path | None = None,
) -> tuple[str, int]:
    """
    Re-encode to YouTube Shorts format: 9:16 vertical (1080x1920), H.264 main,
    AAC 44100 Hz stereo, faststart.

    Uses a blurred-pillarbox composite:
      - Background: source scaled up to fill 1080x1920, then gaussian-blurred
      - Foreground: source letterboxed to fit inside 1080x1920, overlaid centered
    If subtitle_path is provided and exists, subtitles are burned in.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build filter_complex for blurred pillarbox
    # [bg]: scale to overfill 9:16, crop center, heavy blur
    # [fg]: scale to fit inside 9:16 (letterboxed), pad to exact 1080x1920
    # overlay fg centered on bg
    filter_complex = (
        "[0:v]split=2[fg_raw][bg_raw];"
        "[bg_raw]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "gblur=sigma=25[bg];"
        "[fg_raw]scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2[fg];"
        "[bg][fg]overlay=0:0"
    )

    # Append subtitle burn-in if available
    sub_filter = ""
    if subtitle_path and subtitle_path.exists():
        # Escape Windows backslashes for FFmpeg filter syntax
        sub_escaped = str(subtitle_path).replace("\\", "/").replace(":", "\\:")
        sub_filter = f",subtitles='{sub_escaped}':si=0:force_style='FontSize=14,Alignment=2,MarginV=30'"

    filter_complex += sub_filter

    cmd = [
        str(ffmpeg_bin),
        "-ss", format_timestamp(start_sec),
        "-i", str(source_path),
        "-t", format_timestamp(duration_sec),
        "-filter_complex", filter_complex,
        "-c:v", "libx264",
        "-profile:v", "main",
        "-crf", "20",
        "-preset", "fast",
        "-c:a", "aac",
        "-ar", "44100",
        "-ac", "2",
        "-b:a", "192k",
        "-movflags", "+faststart",
        "-y",
        str(output_path),
    ]
    return _run_ffmpeg(cmd)


def cut_single_clip(
    source_path: Path,
    output_dir: Path,
    video_id: str,
    clip_index: int,
    candidate: ClipCandidate,
    ffmpeg_bin: Path,
    ffprobe_bin: Path,
    shorts_output: bool = True,
    subtitle_path: Path | None = None,
) -> CutResult:
    clip_id = str(uuid.uuid4())
    start = candidate.start_sec
    end = candidate.end_sec
    duration = end - start

    out_name = f"{video_id}_clip{clip_index:02d}_{int(start)}s-{int(end)}s.mp4"
    output_path = output_dir / out_name

    log.info(f"[cut] {video_id} clip {clip_index}: {start:.1f}s → {end:.1f}s ({duration:.1f}s)")

    re_encoded = False

    if shorts_output:
        stderr, rc = cut_clip_shorts(source_path, output_path, start, duration, ffmpeg_bin, subtitle_path)
        re_encoded = True
        if rc != 0 or not output_path.exists():
            log.error(f"[cut] Shorts encode failed (rc={rc}): {stderr[-300:]}")
            output_path.unlink(missing_ok=True)
            return CutResult(
                clip_id=clip_id, video_id=video_id, output_path=None,
                start_sec=start, end_sec=end, duration_sec=duration,
                file_size_bytes=0, success=False,
                error=f"Shorts encode failed: {stderr[-200:]}",
                ffmpeg_returncode=rc, re_encoded=True,
            )
    else:
        stderr, rc = cut_clip(source_path, output_path, start, duration, ffmpeg_bin, ffprobe_bin, encode=False)
        if rc != 0 or not output_path.exists():
            log.warning(f"[cut] Lossless cut failed (rc={rc}), retrying with re-encode")
            stderr, rc = cut_clip(source_path, output_path, start, duration, ffmpeg_bin, ffprobe_bin, encode=True)
            re_encoded = True

        if rc == 0 and output_path.exists() and not re_encoded:
            actual_duration = _get_duration(output_path, ffprobe_bin)
            if actual_duration is not None and abs(actual_duration - duration) > 2.0:
                log.warning(
                    f"[cut] A/V sync issue (expected {duration:.1f}s, got {actual_duration:.1f}s) — re-encoding"
                )
                stderr, rc = cut_clip(source_path, output_path, start, duration, ffmpeg_bin, ffprobe_bin, encode=True)
                re_encoded = True

    if rc != 0 or not output_path.exists():
        log.error(f"[cut] All cut attempts failed for {video_id} clip {clip_index}: {stderr[-300:]}")
        return CutResult(
            clip_id=clip_id,
            video_id=video_id,
            output_path=None,
            start_sec=start,
            end_sec=end,
            duration_sec=duration,
            file_size_bytes=0,
            success=False,
            error=stderr[-300:],
            ffmpeg_returncode=rc,
            re_encoded=re_encoded,
        )

    file_size = output_path.stat().st_size if output_path.exists() else 0
    log.info(f"[cut] Clip saved: {out_name} ({file_size / 1_048_576:.1f} MB, re_encoded={re_encoded})")

    return CutResult(
        clip_id=clip_id,
        video_id=video_id,
        output_path=output_path,
        start_sec=start,
        end_sec=end,
        duration_sec=duration,
        file_size_bytes=file_size,
        success=True,
        error=None,
        ffmpeg_returncode=rc,
        re_encoded=re_encoded,
    )


def cut_all_clips(
    source_path: Path,
    candidates: list[ClipCandidate],
    output_dir: Path,
    video_id: str,
    ffmpeg_bin: Path,
    ffprobe_bin: Path,
    max_clips: int = 3,
    shorts_output: bool = True,
    subtitle_path: Path | None = None,
) -> list[CutResult]:
    results: list[CutResult] = []
    for i, candidate in enumerate(candidates[:max_clips], start=1):
        result = cut_single_clip(
            source_path=source_path,
            output_dir=output_dir,
            video_id=video_id,
            clip_index=i,
            candidate=candidate,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            shorts_output=shorts_output,
            subtitle_path=subtitle_path,
        )
        results.append(result)
    return results
