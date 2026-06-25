"""
Script source — reads AI-Shorts scripts from `ai_shorts_scripts.json` on Drive.

The file is produced by the Cowork agent (WebSearch trends → copy a viral
structure → write narration + Leonardo image prompts). Its shape:

  {"videos": [
    {"theme": "...", "title": "...", "hook": "...",
     "beats": [
       {"narration": "...", "leonardo_prompt": "..."},
       ...
     ]}
  ]}

`load_next_script` downloads the file, pops the first VALID entry, rewrites the
file on Drive with that entry removed (queue behaviour), and returns it. Empty /
missing → None (the pipeline then exits cleanly, like "no scripts queued").

Mirrors viral_clip_forge/ranker/script_source.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..utils import get_logger
from ..ranker import gdrive
from .config import AiShortsConfig

log = get_logger()


@dataclass
class Beat:
    narration: str
    leonardo_prompt: str


@dataclass
class AiShortsScript:
    theme: str
    title: str
    hook: str
    beats: list[Beat]


def _valid_entry(entry: dict) -> bool:
    if not isinstance(entry, dict):
        return False
    title = entry.get("title")
    beats = entry.get("beats")
    if not isinstance(title, str) or not title.strip():
        return False
    if not isinstance(beats, list) or not beats:
        return False
    for b in beats:
        if not isinstance(b, dict):
            return False
        narration = b.get("narration")
        prompt = b.get("leonardo_prompt")
        if not isinstance(narration, str) or not narration.strip():
            return False
        if not isinstance(prompt, str) or not prompt.strip():
            return False
    return True


def load_next_script(cfg: AiShortsConfig) -> tuple[AiShortsScript | None, int]:
    """Pop the first valid script from the Drive queue file and persist removal.

    Returns (script, remaining_count). script is None when the queue is empty or
    the file is missing; remaining_count is 0 in those cases.
    """
    service = gdrive.get_service(cfg.gdrive_token_path, cfg.gdrive_client_secret_path)

    file_id = gdrive.find_file(service, cfg.drive_scripts_name, cfg.drive_scripts_folder)
    if not file_id:
        # Fall back to a search without folder scoping (file may sit in My Drive root)
        file_id = gdrive.find_file(service, cfg.drive_scripts_name)
    if not file_id:
        log.warning(
            "[aishorts] %s not found on Drive (folder '%s'). Upload one to queue a video.",
            cfg.drive_scripts_name, cfg.drive_scripts_folder,
        )
        return None, 0

    try:
        raw = gdrive.download_text(service, file_id)
        data = json.loads(raw)
    except Exception as exc:
        log.error("[aishorts] Could not read/parse %s: %s", cfg.drive_scripts_name, exc)
        return None, 0

    videos = data.get("videos") if isinstance(data, dict) else None
    if not isinstance(videos, list) or not videos:
        log.info("[aishorts] No scripts queued in %s.", cfg.drive_scripts_name)
        return None, 0

    chosen = None
    chosen_idx = None
    for idx, entry in enumerate(videos):
        if _valid_entry(entry):
            chosen = entry
            chosen_idx = idx
            break
        log.warning("[aishorts] Skipping malformed script entry at index %d", idx)

    if chosen is None:
        log.warning("[aishorts] No valid script entries in %s.", cfg.drive_scripts_name)
        return None, 0

    # Remove the consumed entry and rewrite the file on Drive (queue behaviour).
    remaining = [e for i, e in enumerate(videos) if i != chosen_idx]
    data["videos"] = remaining
    try:
        gdrive.update_text(service, file_id, json.dumps(data, ensure_ascii=False, indent=2))
        log.info("[aishorts] Consumed 1 script; %d remaining in queue.", len(remaining))
    except Exception as exc:
        # Don't fail the run if the rewrite fails — but warn loudly (risk of reprocessing).
        log.error("[aishorts] Could not rewrite %s after consuming entry: %s", cfg.drive_scripts_name, exc)

    beats = [
        Beat(narration=str(b["narration"]).strip(), leonardo_prompt=str(b["leonardo_prompt"]).strip())
        for b in chosen["beats"]
    ]
    return AiShortsScript(
        theme=str(chosen.get("theme", "")).strip() or "ai_shorts",
        title=chosen["title"].strip(),
        hook=str(chosen.get("hook", "")).strip(),
        beats=beats,
    ), len(remaining)
