from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .scraper import VideoCandidate
from .utils import get_logger

log = get_logger()


@dataclass
class ScoredCandidate:
    candidate: VideoCandidate
    view_score: float
    engagement_ratio: float
    recency_bonus: float
    composite: float


@dataclass
class FilterResult:
    selected: list[VideoCandidate]
    scored_all: list[ScoredCandidate]
    count_before_dedup: int
    count_after_dedup: int
    count_after_threshold: int


def deduplicate(
    candidates: list[VideoCandidate],
    seen_ids: set[str],
) -> list[VideoCandidate]:
    seen_local: set[str] = set()
    result: list[VideoCandidate] = []
    for c in candidates:
        if c.video_id in seen_ids:
            continue
        if c.video_id in seen_local:
            continue
        seen_local.add(c.video_id)
        result.append(c)
    return result


def filter_by_thresholds(
    candidates: list[VideoCandidate],
    min_views: int,
    max_duration_seconds: int,
) -> list[VideoCandidate]:
    result = []
    for c in candidates:
        if c.view_count < min_views:
            continue
        if c.duration_seconds == 0:
            continue
        if c.duration_seconds < 60:
            continue
        if c.duration_seconds > max_duration_seconds:
            continue
        result.append(c)
    return result


def filter_by_language(candidates: list[VideoCandidate]) -> list[VideoCandidate]:
    """Keep only English videos. If both language fields are absent, allow through."""
    result = []
    rejected = 0
    for c in candidates:
        lang = c.default_audio_language or c.default_language
        if lang and not lang.lower().startswith("en"):
            log.info(f"[lang-filter] Rejected {c.video_id} '{c.title[:50]}' lang={lang}")
            rejected += 1
            continue
        result.append(c)
    if rejected:
        log.info(f"[lang-filter] Rejected {rejected} non-English candidates")
    return result


def _days_since(published_at: str) -> float:
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return max(0.0, delta.total_seconds() / 86400)
    except Exception:
        return 7.0


def compute_engagement_score(candidate: VideoCandidate) -> ScoredCandidate:
    view_count = max(candidate.view_count, 1)
    view_score = math.log10(view_count) / math.log10(10_000_000)
    view_score = min(1.0, max(0.0, view_score))

    engagement = (candidate.like_count + candidate.comment_count) / view_count
    engagement_ratio = min(1.0, engagement * 100)

    days_old = _days_since(candidate.published_at)
    recency_bonus = math.exp(-days_old / 7.0)

    composite = (
        0.5 * view_score
        + 0.35 * engagement_ratio
        + 0.15 * recency_bonus
    )

    return ScoredCandidate(
        candidate=candidate,
        view_score=view_score,
        engagement_ratio=engagement,
        recency_bonus=recency_bonus,
        composite=composite,
    )


def _log_scored_candidates(scored: list[ScoredCandidate], top_n: int) -> None:
    log.info(f"  Ranking {len(scored)} candidates after thresholds:")
    for rank, s in enumerate(scored, 1):
        marker = "SEL" if rank <= top_n else "   "
        log.info(
            f"  [{marker}] #{rank:2d} src={s.candidate.source:10s} "
            f"id={s.candidate.video_id} "
            f"'{s.candidate.title[:50]}' "
            f"views={s.candidate.view_count:,} "
            f"score={s.composite:.4f} "
            f"(V={s.view_score:.3f} E={s.engagement_ratio:.4f} R={s.recency_bonus:.3f})"
        )


def run_filter_pipeline(
    candidates: list[VideoCandidate],
    seen_ids: set[str],
    min_views: int,
    max_duration_seconds: int,
    top_n: int = 3,
) -> FilterResult:
    count_before = len(candidates)
    candidates = deduplicate(candidates, seen_ids)
    count_after_dedup = len(candidates)
    candidates = filter_by_language(candidates)
    count_after_language = len(candidates)
    candidates = filter_by_thresholds(candidates, min_views, max_duration_seconds)
    count_after_threshold = len(candidates)
    log.info(
        f"Filter: {count_before} -> {count_after_dedup} (dedup) -> "
        f"{count_after_language} (language) -> {count_after_threshold} (thresholds)"
    )
    scored_all = [compute_engagement_score(c) for c in candidates]
    scored_all.sort(key=lambda s: s.composite, reverse=True)
    _log_scored_candidates(scored_all, top_n)
    return FilterResult(
        selected=[s.candidate for s in scored_all[:top_n]],
        scored_all=scored_all,
        count_before_dedup=count_before,
        count_after_dedup=count_after_dedup,
        count_after_threshold=count_after_threshold,
    )
