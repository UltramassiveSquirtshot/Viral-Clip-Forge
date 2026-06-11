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
class AudioPeak:
    timestamp_sec: float
    energy: float
    is_peak: bool


@dataclass
class ClipCandidate:
    start_sec: float
    end_sec: float
    duration_sec: float
    scene_score: float
    audio_peak_energy: float
    combined_score: float
    reason: str


def _run(cmd: list[str], timeout: int = 120) -> tuple[str, str, int]:
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return result.stdout.decode("utf-8", errors="replace"), result.stderr.decode("utf-8", errors="replace"), result.returncode


def detect_scene_changes(
    video_path: Path,
    ffmpeg_bin: Path,
    threshold: float = 0.40,
) -> list[SceneChange]:
    cmd = [
        str(ffmpeg_bin),
        "-i", str(video_path),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
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
                score = float(score_m.group(1)) if score_m else threshold
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


def detect_audio_peaks(
    video_path: Path,
    ffprobe_bin: Path,
    window_sec: float = 2.0,
    peak_percentile: int = 85,
) -> list[AudioPeak]:
    cmd = [
        str(ffprobe_bin),
        "-v", "quiet",
        "-select_streams", "a:0",
        "-show_entries", "packet=pts_time,size",
        "-of", "csv",
        str(video_path),
    ]
    try:
        stdout, _, rc = _run(cmd, timeout=120)
        if rc != 0 or not stdout.strip():
            log.warning(f"[audio] ffprobe returned no audio data for {video_path.name}")
            return []

        times: list[float] = []
        sizes: list[float] = []
        reader = csv.reader(io.StringIO(stdout))
        for row in reader:
            if len(row) < 3:
                continue
            try:
                t = float(row[1])
                s = float(row[2])
                times.append(t)
                sizes.append(s)
            except (ValueError, IndexError):
                continue

        if not times:
            return []

        times_arr = np.array(times)
        sizes_arr = np.array(sizes, dtype=float)

        # Rolling window mean
        window_pts = max(1, int(window_sec / max(np.diff(times_arr).mean(), 0.01))) if len(times_arr) > 1 else 1
        kernel = np.ones(window_pts) / window_pts
        smoothed = np.convolve(sizes_arr, kernel, mode="same")

        max_val = smoothed.max()
        if max_val > 0:
            normalized = smoothed / max_val
        else:
            normalized = smoothed

        threshold_val = np.percentile(normalized, peak_percentile)
        peaks: list[AudioPeak] = []
        for i, (t, e) in enumerate(zip(times_arr, normalized)):
            peaks.append(AudioPeak(
                timestamp_sec=float(t),
                energy=float(e),
                is_peak=bool(e >= threshold_val),
            ))

        n_peaks = sum(1 for p in peaks if p.is_peak)
        log.info(f"[audio] {n_peaks} audio peaks detected in {video_path.name}")
        return peaks

    except Exception as exc:
        log.warning(f"[audio] Audio peak detection failed for {video_path.name}: {exc}")
        return []


def _overlap_fraction(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    overlap = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    shorter = min(a_end - a_start, b_end - b_start)
    if shorter <= 0:
        return 0.0
    return overlap / shorter


def find_best_clip_windows(
    scene_changes: list[SceneChange],
    audio_peaks: list[AudioPeak],
    video_duration: float,
    min_duration: int = 30,
    max_duration: int = 90,
    max_clips: int = 3,
) -> list[ClipCandidate]:
    if video_duration <= 0:
        return []

    target_duration = float(min(max_duration, max(min_duration, int((min_duration + max_duration) / 2))))

    candidates: list[ClipCandidate] = []

    for sc in scene_changes:
        start = max(0.0, sc.timestamp_sec - 5.0)
        end = min(video_duration, start + target_duration)
        if end - start < min_duration:
            continue

        audio_score = _max_audio_in_window(audio_peaks, start, end)
        combined = 0.6 * sc.scene_score + 0.4 * audio_score
        candidates.append(ClipCandidate(
            start_sec=start,
            end_sec=end,
            duration_sec=end - start,
            scene_score=sc.scene_score,
            audio_peak_energy=audio_score,
            combined_score=combined,
            reason="scene_change",
        ))

    peak_timestamps = [p.timestamp_sec for p in audio_peaks if p.is_peak]
    for ts in peak_timestamps:
        start = max(0.0, ts - 10.0)
        end = min(video_duration, start + target_duration)
        if end - start < min_duration:
            continue

        scene_score = _max_scene_in_window(scene_changes, start, end)
        audio_score = _max_audio_in_window(audio_peaks, start, end)
        combined = 0.6 * scene_score + 0.4 * audio_score
        candidates.append(ClipCandidate(
            start_sec=start,
            end_sec=end,
            duration_sec=end - start,
            scene_score=scene_score,
            audio_peak_energy=audio_score,
            combined_score=combined,
            reason="audio_peak",
        ))

    if not candidates:
        return _evenly_spaced_clips(video_duration, min_duration, max_duration, max_clips)

    candidates.sort(key=lambda c: c.combined_score, reverse=True)

    selected: list[ClipCandidate] = []
    for cand in candidates:
        overlaps = any(
            _overlap_fraction(cand.start_sec, cand.end_sec, sel.start_sec, sel.end_sec) > 0.5
            for sel in selected
        )
        if not overlaps:
            selected.append(cand)
        if len(selected) >= max_clips:
            break

    selected.sort(key=lambda c: c.start_sec)
    log.info(f"[analyzer] Selected {len(selected)} clip windows")
    return selected


def _max_audio_in_window(peaks: list[AudioPeak], start: float, end: float) -> float:
    values = [p.energy for p in peaks if start <= p.timestamp_sec <= end]
    return max(values) if values else 0.0


def _max_scene_in_window(changes: list[SceneChange], start: float, end: float) -> float:
    values = [sc.scene_score for sc in changes if start <= sc.timestamp_sec <= end]
    return max(values) if values else 0.0


def _evenly_spaced_clips(
    video_duration: float,
    min_duration: int,
    max_duration: int,
    max_clips: int,
) -> list[ClipCandidate]:
    if video_duration < min_duration:
        return [ClipCandidate(
            start_sec=0.0,
            end_sec=video_duration,
            duration_sec=video_duration,
            scene_score=0.0,
            audio_peak_energy=0.0,
            combined_score=0.0,
            reason="full_video",
        )]

    clip_dur = float(min(max_duration, max(min_duration, int(video_duration / max_clips))))
    result: list[ClipCandidate] = []
    step = video_duration / max_clips
    for i in range(max_clips):
        start = i * step
        end = min(video_duration, start + clip_dur)
        result.append(ClipCandidate(
            start_sec=start,
            end_sec=end,
            duration_sec=end - start,
            scene_score=0.0,
            audio_peak_energy=0.0,
            combined_score=0.0,
            reason="evenly_spaced",
        ))
    return result
