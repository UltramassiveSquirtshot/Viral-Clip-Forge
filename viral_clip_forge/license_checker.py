from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from .scraper import VideoCandidate
from .utils import get_logger, retry

log = get_logger()


class LicenseStatus(str, Enum):
    CC_BY = "cc_by"
    STANDARD = "standard"
    UNCERTAIN = "uncertain"
    UNAVAILABLE = "unavailable"


@dataclass
class LicenseResult:
    video_id: str
    api_license: str | None
    ytdlp_license: str | None
    final_status: LicenseStatus
    confidence: str
    checked_at: str


def _check_via_ytdlp(video_id: str) -> str | None:
    try:
        import yt_dlp

        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                return None
            return info.get("license") or None
    except Exception as exc:
        log.debug(f"yt-dlp license check failed for {video_id}: {exc}")
        return None


@retry(max_attempts=2, backoff_secs=3.0, exceptions=(Exception,))
def _check_via_ytdlp_with_retry(video_id: str) -> str | None:
    return _check_via_ytdlp(video_id)


def _determine_final_license(
    api_license: str | None,
    ytdlp_license: str | None,
) -> tuple[LicenseStatus, str]:
    api_is_cc = api_license == "creativeCommon"
    ytdlp_is_cc = bool(ytdlp_license and "creative commons" in ytdlp_license.lower())

    if api_is_cc and ytdlp_is_cc:
        return LicenseStatus.CC_BY, "high"
    if api_is_cc and not ytdlp_is_cc:
        return LicenseStatus.CC_BY, "medium"
    if not api_is_cc and ytdlp_is_cc:
        return LicenseStatus.CC_BY, "medium"
    if api_license == "youtube" and not ytdlp_is_cc:
        return LicenseStatus.STANDARD, "high"
    if api_license is None and ytdlp_license is None:
        return LicenseStatus.UNCERTAIN, "low"

    return LicenseStatus.UNCERTAIN, "low"


def check_video_license(video: VideoCandidate) -> LicenseResult:
    log.info(f"[license] Checking {video.video_id} — API license field: {video.api_license!r}")

    ytdlp_license = _check_via_ytdlp_with_retry(video.video_id)
    log.debug(f"[license] yt-dlp result for {video.video_id}: {ytdlp_license!r}")

    final_status, confidence = _determine_final_license(video.api_license, ytdlp_license)

    log.info(
        f"[license] {video.video_id} → {final_status.value} (confidence={confidence}) "
        f"api={video.api_license!r} ytdlp={ytdlp_license!r}"
    )

    return LicenseResult(
        video_id=video.video_id,
        api_license=video.api_license,
        ytdlp_license=ytdlp_license,
        final_status=final_status,
        confidence=confidence,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )
