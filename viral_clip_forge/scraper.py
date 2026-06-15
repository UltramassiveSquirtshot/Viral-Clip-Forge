from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import NicheConfig
from .utils import get_logger, parse_iso_duration, retry

log = get_logger()

_DAILY_QUOTA_LIMIT = 9_500
_SEARCH_COST = 100
_LIST_COST = 1


@dataclass
class VideoCandidate:
    video_id: str
    title: str
    channel_id: str
    channel_title: str
    published_at: str
    view_count: int
    like_count: int
    comment_count: int
    duration_seconds: int
    category_id: str
    tags: list[str]
    api_license: str
    thumbnail_url: str
    source: str
    niche: str
    default_language: str = ""
    default_audio_language: str = ""


class QuotaExhaustedError(Exception):
    pass


def _build_service(api_key: str):
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def _parse_video_item(item: dict, niche: str, source: str) -> VideoCandidate | None:
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    details = item.get("contentDetails", {})
    status = item.get("status", {})

    video_id = item.get("id", "")
    if not video_id:
        return None

    thumbnails = snippet.get("thumbnails", {})
    thumb = (
        thumbnails.get("maxres", {}).get("url")
        or thumbnails.get("high", {}).get("url")
        or thumbnails.get("default", {}).get("url")
        or ""
    )

    return VideoCandidate(
        video_id=video_id,
        title=snippet.get("title", ""),
        channel_id=snippet.get("channelId", ""),
        channel_title=snippet.get("channelTitle", ""),
        published_at=snippet.get("publishedAt", ""),
        view_count=int(stats.get("viewCount", 0)),
        like_count=int(stats.get("likeCount", 0)),
        comment_count=int(stats.get("commentCount", 0)),
        duration_seconds=parse_iso_duration(details.get("duration", "")),
        category_id=snippet.get("categoryId", ""),
        tags=snippet.get("tags", []),
        api_license=status.get("license", ""),
        thumbnail_url=thumb,
        source=source,
        niche=niche,
        default_language=snippet.get("defaultLanguage", ""),
        default_audio_language=snippet.get("defaultAudioLanguage", ""),
    )


@retry(max_attempts=3, backoff_secs=2.0, exceptions=(Exception,))
def fetch_trending_by_category(
    api_key: str,
    niche: NicheConfig,
    today_units: int,
    run_units: list[int],
) -> list[VideoCandidate]:
    if today_units + sum(run_units) >= _DAILY_QUOTA_LIMIT:
        raise QuotaExhaustedError("Daily quota limit reached before trending fetch")

    service = _build_service(api_key)
    candidates: list[VideoCandidate] = []

    for category_id in niche.category_ids:
        try:
            resp = (
                service.videos()
                .list(
                    part="snippet,statistics,contentDetails,status",
                    chart="mostPopular",
                    videoCategoryId=category_id,
                    regionCode=niche.trending_region,
                    maxResults=50,
                )
                .execute()
            )
            run_units.append(_LIST_COST)
            log.info(f"[{niche.name}] Trending category {category_id}: {len(resp.get('items', []))} results")

            for item in resp.get("items", []):
                candidate = _parse_video_item(item, niche.name, "trending")
                if candidate:
                    candidates.append(candidate)

        except HttpError as exc:
            if "quotaExceeded" in str(exc):
                raise QuotaExhaustedError(f"Quota exceeded during trending fetch: {exc}")
            log.warning(f"[{niche.name}] HTTP error fetching trending cat {category_id}: {exc}")

    return candidates


@retry(max_attempts=3, backoff_secs=2.0, exceptions=(Exception,))
def fetch_search_fallback(
    api_key: str,
    niche: NicheConfig,
    today_units: int,
    run_units: list[int],
    max_keywords: int = 2,
) -> list[VideoCandidate]:
    remaining = _DAILY_QUOTA_LIMIT - today_units - sum(run_units)
    if remaining < _SEARCH_COST + 10:
        log.warning(f"[{niche.name}] Insufficient quota for search fallback ({remaining} units left)")
        return []

    service = _build_service(api_key)
    video_ids: list[str] = []

    from datetime import timedelta
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=7)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    for keyword in niche.search_keywords[:max_keywords]:
        remaining = _DAILY_QUOTA_LIMIT - today_units - sum(run_units)
        if remaining < _SEARCH_COST:
            break
        try:
            resp = (
                service.search()
                .list(
                    part="id",
                    q=keyword,
                    type="video",
                    order="viewCount",
                    publishedAfter=published_after,
                    maxResults=25,
                    regionCode=niche.trending_region,
                    relevanceLanguage="en",
                )
                .execute()
            )
            run_units.append(_SEARCH_COST)
            ids = [item["id"]["videoId"] for item in resp.get("items", []) if item.get("id", {}).get("videoId")]
            video_ids.extend(ids)
            log.info(f"[{niche.name}] Search '{keyword}': {len(ids)} video IDs")
        except HttpError as exc:
            if "quotaExceeded" in str(exc):
                raise QuotaExhaustedError(f"Quota exceeded during search: {exc}")
            log.warning(f"[{niche.name}] Search error for '{keyword}': {exc}")

    if not video_ids:
        return []

    return fetch_video_details(api_key, list(dict.fromkeys(video_ids)), niche.name, run_units, source="search")


