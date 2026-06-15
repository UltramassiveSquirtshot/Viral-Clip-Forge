"""
Viral Clip Forge — Analytics Runner

Standalone entry point, completely separate from main.py.

Usage:
  python analyze.py                   # run analytics, write reports, upload to Drive, notify Telegram
  python analyze.py --setup-analytics # one-time OAuth for YouTube Analytics (separate client secret)
  python analyze.py --setup-gdrive    # one-time OAuth for Google Drive upload

What it does:
  1. Fetches YouTube Analytics for all clips uploaded in the past 14 days
  2. Fetches YouTube Search Suggestions for trending keyword candidates
  3. Correlates analytics with production metadata (clip reason, slot, keyword)
  4. Writes YYYY-MM-DD_analytics.md + YYYY-MM-DD_context.json to data/analytics_reports/
  5. Uploads both files to Google Drive (lorenzotervel@gmail.com)
  6. Sends Telegram notification with Drive links
"""

import logging
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    import pip_system_certs.wrapt_requests  # noqa: F401
except ImportError:
    pass


def _setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    log_file = log_dir / f"analytics_{datetime.now().strftime('%Y-%m-%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [analytics] %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_file), encoding="utf-8"),
        ],
    )


def cmd_setup_analytics(config) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow
    client_secret = config.analytics_client_secret_path
    if not client_secret.exists():
        print(
            f"ERROR: Analytics client secret not found at {client_secret}\n"
            "Steps:\n"
            "  1. Go to Google Cloud Console → APIs & Services → Credentials\n"
            "  2. Create a new OAuth 2.0 Desktop client (or reuse existing)\n"
            "  3. Download JSON and save as data/analytics_client_secret.json\n"
            "  4. Enable 'YouTube Analytics API' in APIs & Services → Enabled APIs"
        )
        return
    scopes = ["https://www.googleapis.com/auth/yt-analytics.readonly"]
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), scopes)
    creds = flow.run_local_server(port=0, open_browser=True)
    config.analytics_token_path.parent.mkdir(parents=True, exist_ok=True)
    config.analytics_token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"Analytics OAuth complete. Token saved to {config.analytics_token_path}")


def cmd_setup_gdrive(config) -> None:
    from viral_clip_forge.analytics.uploader_gdrive import run_gdrive_oauth_flow
    run_gdrive_oauth_flow(
        token_path=config.gdrive_token_path,
        client_secret_path=config.analytics_client_secret_path,
    )


def cmd_analyze(config) -> int:
    log = logging.getLogger(__name__)
    lock_path = config.analytics_lock_path

    # Write lock file
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(os.getpid()), encoding="utf-8")

    try:
        # Step 1: Fetch YouTube Analytics
        log.info("=== Step 1: Fetching YouTube Analytics ===")
        from viral_clip_forge.analytics.fetcher import fetch_analytics, ScopeMissingError
        try:
            clips = fetch_analytics(config)
        except ScopeMissingError as exc:
            log.error("Analytics scope error:\n%s", exc)
            _notify_error(config, str(exc))
            return 1

        if not clips:
            log.warning("No analytics data available yet. Need at least 7 days of uploaded clips.")
            _notify_no_data(config)
            return 0

        # Step 2: Fetch trending keywords
        log.info("=== Step 2: Fetching trending keyword suggestions ===")
        from viral_clip_forge.analytics.trending_topics import fetch_trending_keywords
        suggested_search, suggested_cc = fetch_trending_keywords(config, niche_name="tech")

        # Step 3: Build correlation report
        log.info("=== Step 3: Building correlation report ===")
        from viral_clip_forge.analytics.correlator import build_correlation_report
        report = build_correlation_report(clips)
        report.suggested_search_keywords = suggested_search
        report.suggested_cc_keywords = suggested_cc

        # Also surface trending candidates in the report
        report.trending_candidates = suggested_search + suggested_cc

        # Step 4: Write report files
        log.info("=== Step 4: Writing report files ===")
        from viral_clip_forge.analytics.report import write_reports
        md_path, json_path = write_reports(report, config.analytics_reports_dir)

        # Step 5: Upload to Google Drive
        log.info("=== Step 5: Uploading to Google Drive ===")
        md_url, json_url = "", ""
        if config.gdrive_token_path.exists():
            try:
                from viral_clip_forge.analytics.uploader_gdrive import upload_reports
                md_url, json_url = upload_reports(
                    md_path=md_path,
                    json_path=json_path,
                    token_path=config.gdrive_token_path,
                    client_secret_path=config.youtube_client_secret_path,
                )
            except Exception as exc:
                log.warning("Google Drive upload failed: %s", exc)
                log.info("Reports saved locally at %s", config.analytics_reports_dir)
        else:
            log.info("Google Drive not set up — skipping upload. Run: python analyze.py --setup-gdrive")

        # Step 6: Telegram notification
        log.info("=== Step 6: Sending Telegram notification ===")
        _notify_complete(config, report, md_path, md_url, json_url)

        log.info("=== Analytics complete ===")
        log.info("  Report: %s", md_path)
        log.info("  Context: %s", json_path)
        if md_url:
            log.info("  Drive (report): %s", md_url)
        if json_url:
            log.info("  Drive (context): %s", json_url)

        return 0

    finally:
        lock_path.unlink(missing_ok=True)


