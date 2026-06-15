"""
YouTube Analytics API fetcher.

Reads per-clip performance metrics for all clips uploaded in the past 14 days.
Uses the same OAuth token as the uploader (youtube_token_path).

Required scope: https://www.googleapis.com/auth/yt-analytics.readonly
If the scope is missing from the current token, raises ScopeMissingError with
instructions for re-authorizing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

ANALYTICS_SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly"]


class ScopeMissingError(Exception):
    pass


@dataclass
class ClipAnalytics:
    youtube_video_id: str
    clip_id: str
    clip_duration_sec: float
    views: int
    watch_time_minutes: float
    avg_view_duration_sec: float
    retention_rate: float          # avg_view_duration_sec / clip_duration_sec
    impressions: int
    ctr: float                     # impressions click-through rate (0–1)
    likes: int
    days_since_publish: int
    published_at: str              # ISO date string
    source_keyword: str            # search keyword that found the source video
    clip_reason: str               # scene_change / loudness_spike / evenly_spaced
    combined_score: float          # production-time composite score
    slot_day: str                  # e.g. "Tuesday"
    slot_time: str                 # e.g. "13:00"


def _get_credentials(config):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = config.analytics_token_path
    client_secret_path = config.analytics_client_secret_path
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), ANALYTICS_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token_path.write_text(creds.to_json(), encoding="utf-8")
            except Exception as exc:
                raise ScopeMissingError(
                    f"Analytics token refresh failed: {exc}\n"
                    "Re-authorize with: python analyze.py --setup-analytics"
                ) from exc
        else:
            raise ScopeMissingError(
                "Analytics token not found or invalid.\n"
                "Run once to authenticate: python analyze.py --setup-analytics\n"
                "Make sure 'YouTube Analytics API' is enabled in your Google Cloud project\n"
                "and that data/analytics_client_secret.json exists."
            )

    return creds


def _load_manifests(manifests_dir: Path, days: int = 14) -> list[dict]:
    """Return manifest dicts from the past N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    manifests = []
    if not manifests_dir.exists():
        return manifests
    for d in sorted(manifests_dir.iterdir(), reverse=True):
        manifest_path = d / "manifest.json"
        if not manifest_path.exists():
            continue
        import json
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            run_date_str = data.get("run_date", "")
            if run_date_str:
                run_date = datetime.fromisoformat(run_date_str.replace("Z", "+00:00"))
                if run_date < cutoff:
                    continue
            manifests.append(data)
        except Exception as exc:
            log.warning("Could not parse manifest %s: %s", manifest_path, exc)
    return manifests


def _extract_clip_stubs(manifests: list[dict]) -> list[dict]:
    """Extract clip metadata from manifests for correlation later."""
    stubs = []
    for manifest in manifests:
        for niche_data in manifest.get("niches", {}).values():
            for video in niche_data.get("results", []):
                source_keyword = video.get("source", "")
                for clip in video.get("clips", []):
                    yt_id = clip.get("youtube_video_id")
                    if not yt_id or clip.get("upload_status") != "scheduled":
                        continue
                    pub_at = clip.get("scheduled_publish_at", "")
                    # Parse slot day and time
                    slot_day, slot_time = "", ""
                    if pub_at:
                        try:
                            dt = datetime.fromisoformat(pub_at)
                            slot_day = dt.strftime("%A")
                            slot_time = dt.strftime("%H:%M")
                        except Exception:
                            pass
                    stubs.append({
                        "youtube_video_id": yt_id,
                        "clip_id": clip.get("clip_id", ""),
                        "clip_duration_sec": clip.get("duration_sec", 60.0),
                        "combined_score": clip.get("combined_score", 0.0),
                        "clip_reason": clip.get("reason", ""),
                        "source_keyword": source_keyword,
                        "published_at": pub_at[:10] if pub_at else "",
                        "slot_day": slot_day,
                        "slot_time": slot_time,
                    })
    return stubs


def fetch_analytics(config) -> list[ClipAnalytics]:
    """
    Fetch YouTube Analytics for all uploaded clips from the past 14 days.
    Returns a list of ClipAnalytics dataclasses.
    """
    manifests_dir = config.state_db_path.parent / "manifests"
    manifests = _load_manifests(manifests_dir, days=14)
    stubs = _extract_clip_stubs(manifests)

    if not stubs:
        log.info("[analytics] No uploaded clips found in manifests (past 14 days)")
        return []

    log.info("[analytics] Found %d uploaded clips to analyze", len(stubs))

    try:
        creds = _get_credentials(config)
    except ScopeMissingError as exc:
        log.error("[analytics] %s", exc)
        raise

    from googleapiclient.discovery import build

    yt_analytics = build("youtubeAnalytics", "v2", credentials=creds)

    today = datetime.now(timezone.utc).date()
    start_date = (today - timedelta(days=14)).isoformat()
    end_date = today.isoformat()

    results: list[ClipAnalytics] = []

    for stub in stubs:
        yt_id = stub["youtube_video_id"]
        try:
            resp = yt_analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="views,estimatedMinutesWatched,averageViewDuration,likes",
                dimensions="video",
                filters=f"video=={yt_id}",
            ).execute()

            rows = resp.get("rows", [])
            if not rows:
                log.debug("[analytics] No data yet for %s (may not be published)", yt_id)
                continue

            row = rows[0]
            # columns: video, views, estimatedMinutesWatched, averageViewDuration, likes
            views = int(row[1])
            watch_mins = float(row[2])
            avg_duration = float(row[3])
            likes = int(row[4])
            # impressions/CTR not available per-video in Analytics API v2
            impressions = 0
            ctr = 0.0

            clip_duration = stub["clip_duration_sec"] or 60.0
            retention = avg_duration / clip_duration if clip_duration > 0 else 0.0

            pub_str = stub["published_at"]
            days_since = 0
            if pub_str:
                try:
                    pub_date = datetime.strptime(pub_str, "%Y-%m-%d").date()
                    days_since = (today - pub_date).days
                except Exception:
                    pass

            results.append(ClipAnalytics(
                youtube_video_id=yt_id,
                clip_id=stub["clip_id"],
                clip_duration_sec=clip_duration,
                views=views,
                watch_time_minutes=watch_mins,
                avg_view_duration_sec=avg_duration,
                retention_rate=round(retention, 4),
                impressions=impressions,
                ctr=round(ctr, 4),
                likes=likes,
                days_since_publish=days_since,
                published_at=pub_str,
                source_keyword=stub["source_keyword"],
                clip_reason=stub["clip_reason"],
                combined_score=stub["combined_score"],
                slot_day=stub["slot_day"],
                slot_time=stub["slot_time"],
            ))
            log.debug("[analytics] %s: views=%d retention=%.1f%%", yt_id, views, retention * 100)

        except Exception as exc:
            log.warning("[analytics] Could not fetch data for %s: %s", yt_id, exc)

    log.info("[analytics] Fetched analytics for %d/%d clips", len(results), len(stubs))
    return results
