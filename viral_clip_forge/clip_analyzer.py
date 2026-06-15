from __future__ import annotations

import csv
import io
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .utils import get_logger

log = get_logger()


@dataclass
class SceneChange:
    timestamp_sec: float
    scene_score: float


@dataclass
class AudioLoudness:
    timestamp_sec: float
    rms_db: float
    spike: float  # normalised spike above rolling mean, 0–1


@dataclass
class SpeechInterval:
    start_sec: float
    end_sec: float


@dataclass
class ClipCandidate:
    start_sec: float
    end_sec: float
    duration_sec: float
    scene_score: float
    loudness_spike: float
    speech_coverage: float
    motion_variance: float
    duration_score: float
    combined_score: float
    final_score: float
    reason: str


def _run(cmd: list[str], timeout: int = 120) -> tuple[str, str, int]:
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return result.stdout.decode("utf-8", errors="replace"), result.stderr.decode("utf-8", errors="replace"), result.returncode


# ---------------------------------------------------------------------------
# Scene detection
# ---------------------------------------------------------------------------

def detect_scene_changes(
    video_path: Path,
    ffmpeg_bin: Path,
    threshold: float = 0.40,
) -> list[SceneChange]:
    """Detect scene changes using FFmpeg select filter (low threshold for dense signal)."""
    dense_threshold = min(threshold, 0.15)
    cmd = [
        str(ffmpeg_bin),
        "-i", str(video_path),
        "-vf", f"select='gt(scene,{dense_threshold})',showinfo",
        "-vsync", "vfr",
        "-f", "null",
        "-",
    ]
    try:
        _, stderr, _ = _run(cmd, timeout=300)
        changes: list[SceneChange] = []
        for line in stderr.splitlines():
            m = re.search(r"pts_time:([\d.]+)", line)
            if m:
                ts = float(m.group(1))
                score_m = re.search(r"scene_score=([\d.]+)", line)
                score = float(score_m.group(1)) if score_m else dense_threshold
                changes.append(SceneChange(timestamp_sec=ts, scene_score=score))

        if changes:
            log.info(f"[scene] FFmpeg detected {len(changes)} scene changes in {video_path.name}")
            return changes

    except Exception as exc:
        log.warning(f"[scene] FFmpeg scene detection failed: {exc} — trying PySceneDetect fallback")

    return _detect_scenes_scenedetect(video_path, threshold)


