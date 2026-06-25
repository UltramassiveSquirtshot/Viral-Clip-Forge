"""
Google Drive helpers for AI-Shorts images.

Per run, images live in `ViralClipForge/ai_shorts/{RUN_ID}/images/`. The user
generates them on Leonardo.ai and uploads each one named by the SECOND at which
it should appear on screen (e.g. `0.0.png`, `3.4.png`, `7.1.png`). We list them,
parse the timestamp from the filename, and download in timestamp order.

Reuses the ranker's full-`drive`-scope service and the analytics
`_get_or_create_folder` idempotent-folder helper.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..utils import get_logger
from ..ranker import gdrive
from ..analytics.uploader_gdrive import _get_or_create_folder
from .config import AiShortsConfig

log = get_logger()

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass
class RunImage:
    timestamp: float
    name: str
    local_path: Path


def _service(cfg: AiShortsConfig):
    return gdrive.get_service(cfg.gdrive_token_path, cfg.gdrive_client_secret_path)


def _images_folder_id(service, cfg: AiShortsConfig, run_id: str, create: bool) -> str | None:
    """Resolve (optionally creating) the images folder id for a run."""
    if create:
        root = _get_or_create_folder(service, cfg.drive_root_folder)
        aishorts = _get_or_create_folder(service, cfg.drive_aishorts_folder, parent_id=root)
        run = _get_or_create_folder(service, run_id, parent_id=aishorts)
        return _get_or_create_folder(service, "images", parent_id=run)
    # read-only resolution: walk the path, return None if any segment missing
    folder_id = None
    for name in (cfg.drive_root_folder, cfg.drive_aishorts_folder, run_id, "images"):
        q = (f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
             "and trashed=false")
        if folder_id:
            q += f" and '{folder_id}' in parents"
        resp = service.files().list(q=q, fields="files(id)").execute()
        files = resp.get("files", [])
        if not files:
            return None
        folder_id = files[0]["id"]
    return folder_id


def create_run_folder(cfg: AiShortsConfig, run_id: str) -> str:
    """Create the (empty) images folder for a run. Returns its Drive web URL."""
    service = _service(cfg)
    folder_id = _images_folder_id(service, cfg, run_id, create=True)
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    log.info("[aishorts] Run images folder ready: %s", url)
    return url


def _list_image_files(service, folder_id: str) -> list[dict]:
    resp = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id, name)",
        pageSize=200,
    ).execute()
    out = []
    for f in resp.get("files", []):
        if Path(f["name"]).suffix.lower() in _IMAGE_EXTS:
            out.append(f)
    return out


def count_images(cfg: AiShortsConfig, run_id: str) -> int:
    """Count image files currently in the run's Drive folder (0 if folder missing)."""
    try:
        service = _service(cfg)
        folder_id = _images_folder_id(service, cfg, run_id, create=False)
        if not folder_id:
            return 0
        return len(_list_image_files(service, folder_id))
    except Exception as exc:
        log.warning("[aishorts] count_images failed: %s", exc)
        return 0


def _parse_timestamp(name: str) -> float | None:
    """Parse a leading numeric timestamp from a filename like '3.4.png' → 3.4."""
    stem = Path(name).stem
    try:
        return float(stem)
    except ValueError:
        return None


def download_images(cfg: AiShortsConfig, run_id: str, dest_dir: Path) -> list[RunImage]:
    """Download all run images to dest_dir, ordered by the timestamp in the name.

    Files whose stem is not a number are skipped with a warning.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    service = _service(cfg)
    folder_id = _images_folder_id(service, cfg, run_id, create=False)
    if not folder_id:
        log.warning("[aishorts] Images folder not found for run %s", run_id)
        return []

    images: list[RunImage] = []
    for f in _list_image_files(service, folder_id):
        ts = _parse_timestamp(f["name"])
        if ts is None:
            log.warning("[aishorts] Skipping image with non-numeric name: %s", f["name"])
            continue
        local = dest_dir / f["name"]
        data = service.files().get_media(fileId=f["id"]).execute()
        local.write_bytes(data if isinstance(data, bytes) else bytes(data))
        images.append(RunImage(timestamp=ts, name=f["name"], local_path=local))

    images.sort(key=lambda im: im.timestamp)
    log.info("[aishorts] Downloaded %d images for run %s", len(images), run_id)
    return images
