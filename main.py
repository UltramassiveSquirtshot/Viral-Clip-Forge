import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from viral_clip_forge.approval import ApprovalError, approve_run, list_pending_runs, reject_run
from viral_clip_forge.config import load_config, ConfigurationError
from viral_clip_forge.pipeline import run_pipeline


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Viral Clip Forge")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--approve", metavar="RUN_ID", help="Approve a pending run and move its clips into clips/")
    group.add_argument("--reject", metavar="RUN_ID", help="Reject a pending run and discard its clips")
    group.add_argument("--list-pending", action="store_true", help="List runs awaiting approval")
    return parser.parse_args(argv)


def _load_config_or_exit() -> "AppConfig":  # noqa: F821
    try:
        return load_config()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        print("Create a .env file with YOUTUBE_API_KEY set. See .env.example.", file=sys.stderr)
        sys.exit(1)


def cmd_list_pending(config) -> int:
    pending = list_pending_runs(config)
    if not pending:
        print("No runs awaiting approval.")
        return 0

    for run in pending:
        print(f"\nRun {run['run_id']} ({run['run_date']})")
        print(f"  Manifest: {run['manifest_path']}")
        for niche_name, videos in run["niches"].items():
            print(f"  [{niche_name}]")
            for video in videos:
                print(f"    - {video['title']}  ({video['url']})")
                print(f"      channel={video['channel']} views={video['view_count']} license={video['license']}")
                for clip in video["clips"]:
                    if not clip["cut_successful"]:
                        continue
                    print(
                        f"        clip {clip['index']}: "
                        f"{clip['start_sec']:.1f}s-{clip['end_sec']:.1f}s "
                        f"({clip['duration_sec']:.1f}s, score={clip['combined_score']}) "
                        f"-> {clip['output_path']}"
                    )
    print(f"\n{len(pending)} run(s) awaiting approval.")
    print("Approve with: python main.py --approve <run_id>")
    print("Reject with:  python main.py --reject <run_id>")
    return 0


def cmd_approve(config, run_id: str) -> int:
    try:
        result = approve_run(config, run_id)
    except ApprovalError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Run {result['run_id']} approved.")
    print(f"Moved {len(result['moved_clips'])} clip(s) to {result['output_dir']}:")
    for name in result["moved_clips"]:
        print(f"  - {name}")
    return 0


def cmd_reject(config, run_id: str) -> int:
    try:
        result = reject_run(config, run_id)
    except ApprovalError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Run {result['run_id']} rejected. Pending clips discarded: {result['deleted_pending_dir']}")
    return 0


def cmd_run(config) -> int:
    result = run_pipeline(config)

    print(f"\nRun {result.run_id}: {result.status}")
    print(f"  Niches: {', '.join(result.niches_processed)}")
    print(f"  Videos found: {result.videos_found}")
    print(f"  Videos processed: {result.videos_processed}")
    print(f"  Videos skipped (license): {result.videos_skipped_license}")
    print(f"  Clips produced: {result.clips_produced}")
    print(f"  API units used: {result.api_units_used}")
    print(f"  Approval status: {result.approval_status}")
    if result.approval_status == "pending":
        print(f"  Pending clips dir: {result.pending_dir}")
        print(f"  Approve with: python main.py --approve {result.run_id}")
        print(f"  Reject with:  python main.py --reject {result.run_id}")
    if result.manifest_path:
        print(f"  Manifest: {result.manifest_path}")
    if result.errors:
        print(f"  Errors ({len(result.errors)}):")
        for e in result.errors:
            print(f"    - {e}")

    return 0 if result.status in ("completed", "partial") else 1


def main() -> int:
    args = parse_args(sys.argv[1:])
    config = _load_config_or_exit()

    if args.list_pending:
        return cmd_list_pending(config)
    if args.approve:
        return cmd_approve(config, args.approve)
    if args.reject:
        return cmd_reject(config, args.reject)

    return cmd_run(config)


if __name__ == "__main__":
    sys.exit(main())