def _detect_scenes_scenedetect(
    video_path: Path,
    threshold: float = 0.40,
) -> list[SceneChange]:
    try:
        from scenedetect import detect, ContentDetector
        native_threshold = threshold * 70
        scenes = detect(str(video_path), ContentDetector(threshold=native_threshold))
        changes: list[SceneChange] = []
        for start_tc, _ in scenes:
            changes.append(SceneChange(timestamp_sec=start_tc.get_seconds(), scene_score=threshold))
        log.info(f"[scene] PySceneDetect found {len(changes)} scenes in {video_path.name}")
        return changes
    except Exception as exc:
        log.warning(f"[scene] PySceneDetect also failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# True loudness via astats
# ---------------------------------------------------------------------------

def detect_audio_loudness(
    video_path: Path,
    ffmpeg_bin: Path,
    rolling_window_sec: float = 5.0,
    spike_threshold_db: float = 6.0,
) -> list[AudioLoudness]:
    """
    Extract per-frame RMS loudness with FFmpeg astats.
    Returns a normalised spike value (0–1) measuring how much each frame
    exceeds the rolling mean — high values = sudden loud moments.
    """
    cmd = [
        str(ffmpeg_bin),
        "-i", str(video_path),
        "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
        "-vn",
        "-f", "null",
        "-",
    ]
    try:
        _, stderr, rc = _run(cmd, timeout=300)
        if rc != 0 and not stderr.strip():
            log.warning(f"[loudness] astats returned no data for {video_path.name}")
            return []

        times: list[float] = []
        rms_vals: list[float] = []

        current_ts: float | None = None
        for line in stderr.splitlines():
            ts_m = re.search(r"pts_time:([\d.]+)", line)
            if ts_m:
                current_ts = float(ts_m.group(1))
            rms_m = re.search(r"lavfi\.astats\.Overall\.RMS_level=([-\d.]+|inf|-inf)", line)
            if rms_m and current_ts is not None:
                raw = rms_m.group(1)
                if raw in ("-inf", "inf"):
                    rms_db = -100.0
                else:
                    rms_db = float(raw)
                times.append(current_ts)
                rms_vals.append(rms_db)
                current_ts = None

        if not times:
            log.warning(f"[loudness] No RMS data parsed for {video_path.name}")
            return []

        times_arr = np.array(times)
        rms_arr = np.array(rms_vals)

        # Rolling mean over rolling_window_sec
        if len(times_arr) > 1:
            avg_interval = max(np.diff(times_arr).mean(), 0.01)
            window_pts = max(1, int(rolling_window_sec / avg_interval))
        else:
            window_pts = 1
        kernel = np.ones(window_pts) / window_pts
        rolling_mean = np.convolve(rms_arr, kernel, mode="same")

        # Spike = how much louder than rolling mean (in dB), clamped positive, normalised
        spike_raw = np.clip(rms_arr - rolling_mean, 0, None)
        max_spike = spike_raw.max()
        spike_norm = spike_raw / max_spike if max_spike > 0 else spike_raw

        result = [
            AudioLoudness(
                timestamp_sec=float(t),
                rms_db=float(r),
                spike=float(s),
            )
            for t, r, s in zip(times_arr, rms_arr, spike_norm)
        ]
        n_spikes = sum(1 for a in result if a.spike > 0.5)
        log.info(f"[loudness] {n_spikes} loud spikes detected in {video_path.name}")
        return result

    except Exception as exc:
        log.warning(f"[loudness] Audio loudness detection failed for {video_path.name}: {exc}")
        return []


# ---------------------------------------------------------------------------
# Speech-activity detection via silencedetect
# ---------------------------------------------------------------------------

def detect_speech_intervals(
    video_path: Path,
    ffmpeg_bin: Path,
    noise_db: float = -35.0,
    min_silence_sec: float = 0.3,
) -> list[SpeechInterval]:
    """
    Use FFmpeg silencedetect to find speech-dense (non-silent) regions.
    Returns intervals where audio is above noise_db for at least min_silence_sec.
    """
    cmd = [
        str(ffmpeg_bin),
        "-i", str(video_path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_sec}",
        "-vn",
        "-f", "null",
        "-",
    ]
    try:
        _, stderr, rc = _run(cmd, timeout=300)

        silence_starts: list[float] = []
        silence_ends: list[float] = []
        for line in stderr.splitlines():
            ss = re.search(r"silence_start:\s*([\d.]+)", line)
            se = re.search(r"silence_end:\s*([\d.]+)", line)
            if ss:
                silence_starts.append(float(ss.group(1)))
            if se:
                silence_ends.append(float(se.group(1)))

        # Build speech intervals as the complement of silence
        intervals: list[SpeechInterval] = []
        cursor = 0.0
        for s_start, s_end in zip(silence_starts, silence_ends):
            if s_start > cursor:
                intervals.append(SpeechInterval(start_sec=cursor, end_sec=s_start))
            cursor = s_end
        # Trailing speech (after last silence end)
        if silence_ends:
            intervals.append(SpeechInterval(start_sec=cursor, end_sec=float("inf")))
        elif not silence_starts:
            # No silence detected at all — entire video is speech
            intervals.append(SpeechInterval(start_sec=0.0, end_sec=float("inf")))

        log.info(f"[speech] {len(intervals)} speech intervals detected in {video_path.name}")
        return intervals

    except Exception as exc:
        log.warning(f"[speech] silencedetect failed for {video_path.name}: {exc}")
        return []


def _speech_coverage(intervals: list[SpeechInterval], start: float, end: float) -> float:
    """Fraction of [start, end] covered by speech intervals."""
    if not intervals or end <= start:
        return 0.0
    window = end - start
    covered = 0.0
    for iv in intervals:
        iv_end = min(iv.end_sec, end)
        iv_start = max(iv.start_sec, start)
        if iv_end > iv_start:
            covered += iv_end - iv_start
    return min(1.0, covered / window)


# ---------------------------------------------------------------------------
# Motion variance from scene score stream
# ---------------------------------------------------------------------------

def _motion_variance(
    scene_changes: list[SceneChange],
    start: float,
    end: float,
    variance_window_sec: float = 3.0,
) -> float:
    """
    Normalised rolling variance of scene scores within [start, end].
    High variance = visually dynamic region (rapid alternating cuts vs static).
    Returns 0–1.
    """
    scores = [(sc.timestamp_sec, sc.scene_score) for sc in scene_changes if start <= sc.timestamp_sec <= end]
    if len(scores) < 2:
        return 0.0
    vals = np.array([s for _, s in scores])
    var = float(np.var(vals))
    # Normalise: variance of uniform [0,1] is 1/12 ≈ 0.083; cap at 0.25
    return min(1.0, var / 0.25)


# ---------------------------------------------------------------------------
# Overlap deduplication
# ---------------------------------------------------------------------------

def _overlap_fraction(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    overlap = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    shorter = min(a_end - a_start, b_end - b_start)
    if shorter <= 0:
        return 0.0
    return overlap / shorter


# ---------------------------------------------------------------------------
# Anchor search: slide window to maximise signal
# ---------------------------------------------------------------------------

def _best_anchor(
    trigger_ts: float,
    target_duration: float,
    video_duration: float,
    dead_start: float,
    dead_end: float,
    scene_changes: list[SceneChange],
    loudness: list[AudioLoudness],
    speech_intervals: list[SpeechInterval],
    step_sec: float = 2.0,
    search_radius_sec: float = 15.0,
) -> tuple[float, float]:
    """
    Slide a window of target_duration in step_sec increments around trigger_ts
    (±search_radius_sec), score each position, return (best_start, best_end).
    """
    best_start = max(dead_start, trigger_ts - 10.0)
    best_score = -1.0

    raw_start = trigger_ts - search_radius_sec
    raw_end = trigger_ts + search_radius_sec

    pos = raw_start
    while pos <= raw_end:
        s = max(dead_start, pos)
        e = min(dead_end, s + target_duration)
        if e - s < target_duration * 0.8:
            pos += step_sec
            continue

        ls = _max_loudness_spike(loudness, s, e)
        sc = _max_scene_in_window(scene_changes, s, e)
        sp = _speech_coverage(speech_intervals, s, e)
        mv = _motion_variance(scene_changes, s, e)
        score = 0.30 * ls + 0.25 * sc + 0.25 * sp + 0.20 * mv

        if score > best_score:
            best_score = score
            best_start = s

        pos += step_sec

    best_end = min(dead_end, best_start + target_duration)
    return best_start, best_end


# ---------------------------------------------------------------------------
# Helper signal accessors
# ---------------------------------------------------------------------------

def _max_loudness_spike(loudness: list[AudioLoudness], start: float, end: float) -> float:
    vals = [a.spike for a in loudness if start <= a.timestamp_sec <= end]
    return max(vals) if vals else 0.0


def _max_scene_in_window(changes: list[SceneChange], start: float, end: float) -> float:
    vals = [sc.scene_score for sc in changes if start <= sc.timestamp_sec <= end]
    return max(vals) if vals else 0.0


# ---------------------------------------------------------------------------
# Main entry: find best clip windows
# ---------------------------------------------------------------------------

def find_best_clip_windows(
    scene_changes: list[SceneChange],
    audio_loudness: list[AudioLoudness],
    speech_intervals: list[SpeechInterval],
    video_duration: float,
    min_duration: int = 30,
    max_duration: int = 90,
    max_clips: int = 3,
    preferred_duration: int = 45,
    dead_zone_start_pct: float = 0.08,
    dead_zone_end_pct: float = 0.05,
) -> list[ClipCandidate]:
    if video_duration <= 0:
        return []

    dead_start = video_duration * dead_zone_start_pct
    dead_end = video_duration * (1.0 - dead_zone_end_pct)
    usable = dead_end - dead_start
    if usable < min_duration:
        # Video too short to have dead zones — use full range
        dead_start = 0.0
        dead_end = video_duration

    target_duration = float(min(max_duration, max(min_duration, preferred_duration)))

    candidates: list[ClipCandidate] = []

    # Scene-change triggers
    for sc in scene_changes:
        if not (dead_start <= sc.timestamp_sec <= dead_end):
            continue
        start, end = _best_anchor(
            sc.timestamp_sec, target_duration, video_duration,
            dead_start, dead_end,
            scene_changes, audio_loudness, speech_intervals,
        )
        if end - start < min_duration:
            continue
        candidates.append(_make_candidate(
            start, end, scene_changes, audio_loudness, speech_intervals,
            preferred_duration, reason="scene_change",
        ))

    # Loudness spike triggers
    spike_threshold = 0.5
    loud_triggers = [a.timestamp_sec for a in audio_loudness if a.spike >= spike_threshold]
    for ts in loud_triggers:
        if not (dead_start <= ts <= dead_end):
            continue
        start, end = _best_anchor(
            ts, target_duration, video_duration,
            dead_start, dead_end,
            scene_changes, audio_loudness, speech_intervals,
        )
        if end - start < min_duration:
            continue
        candidates.append(_make_candidate(
            start, end, scene_changes, audio_loudness, speech_intervals,
            preferred_duration, reason="loudness_spike",
        ))

    if not candidates:
        return _evenly_spaced_clips(
            video_duration, min_duration, max_duration, max_clips,
            dead_start, dead_end, preferred_duration,
        )

    # Sort by final_score descending
    candidates.sort(key=lambda c: c.final_score, reverse=True)

    # Greedy deduplication at 30% overlap
    selected: list[ClipCandidate] = []
    for cand in candidates:
        if any(
            _overlap_fraction(cand.start_sec, cand.end_sec, sel.start_sec, sel.end_sec) > 0.30
            for sel in selected
        ):
            continue
        selected.append(cand)
        if len(selected) >= max_clips:
            break

    selected.sort(key=lambda c: c.start_sec)
    log.info(f"[analyzer] Selected {len(selected)} clip windows (from {len(candidates)} candidates)")
    return selected


def _make_candidate(
    start: float,
    end: float,
    scene_changes: list[SceneChange],
    loudness: list[AudioLoudness],
    speech_intervals: list[SpeechInterval],
    preferred_duration: int,
    reason: str,
) -> ClipCandidate:
    duration = end - start
    ls = _max_loudness_spike(loudness, start, end)
    sc = _max_scene_in_window(scene_changes, start, end)
    sp = _speech_coverage(speech_intervals, start, end)
    mv = _motion_variance(scene_changes, start, end)
    dur_score = max(0.0, 1.0 - abs(duration - preferred_duration) / max(preferred_duration, 1))

    combined = 0.30 * ls + 0.25 * sc + 0.25 * sp + 0.20 * mv
    final = combined * (0.8 + 0.2 * dur_score)

    return ClipCandidate(
        start_sec=start,
        end_sec=end,
        duration_sec=duration,
        scene_score=sc,
        loudness_spike=ls,
        speech_coverage=sp,
        motion_variance=mv,
        duration_score=dur_score,
        combined_score=combined,
        final_score=final,
        reason=reason,
    )


def _evenly_spaced_clips(
    video_duration: float,
    min_duration: int,
    max_duration: int,
    max_clips: int,
    dead_start: float,
    dead_end: float,
    preferred_duration: int,
) -> list[ClipCandidate]:
    usable = dead_end - dead_start
    if usable < min_duration:
        return [ClipCandidate(
            start_sec=0.0, end_sec=video_duration, duration_sec=video_duration,
            scene_score=0.0, loudness_spike=0.0, speech_coverage=0.0,
            motion_variance=0.0, duration_score=0.0, combined_score=0.0,
            final_score=0.0, reason="full_video",
        )]

    clip_dur = float(min(max_duration, max(min_duration, preferred_duration)))
    result: list[ClipCandidate] = []
    step = usable / max_clips
    for i in range(max_clips):
        start = dead_start + i * step
        end = min(dead_end, start + clip_dur)
        dur = end - start
        dur_score = max(0.0, 1.0 - abs(dur - preferred_duration) / max(preferred_duration, 1))
        result.append(ClipCandidate(
            start_sec=start, end_sec=end, duration_sec=dur,
            scene_score=0.0, loudness_spike=0.0, speech_coverage=0.0,
            motion_variance=0.0, duration_score=dur_score, combined_score=0.0,
            final_score=0.0, reason="evenly_spaced",
        ))
    return result
