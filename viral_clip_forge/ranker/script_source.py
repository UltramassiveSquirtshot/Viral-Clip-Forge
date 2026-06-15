"""
Script source — reads ranking scripts from `ranker_scripts.json` on Google Drive.

The file is produced by the user pasting a fixed prompt into any LLM chat (see the
prompt file uploaded to Drive: ViralClipForge_Ranker_ScriptPrompt.txt). Its shape:

  {"videos": [
    {"theme": "...", "title": "...",
     "labels": ["l1","l2","l3","l4","l5"],
     "search_queries": ["q1","q2","q3","q4","q5"]}
  ]}

`load_next_script` downloads the file, pops the first VALID entry, rewrites the file
on Drive with that entry removed (queue behaviour), and returns it. Empty / missing →
None (the pipeline then exits cleanly, like "no CC videos found").
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..utils import get_logger
from . import gdrive
from .config import RankerConfig

log = get_logger()


@dataclass
class RankingScript:
    theme: str
    title: str
    labels: list[str]
    search_queries: list[str]


def _valid_entry(entry: dict, top_n: int) -> bool:
    if not isinstance(entry, dict):
        return False
    title = entry.get("title")
    labels = entry.get("labels")
    queries = entry.get("search_queries")
    if not isinstance(title, str) or not title.strip():
        return False
    if not isinstance(labels, list) or len(labels) != top_n:
        return False
    if not all(isinstance(x, str) and x.strip() for x in labels):
        return False
    if not isinstance(queries, list) or len(queries) != top_n:
        return False
    if not all(isinstance(x, str) and x.strip() for x in queries):
        return False
    return True


def load_next_script(cfg: RankerConfig) -> RankingScript | None:
    """Pop the first valid script from the Drive queue file and persist the removal."""
    service = gdrive.get_service(cfg.gdrive_token_path, cfg.gdrive_client_secret_path)

    file_id = gdrive.find_file(service, cfg.drive_scripts_name, cfg.drive_scripts_folder)
    if not file_id:
        # Fall back to a search without folder scoping (file may sit in My Drive root)
        file_id = gdrive.find_file(service, cfg.drive_scripts_name)
    if not file_id:
        log.warning(
            "[ranker] %s not found on Drive (folder '%s'). Upload one to queue a video.",
            cfg.drive_scripts_name, cfg.drive_scripts_folder,
        )
        return None

    try:
        raw = gdrive.download_text(service, file_id)
        data = json.loads(raw)
    except Exception as exc:
        log.error("[ranker] Could not read/parse %s: %s", cfg.drive_scripts_name, exc)
        return None

    videos = data.get("videos") if isinstance(data, dict) else None
    if not isinstance(videos, list) or not videos:
        log.info("[ranker] No scripts queued in %s.", cfg.drive_scripts_name)
        return None

    chosen = None
    chosen_idx = None
    for idx, entry in enumerate(videos):
        if _valid_entry(entry, cfg.top_n):
            chosen = entry
            chosen_idx = idx
            break
        log.warning("[ranker] Skipping malformed script entry at index %d", idx)

    if chosen is None:
        log.warning("[ranker] No valid script entries in %s.", cfg.drive_scripts_name)
        return None

    # Remove the consumed entry and rewrite the file on Drive (queue behaviour).
    remaining = [e for i, e in enumerate(videos) if i != chosen_idx]
    data["videos"] = remaining
    try:
        gdrive.update_text(service, file_id, json.dumps(data, ensure_ascii=False, indent=2))
        log.info("[ranker] Consumed 1 script; %d remaining in queue.", len(remaining))
    except Exception as exc:
        # Don't fail the run if the rewrite fails — but warn loudly (risk of reprocessing).
        log.error("[ranker] Could not rewrite %s after consuming entry: %s", cfg.drive_scripts_name, exc)

    return RankingScript(
        theme=str(chosen.get("theme", "")).strip() or "ranking",
        title=chosen["title"].strip(),
        labels=[s.strip() for s in chosen["labels"]],
        search_queries=[s.strip() for s in chosen["search_queries"]],
    )
