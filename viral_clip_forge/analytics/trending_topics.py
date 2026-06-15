"""
Fetches YouTube Search Suggestions for the current niche keywords.

Uses the public autocomplete endpoint (no API key required).
Seeds: current config.search_keywords for each niche.
Returns ranked keyword candidates scored by novelty and specificity.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

_SUGGEST_URL = "https://suggestqueries.google.com/complete/search"


def _fetch_suggestions(seed: str, lang: str = "en") -> list[str]:
    params = urllib.parse.urlencode({
        "client": "youtube",
        "ds": "yt",
        "q": seed,
        "hl": lang,
    })
    url = f"{_SUGGEST_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            # Response format: ["seed", ["suggestion1", "suggestion2", ...], ...]
            data = json.loads(raw)
            return data[1] if len(data) > 1 else []
    except Exception as exc:
        log.warning("[trending] Failed to fetch suggestions for '%s': %s", seed, exc)
        return []


def _score_keyword(kw: str, existing: set[str]) -> float:
    """Score a keyword candidate. Higher = better."""
    score = 0.0
    # Novelty: not already in our keyword list
    if kw.lower() not in existing:
        score += 1.0
    # Specificity: longer phrases are more targeted
    word_count = len(kw.split())
    score += min(word_count / 5.0, 0.6)
    # Temporal signals: year or "2025"/"2026" = fresh content
    if any(yr in kw for yr in ["2025", "2026", "2027"]):
        score += 0.3
    # Tutorial/explained = good for CC-BY
    if any(w in kw.lower() for w in ["tutorial", "explained", "how to", "guide", "course"]):
        score += 0.2
    # Penalize very short/generic
    if word_count < 2:
        score -= 0.5
    return score


def fetch_trending_keywords(
    config,
    niche_name: str = "tech",
    top_n: int = 15,
) -> tuple[list[str], list[str]]:
    """
    Returns (suggested_search_keywords, suggested_cc_keywords).

    Seeds are taken from the current config (which may already include analytics overrides).
    """
    niche = config.niches.get(niche_name)
    if not niche:
        log.warning("[trending] Niche '%s' not found in config", niche_name)
        return [], []

    existing = {kw.lower() for kw in niche.search_keywords + niche.cc_search_keywords}
    all_candidates: list[tuple[str, float]] = []
    seen: set[str] = set()

    seeds = niche.search_keywords[:5]  # limit seeds to avoid rate-limiting
    for seed in seeds:
        suggestions = _fetch_suggestions(seed)
        for s in suggestions:
            s_clean = s.strip()
            if s_clean.lower() in seen:
                continue
            seen.add(s_clean.lower())
            score = _score_keyword(s_clean, existing)
            all_candidates.append((s_clean, score))
        time.sleep(0.3)  # be polite to the autocomplete endpoint

    all_candidates.sort(key=lambda x: x[1], reverse=True)

    # Split into general vs CC-BY candidates
    cc_signals = {"tutorial", "explained", "how to", "guide", "course", "lecture", "open source"}
    general: list[str] = []
    cc: list[str] = []

    for kw, _ in all_candidates:
        if any(sig in kw.lower() for sig in cc_signals):
            cc.append(kw)
        else:
            general.append(kw)

    suggested_search = general[:8]
    suggested_cc = cc[:5]

    log.info(
        "[trending] Found %d candidates → %d search, %d CC suggestions",
        len(all_candidates), len(suggested_search), len(suggested_cc),
    )
    return suggested_search, suggested_cc
