from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .utils import get_logger

log = get_logger()


@dataclass
class DownloadResult:
    video_id: str
    output_path: Path | None
    actual_format: str
    file_size_bytes: int
    duration_seconds: float
    download_duration_secs: float
    success: bool
    error: str | None


def download_video(
    video_id: str,
    output_dir: Path,
    ffmpeg_bin_dir: Path,
    max_height: int = 1080,
) -> DownloadResult:
    import yt_dlp

    output_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://www.youtube.com/watch?v={video_id}"
    outtmpl = str(output_dir / "%(id)s.%(ext)s")

    ffmpeg_dir = str(ffmpeg_bin_dir.parent) if ffmpeg_bin_dir.is_file() else str(ffmpeg_bin_dir)

    ydl_opts = {
        "format": (
            f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]"
            f"/best[height<={max_height}][ext=mp4]/best"
        ),
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
        "ffmpeg_location": ffmpeg_dir,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "skip_unavailable_fragments": False,
    }

    start = time.time()
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return DownloadResult(
                    video_id=video_id,
                    output_path=None,
                    actual_format="",
                    file_size_bytes=0,
                    duration_seconds=0,
                    download_duration_secs=time.time() - start,
                    success=False,
                    error="extract_info returned None",
                )

            ext = info.get("ext", "mp4")
            output_path = output_dir / f"{video_id}.{ext}"
            if not output_path.exists():
                output_path = output_dir / f"{video_id}.mp4"

            size = output_path.stat().st_size if output_path.exists() else 0
            duration = float(info.get("duration") or 0)

            log.info(f"[download] {video_id} → {output_path.name} ({size / 1_048_576:.1f} MB)")
            return DownloadResult(
                video_id=video_id,
                output_path=output_path if output_path.exists() else None,
                actual_format=ext,
                file_size_bytes=size,
                duration_seconds=duration,
                download_duration_secs=time.time() - start,
                success=output_path.exists(),
                error=None if output_path.exists() else "output file not found after download",
            )

    except Exception as exc:
        error_msg = str(exc)
        log.error(f"[download] Failed to download {video_id}: {error_msg}")
        return DownloadResult(
            video_id=video_id,
            output_path=None,
            actual_format="",
            file_size_bytes=0,
            duration_seconds=0,
            download_duration_secs=time.time() - start,
            success=False,
            error=error_msg,
        )


def cleanup_stale_downloads(download_dir: Path, max_age_hours: int = 24) -> int:
    """Delete video files older than max_age_hours. Returns count deleted."""
    if not download_dir.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    deleted = 0

    for f in download_dir.iterdir():
        if f.suffix in {".mp4", ".webm", ".mkv", ".part"} and f.is_file():
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                try:
                    f.unlink()
                    log.info(f"[cleanup] Deleted stale download: {f.name}")
                    deleted += 1
                except Exception as exc:
                    log.warning(f"[cleanup] Could not delete {f.name}: {exc}")

    return deleted
