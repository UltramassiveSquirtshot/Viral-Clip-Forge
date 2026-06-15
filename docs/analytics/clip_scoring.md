# Clip Scoring & Retention Curve Interpretation

## Current Scoring Formula
```
combined = 0.30 * loudness_spike + 0.25 * scene_score + 0.25 * speech_coverage + 0.20 * motion_variance
final    = combined * (0.8 + 0.2 * duration_score)
```

## How to Map Retention Data to Weight Adjustments

### If scene_change clips outperform evenly_spaced by >15% retention:
- Lower `scene_threshold` from 0.40 → 0.35 to detect more scene changes
- Raise scene weight to 0.35, lower loudness to 0.25

### If loudness_spike clips outperform others by >15% retention:
- Lower `audio_peak_percentile` from 85 → 80 to detect more peaks
- Raise loudness weight to 0.40, lower scene to 0.20

### If evenly_spaced clips perform on par with triggered clips:
- The signal detection is too noisy — raise `scene_threshold` to 0.45
- The video content may be consistently good — consider raising `max_clips_per_video`

### If all clip types show low retention (<25%):
- Problem is not the clip selection algorithm — it's the source video quality
- Tighten `min_views` threshold and/or raise `engagement_ratio` floor
- Do not adjust FFmpeg thresholds

## Duration Score Interpretation
- `preferred_clip_duration` is currently 45s
- If top-10 retention clips average duration > 55s → raise preferred to 55s
- If top-10 retention clips average duration < 40s → lower preferred to 38s
- YouTube Shorts algorithm favors 30–60s; above 60s retention drops sharply for this content type

## Dead Zone Tuning
- `dead_zone_start_pct = 0.08`: skips first 8% (intros, black frames)
- `dead_zone_end_pct = 0.05`: skips last 5% (outros, subscribe prompts)
- If clips consistently start mid-sentence → raise dead_zone_start_pct to 0.10
- If clips cut off early before a conclusion → lower dead_zone_end_pct to 0.03

## Clip Reason Performance Benchmarks
| reason         | expected retention | action if underperforming          |
|---------------|--------------------|------------------------------------|
| scene_change  | 35–55%             | lower scene_threshold              |
| loudness_spike| 30–50%             | lower audio_peak_percentile        |
| evenly_spaced | 20–35%             | source videos are not exciting     |
