# Publishing Strategies & Slot Optimization

## Upload Frequency Rules
- Maximum 3 Shorts per day, 3–4 days per week (algorithm safe limit)
- Approved upload days: Tuesday, Wednesday, Thursday, Saturday
- Approved time slots (Rome local): 08:00, 13:00, 19:30
- Never upload more than 3 clips in a 24-hour window — algorithm penalizes burst uploads

## Slot Performance Interpretation
- **Morning (08:00)**: Best for educational/tutorial content. Viewers commuting or starting work.
- **Midday (13:00)**: Highest general CTR window. Best for trending/news-adjacent content.
- **Evening (19:30)**: Best for entertainment and review content. Highest watch-time completion.

When analyzing slot performance from retention_rate data:
- If a slot shows >10% higher avg retention than others across 3+ clips → promote it to priority #1
- If a slot shows <5% avg retention consistently → demote it to last priority
- Rotate slot priority quarterly to avoid audience prediction patterns

## Suggested Cadence Adjustments
- If clips_produced < 3 in a week: do NOT increase upload frequency — fix the scraper/filter first
- If uploads_scheduled < clips_produced: check YouTube quota and token health
- If retention_rate < 20% across all clips: problem is content selection, not schedule

## CC-BY Freebooting Risk Management
- Always verify license at download time (yt-dlp metadata check)
- Prefer videos from established edu/research channels over random uploads
- Videos with >100k views and CC-BY license are higher risk of false-license tagging — double-check
- If a channel has multiple CC-BY videos with high engagement, it's a reliable source — note the channel_id
