# Viral Clip Forge — Cowork Agent Instructions

## Run Command

```
cd "C:\Users\Utente\Desktop\PROGETTI\Viral Clip Forge" && C:\Python313\python.exe main.py
```

## Schedule

Cron: `0 7,19 * * *` — runs at 07:00 and 19:00 UTC (08:00 and 20:00 Rome time)

## What this pipeline does

1. Fetches trending YouTube videos in **Tech** (category 28) and **Finance** (category 25) niches
2. Filters by engagement score → picks top 3 per niche
3. Checks each video for **Creative Commons (CC-BY)** license via YouTube API + yt-dlp
4. Downloads only CC-BY licensed videos via yt-dlp
5. Detects exciting moments using FFmpeg scene detection + audio peak analysis
6. Cuts highlight clips (30–90s each, up to 3 per video) using FFmpeg
7. Writes a JSON manifest to `data/manifests/`

## Outputs

- **Clips**: `clips/{video_id}_clip{N}_{start}s-{end}s.mp4`
- **Manifest**: `data/manifests/YYYY-MM-DDTHH-MM/manifest.json`
- **Logs**: `logs/pipeline_YYYY-MM-DD.log`
- **State DB**: `data/state.db` (SQLite — tracks processed videos, prevents re-processing)

## Human-in-the-loop approval

Controlled by `REQUIRE_APPROVAL` in `.env` (default `true`).

When `REQUIRE_APPROVAL=true`:

- Selected clips are cut into `clips/pending/<run_id>/` instead of `clips/`.
- The manifest gets `"approval_status": "pending"` and `"pending_dir": "<path>"`.
- Nothing is published to `clips/` until a human approves the run.
- **Cowork agent**: after a run finishes with `approval_status == "pending"`,
  read the manifest, summarize each selected video (title, channel, URL,
  view count, license) and its planned clips (start/end timestamps,
  duration, score), and notify the user. Do **not** run `--approve`
  yourself — wait for the user's reply.
  - User approves → run `python 