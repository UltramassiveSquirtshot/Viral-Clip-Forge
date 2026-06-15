# YouTube Analytics Data Interpretation

## Key Metrics and What They Mean

### retention_rate = avg_view_duration / clip_duration
The single most important metric for Shorts.
- < 20%: clip fails to hook — wrong moment selected or bad source video
- 20–40%: acceptable for early stage, prioritize improving
- 40–60%: good — algorithm will start suggesting this clip
- > 60%: excellent — this clip type/keyword/slot combination works; replicate it

### impressionsCtr (CTR)
Ratio of clicks to impressions on the thumbnail/title.
- < 2%: thumbnail or title problem, not a clip problem
- 2–5%: normal range for a new channel
- > 5%: strong title/thumbnail signal — note what the source video topic was

### impressions
Number of times the clip was shown to viewers (not the same as views).
Low impressions (< 500 in 7 days) = algorithm hasn't tested the clip yet.
Do NOT judge retention_rate on clips with < 200 impressions — sample size too small.

### watchTimeMinutes
Total watch time across all viewers.
For a 60s clip with 100 views at 50% retention: 100 × 30s = 50 minutes.
More useful in aggregate than per-clip — used to assess channel health over time.

### averageViewDuration
Raw seconds, not a ratio. Always convert to retention_rate for comparison across clips
of different lengths: `retention_rate = avg_view_duration / clip_duration_sec`

## Minimum Data Requirements Before Drawing Conclusions
- Need at least **7 days** of data per clip before retention stabilizes
- Need at least **200 impressions** per clip for CTR to be meaningful
- Need at least **5 clips** per `clip.reason` category to compare categories
- Need at least **3 clips** per time slot to compare slots

## Common Misinterpretations
- **High views ≠ high retention**: A clip can go semi-viral on impressions but have 10% retention
- **Low views ≠ bad clip**: New clips need 3–7 days for algorithm to distribute impressions
- **Zero analytics data**: The clip may not have been published yet (still private/scheduled)
- **Missing clip_id in analytics**: YouTube video ID in manifest must match what was uploaded

## Correlation Score Interpretation
The pipeline computes a `composite_score` (0–1) at clip production time.
When comparing composite_score vs actual retention_rate:
- correlation > 0.6: our scoring predicts performance well — trust the algorithm
- correlation 0.3–0.6: weak signal — consider rebalancing weights
- correlation < 0.3: our scoring is not predictive — something else drives retention
  (likely source video quality or keyword match, not clip moment selection)
