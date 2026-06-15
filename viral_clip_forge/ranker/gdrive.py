"""
Google Drive helper for the ranker — fully self-contained, independent of the
analytics Drive uploader.

Uses the full `drive` scope (read + write to ANY file) because it must read a
`ranker_scripts.json` the user uploads BY HAND (the analytics `drive.file` scope
only sees files the app itself created) and rewrite it after consuming an entry.

Own OAuth client + own token (`data/ranker_gdrive_token.json`), separate from the
YouTube and analytics tokens. Set up once with:  python main.py --setup-gdrive
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]


def get_service(token_path: Path, client_secret_path: Path):
    """Build a Drive v3 service from the ranker's own token. Raises if not set up."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError(
                "Ranker Google Drive token not found or invalid.\n"
                "Run: python main.py --setup-gdrive"
            )

    return build("drive", "v3", credentials=creds)


def run_setup(token_path: Path, client_secret_path: Path) -> None:
    """Interactive OAuth consent flow — call once via `main.py --setup-gdrive`."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_secret_path.exists():
        raise FileNotFoundError(
            f"Ranker client secret not found at {client_secret_path}.\n"
            "Download an OAuth 2.0 Desktop client JSON from Google Cloud Console "
            "and save it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    log.info("[ranker-gdrive] Token saved to %s", token_path)
    print(f"Ranker Google Drive authenticated. Token saved to {token_path}")


def _find_folder_id(service, folder_name: str) -> str | None:
    resp = service.files().list(
        q=(
            f"name='{folder_name}' and "
            "mimeType='application/vnd.google-apps.folder' and trashed=false"
        ),
        fields="files(id, name)",
        pageSize=10,
    ).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def find_file(service, name: str, folder_name: str | None = None) -> str | None:
    """Return the file id of `name` (optionally scoped to a folder), or None."""
    query = f"name='{name}' and trashed=false"
    if folder_name:
        folder_id = _find_folder_id(service, folder_name)
        if folder_id:
            query += f" and '{folder_id}' in parents"
    resp = service.files().list(
        q=query,
        fields="files(id, name, modifiedTime)",
        orderBy="modifiedTime desc",
        pageSize=10,
    ).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def download_text(service, file_id: str) -> str:
    """Download a Drive file's raw bytes and decode as UTF-8 text."""
    data = service.files().get_media(fileId=file_id).execute()
    if isinstance(data, bytes):
        return data.decode("utf-8")
    return str(data)


def update_text(service, file_id: str, text: str, mime_type: str = "application/json") -> None:
    """Overwrite an existing Drive file's content with `text`."""
    from googleapiclient.http import MediaIoBaseUpload

    media = MediaIoBaseUpload(
        io.BytesIO(text.encode("utf-8")),
        mimetype=mime_type,
        resumable=False,
    )
    service.files().update(fileId=file_id, media_body=media).execute()
