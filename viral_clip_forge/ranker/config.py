"""
RankerConfig — ranking-Shorts settings layered on top of the shared AppConfig.

Holds a reference to AppConfig (for ffmpeg, output dir, scheduler/upload/telegram,
Drive token paths, API keys) rather than duplicating those fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig


def _default_font() -> str:
    """Pick a bold system font for drawtext; fall back to Arial if the bold is missing."""
    candidates = [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\ariblk.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return candidates[0]


@dataclass
class RankerConfig:
    app: AppConfig
    top_n: int = 5
    clip_seconds: float = 6.0          # per-rank segment length → ~30s total
    width: int = 1080
    height: int = 1920
    font_path: str = field(default_factory=_default_font)
    drive_scripts_folder: str = "ViralClipForge"
    drive_scripts_name: str = "ranker_scripts.json"
    youtube_category_id: str = "24"     # Entertainment

    @property
    def download_dir(self) -> Path:
        return self.app.download_dir / "ranker"

    @property
    def music_dir(self) -> Path:
        return self.app.download_dir / "ranker" / "music"

    @property
    def output_dir(self) -> Path:
        return self.app.output_dir

    @property
    def ffmpeg_bin(self) -> Path:
        return self.app.ffmpeg_bin

    @property
    def gdrive_token_path(self) -> Path:
        return self.app.ranker_gdrive_token_path

    @property
    def gdrive_client_secret_path(self) -> Path:
        return self.app.ranker_client_secret_path


def build_ranker_config(app: AppConfig) -> RankerConfig:
    return RankerConfig(app=app)