@retry(max_attempts=3, backoff_secs=2.0, exceptions=(Exception,))
def fetch_video_details(
    api_key: str,
    video_ids: list[str],
    niche_name: str,
    run_units: list[int],
    source: str = "details",
) -> list[VideoCandidate]:
    service = _build_service(api_key)
    candidates: list[VideoCandidate] = []

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        try:
            resp = (
                service.videos()
                .list(
                    part="snippet,statistics,contentDetails,status",
                    id=",".join(batch),
                )
                .execute()
            )
            run_units.append(_LIST_COST)
            for item in resp.get("items", []):
                candidate = _parse_video_item(item, niche_name, source)
                if candidate:
                    candidates.append(candidate)
        except HttpError as exc:
            if "quotaExceeded" in str(exc):
                raise QuotaExhaustedError(f"Quota exceeded during details fetch: {exc}")
            log.warning(f"Details fetch error for batch: {exc}")

    return candidates


@retry(max_attempts=3, backoff_secs=2.0, exceptions=(Exception,))
def fetch_cc_videos_by_topic(
    api_key: str,
    niche: NicheConfig,
    today_units: int,
    run_units: list[int],
    max_keywords: int = 3,
) -> list[VideoCandidate]:
    """Search for CC-licensed videos using videoLicense=creativeCommon."""
    remaining = _DAILY_QUOTA_LIMIT - today_units - sum(run_units)
    if remaining < _SEARCH_COST + 10:
        log.warning(f"[{niche.name}] Insufficient quota for CC search ({remaining} units left)")
        return []

    service = _build_service(api_key)
    video_ids: list[str] = []

    from datetime import timedelta
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=30)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    keywords = niche.cc_search_keywords[:max_keywords] if niche.cc_search_keywords else niche.search_keywords[:max_keywords]

    for keyword in keywords:
        remaining = _DAILY_QUOTA_LIMIT - today_units - sum(run_units)
        if remaining < _SEARCH_COST:
            log.warning(f"[{niche.name}] Stopping CC search — quota too low ({remaining} units)")
            break
        try:
            resp = (
                service.search()
                .list(
                    part="id",
                    q=keyword,
                    type="video",
                    order="viewCount",
                    videoLicense="creativeCommon",
                    publishedAfter=published_after,
                    maxResults=25,
                    regionCode=niche.trending_region,
                    relevanceLanguage="en",
                )
                .execute()
            )
            run_units.append(_SEARCH_COST)
            ids = [
                item["id"]["videoId"]
                for item in resp.get("items", [])
                if item.get("id", {}).get("videoId")
            ]
            video_ids.extend(ids)
            log.info(f"[{niche.name}] CC search '{keyword}': {len(ids)} video IDs")
        except HttpError as exc:
            if "quotaExceeded" in str(exc):
                raise QuotaExhaustedError(f"Quota exceeded during CC search: {exc}")
            log.warning(f"[{niche.name}] CC search error for '{keyword}': {exc}")

    if not video_ids:
        return []

    deduped = list(dict.fromkeys(video_ids))
    log.info(f"[{niche.name}] CC search total unique IDs: {len(deduped)}")
    return fetch_video_details(api_key, deduped, niche.name, run_units, source="cc_search")


def fetch_with_scrapetube_fallback(
    niche: NicheConfig,
    limit: int = 20,
) -> list[str]:
    """Return video IDs from scrapetube search (no API quota used)."""
    try:
        import scrapetube
        video_ids: list[str] = []
        keyword = niche.search_keywords[0]
        results = scrapetube.get_search(keyword, limit=limit)
        for video in results:
            vid = video.get("videoId") or (video.get("videoRenderer", {}).get("videoId"))
            if vid:
                video_ids.append(vid)
        log.info(f"[{niche.name}] scrapetube fallback returned {len(video_ids)} IDs")
        return video_ids
    except Exception as exc:
        log.warning(f"[{niche.name}] scrapetube fallback failed: {exc}")
        return []


def scrape_niche(
    api_key: str,
    niche: NicheConfig,
    today_units: int,
    run_units: list[int],
) -> list[VideoCandidate]:
    """Primary: CC-licensed search. Fallback to trending only if CC yields < 5 results."""
    candidates: list[VideoCandidate] = []

    # PRIMARY: CC-BY targeted search
    try:
        candidates = fetch_cc_videos_by_topic(api_key, niche, today_units, run_units, max_keywords=5)
        log.info(f"[{niche.name}] CC search returned {len(candidates)} candidates")
    except QuotaExhaustedError as exc:
        log.warning(f"[{niche.name}] Quota exhausted in CC search: {exc}")
    except Exception as exc:
        log.error(f"[{niche.name}] CC search failed: {exc}")

    # FALLBACK: trending if CC yields too few results
    if len(candidates) < 5:
        log.info(
            f"[{niche.name}] CC search yielded only {len(candidates)} results — "
            f"supplementing with trending (note: trending videos will likely fail license check)"
        )
        try:
            trending = fetch_trending_by_category(api_key, niche, today_units, run_units)
            candidates.extend(trending)
            log.info(f"[{niche.name}] Added {len(trending)} trending candidates")
        except QuotaExhaustedError as exc:
            log.warning(f"[{niche.name}] Quota exhausted in trending fallback: {exc}")
            ids = fetch_with_scrapetube_fallback(niche)
            if ids:
                try:
                    sc = fetch_video_details(api_key, ids, niche.name, run_units, source="scrapetube")
                    candidates.extend(sc)
                except Exception as exc2:
                    log.error(f"[{niche.name}] scrapetube details fetch failed: {exc2}")
        except Exception as exc:
            log.error(f"[{niche.name}] Trending fallback failed: {exc}")

    log.info(f"[{niche.name}] Total candidates collected: {len(candidates)}")
    return candidates
