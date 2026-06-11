from __future__ import annotations

import urllib.request
import urllib.parse
import json

from .utils import get_logger

log = get_logger()


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:
        log.warning(f"[telegram] Failed to send notification: {exc}")
        return False


def build_pending_message(result) -> str:
    lines = [
        f"🎬 <b>Viral Clip Forge</b> — run ready for approval",
        f"Run ID: <code>{result.run_id}</code>",
        f"Clips produced: {result.clips_produced}",
        "",
    ]

    if result.manifest_path:
        try:
            with open(result.manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
            for niche_name, niche in manifest.get("niches", {}).items():
                lines.append(f"<b>[{niche_name}]</b>")
                for video in niche.get("results", []):
                    if video.get("status") != "clipped":
                        continue
                    lines.append(f"  • <a href=\"{video['url']}\">{video['title'][:60]}</a>")
                    lines.append(f"    {video['channel']} · {video['view_count']:,} views · license: {video['license']}")
                    for clip in video.get("clips", []):
                        if not clip.get("cut_successful"):
                            continue
                        lines.append(
                            f"    clip {clip['index']}: {clip['start_sec']:.0f}s–{clip['end_sec']:.0f}s "
                            f"({clip['duration_sec']:.0f}s, score={clip.get('combined_score', 0):.2f})"
                        )
                lines.append("")
        except Exception as exc:
            log.warning(f"[telegram] Could not read manifest for notification: {exc}")

    lines += [
        "Approve or reject from your machine:",
        f"  python main.py --approve {result.run_id}",
        f"  python main.py --reject {result.run_id}",
    ]
    return "\n".join(lines)


def build_noclips_message(result) -> str:
    return (
        f"🎬 <b>Viral Clip Forge</b> — run completed, no CC-BY clips found\n"
        f"Run ID: <code>{result.run_id}</code>\n"
        f"Videos skipped (license): {result.videos_skipped_license}\n"
        f"API units used: {result.api_units_used}"
    )


def notify_run(token: str, chat_id: str, result) -> None:
    if not token or not chat_id:
        log.debug("[telegram] No token/chat_id configured — skipping notification")
        return

    if result.approval_status == "pending":
        text = build_pending_message(result)
    else:
        text = build_noclips_message(result)

    ok = send_telegram(token, chat_id, text)
    if ok:
        log.info("[telegram] Notification sent")
