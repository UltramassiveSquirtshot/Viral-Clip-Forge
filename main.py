import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# On Windows, Avast (and similar AV) intercept HTTPS with their own CA cert.
# pip-system-certs patches the SSL stack to trust the Windows certificate store,
# which already includes the AV's intercepting CA, fixing verification for all libs.
try:
    import pip_system_certs.wrapt_requests  # noqa: F401
except ImportError:
    pass

from viral_clip_forge.config import load_config, ConfigurationError
from viral_clip_forge.pipeline import run_pipeline


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Viral Clip Forge")
    parser.add_argument(
        "--setup-youtube",
        action="store_true",
        help="Run OAuth consent flow to authenticate with YouTube and save token",
    )
    return parser.parse_args(argv)


def _load_config_or_exit():
    try:
        return load_config()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        print("Create a .env file with YOUTUBE_API_KEY set. See .env.example.", file=sys.stderr)
        sys.exit(1)


def cmd_setup_youtube(config) -> int:
    from viral_clip_forge.youtube_uploader import run_oauth_flow
    print("Opening browser for YouTube OAuth authentication...")
    try:
        run_oauth_flow(config)
        print(f"Authentication successful. Token saved to {config.youtube_token_path}")
        return 0
    except Exception as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        return 1


def cmd_run(config) -> int:
    import os

    # Write PID lockfile so the Telegram listener can detect a running pipeline
    lock_path = config.pipeline_lock_path
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(os.getpid()))

    try:
        result = run_pipeline(config)
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass

    print(f"\n{'='*70}")
    print(f"Run {result.run_id}  |  Status: {result.status.upper()}")
    print(f"API units used: {result.api_units_used}  |  Clips produced: {result.clips_produced}")
    uploads = getattr(result, "uploads_scheduled", 0)
    if uploads:
        print(f"Uploads scheduled on YouTube: {uploads}")
    print(f"{'='*70}")

    for ns in result.niche_summaries:
        print(f"\nNiche: {ns['niche'].upper()}")
        src = ns.get("sources", {})
        print(
            f"  Sources: cc_search={src.get('cc_search', 0)}  "
            f"trending={src.get('trending', 0)}  "
            f"scrapetube={src.get('scrapetube', 0)}"
        )
        fs = ns.get("filter_stats", {})
        print(
            f"  Filter:  {fs.get('before_dedup', '?')} raw -> "
            f"{fs.get('after_dedup', '?')} dedup -> "
            f"{fs.get('after_threshold', '?')} passed thresholds -> "
            f"{ns['videos_selected']} selected"
        )

        candidates = ns.get("top_candidates", [])
        if candidates:
            print(f"  Candidates:")
            print(f"  {'Title':<46} {'Views':>10}  {'Score':>6}  {'Source':>10}  Status")
            print(f"  {'-'*46} {'-'*10}  {'-'*6}  {'-'*10}  {'-'*20}")
            for c in candidates:
                title = c["title"]
                title_trunc = (title[:43] + "...") if len(title) > 46 else title
                score_str = f"{c['score']:.4f}" if c["score"] is not None else "   N/A"
                views_str = f"{c['views']:,}"
                print(
                    f"  {title_trunc:<46} {views_str:>10}  {score_str:>6}  "
                    f"{c['source']:>10}  {c['status']}"
                )
        else:
            print("  No candidates passed filtering.")

    if result.clips_produced == 0:
        print(f"\nNo clips produced. Diagnosis:")
        for ns in result.niche_summaries:
            src = ns.get("sources", {})
            fs = ns.get("filter_stats", {})
            cc_found = src.get("cc_search", 0)
            after_threshold = fs.get("after_threshold", 0)
            skipped = ns.get("videos_skipped", 0)
            selected = ns.get("videos_selected", 0)
            clipped = ns.get("videos_clipped", 0)
            if cc_found == 0:
                reason = "CC search returned 0 results — no CC-BY content found for these keywords"
            elif after_threshold == 0:
                reason = f"{cc_found} CC videos found but none passed view/duration thresholds"
            elif skipped == selected and selected > 0:
                reason = f"{selected} selected but all failed license check (API license mismatch)"
            elif clipped == 0 and selected > 0:
                reason = f"{selected} selected and licensed but download/cut failed"
            else:
                reason = "unknown — check logs"
            print(f"  [{ns['niche']}] {reason}")

    if result.manifest_path:
        print(f"\nManifest: {result.manifest_path}")
    if result.errors:
        print(f"\nErrors ({len(result.errors)}):")
        for e in result.errors:
            print(f"  - {e}")

    return 0 if result.status in ("completed", "partial") else 1


def main() -> int:
    args = parse_args(sys.argv[1:])
    config = _load_config_or_exit()

    if args.setup_youtube:
        return cmd_setup_youtube(config)

    return cmd_run(config)


if __name__ == "__main__":
    sys.exit(main())
