"""
Strategy comparison: CC-BY search vs Trending (Fair Use).
Queries YouTube API and prints side-by-side stats.
Cost: ~610 API units.
"""
from __future__ import annotations

import os
import sys
import math
import io

# Force UTF-8 output on Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import pip_system_certs.wrapt_requests  # noqa: F401
except ImportError:
    pass

from dotenv import load_dotenv
load_dotenv()

from googleapiclient.discovery import build

API_KEY = os.getenv("YOUTUBE_API_KEY", "")
if not API_KEY:
    print("ERROR: YOUTUBE_API_KEY not set in .env")
    sys.exit(1)

NICHES = {
    "tech": {
        "category_id": "28",
        "cc_keywords": [
            "open source tutorial",
            "linux explained",
            "python programming tutorial",
        ],
        "fair_use_keywords": [
            "AI breakthrough 2025",
            "tech news",
            "new gadget review",
        ],
    },
}

REGION = "US"
units_used = [0]


def build_service():
    return build("youtube", "v3", developerKey=API_KEY, cache_discovery=False)


def fetch_video_details(service, video_ids: list[str]) -> list[dict]:
    results = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        resp = service.videos().list(
            part="snippet,statistics,contentDetails,status",
            id=",".join(batch),
        ).execute()
        units_used[0] += 1
        results.extend(resp.get("items", []))
    return results


def parse_item(item: dict) -> dict:
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    status = item.get("status", {})
    details = item.get("contentDetails", {})

    views = int(stats.get("viewCount", 0))
    likes = int(stats.get("likeCount", 0))
    comments = int(stats.get("commentCount", 0))
    engagement = (likes + comments) / max(views, 1) * 100

    published = snippet.get("publishedAt", "")
    try:
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        days_old = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except Exception:
        days_old = 30.0

    view_score = min(1.0, math.log10(max(views, 1)) / math.log10(10_000_000))
    engagement_score = min(1.0, engagement)
    recency = math.exp(-days_old / 7.0)
    composite = 0.5 * view_score + 0.35 * engagement_score + 0.15 * recency

    return {
        "video_id": item.get("id", ""),
        "title": snippet.get("title", "")[:70],
        "channel": snippet.get("channelTitle", ""),
        "views": views,
        "likes": likes,
        "comments": comments,
        "engagement_pct": round(engagement, 3),
        "days_old": round(days_old, 1),
        "composite": round(composite, 4),
        "license": status.get("license", "unknown"),
        "duration": details.get("duration", ""),
    }


def search_cc(service, keyword: str, niche: str) -> list[str]:
    published_after = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = service.search().list(
        part="id",
        q=keyword,
        type="video",
        order="viewCount",
        videoLicense="creativeCommon",
        publishedAfter=published_after,
        maxResults=25,
        regionCode=REGION,
    ).execute()
    units_used[0] += 100
    ids = [i["id"]["videoId"] for i in resp.get("items", []) if i.get("id", {}).get("videoId")]
    print(f"  CC search '{keyword}' [{niche}]: {len(ids)} results")
    return ids


def search_fair_use(service, keyword: str, niche: str) -> list[str]:
    published_after = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = service.search().list(
        part="id",
        q=keyword,
        type="video",
        order="viewCount",
        publishedAfter=published_after,
        maxResults=25,
        regionCode=REGION,
    ).execute()
    units_used[0] += 100
    ids = [i["id"]["videoId"] for i in resp.get("items", []) if i.get("id", {}).get("videoId")]
    print(f"  Fair-use search '{keyword}' [{niche}]: {len(ids)} results")
    return ids


def fetch_trending(service, category_id: str, niche: str) -> list[dict]:
    resp = service.videos().list(
        part="snippet,statistics,contentDetails,status",
        chart="mostPopular",
        videoCategoryId=category_id,
        regionCode=REGION,
        maxResults=50,
    ).execute()
    units_used[0] += 1
    items = resp.get("items", [])
    print(f"  Trending category {category_id} [{niche}]: {len(items)} results")
    return items


