import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from viral_clip_forge.config import load_config, ConfigurationError
from viral_clip_forge.pipeline import run_pipeline


def main() -> int:
    try:
        config = load_config()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        print("Create a .env file with YOUTUBE_API_KEY set. See .env.example.", file=sys.stderr)
        return 1

    result = run_pipeline(config)

    print(f"\nRun {result.run_id}: {result.status}")
    print(f"  Niches: {', '.join(result.niches_processed)}")
    print(f"  Videos found: {result.videos_found}")
    print(f"  Videos processed: {result.videos_processed}")
    print(f"  Videos skipped (license): {result.videos_skipped_license}")
    print(f"  Clips produced: {result.clips_produced}")
    print(f"  API units used: {result.api_units_used}")
    if result.manifest_path:
        print(f"  Manifest: {result.manifest_path}")
    if result.errors:
        print(f"  Errors ({len(result.errors)}):")
        for e in result.errors:
            print(f"    - {e}")

    return 0 if result.status in ("completed", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
