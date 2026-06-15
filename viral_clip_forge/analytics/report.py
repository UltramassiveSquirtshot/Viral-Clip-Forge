"""
Formats CorrelationReport into:
  - YYYY-MM-DD_analytics.md  (human-readable, sections keyed to docs/analytics/)
  - YYYY-MM-DD_context.json  (machine-readable dump for Claude Code sessions)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .correlator import CorrelationReport

log = logging.getLogger(__name__)


def _pct(f: float) -> str:
    return f"{f * 100:.1f}%"


def _bar(f: float, width: int = 20) -> str:
    filled = round(f * width)
    return "█" * filled + "░" * (width - filled)


def _group_table(groups, title: str) -> str:
    if not groups:
        return f"### {title}\n_Not enough data (need ≥3 clips per group)_\n"
    lines = [f"### {title}", ""]
    lines.append(f"{'Group':<30} {'Clips':>5} {'Avg Retention':>14} {'Avg CTR':>8} {'Avg Views':>10}")
    lines.append("-" * 72)
    for g in groups:
        lines.append(
            f"{g.label:<30} {g.count:>5} {_pct(g.avg_retention):>14} "
            f"{_pct(g.avg_ctr):>8} {g.avg_views:>10,.0f}"
        )
    lines.append("")
    return "\n".join(lines)


def build_markdown_report(report: "CorrelationReport", generated_at: str) -> str:
    lines: list[str] = []

    lines += [
        f"# Viral Clip Forge — Analytics Report",
        f"**Generated:** {generated_at}",
        f"**Period:** last 14 days",
        f"",
        f"---",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total clips uploaded | {report.total_clips} |",
        f"| Clips with analytics data | {report.clips_with_data} |",
        f"| Clips still in early phase (<200 impressions) | {report.clips_low_sample} |",
        f"| Score→retention correlation | {report.score_retention_correlation:+.2f} |",
        f"| Our scoring predicts performance? | {'✅ Yes' if report.score_predicts_performance else '❌ No (r < 0.4)'} |",
        f"",
    ]

    # Clip reason performance — see docs/analytics/clip_scoring.md
    lines += [
        "---",
        "",
        "## Clip Selection Method Performance",
        "_Reference: `docs/analytics/clip_scoring.md` for weight adjustment rules_",
        "",
    ]
    lines.append(_group_table(report.by_reason, "Retention by Clip Reason"))

    if report.by_reason:
        best = report.by_reason[0]
        worst = report.by_reason[-1]
        lines += [
            f"**Best:** `{best.label}` at {_pct(best.avg_retention)} avg retention",
            f"**Worst:** `{worst.label}` at {_pct(worst.avg_retention)} avg retention",
            "",
        ]
        if len(report.by_reason) >= 2:
            diff = best.avg_retention - worst.avg_retention
            if diff > 0.15:
                lines.append(
                    f"> ⚠️ **{_pct(diff)} gap** between best and worst reason — "
                    f"consider tuning thresholds to favor `{best.label}` clips."
                )
            else:
                lines.append("> ✅ Clip selection methods are performing similarly.")
        lines.append("")

    # Duration analysis
    lines += [
        "---",
        "",
        "## Duration Analysis",
        "",
        f"- Average duration of top-25% clips by retention: **{report.avg_duration_top_clips:.0f}s**",
        f"- Current `preferred_clip_duration`: see `analytics_insights.json` or config default",
        f"- **Suggested `preferred_clip_duration`:** `{report.suggested_preferred_duration}s`",
        "",
    ]

    # Slot performance — see docs/analytics/strategies.md
    lines += [
        "---",
        "",
        "## Upload Slot Performance",
        "_Reference: `docs/analytics/strategies.md` for slot optimization rules_",
        "",
    ]
    lines.append(_group_table(report.by_slot, "Retention by Day + Time Slot"))

    # Keyword performance — see docs/analytics/content_signals.md
    lines += [
        "---",
        "",
        "## Keyword Performance",
        "_Reference: `docs/analytics/content_signals.md` for keyword strategy_",
        "",
    ]
    lines.append(_group_table(report.by_keyword, "Retention by Source Keyword"))

    if report.keyword_performance:
        sorted_kw = sorted(report.keyword_performance.items(), key=lambda x: x[1], reverse=True)
        lines += ["**Historical keyword performance (avg retention):**", ""]
        for kw, ret in sorted_kw:
            bar = _bar(min(ret, 1.0))
            lines.append(f"- `{kw}`: {bar} {_pct(ret)}")
        lines.append("")

    # Trending suggestions
    lines += [
        "---",
        "",
        "## Keyword Suggestions for Next Run",
        "_Based on YouTube Search Suggestions + historical performance_",
        "",
    ]

    if report.suggested_search_keywords:
        lines += ["**Suggested `search_keywords` (replace current):**", ""]
        for kw in report.suggested_search_keywords:
            lines.append(f"- `{kw}`")
        lines.append("")
    else:
        lines += ["_No trending search keyword suggestions available._", ""]

    if report.suggested_cc_keywords:
        lines += ["**Suggested `cc_search_keywords` (replace current):**", ""]
        for kw in report.suggested_cc_keywords:
            lines.append(f"- `{kw}`")
        lines.append("")
    else:
        lines += ["_No trending CC keyword suggestions available._", ""]

    # Config recommendations
    lines += [
        "---",
        "",
        "## Recommended analytics_insights.json",
        "_Paste this into Claude Code to apply after your review._",
        "_Reference: `docs/analytics/data_interpretation.md` for metric thresholds_",
        "",
        "```json",
        json.dumps({
            "generated_at": generated_at,
            "config_overrides": {
                "preferred_clip_duration": report.suggested_preferred_duration,
            },
            "keyword_overrides": {
                "tech": {
                    "search_keywords": report.suggested_search_keywords or [],
                    "cc_search_keywords": report.suggested_cc_keywords or [],
                }
            },
        }, indent=2),
        "```",
        "",
        "> ⚠️ Review the suggestions above before applying. The JSON block above is a starting",
        "> point — adjust based on the retention data and your own judgment.",
        "> Ask Claude Code to help reason about the data using `YYYY-MM-DD_context.json`.",
        "",
    ]

    return "\n".join(lines)


def build_context_json(report: "CorrelationReport", generated_at: str) -> dict:
    return {
        "generated_at": generated_at,
        "summary": {
            "total_clips": report.total_clips,
            "clips_with_data": report.clips_with_data,
            "clips_low_sample": report.clips_low_sample,
            "score_retention_correlation": report.score_retention_correlation,
            "score_predicts_performance": report.score_predicts_performance,
            "suggested_preferred_duration": report.suggested_preferred_duration,
        },
        "by_reason": [
            {
                "label": g.label,
                "count": g.count,
                "avg_retention": g.avg_retention,
                "avg_ctr": g.avg_ctr,
                "avg_views": g.avg_views,
            }
            for g in report.by_reason
        ],
        "by_slot": [
            {
                "label": g.label,
                "count": g.count,
                "avg_retention": g.avg_retention,
                "avg_ctr": g.avg_ctr,
                "avg_views": g.avg_views,
            }
            for g in report.by_slot
        ],
        "by_keyword": [
            {
                "label": g.label,
                "count": g.count,
                "avg_retention": g.avg_retention,
                "avg_ctr": g.avg_ctr,
                "avg_views": g.avg_views,
            }
            for g in report.by_keyword
        ],
        "keyword_performance": report.keyword_performance,
        "trending_candidates": report.trending_candidates,
        "suggested_search_keywords": report.suggested_search_keywords,
        "suggested_cc_keywords": report.suggested_cc_keywords,
        "raw_clips": report.raw_clips,
    }


def write_reports(report: "CorrelationReport", reports_dir: Path) -> tuple[Path, Path]:
    """Write .md and .json reports. Returns (md_path, json_path)."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    generated_at = datetime.now(timezone.utc).isoformat()[:19]

    md_path = reports_dir / f"{today}_analytics.md"
    json_path = reports_dir / f"{today}_context.json"

    md_content = build_markdown_report(report, generated_at)
    md_path.write_text(md_content, encoding="utf-8")
    log.info("[report] Wrote %s", md_path)

    ctx = build_context_json(report, generated_at)
    json_path.write_text(json.dumps(ctx, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("[report] Wrote %s", json_path)

    return md_path, json_path