def _notify_complete(config, report, md_path: Path, md_url: str, json_url: str) -> None:
    from viral_clip_forge.notifier import send_telegram

    if not config.telegram_bot_token or not config.telegram_chat_id:
        return

    top_retention = 0.0
    if report.raw_clips:
        top_retention = max(c["retention_rate"] for c in report.raw_clips)

    lines = [
        "📊 <b>Analytics Report Ready</b>",
        f"  {report.clips_with_data} clips analyzed · top retention: {top_retention * 100:.1f}%",
        f"  Score predicts performance: {'✅' if report.score_predicts_performance else '❌ (needs tuning)'}",
    ]

    if report.suggested_search_keywords:
        kws = ", ".join(f"<i>{k}</i>" for k in report.suggested_search_keywords[:3])
        lines.append(f"  Trending keywords: {kws}…")

    if md_url:
        lines.append(f"\n📄 <a href=\"{md_url}\">Open Report (Drive)</a>")
    else:
        lines.append(f"\n📄 Report saved locally: <code>{md_path.name}</code>")

    if json_url:
        lines.append(f"🔧 <a href=\"{json_url}\">Context JSON (paste into Claude)</a>")

    send_telegram(config.telegram_bot_token, config.telegram_chat_id, "\n".join(lines))


def _notify_no_data(config) -> None:
    from viral_clip_forge.notifier import send_telegram
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return
    send_telegram(
        config.telegram_bot_token,
        config.telegram_chat_id,
        "📊 Analytics run: no data yet. Need at least 7 days of published clips.",
    )


def _notify_error(config, msg: str) -> None:
    from viral_clip_forge.notifier import send_telegram
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return
    short = msg[:300]
    send_telegram(
        config.telegram_bot_token,
        config.telegram_chat_id,
        f"❌ Analytics failed:\n<code>{short}</code>",
    )


def main() -> int:
    from viral_clip_forge.config import load_config, ConfigurationError

    try:
        config = load_config()
    except ConfigurationError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    _setup_logging(config.log_dir)
    log = logging.getLogger(__name__)

    args = sys.argv[1:]

    if "--setup-analytics" in args:
        try:
            cmd_setup_analytics(config)
            return 0
        except Exception as exc:
            log.error("Analytics OAuth setup failed: %s", exc)
            return 1

    if "--setup-gdrive" in args:
        try:
            cmd_setup_gdrive(config)
            return 0
        except Exception as exc:
            log.error("Google Drive setup failed: %s", exc)
            return 1

    log.info("Viral Clip Forge — Analytics Runner starting")
    return cmd_analyze(config)


if __name__ == "__main__":
    sys.exit(main())