def print_table(videos: list[dict], label: str):
    print(f"\n{'='*80}")
    print(f"  {label}  ({len(videos)} videos)")
    print(f"{'='*80}")
    if not videos:
        print("  (no results)")
        return

    print(f"  {'Title':<45} {'Views':>10}  {'Eng%':>5}  {'Age(d)':>6}  {'Score':>6}  License")
    print(f"  {'-'*45} {'-'*10}  {'-'*5}  {'-'*6}  {'-'*6}  {'-'*15}")
    for v in sorted(videos, key=lambda x: x["composite"], reverse=True)[:15]:
        title = (v["title"][:42] + "...") if len(v["title"]) > 45 else v["title"]
        print(
            f"  {title:<45} {v['views']:>10,}  {v['engagement_pct']:>5.2f}  "
            f"{v['days_old']:>6.1f}  {v['composite']:>6.4f}  {v['license']}"
        )


def print_summary(cc_videos: list[dict], fu_videos: list[dict]):
    def avg(lst, key):
        vals = [v[key] for v in lst if v[key] > 0]
        return sum(vals) / len(vals) if vals else 0

    print(f"\n{'='*80}")
    print("  SUMMARY COMPARISON")
    print(f"{'='*80}")
    print(f"  {'Metric':<30} {'CC-BY':>15}  {'Fair-Use/Trending':>18}")
    print(f"  {'-'*30} {'-'*15}  {'-'*18}")

    metrics = [
        ("Videos found", len(cc_videos), len(fu_videos)),
        ("Avg views", f"{avg(cc_videos, 'views'):,.0f}", f"{avg(fu_videos, 'views'):,.0f}"),
        ("Avg engagement %", f"{avg(cc_videos, 'engagement_pct'):.3f}%", f"{avg(fu_videos, 'engagement_pct'):.3f}%"),
        ("Avg age (days)", f"{avg(cc_videos, 'days_old'):.1f}", f"{avg(fu_videos, 'days_old'):.1f}"),
        ("Avg composite score", f"{avg(cc_videos, 'composite'):.4f}", f"{avg(fu_videos, 'composite'):.4f}"),
        ("Max views", f"{max((v['views'] for v in cc_videos), default=0):,}", f"{max((v['views'] for v in fu_videos), default=0):,}"),
        ("CC-BY licensed", f"{sum(1 for v in cc_videos if v['license'] == 'creativeCommon')}/{len(cc_videos)}", f"{sum(1 for v in fu_videos if v['license'] == 'creativeCommon')}/{len(fu_videos)}"),
    ]

    for label, cc_val, fu_val in metrics:
        print(f"  {label:<30} {str(cc_val):>15}  {str(fu_val):>18}")

    print(f"\n  API units used this run: {units_used[0]}")


def main():
    print("Viral Clip Forge — Strategy Comparison")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Querying YouTube API...\n")

    service = build_service()
    cc_video_ids: list[str] = []
    fu_video_ids: list[str] = []
    fu_items: list[dict] = []

    for niche_name, cfg in NICHES.items():
        print(f"[{niche_name.upper()}] CC-BY search:")
        for kw in cfg["cc_keywords"]:
            cc_video_ids.extend(search_cc(service, kw, niche_name))

        print(f"[{niche_name.upper()}] Fair-use (trending + search):")
        trending_items = fetch_trending(service, cfg["category_id"], niche_name)
        fu_items.extend(trending_items)
        for kw in cfg["fair_use_keywords"]:
            fu_video_ids.extend(search_fair_use(service, kw, niche_name))

    print(f"\nFetching details...")

    cc_video_ids = list(dict.fromkeys(cc_video_ids))
    fu_video_ids = list(dict.fromkeys(fu_video_ids))

    cc_items = fetch_video_details(service, cc_video_ids)
    fu_detail_items = fetch_video_details(service, fu_video_ids)

    # Merge trending items (already have full details) with searched fair-use
    all_fu_ids_seen = set()
    fu_all_items = []
    for item in fu_items + fu_detail_items:
        vid_id = item.get("id", "")
        if vid_id and vid_id not in all_fu_ids_seen:
            all_fu_ids_seen.add(vid_id)
            fu_all_items.append(item)

    cc_parsed = [parse_item(i) for i in cc_items]
    fu_parsed = [parse_item(i) for i in fu_all_items]

    print_table(cc_parsed, "CC-BY STRATEGY — Creative Commons Licensed Videos")
    print_table(fu_parsed, "FAIR-USE STRATEGY — Trending + Popular Videos (Standard License)")
    print_summary(cc_parsed, fu_parsed)


if __name__ == "__main__":
    main()
