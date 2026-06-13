"""
Algorithm-aware YouTube upload scheduler.

Slots are distributed across Tue/Wed/Thu/Sat at 08:00, 13:00, 19:30 Rome time,
max 3 uploads per day, per the YouTube Algorithm Guide 2026 recommendations for
Shorts in the Tech niche.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")


def _load_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"slots_used": {}}


def _save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, state_path)


def _day_key(dt: datetime) -> str:
    return dt.astimezone(ROME).strftime("%Y-%m-%d")


def _parse_slot_time(day: datetime, slot_str: str) -> datetime:
    h, m = map(int, slot_str.split(":"))
    rome_day = day.astimezone(ROME).replace(hour=h, minute=m, second=0, microsecond=0)
    return rome_day


_VALID_DAYS = {"Tuesday", "Wednesday", "Thursday", "Saturday"}
_SLOT_TIMES = ["08:00", "13:00", "19:30"]
_MAX_PER_DAY = 3


def sync_state_from_youtube(config, state_path: Path, now: datetime | None = None) -> dict[str, int]:
    """
    Query YouTube API for videos with status=scheduled and rebuild slots_used from their
    publishAt timestamps. This ensures the local state is always consistent with YouTube,
    even if schedule_state.json was lost or a manual upload was made via YouTube Studio.
    Returns the updated slots_used dict (also persisted to state_path).
    """
    if now is None:
        now = datetime.now(tz=ROME)

    try:
        from googleapiclient.discovery import build
        from viral_clip_forge.youtube_uploader import _get_credentials
        creds = _get_credentials(config)
        youtube = build("youtube", "v3", credentials=creds)
    except Exception:
        return {}

    slots_used: dict[str, int] = {}
    page_token = None

    try:
        while True:
            kwargs: dict = {
                "part": "status",
                "mine": True,
                "maxResults": 50,
                "type": "video",
            }
            if page_token:
                kwargs["pageToken"] = page_token

            resp = youtube.search().list(**kwargs).execute()

            video_ids = [item["id"]["videoId"] for item in resp.get("items", []) if "videoId" in item.get("id", {})]
            if video_ids:
                details = youtube.videos().list(
                    part="status",
                    id=",".join(video_ids),
                ).execute()
                for item in details.get("items", []):
                    status = item.get("status", {})
                    if status.get("privacyStatus") == "private" and status.get("publishAt"):
                        publish_at = datetime.fromisoformat(
                            status["publishAt"].replace("Z", "+00:00")
                        ).astimezone(ROME)
                        if publish_at > now:
                            day_key = _day_key(publish_at)
                            slots_used[day_key] = slots_used.get(day_key, 0) + 1

            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Could not sync schedule from YouTube: %s", exc)
        return {}

    state = _load_state(state_path)
    state["slots_used"] = slots_used
    _save_state(state_path, state)
    return slots_used


def next_upload_slots(n_clips: int, state_path: Path, now: datetime | None = None) -> list[datetime]:
    """
    Reserve n_clips upload slots and return their scheduled datetimes.
    Modifies state_path to track booked slots across runs.
    """
    if now is None:
        now = datetime.now(tz=ROME)
    else:
        now = now.astimezone(ROME)

    state = _load_state(state_path)
    slots_used: dict[str, int] = state.get("slots_used", {})

    # Prune old entries (keep only future dates)
    today_key = _day_key(now)
    slots_used = {k: v for k, v in slots_used.items() if k >= today_key}

    result: list[datetime] = []
    candidate = now

    while len(result) < n_clips:
        day_name = candidate.strftime("%A")
        if day_name in _VALID_DAYS:
            day_key = _day_key(candidate)
            used = slots_used.get(day_key, 0)
            for slot_str in _SLOT_TIMES:
                if len(result) >= n_clips:
                    break
                slot_dt = _parse_slot_time(candidate, slot_str)
                # Must be at least 10 minutes in the future (YouTube needs time to process)
                if slot_dt <= now + timedelta(minutes=10):
                    continue
                if used >= _MAX_PER_DAY:
                    break
                result.append(slot_dt)
                used += 1
                slots_used[day_key] = used

        candidate = (candidate + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(ROME)

        # Safety: don't loop more than 30 days
        if (candidate - now).days > 30:
            break

    state["slots_used"] = slots_used
    _save_state(state_path, state)
    return result
