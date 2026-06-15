# YouTube Algorithm Signals & Traffic Growth

## How Shorts Traffic Flows
1. **Browse/Home feed** (impressions phase): Algorithm tests your clip on a small audience
2. **Retention gate**: If retention > ~40%, algorithm expands distribution
3. **Suggested traffic**: Once clips pass the retention gate, they appear alongside related content
4. **Search traffic**: Builds over weeks as the clip gets indexed for its keywords

The most important phase is 1→2. A clip that fails the retention gate in the first 48h
rarely recovers, even with manual promotion.

## Signals That Trigger Wider Distribution
- High completion rate (retention > 40% in first 200 impressions)
- Likes within the first 24h (even 5–10 likes helps)
- Shares and comments in first 48h
- Re-watches (avg_view_duration > clip_duration means re-watches — very strong signal)

## What Kills Distribution Early
- Long dead zones at clip start (viewer drops off in first 3 seconds)
- Abrupt audio cuts (sounds unfinished — viewer leaves)
- Black frames or freeze frames mid-clip
- Title/thumbnail mismatch with content (viewer feels deceived → instant exit)

## Containment Signals (AI Disclosure)
Clips are uploaded with `containsSyntheticMedia=true` (AI disclosure flag).
As of 2026, this flag does not penalize distribution but it does label the content.
YouTube's policy requires this for AI-generated voiceover or synthetic visuals.
Since we clip real human content, this flag is technically overcautious but keeps us
compliant with the broadest interpretation of their policy.

## Channel-Level Algorithm Health
- Upload consistency matters more than quantity — 3 uploads/week consistently beats
  5 uploads one week and 0 the next
- If the channel goes 2+ weeks with no uploads, expect a temporary impression drop on
  the next batch — plan for lower CTR in the first 48h
- Subscriber count has minimal effect on Shorts distribution (unlike long-form videos)
- Watch time from Shorts does NOT count toward the YouTube Partner Program monetization
  threshold (500 hours long-form watch time required) — this pipeline is for growth, not revenue

## Traffic Source Interpretation
| Source           | Meaning                                                          |
|-----------------|------------------------------------------------------------------|
| YouTube Search  | Keyword is working — note which keywords drive search traffic    |
| Suggested Videos| Algorithm is distributing — clip passed retention gate           |
| Browse Features | Home feed / Shorts shelf — strong signal from algorithm          |
| External        | Shared links — note which clips get shared organically           |
| Direct / Other  | Minimal signal — usually internal testing or bot traffic         |

If >60% of traffic is "Browse Features", the algorithm is actively pushing — do not change
anything about that clip type. Replicate its keyword, duration, and clip reason.
