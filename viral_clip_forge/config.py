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
    cc_search_keywords: list[str]
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
    preferred_clip_duration: int = 45
    dead_zone_start_pct: float = 0.08
    dead_zone_end_pct: float = 0.05
    shorts_output: bool = True
    min_views: int = 10_000
    max_video_duration: int = 1800
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # YouTube upload
    youtube_client_secret_path: Path = field(default_factory=lambda: _BASE / "data" / "youtube_client_secret.json")
    youtube_token_path: Path = field(default_factory=lambda: _BASE / "data" / "youtube_token.json")
    youtube_channel_id: str = ""
    # Algorithm-aware scheduler
    schedule_state_path: Path = field(default_factory=lambda: _BASE / "data" / "schedule_state.json")
    max_uploads_per_day: int = 3
    upload_days: list[str] = field(default_factory=lambda: ["Tuesday", "Wednesday", "Thursday", "Saturday"])
    upload_slots_local: list[str] = field(default_factory=lambda: ["08:00", "13:00", "19:30"])
    # Telegram listener
    pipeline_lock_path: Path = field(default_factory=lambda: _BASE / "data" / "pipeline.lock")
    analytics_lock_path: Path = field(default_factory=lambda: _BASE / "data" / "analytics.lock")
    telegram_listener_state_path: Path = field(default_factory=lambda: _BASE / "data" / "listener_state.json")
    # Analytics — separate OAuth client from YouTube pipeline
    analytics_client_secret_path: Path = field(default_factory=lambda: _BASE / "data" / "analytics_client_secret.json")
    analytics_token_path: Path = field(default_factory=lambda: _BASE / "data" / "analytics_token.json")
    analytics_insights_path: Path = field(default_factory=lambda: _BASE / "data" / "analytics_insights.json")
    analytics_reports_dir: Path = field(default_factory=lambda: _BASE / "data" / "analytics_reports")
    gdrive_token_path: Path = field(default_factory=lambda: _BASE / "data" / "gdrive_token.json")
    # Ranking Shorts (ranker.py) — own Drive token (full drive scope) + stock/music API keys
    ranker_gdrive_token_path: Path = field(default_factory=lambda: _BASE / "data" / "ranker_gdrive_token.json")
    ranker_client_secret_path: Path = field(default_factory=lambda: _BASE / "data" / "ranker_client_secret.json")
    ranker_lock_path: Path = field(default_factory=lambda: _BASE / "data" / "ranker.lock")
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
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
        cc_search_keywords=[
            "open source tutorial",
            "linux explained",
            "python programming tutorial",
            "AI explained",
            "technology review creative commons",
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

    cfg = AppConfig(
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
        preferred_clip_duration=int(os.getenv("PREFERRED_CLIP_DURATION", "45")),
        dead_zone_start_pct=float(os.getenv("DEAD_ZONE_START_PCT", "0.08")),
        dead_zone_end_pct=float(os.getenv("DEAD_ZONE_END_PCT", "0.05")),
        shorts_output=os.getenv("SHORTS_OUTPUT", "true").lower() != "false",
        min_views=int(os.getenv("MIN_VIEWS", "10000")),
        max_video_duration=int(os.getenv("MAX_VIDEO_DURATION", "1800")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        youtube_client_secret_path=Path(os.getenv(
            "YOUTUBE_CLIENT_SECRET_PATH",
            str(_BASE / "data" / "youtube_client_secret.json"),
        )),
        youtube_token_path=Path(os.getenv(
            "YOUTUBE_TOKEN_PATH",
            str(_BASE / "data" / "youtube_token.json"),
        )),
        youtube_channel_id=os.getenv("YOUTUBE_CHANNEL_ID", ""),
        schedule_state_path=Path(os.getenv(
            "SCHEDULE_STATE_PATH",
            str(_BASE / "data" / "schedule_state.json"),
        )),
        max_uploads_per_day=int(os.getenv("MAX_UPLOADS_PER_DAY", "3")),
        pipeline_lock_path=Path(os.getenv(
            "PIPELINE_LOCK_PATH",
            str(_BASE / "data" / "pipeline.lock"),
        )),
        telegram_listener_state_path=Path(os.getenv(
            "TELEGRAM_LISTENER_STATE_PATH",
            str(_BASE / "data" / "listener_state.json"),
        )),
        ranker_gdrive_token_path=Path(os.getenv(
            "RANKER_GDRIVE_TOKEN_PATH",
            str(_BASE / "data" / "ranker_gdrive_token.json"),
        )),
        ranker_client_secret_path=Path(os.getenv(
            "RANKER_CLIENT_SECRET_PATH",
            str(_BASE / "data" / "ranker_client_secret.json"),
        )),
        pexels_api_key=os.getenv("PEXELS_API_KEY", ""),
        pixabay_api_key=os.getenv("PIXABAY_API_KEY", ""),
        niches=_DEFAULT_NICHES,
    )

    # Pull the latest analytics_insights.json from Google Drive before applying.
    # Uses ranker OAuth token (full drive scope); silently falls back to local file.
    if cfg.ranker_gdrive_token_path.exists():
        try:
            from .analytics.uploader_gdrive import download_insights
            download_insights(cfg.ranker_gdrive_token_path, cfg.ranker_client_secret_path, cfg.analytics_insights_path)
        except Exception:
            pass

    from .analytics.applier import apply_analytics_insights
    apply_analytics_insights(cfg, cfg.analytics_insights_path)
    return cfg
