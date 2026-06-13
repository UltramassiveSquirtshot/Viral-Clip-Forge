from __future__ import annotations

import json
import urllib.request

from .utils import get_logger

log = get_logger()


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:
        log.warning(f"[telegram] Failed to send notification: {exc}")
        return False


def _fmt(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def build_run_summary(result) -> str:
    lines = [
        f"🎬 <b>Viral Clip Forge</b> — run complete",
        f"📅 {result.started_at[:10]}  ·  {result.clips_produced} clips  ·  {result.uploads_scheduled} scheduled on YT",
        "",
    ]

    if result.manifest_path:
        try:
            with open(result.manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
            for niche_name, niche in manifest.get("niches", {}).items():
                clipped_videos = [v for v in niche.get("results", []) if v.get("status") == "clipped"]
                if not clipped_videos:
                    continue
                lines.append(f"<b>[{niche_name.upper()}]</b>")
                for video in clipped_videos:
                    lines.append(
                        f"• <a href=\"{video['url']}\">{video['title'][:55]}</a>"
                    )
                    lines.append(
                        f"  {video['channel']} · {video['view_count']:,} views"
                    )
                    scheduled_clips = [
                        c for c in video.get("clips", [])
                        if c.get("cut_successful") and c.get("upload_status") == "scheduled"
                    ]
                    for clip in scheduled_clips:
                        publish = (clip.get("scheduled_publish_at") or "")[:16].replace("T", " ")
                        yt_url = clip.get("youtube_url", "")
                        lines.append(
                            f"  ↳ clip {clip['index']}: {_fmt(clip['start_sec'])}–{_fmt(clip['end_sec'])}"
                            f"  → <a href=\"{yt_url}\">YT</a> @ {publish}"
                        )
                lines.append("")
        except Exception as exc:
            log.warning(f"[telegram] Could not read manifest for notification: {exc}")

    if result.uploads_scheduled == 0 and result.clips_produced == 0:
        lines.append(f"ℹ️ No CC-BY clips found · API units used: {result.api_units_used}")
    elif result.uploads_scheduled == 0 and result.clips_produced > 0:
        lines.append(f"⚠️ {result.clips_produced} clip(s) cut but not uploaded — run python main.py --setup-youtube")
    else:
        # Find next publish time
        next_pub = None
        if result.manifest_path:
            try:
                with open(result.manifest_path, encoding="utf-8") as f:
                    manifest = json.load(f)
                times = [
                    c.get("scheduled_publish_at")
                    for n in manifest.get("niches", {}).values()
                    for v in n.get("results", [])
                    for c in v.get("clips", [])
                    if c.get("scheduled_publish_at")
                ]
                if times:
                    next_pub = sorted(times)[0][:16].replace("T", " ")
            except Exception:
                pass
        if next_pub:
            lines.append(f"📦 {result.uploads_scheduled} clip(s) scheduled · next publish at {next_pub}")

    if result.errors:
        lines.append(f"\n⚠️ {len(result.errors)} error(s) — check logs")

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

    text = build_run_summary(result)
    ok = send_telegram(token, chat_id, text)
    if ok:
        log.info("[telegram] Notification sent")
