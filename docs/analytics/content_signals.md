# Content Signals & Keyword Performance

## What Makes a Good Source Video for Clipping
High-performing source videos share these traits:
- Clear speech (minimal background noise) → high speech_coverage in clips
- Structured content with natural breaks (demos, explanations, transitions)
- Mid-range view count (10k–500k): viral videos are often already clipped by others
- Published within the last 30 days: recency drives suggested traffic for clips too
- Tutorial or review format: predictable structure = predictable clip moments

## Keyword Category Performance (General Benchmarks)
| Category         | Avg CTR | Avg Retention | Notes                                      |
|-----------------|---------|---------------|--------------------------------------------|
| AI tutorials    | 6–9%    | 45–60%        | Best performing niche in 2025–2026         |
| Gadget reviews  | 4–7%    | 30–45%        | Competitive — need unique angle            |
| Tech news       | 3–5%    | 20–35%        | Short shelf life, post within 48h          |
| Programming     | 5–8%    | 50–65%        | High retention but smaller audience        |
| Startup/funding | 2–4%    | 15–25%        | Poor for Shorts format                     |

## Keyword Specificity Rule
More specific = better targeting = higher CTR and lower competition.

Prefer:
- "Claude AI vs ChatGPT 2026" over "AI comparison"
- "Python async tutorial beginner" over "python programming"
- "Vision Pro apps review" over "Apple review"

Avoid single-word or two-word generic keywords as primary search terms.
They produce too many irrelevant results and waste API quota.

## CC-BY Keyword Strategy
For `cc_search_keywords`, add "tutorial" or "explained" to terms — these formats are more
commonly licensed CC-BY by educators and researchers.

Good CC-BY keyword patterns:
- "[topic] tutorial CC"
- "[topic] explained open source"
- "[topic] free course"
- "university lecture [topic]"
- "[framework] crash course"

## Trending Signal Interpretation
When YouTube Suggestions return new terms not in current keyword list:
- If suggestion is a proper noun (product name, person, event): high priority — add immediately
- If suggestion is a variation of existing keyword: medium priority — replace the weaker variant
- If suggestion contains "how to" or "tutorial": always add to cc_search_keywords too
- If suggestion is vague/generic (one word): skip — low signal-to-noise ratio
