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
     