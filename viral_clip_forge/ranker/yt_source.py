"""
YouTube CC-BY footage source for the ranker pipeline.

Replaces pexels_source.py. Searches YouTube for CC-licensed videos per rank
query, returns ordered candidates (by views). On /pick, downloads the chosen
video (cached locally) and cuts the best unused 6-second moment.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
import json
from pathlib import Path

from ..utils import get_logger
from ..config import AppConfig
from .config import RankerConfig
from . import pool as pool_mod

log = get_logger()

_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def search_candidates(api_key: str, query: str, top_n: int = 5, min_duration_sec: int = 180) -> list[dict]:
    """
    Search YouTube CC-BY videos for a query, sorted by view count descending.
    Returns list of {id, title, channel, views, duration_sec}.
    min_duration_sec: skip videos shorter than this (default 3 min — filters out clips, keeps compilations).
    """
    # Step 1: search IDs
    params = urllib.parse.urlencode({
        "part": "id",
        "q": query,
        "type": "video",
        "videoLicense": "creativeCommon",
        "videoDuration": "medium",  # 4–20 min — filters out shorts/clips
        "maxResults": 25,
        "key": api_key,
    })
    try:
        req = urllib.request.Request(f"{_SEARCH_URL}?{params}")
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as exc:
        log.warning("[yt_source] Search failed for '%s': %s", query, exc)
        return []

    ids = [item["id"]["videoId"] for item in data.get("items", []) if item.get("id", {}).get("videoId")]
    if not ids:
        return []

    # Step 2: get stats + duration
    params2 = urllib.parse.urlencode({
        "part": "snippet,statistics,contentDetails",
        "id": ",".join(ids),
        "key": api_key,
    })
    try:
        req2 = urllib.request.Request(f"{_VIDEOS_URL}?{params2}")
        with urllib.request.urlopen(req2, timeout=15) as r:
            details = json.loads(r.read())
    except Exception as exc:
        log.warning("[yt_source] Details fetch failed: %s", exc)
        return []

    from ..utils import parse_iso_duration
    candidates = []
    for item in details.get("items", []):
        vid = item.get("id", "")
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        cd = item.get("contentDetails", {})
        candidates.append({
            "id": vid,
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "views": int(stats.get("viewCount", 0)),
            "duration_sec": parse_iso_duration(cd.get("duration", "")),
        })

    # Filter out videos too short to yield 5 non-overlapping 6s clips
    candidates = [c for c in candidates if c["duration_sec"] >= min_duration_sec]
    candidates.sort(key=lambda x: x["views"], reverse=True)
    return candidates[:top_n]


def download_and_suggest_timestamps(
    cfg: RankerConfig,
    app_cfg: AppConfig,
    video_id: str,
    n: int = 5,
) -> tuple[Path | None, list[tuple[float, float]]]:
    """
    Download a CC-BY video (cached) and return the top-N suggested (start, end) segments.
    Uses subtitle-aware analysis when an .en.srt file is available alongside the video:
      - parses SRT for excitement signals (keyword density + reaction words)
      - detects natural clip end boundaries from subtitle pauses
      - blends subtitle score (25%) with FFmpeg audio/scene/speech signals (75%)
      - produces variable-duration clips (min_clip_seconds–max_clip_seconds) instead
        of the fixed 6s hardcoded previously
    Falls back to the original FFmpeg-only analysis if no SRT is present.
    Does NOT cut anything.
    Returns (src_path, [(start_sec, end_sec), ...]) or (None, []) on failure.
    """
    from ..downloader import download_video
    from ..clip_analyzer import (
        detect_scene_changes, detect_audio_loudness, detect_speech_intervals,
        find_best_clip_windows,
    )
    from .pool import ranges_overlap
    from .subtitle_analyzer import parse_srt, score_subtitle_windows, find_natural_end
    import subprocess as _sp

    min_dur = cfg.min_clip_seconds
    max_dur = cfg.max_clip_seconds
    preferred_dur = cfg.clip_seconds

    src_dir = cfg.download_dir / "sources"
    src_dir.mkdir(parents=True, exist_ok=True)

    existing = [p for p in src_dir.glob(f"{video_id}.*") if p.suffix.lower() not in (".srt", ".vtt")]
    if existing:
        src_path = existing[0]
        log.info("[yt_source] Using cached source: %s", src_path.name)
    else:
        log.info("[yt_source] Downloading %s", video_id)
        result = download_video(video_id, src_dir, app_cfg.ffmpeg_bin)
        if not result.success or not result.output_path:
            log.error("[yt_source] Download failed for %s: %s", video_id, result.error)
            return None, []
        src_path = result.output_path

    # --- Load subtitles if available ---
    srt_path = src_dir / f"{video_id}.en.srt"
    subtitle_entries = parse_srt(srt_path) if srt_path.exists() else []
    has_subtitles = bool(subtitle_entries)
    if has_subtitles:
        log.info("[yt_source] Subtitle analysis active (%d entries)", len(subtitle_entries))
    else:
        log.info("[yt_source] No SRT found — using FFmpeg-only analysis")

    try:
        scenes = detect_scene_changes(src_path, app_cfg.ffmpeg_bin, app_cfg.scene_threshold)
        audio  = detect_audio_loudness(src_path, app_cfg.ffmpeg_bin, app_cfg.audio_peak_percentile)
        speech = detect_speech_intervals(src_path, app_cfg.ffmpeg_bin)
    except Exception as exc:
        log.error("[yt_source] Analysis failed for %s: %s", video_id, exc)
        return src_path, []

    try:
        r = _sp.run(
            [str(app_cfg.ffprobe_bin), "-v", "quiet",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(src_path)],
            stdout=_sp.PIPE, stderr=_sp.PIPE, timeout=30,
        )
        video_duration = float(r.stdout.decode().strip() or "0")
    except Exception:
        video_duration = 0.0

    if video_duration < min_dur:
        log.warning("[yt_source] Video %s too short (%.1fs)", video_id, video_duration)
        return src_path, []

    # --- FFmpeg-based candidate windows (scores already computed inside) ---
    windows = find_best_clip_windows(
        scenes, audio, speech, video_duration,
        min_duration=int(min_dur),
        max_duration=int(max_dur),
        max_clips=80,
        preferred_duration=int(preferred_dur),
        dead_zone_start_pct=0.02,
        dead_zone_end_pct=0.02,
    )

    if has_subtitles:
        # Build a fast lookup: subtitle excitement score at each timestamp
        sub_windows = score_subtitle_windows(subtitle_entries, video_duration, min_dur, max_dur)
        # Index by start_sec for O(1) lookup (rounded to nearest 0.5s)
        sub_score_map: dict[int, float] = {}
        for sw_start, sw_end, sw_score in sub_windows:
            key = int(sw_start * 2)  # 0.5s resolution
            if sub_score_map.get(key, 0) < sw_score:
                sub_score_map[key] = sw_score

        def _sub_score_at(t: float) -> float:
            return sub_score_map.get(int(t * 2), 0.0)

        # Re-score windows: blend FFmpeg scores (75%) with subtitle excitement (25%)
        for w in windows:
            sub_s = _sub_score_at(w.start_sec)
            # New blended score — weights adjusted to give subtitle signal dominant pull
            blended = (
                0.22 * w.loudness_spike
                + 0.18 * w.scene_score
                + 0.12 * w.speech_coverage
                + 0.13 * w.motion_variance
                + 0.35 * sub_s
            )
            duration_bonus = 0.8 + 0.2 * w.duration_score
            w.combined_score = blended
            w.final_score = blended * duration_bonus

        # Also inject pure subtitle windows that may not have triggered FFmpeg signals
        from ..clip_analyzer import ClipCandidate
        existing_starts = {int(w.start_sec * 2) for w in windows}
        for sw_start, sw_end, sw_score in sub_windows:
            key = int(sw_start * 2)
            if key not in existing_starts and sw_score >= 0.3:
                natural_end = find_natural_end(
                    subtitle_entries, sw_start, min_dur, max_dur
                )
                natural_end = min(natural_end, video_duration)
                dur = natural_end - sw_start
                windows.append(ClipCandidate(
                    start_sec=sw_start,
                    end_sec=natural_end,
                    duration_sec=dur,
                    scene_score=0.0,
                    loudness_spike=0.0,
                    speech_coverage=1.0,
                    motion_variance=0.0,
                    duration_score=max(0.0, 1.0 - abs(dur - preferred_dur) / preferred_dur),
                    combined_score=sw_score,
                    final_score=sw_score,
                    reason="subtitle_excitement",
                ))
                existing_starts.add(key)

        # For each selected window, refine end to a natural subtitle boundary
        def _natural_end_for(start: float, ffmpeg_end: float) -> float:
            natural = find_natural_end(subtitle_entries, start, min_dur, max_dur)
            natural = min(natural, video_duration)
            # Only use natural end if it's meaningfully different from FFmpeg end
            if abs(natural - ffmpeg_end) > 0.5:
                return natural
            return ffmpeg_end

    else:
        def _natural_end_for(start: float, ffmpeg_end: float) -> float:
            return ffmpeg_end

    # --- Select top-N non-overlapping windows ---
    used: list[list[float]] = []
    selected: list[tuple[float, float]] = []
    for w in sorted(windows, key=lambda x: x.final_score, reverse=True):
        if len(selected) >= n:
            break
        raw_end = w.end_sec if w.end_sec > w.start_sec else w.start_sec + preferred_dur
        end = _natural_end_for(w.start_sec, raw_end)
        if not ranges_overlap(w.start_sec, end, used, min_gap=2.0):
            selected.append((w.start_sec, end))
            used.append([w.start_sec, end])
            log.debug(
                "[yt_source] Selected %.1fs–%.1fs (score=%.3f reason=%s)",
                w.start_sec, end, w.final_score, w.reason,
            )

    # --- Fallback: fill remaining slots with evenly spaced segments ---
    if len(selected) < n and video_duration >= min_dur:
        step = max(preferred_dur + 5.0, video_duration / (n + 1))
        t = step
        while t + min_dur <= video_duration and len(selected) < n:
            end = t + preferred_dur
            if not ranges_overlap(t, end, used, min_gap=2.0):
                selected.append((t, min(end, video_duration)))
                used.append([t, end])
            t += step

    selected.sort(key=lambda x: x[0])
    log.info("[yt_source] Suggested segments for %s: %s", video_id, selected)
    return src_path, selected


def cut_clips_at_timestamps(
    cfg: RankerConfig,
    app_cfg: AppConfig,
    src_path: Path,
    segments: list[tuple[float, float]],
    clip_dur: float | None = None,
) -> list[Path]:
    """
    Cut clips for each (start, end) segment.
    clip_dur is ignored when segments contains pairs; kept for signature compatibility.
    Returns list of output Paths (skips failures silently).
    """
    from ..clip_analyzer import ClipCandidate
    from ..clip_cutter import cut_single_clip

    default_dur = clip_dur if clip_dur is not None else cfg.clip_seconds

    cut_dir = cfg.download_dir / "rank_previews"
    cut_dir.mkdir(parents=True, exist_ok=True)

    clips: list[Path] = []
    video_id = src_path.stem
    for i, seg in enumerate(segments, start=1):
        if isinstance(seg, (list, tuple)) and len(seg) == 2:
            start, end = float(seg[0]), float(seg[1])
        else:
            start = float(seg)
            end = start + default_dur
        dur = end - start
        candidate = ClipCandidate(
            start_sec=start, end_sec=end, duration_sec=dur,
            scene_score=0.0, loudness_spike=0.0, speech_coverage=0.0,
            motion_variance=0.0, duration_score=1.0, combined_score=0.0,
            final_score=1.0, reason="manual_segment",
        )
        cut = cut_single_clip(
            source_path=src_path,
            output_dir=cut_dir,
            video_id=f"rank_{video_id}",
            clip_index=i,
            candidate=candidate,
            ffmpeg_bin=app_cfg.ffmpeg_bin,
            ffprobe_bin=app_cfg.ffprobe_bin,
            shorts_output=True,
        )
        if cut.success and cut.output_path:
            clips.append(cut.output_path)
            log.info("[yt_source] Cut clip %d: %.1fs–%.1fs → %s", i, start, end, cut.output_path.name)
        else:
            log.warning("[yt_source] Cut failed for %.1fs–%.1fs: %s", start, end, cut.error)

    return clips


def download_and_cut_5clips(
    cfg: RankerConfig,
    app_cfg: AppConfig,
    video_id: str,
    n_clips: int = 5,
) -> list[Path]:
    """
    Download a CC-BY video (cached) and cut the best N non-overlapping moments of
    clip_seconds each. Returns a list of Path (may be fewer than n_clips if the
    video is too short or analysis finds fewer windows).
    """
    from ..downloader import download_video
    from ..clip_analyzer import (
        detect_scene_changes, detect_audio_loudness, detect_speech_intervals,
        find_best_clip_windows,
    )
    from ..clip_cutter import cut_single_clip
    from .pool import ranges_overlap

    clip_dur = cfg.clip_seconds  # 6.0 s

    src_dir = cfg.download_dir / "sources"
    src_dir.mkdir(parents=True, exist_ok=True)

    # Reuse cached download
    existing = list(src_dir.glob(f"{video_id}.*"))
    if existing:
        src_path = existing[0]
        log.info("[yt_source] Using cached source: %s", src_path.name)
    else:
        log.info("[yt_source] Downloading %s", video_id)
        result = download_video(video_id, src_dir, app_cfg.ffmpeg_bin)
        if not result.success or not result.output_path:
            log.error("[yt_source] Download failed for %s: %s", video_id, result.error)
            return []
        src_path = result.output_path

    # Analyse
    try:
        scenes = detect_scene_changes(src_path, app_cfg.ffmpeg_bin, app_cfg.scene_threshold)
        audio = detect_audio_loudness(src_path, app_cfg.ffmpeg_bin, app_cfg.audio_peak_percentile)
        speech = detect_speech_intervals(src_path, app_cfg.ffmpeg_bin)
    except Exception as exc:
        log.error("[yt_source] Analysis failed for %s: %s", video_id, exc)
        return []

    import subprocess
    try:
        r = subprocess.run(
            [str(app_cfg.ffprobe_bin), "-v", "quiet",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(src_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        video_duration = float(r.stdout.decode().strip() or "0")
    except Exception:
        video_duration = 0.0

    if video_duration < clip_dur:
        log.warning("[yt_source] Video %s too short (%.1fs)", video_id, video_duration)
        return []

    # Find many candidate windows (we'll pick the top N non-overlapping ones)
    windows = find_best_clip_windows(
        scenes, audio, speech, video_duration,
        min_duration=int(clip_dur),
        max_duration=int(clip_dur * 2),
        max_clips=50,
        preferred_duration=int(clip_dur),
        dead_zone_start_pct=0.02,
        dead_zone_end_pct=0.02,
    )

    # Select top N non-overlapping windows
    used: list[list[float]] = []
    selected = []
    for w in sorted(windows, key=lambda x: x.final_score, reverse=True):
        if len(selected) >= n_clips:
            break
        if not ranges_overlap(w.start_sec, w.end_sec, used, min_gap=2.0):
            w.end_sec = w.start_sec + clip_dur
            w.duration_sec = clip_dur
            selected.append(w)
            used.append([w.start_sec, w.end_sec])

    # Fallback: fill remaining slots by linear scan
    if len(selected) < n_clips:
        t = 5.0
        step = clip_dur + 3.0
        while t + clip_dur <= video_duration and len(selected) < n_clips:
            if not ranges_overlap(t, t + clip_dur, used, min_gap=2.0):
                from ..clip_analyzer import ClipCandidate
                w = ClipCandidate(
                    start_sec=t, end_sec=t + clip_dur, duration_sec=clip_dur,
                    scene_score=0.0, loudness_spike=0.0, speech_coverage=0.0,
                    motion_variance=0.0, duration_score=1.0, combined_score=0.0,
                    final_score=0.0, reason="fallback_linear",
                )
                selected.append(w)
                used.append([t, t + clip_dur])
            t += step

    if not selected:
        log.warning("[yt_source] No windows found in %s", video_id)
        return []

    # Sort by start time so the composed video flows chronologically
    selected.sort(key=lambda x: x.start_sec)

    cut_dir = cfg.download_dir / "rank_previews"
    cut_dir.mkdir(parents=True, exist_ok=True)

    clips: list[Path] = []
    for i, w in enumerate(selected, start=1):
        cut = cut_single_clip(
            source_path=src_path,
            output_dir=cut_dir,
            video_id=f"rank_{video_id}",
            clip_index=i,
            candidate=w,
            ffmpeg_bin=app_cfg.ffmpeg_bin,
            ffprobe_bin=app_cfg.ffprobe_bin,
            shorts_output=True,
        )
        if cut.success and cut.output_path:
            clips.append(cut.output_path)
            log.info("[yt_source] Clip %d: %.1fs–%.1fs → %s", i, w.start_sec, w.end_sec, cut.output_path.name)
        else:
            log.warning("[yt_source] Clip %d cut failed: %s", i, cut.error)

    return clips


def fetch_clip_for_rank(
    cfg: RankerConfig,
    app_cfg: AppConfig,
    video_id: str,
    used_ranges: list[list[float]],
    label: str,
    rank_num: int,
) -> tuple[Path, float, float] | None:
    """
    Download (or reuse cache) a CC-BY video and cut the best 6-second moment
    not overlapping with already-used ranges.

    Returns (clip_path, start_sec, end_sec) or None on failure.
    """
    from ..downloader import download_video
    from ..clip_analyzer import (
        detect_scene_changes, detect_audio_loudness, detect_speech_intervals,
        find_best_clip_windows,
    )
    from ..clip_cutter import cut_single_clip
    from .pool import ranges_overlap

    clip_dur = cfg.clip_seconds  # 6.0 seconds per rank segment

    # Download dir for ranker source videos (separate from normalized clips)
    src_dir = cfg.download_dir / "sources"
    src_dir.mkdir(parents=True, exist_ok=True)

    # Check if already downloaded
    existing = list(src_dir.glob(f"{video_id}.*"))
    if existing:
        src_path = existing[0]
        log.info("[yt_source] Using cached source: %s", src_path.name)
    else:
        log.info("[yt_source] Downloading %s for rank %d (%s)", video_id, rank_num, label)
        result = download_video(video_id, src_dir, app_cfg.ffmpeg_bin)
        if not result.success or not result.output_path:
            log.error("[yt_source] Download failed for %s: %s", video_id, result.error)
            return None
        src_path = result.output_path

    # Analyse the video
    try:
        scenes = detect_scene_changes(src_path, app_cfg.ffmpeg_bin, app_cfg.scene_threshold)
        audio = detect_audio_loudness(src_path, app_cfg.ffmpeg_bin, app_cfg.audio_peak_percentile)
        speech = detect_speech_intervals(src_path, app_cfg.ffmpeg_bin)
    except Exception as exc:
        log.error("[yt_source] Analysis failed for %s: %s", video_id, exc)
        return None

    # Get video duration
    import subprocess
    try:
        r = subprocess.run(
            [str(app_cfg.ffprobe_bin), "-v", "quiet",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(src_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        video_duration = float(r.stdout.decode().strip() or "0")
    except Exception:
        video_duration = 0.0

    if video_duration < clip_dur:
        log.warning("[yt_source] Video %s too short (%.1fs)", video_id, video_duration)
        return None

    # Find best windows (use clip_dur as both min and preferred — we want exactly 6s)
    windows = find_best_clip_windows(
        scenes, audio, speech, video_duration,
        min_duration=int(clip_dur),
        max_duration=int(clip_dur * 2),
        max_clips=20,
        preferred_duration=int(clip_dur),
        dead_zone_start_pct=0.02,
        dead_zone_end_pct=0.02,
    )

    # Pick the highest-scoring window not overlapping already-used ranges
    chosen = None
    for w in sorted(windows, key=lambda x: x.final_score, reverse=True):
        if not ranges_overlap(w.start_sec, w.end_sec, used_ranges):
            chosen = w
            break

    # Fallback: if no scored window avoids used ranges, scan linearly
    if chosen is None:
        step = clip_dur + 5.0
        t = 5.0
        while t + clip_dur <= video_duration:
            if not ranges_overlap(t, t + clip_dur, used_ranges):
                from ..clip_analyzer import ClipCandidate
                chosen = ClipCandidate(
                    start_sec=t, end_sec=t + clip_dur,
                    duration_sec=clip_dur,
                    scene_score=0.0, loudness_spike=0.0, speech_coverage=0.0,
                    motion_variance=0.0, duration_score=1.0, combined_score=0.0,
                    final_score=0.0, reason="fallback_linear",
                )
                break
            t += step

    if chosen is None:
        log.warning("[yt_source] No unused window found in %s", video_id)
        return None

    # Force exactly clip_dur length
    chosen.end_sec = chosen.start_sec + clip_dur
    chosen.duration_sec = clip_dur

    # Cut dir for rank preview clips
    cut_dir = cfg.download_dir / "rank_previews"
    cut_dir.mkdir(parents=True, exist_ok=True)

    cut = cut_single_clip(
        source_path=src_path,
        output_dir=cut_dir,
        video_id=f"rank{rank_num:02d}_{video_id}",
        clip_index=1,
        candidate=chosen,
        ffmpeg_bin=app_cfg.ffmpeg_bin,
        ffprobe_bin=app_cfg.ffprobe_bin,
        shorts_output=True,
    )

    if not cut.success or not cut.output_path:
        log.error("[yt_source] Cut failed for %s: %s", video_id, cut.error)
        return None

    log.info("[yt_source] Cut rank %d: %s (%.1fs–%.1fs)", rank_num, cut.output_path.name,
             chosen.start_sec, chosen.end_sec)
    return cut.output_path, chosen.start_sec, chosen.end_sec
