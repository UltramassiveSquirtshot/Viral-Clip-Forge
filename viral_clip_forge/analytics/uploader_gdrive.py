"""
Google Drive uploader for analytics reports.

Uploads files to lorenzotervel@gmail.com Drive under folder:
  ViralClipForge/analytics_reports/

OAuth token stored at data/gdrive_token.json (separate from YouTube token
to avoid scope conflicts).

Run once to authenticate:
  python analyze.py --setup-gdrive
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_FOLDER_NAME = "analytics_reports"
_PARENT_FOLDER_NAME = "ViralClipForge"


def _get_credentials(token_path: Path, client_secret_path: Path):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GDRIVE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError(
                "Google Drive token not found or invalid.\n"
                "Run: python analyze.py --setup-gdrive"
            )
    return creds


def run_gdrive_oauth_flow(token_path: Path, client_secret_path: Path) -> None:
    """Interactive OAuth consent flow — call once via --setup-gdrive."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_secret_path.exists():
        raise FileNotFoundError(
            f"Client secret not found at {client_secret_path}.\n"
            "Download OAuth 2.0 Desktop credentials from Google Cloud Console\n"
            "and save them there (same file as YouTube credentials is fine)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), GDRIVE_SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    log.info("[gdrive] Token saved to %s", token_path)
    print(f"Google Drive authenticated. Token saved to {token_path}")


def _get_or_create_folder(service, name: str, parent_id: str | None = None) -> str:
    """Return folder ID, creating it if it doesn't exist."""
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    resp = service.files().list(q=query, fields="files(id, name)").execute()
    files = resp.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = service.files().create(body=metadata, fields="id").execute()
    log.info("[gdrive] Created folder '%s' (id=%s)", name, folder["id"])
    return folder["id"]


def _mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "text/markdown"
    if suffix == ".json":
        return "application/json"
    return "application/octet-stream"


def upload_reports(
    md_path: Path,
    json_path: Path,
    token_path: Path,
    client_secret_path: Path,
) -> tuple[str, str]:
    """
    Upload both report files to Google Drive.
    Returns (md_drive_url, json_drive_url).
    """
    try:
        creds = _get_credentials(token_path, client_secret_path)
    except RuntimeError as exc:
        log.error("[gdrive] %s", exc)
        raise

    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    service = build("drive", "v3", credentials=creds)

    # Ensure folder structure: ViralClipForge/analytics_reports/
    parent_id = _get_or_create_folder(service, _PARENT_FOLDER_NAME)
    folder_id = _get_or_create_folder(service, _FOLDER_NAME, parent_id=parent_id)

    urls: list[str] = []
    for file_path in (md_path, json_path):
        if not file_path.exists():
            log.warning("[gdrive] File not found, skipping upload: %s", file_path)
            urls.append("")
            continue

        # Check if file already exists in folder (overwrite if same name)
        existing = service.files().list(
            q=f"name='{file_path.name}' and '{folder_id}' in parents and trashed=false",
            fields="files(id)",
        ).execute().get("files", [])

        media = MediaFileUpload(str(file_path), mimetype=_mime_type(file_path), resumable=False)

        if existing:
            file_id = existing[0]["id"]
            service.files().update(
                fileId=file_id,
                media_body=media,
            ).execute()
            log.info("[gdrive] Updated %s (id=%s)", file_path.name, file_id)
        else:
            metadata = {"name": file_path.name, "parents": [folder_id]}
            uploaded = service.files().create(
                body=metadata,
                media_body=media,
                fields="id",
            ).execute()
            file_id = uploaded["id"]
            log.info("[gdrive] Uploaded %s (id=%s)", file_path.name, file_id)

        # Make file readable by anyone with the link (so you can open on mobile)
        service.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
        ).execute()

        url = f"https://drive.google.com/file/d/{file_id}/view"
        urls.append(url)
        log.info("[gdrive] %s → %s", file_path.name, url)

    return urls[0] if urls else "", urls[1] if len(urls) > 1 else ""


def download_insights(
    token_path: Path,
    client_secret_path: Path,
    dest_path: Path,
) -> bool:
    """
    Download analytics_insights.json from Google Drive to dest_path.
    Uses the ranker OAuth token (drive scope) since analytics_insights.json
    is hand-uploaded and not accessible via the drive.file scope.
    Returns True if downloaded, False if not found or error.
    """
    if not token_path.exists():
        return False

    # Ranker token uses full drive scope
    _RANKER_SCOPES = ["https://www.googleapis.com/auth/drive"]
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials.from_authorized_user_file(str(token_path), _RANKER_SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_path.write_text(creds.to_json(), encoding="utf-8")
            else:
                log.debug("[gdrive] Ranker token invalid — skipping insights download")
                return False
    except Exception as exc:
        log.debug("[gdrive] Could not load ranker credentials: %s", exc)
        return False

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
        import io

        service = build("drive", "v3", credentials=creds)

        # Search for analytics_insights.json anywhere in Drive
        resp = service.files().list(
            q="name='analytics_insights.json' and trashed=false",
            orderBy="modifiedTime desc",
            pageSize=1,
            fields="files(id, name, modifiedTime)",
        ).execute()

        files = resp.get("files", [])
        if not files:
            log.debug("[gdrive] analytics_insights.json not found on Drive")
            return False

        file_id = files[0]["id"]
        modified = files[0].get("modifiedTime", "")
        log.info("[gdrive] Downloading analytics_insights.json (id=%s modified=%s)", file_id, modified)

        request = service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(buf.getvalue())
        log.info("[gdrive] analytics_insights.json saved to %s", dest_path)
        return True

    except Exception as exc:
        log.warning("[gdrive] Failed to download analytics_insights.json: %s", exc)
        return False
