"""
Always-on Telegram bot listener.

Registered in Windows Task Scheduler to run at logon.
Long-polls Telegram getUpdates and handles:
  /run                   — trigger CC-BY clip pipeline if not already running
  /status                — last run summary from latest manifest
  /next                  — next scheduled YouTube publish times
  /analyze               — trigger weekly analytics run
  /ranker                — propose 5 hardcoded themes (step 1)
  /pick <lettera>        — step 2: pick theme → search YouTube CC-BY, propose 5 videos
                           step 3: pick video → download, analyse, propose timestamps
  /confirm [timestamp]   — step 4: cut at timestamps, compose, upload Drive
                           No args = use suggested; args = custom (e.g. "0:32 1:45 3:10")
  /aishorts              — AI-Shorts step 1: pop script, synth voice, suggest image timestamps
  /assemble <RUN_ID>     — AI-Shorts step 3: download images, compose final video

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


def _handle_ranker_confirm(token: str, chat_id: str, args: str) -> None:
    """Handle /confirm [timestamps] — cut at timestamps, compose, upload to Drive."""
    try:
        subprocess.Popen(
            _PIPELINE_CMD + ["--ranker-confirm", args],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    except Exception as exc:
        _send(token, chat_id, f"❌ Errore avvio confirm: {exc}")


def _handle_ranker_pick(token: str, chat_id: str, letter: str) -> None:
    """Handle /pick b — step 2/3 of the ranker flow."""
    letter = letter.strip().lower()[:1]
    if not letter or letter not in "abcde":
        _send(token, chat_id, "⚠️ Uso: <code>/pick b</code>  (lettera a–e)")
        return
    try:
        subprocess.Popen(
            _PIPELINE_CMD + ["--ranker-pick", letter],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    except Exception as exc:
        _send(token, chat_id, f"❌ Errore avvio ranker pick: {exc}")


def _handle_aishorts_assemble(token: str, chat_id: str, run_id: str) -> None:
    """Handle /assemble <RUN_ID> — AI-Shorts step 3."""
    run_id = run_id.strip()
    if not run_id:
        _send(token, chat_id, "⚠️ Uso: <code>/assemble RUN_ID</code> (vedi la web UI o il messaggio di /aishorts)")
        return
    try:
        subprocess.Popen(
            _PIPELINE_CMD + ["--aishorts-assemble", run_id],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    except Exception as exc:
        _send(token, chat_id, f"❌ Errore avvio assemble: {exc}")


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

            elif text.startswith("/ranker"):
                ranker_lock = _PROJECT_ROOT / "data" / "ranker.lock"
                if _is_process_running(ranker_lock) or _is_process_running(lock_path):
                    _send(token, chat_id, "⚙️ Ranker già in esecuzione.")
                else:
                    # Extract optional custom query after "/ranker "
                    raw_ranker = text[len("/ranker"):].lstrip("@")
                    if raw_ranker and not raw_ranker[0].isspace():
                        parts = raw_ranker.split()
                        raw_ranker = " ".join(parts[1:]) if len(parts) > 1 else ""
                    custom_query = raw_ranker.strip()
                    cmd = _PIPELINE_CMD + ["--ranker"]
                    if custom_query:
                        cmd.append(custom_query)
                    try:
                        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
                    except Exception as exc:
                        _send(token, chat_id, f"❌ Errore avvio ranker: {exc}")

            elif text.startswith("/pick"):
                # handles "/pick b", "/pick  b", "/pick@botname b"
                raw = text[len("/pick"):].lstrip("@")
                if raw and not raw[0].isspace():
                    parts = raw.split()
                    raw = " ".join(parts[1:]) if len(parts) > 1 else ""
                letter_arg = raw.strip()
                if not letter_arg:
                    _send(token, chat_id, "⚠️ Uso: <code>/pick b</code>  (lettera a–e)")
                else:
                    _handle_ranker_pick(token, chat_id, letter_arg)

            elif text.startswith("/confirm"):
                # handles "/confirm", "/confirm 0:32 1:45", "/confirm@botname 0:32"
                raw = text[len("/confirm"):].lstrip("@")
                if raw and not raw[0].isspace():
                    parts = raw.split()
                    raw = " ".join(parts[1:]) if len(parts) > 1 else ""
                confirm_args = raw.strip()
                _handle_ranker_confirm(token, chat_id, confirm_args)

            elif text.startswith("/aishorts"):
                aishorts_lock = _PROJECT_ROOT / "data" / "aishorts.lock"
                if _is_process_running(aishorts_lock):
                    _send(token, chat_id, "⚙️ AI Shorts già in esecuzione.")
                else:
                    _send(token, chat_id, "🎬 Genero script &amp; voce AI Shorts... apri la web UI per i prompt.")
                    try:
                        subprocess.Popen(
                            _PIPELINE_CMD + ["--aishorts"],
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                        )
                    except Exception as exc:
                        _send(token, chat_id, f"❌ Errore avvio AI Shorts: {exc}")

            elif text.startswith("/assemble"):
                # handles "/assemble RUN_ID", "/assemble@botname RUN_ID"
                raw = text[len("/assemble"):].lstrip("@")
                if raw and not raw[0].isspace():
                    parts = raw.split()
                    raw = " ".join(parts[1:]) if len(parts) > 1 else ""
                _handle_aishorts_assemble(token, chat_id, raw.strip())

            elif text.startswith("/"):
                _send(token, chat_id,
                      "Comandi: /run · /status · /next · /analyze\n"
                      "/ranker · /pick &lt;lettera&gt; · /confirm [timestamp]\n"
                      "/aishorts · /assemble &lt;RUN_ID&gt;")


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
