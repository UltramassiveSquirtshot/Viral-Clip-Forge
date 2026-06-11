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


def cut_single_clip(
    source_path: Path,
    output_dir: Path,
    video_id: str,
    clip_index: int,
    candidate: ClipCandidate,
    ffmpeg_bin: Path,
    ffprobe_bin: Path,
) -> CutResult:
    clip_id = str(uuid.uuid4())
    start = candidate.start_sec
    end = candidate.end_sec
    duration = end - start

    out_name = f"{video_id}_clip{clip_index:02d}_{int(start)}s-{int(end)}s.mp4"
    output_path = output_dir / out_name

    log.info(f"[cut] {video_id} clip {clip_index}: {start:.1f}s → {end:.1f}s ({duration:.1f}s)")

    stderr, rc = cut_clip(source_path, output_path, start, duration, ffmpeg_bin, ffprobe_bin, encode=False)
    re_encoded = False

    if rc != 0 or not output_path.exists():
        log.warning(f"[cut] Lossless cut failed (rc={rc}), retrying with re-encode")
        stderr, rc = cut_clip(source_path, output_path, start, duration, ffmpeg_bin, ffprobe_bin, encode=True)
        re_encoded = True

    if rc != 0 or not output_path.exists():
        log.error(f"[cut] Both lossless and re-encode failed for {video_id} clip {clip_index}: {stderr[-300:]}")
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

    actual_duration = _get_duration(output_path, ffprobe_bin)
    if actual_duration is not None and abs(actual_duration - duration) > 2.0 and not re_encoded:
        log.warning(
            f"[cut] A/V sync issue detected (expected {duration:.1f}s, got {actual_duration:.1f}s) — re-encoding"
        )
        stderr, rc = cut_clip(source_path, output_path, start, duration, ffmpeg_bin, ffprobe_bin, encode=True)
        re_encoded = True

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
        )
        results.append(result)
    return results
