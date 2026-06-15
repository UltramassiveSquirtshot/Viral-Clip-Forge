"""
Validates and loads analytics_insights.json into AppConfig at pipeline startup.

Called once from config.py after AppConfig is built. Overwrites fields explicitly —
nothing changes silently.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_ALLOWED_CONFIG_OVERRIDES = {
    "scene_threshold": float,
    "audio_peak_percentile": int,
    "preferred_clip_duration": int,
    "min_views": int,
}


def apply_analytics_insights(config, insights_path: Path) -> None:
    """
    Mutates config in-place with values from analytics_insights.json.
    Logs each override. Skips gracefully if file is missing or malformed.
    """
    if not insights_path.exists():
        log.debug("[insights] No analytics_insights.json found — using defaults")
        return

    try:
        data = json.loads(insights_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("[insights] Could not parse analytics_insights.json: %s — skipping", exc)
        return

    # Apply config_overrides
    overrides = data.get("config_overrides", {})
    for key, cast in _ALLOWED_CONFIG_OVERRIDES.items():
        if key in overrides:
            old_val = getattr(config, key, None)
            try:
                new_val = cast(overrides[key])
                setattr(config, key, new_val)
                log.info("[insights] Override: %s  %s → %s", key, old_val, new_val)
            except (ValueError, TypeError) as exc:
                log.warning("[insights] Invalid value for %s: %s — skipping", key, exc)

    # Apply upload_slot_priorities (reorder upload_slots_local)
    slot_priorities = overrides.get("upload_slot_priorities")
    if slot_priorities and isinstance(slot_priorities, list):
        valid = [s for s in slot_priorities if s in config.upload_slots_local]
        remainder = [s for s in config.upload_slots_local if s not in valid]
        new_slots = valid + remainder
        if new_slots != config.upload_slots_local:
            log.info("[insights] Override: upload_slots_local  %s → %s", config.upload_slots_local, new_slots)
            config.upload_slots_local = new_slots

    # Apply keyword_overrides — completely replaces niche keywords
    keyword_overrides = data.get("keyword_overrides", {})
    for niche_name, kw_data in keyword_overrides.items():
        niche = config.niches.get(niche_name)
        if not niche:
            log.warning("[insights] keyword_overrides: niche '%s' not found — skipping", niche_name)
            continue

        if "search_keywords" in kw_data and isinstance(kw_data["search_keywords"], list):
            new_kws = [str(k) for k in kw_data["search_keywords"] if k]
            if new_kws:
                log.info(
                    "[insights] Override: niches[%s].search_keywords  %s → %s",
                    niche_name, niche.search_keywords, new_kws,
                )
                niche.search_keywords = new_kws

        if "cc_search_keywords" in kw_data and isinstance(kw_data["cc_search_keywords"], list):
            new_cc = [str(k) for k in kw_data["cc_search_keywords"] if k]
            if new_cc:
                log.info(
                    "[insights] Override: niches[%s].cc_search_keywords  %s → %s",
                    niche_name, niche.cc_search_keywords, new_cc,
                )
                niche.cc_search_keywords = new_cc
