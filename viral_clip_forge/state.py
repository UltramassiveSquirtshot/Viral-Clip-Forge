import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from .utils import get_logger

log = get_logger()

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_videos (
    video_id        TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    niche           TEXT NOT NULL,
    license_status  TEXT NOT NULL,
    processed_at    TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    status          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS produced_clips (
    clip_id         TEXT PRIMARY KEY,
    video_id        TEXT NOT NULL REFERENCES processed_videos(video_id),
    start_sec       REAL NOT NULL,
    end_sec         REAL NOT NULL,
    output_path     TEXT NOT NULL,
    combined_score  REAL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_log (
    run_id              TEXT PRIMARY KEY,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    status              TEXT NOT NULL DEFAULT 'running',
    api_units_used      INTEGER NOT NULL DEFAULT 0,
    niches_processed    TEXT NOT NULL DEFAULT '[]',
    approval_status     TEXT NOT NULL DEFAULT 'not_required'
);
"""


def get_db_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema, for existing databases."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(run_log)").fetchall()}
    if "approval_status" not in cols:
        conn.execute(
            "ALTER TABLE run_log ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'not_required'"
        )


def is_video_processed(conn: sqlite3.Connection, video_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM processed_videos WHERE video_id = ?", (video_id,)
    ).fetchone()
    return row is not None


def get_processed_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT video_id FROM processed_videos").fetchall()
    return {r["video_id"] for r in rows}


def mark_video_processed(
    conn: sqlite3.Connection,
    video_id: str,
    title: str,
    niche: str,
    license_status: str,
    run_id: str,
    status: str,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO processed_videos
           (video_id, title, niche, license_status, processed_at, run_id, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (video_id, title, niche, license_status, _now(), run_id, status),
    )
    conn.commit()


def record_clip(
    conn: sqlite3.Connection,
    clip_id: str,
    video_id: str,
    start_sec: float,
    end_sec: float,
    output_path: str,
    combined_score: float | None,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO produced_clips
           (clip_id, video_id, start_sec, end_sec, output_path, combined_score, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (clip_id, video_id, start_sec, end_sec, output_path, combined_score, _now()),
    )
    conn.commit()


def record_run_start(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute(
        "INSERT INTO run_log (run_id, started_at) VALUES (?, ?)",
        (run_id, _now()),
    )
    conn.commit()


def record_run_finish(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    api_units_used: int,
    niches_processed: list[str],
    approval_status: str = "not_required",
) -> None:
    conn.execute(
        """UPDATE run_log
           SET finished_at=?, status=?, api_units_used=?, niches_processed=?, approval_status=?
           WHERE run_id=?""",
        (_now(), status, api_units_used, json.dumps(niches_processed), approval_status, run_id),
    )
    conn.commit()


def set_approval_status(conn: sqlite3.Connection, run_id: str, approval_status: str) -> None:
    conn.execute(
        "UPDATE run_log SET approval_status=? WHERE run_id=?",
        (approval_status, run_id),
    )
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM run_log WHERE run_id=?", (run_id,)).fetchone()


def get_pending_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM run_log WHERE approval_status='pending' ORDER BY started_at"
    ).fetchall()


def update_clip_path(conn: sqlite3.Connection, clip_id: str, new_output_path: str) -> None:
    conn.execute(
        "UPDATE produced_clips SET output_path=? WHERE clip_id=?",
        (new_output_path, clip_id),
    )
    conn.commit()


def delete_clips_for_run(conn: sqlite3.Connection, video_ids: list[str]) -> None:
    if not video_ids:
        return
    placeholders = ",".join("?" * len(video_ids))
    conn.execute(
        f"DELETE FROM produced_clips WHERE video_id IN ({placeholders})",
        video_ids,
    )
    conn.commit()


def get_today_api_units(conn: sqlite3.Connection) -> int:
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT COALESCE(SUM(api_units_used), 0) FROM run_log WHERE started_at LIKE ?",
        (f"{today}%",),
    ).fetchone()
    return int(row[0]) if row else 0


def update_run_api_units(conn: sqlite3.Connection, run_id: str, units: int) -> None:
    conn.execute(
        "UPDATE run_log SET api_units_used = api_units_used + ? WHERE run_id = ?",
        (units, run_id),
    )
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
