# Viral Clip Forge — Pipeline Instructions

## Execution Environment

This pipeline runs **locally on the Windows machine**, scheduled via **Windows Task Scheduler**.
It cannot run in a cloud or remote sandbox — it requires local Python 3.13, local FFmpeg,
a local `.env` file, and local disk for downloads and clips.

## Run Command

```
cd "C:\Users\Utente\Desktop\PROGETTI\Viral Clip Forge" && C:\Python313\python.exe main.py
```

## Schedule

Registered in **Windows Task Scheduler** (not Claude Code / Cowork):
- Task name: `ViralClipForge`
- Cron equivalent: `0 18 * * *` — runs once daily at 20:00 Rome time (18:00 UTC summer / 19:00 UTC winter)
- Rationale: 1 run/day keeps clip production within the 12 algorithm-safe slots/week (4 days × 3 uploads/day)

## What this pipeline does

1. Fetches trending YouTube videos in **Tech** (category 28) niche
2. Filters by engagement score → picks top 3 per niche
3. Checks each video for **Creative Commons (CC-BY)** license via YouTube API + yt-dlp
4. Downloads only CC-BY licensed videos via yt-dlp
5. Detects exciting moments using FFmpeg scene detection + audio peak analysis
6. Cuts highlight clips (30–90s each, up to 3 per video) using FFmpeg
7. Uploads each clip to YouTube as a **scheduled private video** (auto-publishes at optimal time)
8. Sends a Telegram summary with clip titles, YouTube links, and scheduled publish times
9. Writes a JSON manifest to `data/manifests/`

## Upload Scheduling (Algorithm-Aware)

Clips are uploaded to YouTube and scheduled to publish on:
- **Days**: Tuesday, Wednesday, Thursday, Saturday
- **Times** (Rome local): 08:00, 13:00, 19:30
- **Max**: 3 uploads/day

Slot state is tracked in `data/schedule_state.json` — reruns don't double-book.

Per the YouTube Algorithm Guide 2026: max 3 Shorts/day, 3–4 days/week is the "safe limit"
for the algorithm. Videos are uploaded as `containsSyntheticMedia=true` (AI disclosure).

## Outputs

- **Clips**: `clips/{video_id}_clip{N}_{start}s-{end}s.mp4`
- **Manifest**: `data/manifests/YYYY-MM-DDTHH-MM/manifest.json`
- **Logs**: `logs/pipeline_YYYY-MM-DD.log`
- **State DB**: `data/state.db` (SQLite — tracks processed videos, prevents re-processing)
- **Schedule state**: `data/schedule_state.json` (tracks booked YouTube upload slots)

## No approval step

There is no human approval step. Clips go straight to `clips/` and are uploaded to YouTube
immediately after cutting. The scoring algorithm (views, engagement, recency) is the quality gate.

## Telegram Bot Commands

The Telegram listener (`run_listener.bat`) runs as a Windows Task Scheduler task at logon.
Send these commands to the bot:

- `/run` — trigger a pipeline run immediately (if not already running)
- `/status` — last run summary (clips produced, uploads scheduled)
- `/next` — next scheduled YouTube publish times

## First-Time YouTube Setup

Run once to authenticate with the YouTube channel:

```
C:\Python313\python.exe main.py --setup-youtube
```

This opens a browser, you log in with the channel's Google account, and the token is saved
to `data/youtube_token.json`. Subsequent runs use the refresh token silently.

Required files:
- `data/youtube_client_secret.json` — OAuth 2.0 Desktop credentials (already saved)
- `data/youtube_token.json` — created by `--setup-youtube`
- `.env`: `YOUTUBE_CHANNEL_ID=UCxxxxxx` (optional, for verification)

## Telegram Listener Setup

Register in Windows Task Scheduler to start at logon:

```
schtasks /Create /TN "ViralClipForge\TelegramListener" /TR "\"C:\Users\Utente\Desktop\PROGETTI\Viral Clip Forge\run_listener.bat\"" /SC ONLOGON /DELAY 0001:00 /F
```

## Success criteria

- Exit code 0
- `status` in latest manifest is `"completed"` or `"partial"`
- `clips_produced > 0` (may be 0 if no CC-BY videos found that day — normal)
- `uploads_scheduled > 0` if clips were produced (0 means YouTube token not set up yet)

## On failure

- **Quota exhausted**: API has 10,000 units/day. Upload costs ~100 units/video (as of Dec 2025) — up to 100 uploads/day within quota. Next run picks up normally.
- **No CC videos found**: Normal and expected. Logged as `skipped_license` in manifest.
- **FFmpeg error**: Clip is skipped, others continue. Check `logs/` for details.
- **Download error**: Video skipped, pipeline continues.
- **YouTube upload error**: Logged in manifest as `upload_status: "failed"`. Clip is kept locally in `clips/`.

## Environment requirements

- Python 3.13 at `C:\Python313\python.exe`
- `.env` file with `YOUTUBE_API_KEY` set (copy `.env.example` to get started)
- FFmpeg 8.1.1 installed via winget (`winget install Gyan.FFmpeg`)
- Python packages: `pip install -r requirements.txt`
