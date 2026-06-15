"""
Always-on Telegram bot listener.

Registered in Windows Task Scheduler to run at logon.
Long-polls Telegram getUpdates and handles:
  /run    — trigger pipeline if not already running
  /status — last run summary from latest manifest
  /next   — next scheduled YouTube publish times
  /ranker — generate one Top-5 ranking Short from the next Drive-queued script

Security: only responds to TELEGRAM_CHAT_ID from .env.
"""

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

_PIPELINE_CMD = [
    r"C:\Python313\python.exe",
    str(Path(__file__).parent.parent / "main.py"),
]

_ANALYZE_CMD = [
    r"C:\Python313\python.exe",
    str(Path(__file__).parent.parent / "analyze.py"),
]

_RANKER_CMD = [
    r"C:\Python313\python.exe",
    str(Path(__file__).parent.parent / "main.py"),
    "--ranker",
]

_PROJECT_ROOT = Path(__file__).parent.parent


def _api(token: str, method: str, payload: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        log.warning("Telegram API %s HTTP %d: %s", method, exc.code, body)
        return {}
    except Exception as exc:
        log.warning("Telegram API %s error: %s", method, exc)
        return {}


def _send(token: str, chat_id: str, text: str) -> None:
    _api(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def _load_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"offset": 0}


def _save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, state_path)


def _is_process_running(lock_path: Path) -> bool:
    if not lock_path.exists():
        return False
    try:
        pid = int(lock_path.read_text().strip())
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
        if handle == 0:
            lock_path.unlink(missing_ok=True)
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


def _last_manifest_summary() -> str:
    manifests_dir = _PROJECT_ROOT / "data" / "manifests"
    if not manifests_dir.exists():
        return "No runs found."
    dirs = sorted(manifests_dir.iterdir(), reverse=True)
    for d in dirs:
        manifest = d / "manifest.json"
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                date = data.get("run_date", "?")[:10]
                status = data.get("status", "?")
                clips = data.get("clips_produced", 0)
                uploads = data.get("uploads_scheduled", 0)
                lines = [f"Last run: {date} | {status} | {clips} clips | {uploads} scheduled on YT"]
                for niche, nd in data.get("niches", {}).items():
                    for v in nd.get("results", []):
                        if v.get("status") == "clipped":
                            scheduled = [
                                c.get("scheduled_publish_at", "?")
                                for c in v.get("clips", [])
                                if c.get("upload_status") == "scheduled"
                            ]
                            if scheduled:
                                lines.append(f"  • {v['title'][:50]} → {len(scheduled)} clip(s) scheduled")
                return "\n".join(lines)
            except Exception:
                pass
    return "No completed runs found."


def _next_scheduled() -> str:
    manifests_dir = _PROJECT_ROOT / "data" / "manifests"
    if not manifests_dir.exists():
        return "No scheduled uploads found."
    upcoming: list[str] = []
    for d in sorted(manifests_dir.iterdir(), reverse=True)[:5]:
        manifest = d / "manifest.json"
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            for niche, nd in data.get("niches", {}).items():
                for v in nd.get("results", []):
                    for c in v.get("clips", []):
                        if c.get("upload_status") == "scheduled" and c.get("scheduled_publish_at"):
                            upcoming.append(
                                f"  {c['scheduled_publish_at'][:16]} — {v['title'][:40]}"
                            )
        except Exception:
            pass
    if not upcoming:
        return "No upcoming scheduled uploads."
    upcoming.sort()
    return "Next scheduled uploads:\n" + "\n".join(upcoming[:10])


def run_listener(token: str, chat_id: str, state_path: Path, lock_path: Path) -> None:
    log.info("Telegram listener started. Polling for commands...")
    state = _load_state(state_path)
    offset = state.get("offset", 0)

    while True:
        try:
            resp = _api(token, "getUpdates", {
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["message"],
            })
            updates = resp.get("result", [])
        except Exception as exc:
            log.warning("getUpdates error: %s", exc)
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            state["offset"] = offset
            _save_state(state_path, state)

            msg = update.get("message", {})
            from_id = str(msg.get("from", {}).get("id", ""))
            text = (msg.get("text") or "").strip().lower()

            if from_id != str(chat_id):
                continue

            if text in ("/run", "run"):
                if _is_process_running(lock_path):
                    _send(token, chat_id, "⚙️ Pipeline is already running.")
                else:
                    _send(token, chat_id, "🚀 Starting pipeline...")
                    try:
                        subprocess.Popen(
                            _PIPELINE_CMD,
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                        )
                    except Exception as exc:
                        _send(token, chat_id, f"❌ Failed to start pipeline: {exc}")

            elif text in ("/status", "status"):
                _send(token, chat_id, _last_manifest_summary())

            elif text in ("/next", "next"):
                _send(token, chat_id, _next_scheduled())

            elif text in ("/analyze", "analyze"):
                analytics_lock = _PROJECT_ROOT / "data" / "analytics.lock"
                if _is_process_running(analytics_lock):
                    _send(token, chat_id, "⚙️ Analytics already in progress.")
                else:
                    _send(token, chat_id, "📊 Starting analytics run... you'll get a message when the report is ready.")
                    try:
                        subprocess.Popen(
                            _ANALYZE_CMD,
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                        )
                    except Exception as exc:
                        _send(token, chat_id, f"❌ Failed to start analytics: {exc}")

            elif text in ("/ranker", "ranker"):
                ranker_lock = _PROJECT_ROOT / "data" / "ranker.lock"
                if _is_process_running(ranker_lock) or _is_process_running(lock_path):
                    _send(token, chat_id, "⚙️ A pipeline/ranker run is already in progress.")
                else:
                    _send(token, chat_id, "🏆 Starting ranking-Short run... you'll get a summary when it's done.")
                    try:
                        subprocess.Popen(
                            _RANKER_CMD,
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                        )
                    except Exception as exc:
                        _send(token, chat_id, f"❌ Failed to start ranker: {exc}")

            elif text.startswith("/"):
                _send(token, chat_id, "Commands: /run · /status · /next · /analyze · /ranker")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [listener] %(levelname)s %(message)s",
    )

    # Import after sys.path is set by running as module
    sys.path.insert(0, str(_PROJECT_ROOT))

    try:
        import pip_system_certs.wrapt_requests  # noqa: F401
    except ImportError:
        pass

    from viral_clip_forge.config import load_config, ConfigurationError
    try:
        config = load_config()
    except ConfigurationError as exc:
        log.error("Config error: %s", exc)
        sys.exit(1)

    if not config.telegram_bot_token or not config.telegram_chat_id:
        log.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
        sys.exit(1)

    run_listener(
        token=config.telegram_bot_token,
        chat_id=config.telegram_chat_id,
        state_path=config.telegram_listener_state_path,
        lock_path=config.pipeline_lock_path,
    )


if __name__ == "__main__":
    main()
