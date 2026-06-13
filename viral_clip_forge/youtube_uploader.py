"""
YouTube Data API v3 uploader.

Uploads MP4 clips as scheduled private videos (auto-published at publish_at).
OAuth token is stored in config.youtube_token_path and refreshed automatically.
"""

import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def _get_credentials(config):
    """Load or refresh OAuth credentials, prompting if needed."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = config.youtube_token_path
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError(
                "YouTube token not found or invalid. "
                "Run: python main.py --setup-youtube"
            )
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def run_oauth_flow(config) -> None:
    """Interactive OAuth consent flow — call once via --setup-youtube."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_secret = config.youtube_client_secret_path
    if not client_secret.exists():
        raise FileNotFoundError(
            f"Client secret not found at {client_secret}. "
            "Download it from Google Cloud Console and save it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    token_path = config.youtube_token_path
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    log.info("YouTube token saved to %s", token_path)


def upload_clip(
    config,
    clip_path: Path,
    title: str,
    description: str,
    tags: list[str],
    publish_at: datetime,
    category_id: str = "28",
) -> str:
    """
    Upload a clip to YouTube as a scheduled private video.
    Returns the YouTube video ID.
    publish_at must be timezone-aware.
    """
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.auth.transport.requests import Request

    creds = _get_credentials(config)
    youtube = build("youtube", "v3", credentials=creds)

    # YouTube requires RFC 3339 format with Z suffix for UTC
    publish_at_str = publish_at.astimezone(__import__("datetime").timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at_str,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }

    media = MediaFileUpload(
        str(clip_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=4 * 1024 * 1024,
    )

    log.info("Uploading %s → YouTube (scheduled %s)", clip_path.name, publish_at_str)

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            log.debug("Upload progress: %d%%", pct)

    video_id = response["id"]
    log.info("Uploaded %s → https://youtu.be/%s (scheduled %s)", clip_path.name, video_id, publish_at_str)
    return video_id
