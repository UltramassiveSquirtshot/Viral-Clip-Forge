import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_BASE = Path(__file__).parent.parent


@dataclass
class NicheConfig:
    name: str
    category_ids: list[str]
    search_keywords: list[str]
    trending_region: str
    max_results_per_source: int


@dataclass
class AppConfig:
    youtube_api_key: str
    ffmpeg_bin: Path
    ffprobe_bin: Path
    output_dir: Path
    download_dir: Path
    state_db_path: Path
    log_dir: Path
    max_clips_per_video: int = 3
    min_clip_duration: int = 30
    max_clip_duration: int = 90
    scene_threshold: float = 0.40
    audio_peak_percentile: int = 85
    min_views: int = 50_000
    max_video_duration: int = 1800
    require_approval: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    niches: dict[str, NicheConfig] = field(default_factory=dict)


class ConfigurationError(Exception):
    pass


_DEFAULT_NICHES: dict[str, NicheConfig] = {
    "tech": NicheConfig(
        name="tech",
        category_ids=["28"],
        search_keywords=[
            "AI breakthrough 2025",
            "tech news",
            "artificial intelligence",
            "new gadget review",
            "startup funding",
        ],
        trending_region="US",
        max_results_per_source=25,
    ),
    "finance": NicheConfig(
        name="finance",
        category_ids=["25"],
        search_keywords=[
            "stock market crash",
            "bitcoin price",
            "investing strategy 2025",
            "passive income",
            "real estate market",
        ],
        trending_region="US",
        max_results_per_source=25,
    ),
}


def load_config() -> AppConfig:
    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        raise ConfigurationError("YOUTUBE_API_KEY is not set in .env")

    ffmpeg_bin = Path(os.getenv("FFMPEG_BIN", "ffmpeg"))
    ffprobe_bin = Path(os.getenv("FFPROBE_BIN", "ffprobe"))

    return AppConfig(
        youtube_api_key=api_key,
        ffmpeg_bin=ffmpeg_bin,
        ffprobe_bin=ffprobe_bin,
        output_dir=Path(os.getenv("OUTPUT_DIR", str(_BASE / "clips"))),
        download_dir=Path(os.getenv("DOWNLOAD_DIR", str(_BASE / "downloads"))),
        state_db_path=Path(os.getenv("STATE_DB_PATH", str(_BASE / "data" / "state.db"))),
        log_dir=Path(os.getenv("LOG_DIR", str(_BASE / "logs"))),
        max_clips_per_video=int(os.getenv("MAX_CLIPS_PER_VIDEO", "3")),
        min_clip_duration=int(os.getenv("MIN_CLIP_DURATION", "30")),
        max_clip_duration=int(os.getenv("MAX_CLIP_DURATION", "90")),
        scene_threshold=float(os.getenv("SCENE_THRESHOLD", "0.40")),
        audio_peak_percentile=int(os.getenv("AUDIO_PEAK_PERCENTILE", "85")),
        min_views=int(os.getenv("MIN_VIEWS", "50000")),
        max_video_duration=int(os.getenv("MAX_VIDEO_DURATION", "1800")),
        require_approval=os.getenv("REQUIRE_APPROVAL", "true").strip().lower() not in ("false", "0", "no", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        niches=_DEFAULT_NICHES,
    )
