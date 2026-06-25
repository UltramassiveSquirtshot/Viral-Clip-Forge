"""
AiShortsConfig — AI-Shorts settings layered on top of the shared AppConfig.

Reuses the ranker's full-`drive`-scope OAuth token (it must read images the user
uploads BY HAND, which the analytics `drive.file` scope cannot see), the same
bold drawtext font, and the same 1080x1920 output geometry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig
from ..ranker.config import _default_font


@dataclass
class AiShortsConfig:
    app: AppConfig
    width: int = 1080
    height: int = 1920
    fps: int = 30
    voice: str = field(default_factory=lambda: os.getenv("AISHORTS_VOICE", "en-US-AriaNeural"))
    max_total_seconds: float = 60.0       # Shorts hard limit
    warn_total_seconds: float = 58.0      # warn the agent to shorten beyond this
    # Bright / punchy "MrBeast-style" Ken Burns: noticeable zoom drift.
    ken_burns_zoom_step: float = 0.0015
    ken_burns_zoom_max: float = 1.25
    font_path: str = field(default_factory=_default_font)
    drive_root_folder: str = "ViralClipForge"
    drive_aishorts_folder: str = "ai_shorts"
    drive_finals_folder: str = "finals"
    drive_scripts_folder: str = "ViralClipForge"
    drive_scripts_name: str = "ai_shorts_scripts.json"

    @property
    def work_dir(self) -> Path:
        return self.app.download_dir / "aishorts"

    @property
    def output_dir(self) -> Path:
        return self.app.output_dir

    @property
    def ffmpeg_bin(self) -> Path:
        return self.app.ffmpeg_bin

    @property
    def ffprobe_bin(self) -> Path:
        return self.app.ffprobe_bin

    @property
    def gdrive_token_path(self) -> Path:
        return self.app.ranker_gdrive_token_path

    @property
    def gdrive_client_secret_path(self) -> Path:
        return self.app.ranker_client_secret_path

    @property
    def pending_path(self) -> Path:
        return self.app.state_db_path.parent / "aishorts_pending.json"

    @property
    def lock_path(self) -> Path:
        return self.app.state_db_path.parent / "aishorts.lock"

    def run_dir(self, run_id: str) -> Path:
        return self.work_dir / run_id


def build_aishorts_config(app: AppConfig) -> AiShortsConfig:
    return AiShortsConfig(app=app)
