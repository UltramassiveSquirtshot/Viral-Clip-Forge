import functools
import logging
import re
import time
import uuid
from pathlib import Path


def setup_logging(log_dir: Path, run_id: str) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    from datetime import date
    log_file = log_dir / f"pipeline_{date.today().isoformat()}.log"

    logger = logging.getLogger("viral_clip_forge")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)

        logger.addHandler(fh)
        logger.addHandler(ch)

    logger.info(f"Run {run_id} — logging to {log_file}")
    return logger


def get_logger(name: str = "viral_clip_forge") -> logging.Logger:
    return logging.getLogger(name)


def retry(max_attempts: int = 3, backoff_secs: float = 2.0, exceptions: tuple = (Exception,)):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            log = get_logger()
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    if attempt == max_attempts:
                        raise
                    wait = backoff_secs * (2 ** (attempt - 1))
                    log.warning(f"{fn.__name__} attempt {attempt}/{max_attempts} failed: {exc}. Retrying in {wait:.1f}s")
                    time.sleep(wait)
        return wrapper
    return decorator


def parse_iso_duration(duration: str) -> int:
    """Parse ISO 8601 duration string (PT4M30S) to total seconds."""
    if not duration:
        return 0
    pattern = r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
    m = re.match(pattern, duration)
    if not m:
        return 0
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS.mmm for FFmpeg -ss argument."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def generate_run_id() -> str:
    return str(uuid.uuid4())


def ensure_dirs(*paths: Path) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)
