"""
Correlates ClipAnalytics with production metadata to produce a CorrelationReport.

Groups clips by: clip reason, upload slot, source keyword, duration bucket.
Computes correlation between production composite_score and actual retention_rate.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .fetcher import ClipAnalytics

log = logging.getLogger(__name__)

MIN_SAMPLE = 3  # minimum clips per group to report stats


@dataclass
class GroupStats:
    label: str
    count: int
    avg_retention: float
    avg_ctr: float
    avg_views: float
    best_clip_id: str


@dataclass
class CorrelationReport:
    total_clips: int
    clips_with_data: int          # clips that have analytics (published + have impressions)
    clips_low_sample: int         # clips with <200 impressions (too early to judge)

    # Performance by clip production reason
    by_reason: list[GroupStats]

    # Performance by upload slot (day + time)
    by_slot: list[GroupStats]

    # Performance by source keyword
    by_keyword: list[GroupStats]

    # Duration analysis
    avg_duration_top_clips: float      # avg duration of top-25% by retention
    suggested_preferred_duration: int  # rounded to nearest 5s

    # Score correlation
    score_retention_correlation: float  # Pearson r between composite_score and retention_rate
    score_predicts_performance: bool    # True if |r| > 0.4

    # Keyword intelligence (populated by trending_topics)
    keyword_performance: dict[str, float] = field(default_factory=dict)
    trending_candidates: list[str] = field(default_factory=list)
    suggested_search_keywords: list[str] = field(default_factory=list)
    suggested_cc_keywords: list[str] = field(default_factory=list)

    # Raw data for JSON export
    raw_clips: list[dict] = field(default_factory=list)


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = _avg(xs), _avg(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(
        sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)
    )
    return num / den if den > 0 else 0.0


def _group_stats(clips: list["ClipAnalytics"], key_fn, label_fn) -> list[GroupStats]:
    groups: dict[str, list["ClipAnalytics"]] = {}
    for c in clips:
        k = key_fn(c)
        groups.setdefault(k, []).append(c)

    result = []
    for k, group in sorted(groups.items()):
        if len(group) < MIN_SAMPLE:
            continue
        valid = [c for c in group if c.impressions >= 200]
        if not valid:
            valid = group  # use all if none have enough impressions yet
        best = max(valid, key=lambda c: c.retention_rate)
        result.append(GroupStats(
            label=label_fn(k),
            count=len(group),
            avg_retention=round(_avg([c.retention_rate for c in valid]), 4),
            avg_ctr=round(_avg([c.ctr for c in valid]), 4),
            avg_views=round(_avg([c.views for c in valid]), 1),
            best_clip_id=best.youtube_video_id,
        ))
    return sorted(result, key=lambda g: g.avg_retention, reverse=True)


def build_correlation_report(clips: list["ClipAnalytics"]) -> CorrelationReport:
    if not clips:
        return CorrelationReport(
            total_clips=0, clips_with_data=0, clips_low_sample=0,
            by_reason=[], by_slot=[], by_keyword=[],
            avg_duration_top_clips=45.0, suggested_preferred_duration=45,
            score_retention_correlation=0.0, score_predicts_performance=False,
        )

    with_data = [c for c in clips if c.views > 0]
    low_sample = [c for c in clips if 0 < c.impressions < 200]

    # Score vs retention correlation
    scoreable = [c for c in with_data if c.combined_score > 0]
    corr = _pearson(
        [c.combined_score for c in scoreable],
        [c.retention_rate for c in scoreable],
    )

    # Duration: top 25% by retention
    sorted_by_ret = sorted(with_data, key=lambda c: c.retention_rate, reverse=True)
    top_n = max(1, len(sorted_by_ret) // 4)
    top_clips = sorted_by_ret[:top_n]
    avg_top_dur = _avg([c.clip_duration_sec for c in top_clips]) if top_clips else 45.0
    suggested_dur = max(30, min(90, round(avg_top_dur / 5) * 5))

    # Keyword performance: avg retention per source keyword
    kw_perf: dict[str, list[float]] = {}
    for c in with_data:
        if c.source_keyword:
            kw_perf.setdefault(c.source_keyword, []).append(c.retention_rate)
    kw_perf_avg = {k: round(_avg(v), 4) for k, v in kw_perf.items()}

    # Raw clips for JSON export
    raw = []
    for c in clips:
        raw.append({
            "youtube_video_id": c.youtube_video_id,
            "clip_id": c.clip_id,
            "clip_duration_sec": c.clip_duration_sec,
            "views": c.views,
            "impressions": c.impressions,
            "retention_rate": c.retention_rate,
            "avg_view_duration_sec": c.avg_view_duration_sec,
            "ctr": c.ctr,
            "likes": c.likes,
            "days_since_publish": c.days_since_publish,
            "clip_reason": c.clip_reason,
            "combined_score": c.combined_score,
            "source_keyword": c.source_keyword,
            "slot_day": c.slot_day,
            "slot_time": c.slot_time,
        })

    return CorrelationReport(
        total_clips=len(clips),
        clips_with_data=len(with_data),
        clips_low_sample=len(low_sample),
        by_reason=_group_stats(
            with_data,
            key_fn=lambda c: c.clip_reason or "unknown",
            label_fn=lambda k: k,
        ),
        by_slot=_group_stats(
            with_data,
            key_fn=lambda c: f"{c.slot_day}_{c.slot_time}",
            label_fn=lambda k: k.replace("_", " "),
        ),
        by_keyword=_group_stats(
            with_data,
            key_fn=lambda c: c.source_keyword or "unknown",
            label_fn=lambda k: k,
        ),
        avg_duration_top_clips=round(avg_top_dur, 1),
        suggested_preferred_duration=suggested_dur,
        score_retention_correlation=round(corr, 4),
        score_predicts_performance=abs(corr) > 0.4,
        keyword_performance=kw_perf_avg,
        raw_clips=raw,
    )
