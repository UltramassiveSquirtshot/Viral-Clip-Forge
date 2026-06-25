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
- `/analyze` — trigger a weekly analytics run (see Analytics section below)
- `/ranker` — **Step 1**: pop next script from Drive, search YouTube CC-BY candidates (top-5 per rank ordered by views), send proposals per rank, wait for /pick
- `/pick 1=b 2=c 3=b 4=d 5=a` — **Step 2**: download+cut chosen videos (one moment per rank), upload 6s preview clips to Drive, wait for /approve or /reject
- `/approve` — **Step 3**: compose all clips, upload final video to Drive (link via Telegram), save in `clips/` locally. **No YouTube upload** — upload manually from Drive.
- `/reject 2 4` — **Step 3 (partial)**: compose skipping ranks 2 and 4, upload Top-3 to Drive.
- `/pool [theme]` — browse global candidate pool for a theme (reuse videos found in past sessions)
- `/aishorts` — **AI Shorts Step 1**: pop next script from Drive, synthesize voice (edge-TTS), suggest image timestamps. Details in the web UI.
- `/assemble <RUN_ID>` — **AI Shorts Step 3**: download images from Drive, compose final video, upload to Drive.

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

## Ranking Shorts (ranker.py) — v2

A **second, independent content format** alongside the CC-BY clip pipeline. It produces
"Top 5" ranking compilation Shorts from YouTube CC-BY footage, with a 3-step Telegram
approval flow so you can verify each clip before the final video is assembled.

### Run command (step 1 only — then use Telegram)
```
cd "C:\Users\Utente\Desktop\PROGETTI\Viral Clip Forge" && C:\Python313\python.exe main.py --ranker
```
Equivalent to sending `/ranker` in Telegram.

### 3-step Telegram flow
1. `/ranker` — pops next script from Drive, searches YouTube CC-BY top-5 per rank (ordered by views), sends proposals. Candidates show use count `[USATO×N]` if already used in a previous ranking.
2. `/pick 1=b 2=c 3=b 4=d 5=a` — downloads+cuts chosen videos (best 6s moment not yet used), uploads clip previews to Drive (`ViralClipForge/clip_previews/RUN_ID/`), sends Drive links per clip.
3. `/approve` or `/reject 2 4` — composes approved clips, uploads final video to Drive (`ViralClipForge/finals/`), saves locally in `clips/`. **No YouTube upload** — upload manually.

### How footage & music are sourced
- **Footage**: YouTube Data API with `videoLicense=creativeCommon`. Candidates ordered by view count. Same video can be reused across multiple rankings — the pipeline tracks used time-ranges in `data/ranker_pool.json` and cuts a different moment each time.
- **Pool**: `data/ranker_pool.json` — global candidate pool per theme. Candidates found in one session are reusable without re-searching. Browse with `/pool [theme]`.
- **Pending state**: `data/ranker_pending.json` — tracks current 3-step session. Deleted on `/approve`/`/reject`.
- **Music**: not added in v2 (Pixabay integration removed). Final video uses original clip audio.
- **YouTube upload**: NEVER automatic. User uploads manually from Drive.

### Scriptwriting workflow (no LLM API key)
The title + rank labels + per-rank stock-search queries are NOT generated by an LLM at
runtime. You generate them by chatting with **any** LLM and uploading the result to Drive:

1. Open the prompt file on Drive (lorenzotervel@gmail.com):
   **`ViralClipForge_Ranker_ScriptPrompt.txt`**. Copy the prompt block, paste into any
   AI chat (Claude / ChatGPT / Gemini).
2. The AI returns a single JSON object. Save it as **`ranker_scripts.json`** and upload
   it to the **`ViralClipForge`** folder on Drive.
3. Each `--ranker` run downloads the file, consumes (pops + removes) the first `videos`
   entry, and builds one Short from it. When the queue is empty the run exits cleanly
   ("no scripts queued"). Queue several entries to produce several videos over multiple runs.

Canonical file shape:
```json
{"videos": [
  {"theme": "lucky escapes",
   "title": "TOP 5 LUCKIEST PEOPLE EVER",
   "labels": ["Poor Driving","Perfect Timing","Close Call","Lucky Save","Miracle Escape"],
   "search_queries": ["dashcam near miss","pedestrian close call","car accident near miss","lucky escape","amazing survival moment"]}
]}
```

### First-time Drive setup (ranker's own OAuth — separate from analytics)
The ranker reads a **hand-uploaded** file, so it needs the full `drive` scope (the
analytics `drive.file` scope can only see files the app itself created). It uses its own
OAuth client (`data/ranker_client_secret.json`) and token (`data/ranker_gdrive_token.json`),
independent of `analyze.py`. Run once:
```
C:\Python313\python.exe main.py --setup-gdrive
```

### Success criteria
- `/ranker` exits 0 with Telegram messages per rank and `data/ranker_pending.json` step=`pending_pick`.
- `/pick` exits 0 with Drive links per clip preview and step=`pending_approve`.
- `/approve` exits 0 with final video on Drive and `data/ranker_pending.json` deleted.
- Final video saved in `clips/` locally.
- `uploads_scheduled` is always 0 for the ranker (YouTube upload is manual).

## AI Shorts (aishorts) — fully-synthetic format

A **third, independent content format**: Shorts made entirely from AI-generated
images + an edge-TTS narrator voice — no third-party footage, no paid APIs. The
**web UI (`/ai-shorts`)** is the primary control surface; Telegram only sends a
compact notification + link. Cost ~€0 (only new dependency: `edge-tts`).

### How it works — 2 steps + a manual image step

1. **`/aishorts`** (web UI button, Telegram `/aishorts`, or scheduled) — pops the
   next script from the Drive queue (`ai_shorts_scripts.json`), synthesizes the
   voice per beat with **edge-TTS** (gets per-word timings), builds the real
   timeline, verifies total **< 60s**, creates an empty Drive folder
   `ViralClipForge/ai_shorts/{RUN_ID}/images/`, saves `data/aishorts_pending.json`
   (step=`pending_images`), and notifies Telegram compactly. The web UI then shows
   each scene's **Leonardo prompt (copyable)** and its **suggested filename**
   (`<timestamp>.png`).

2. **You generate the images** on Leonardo.ai, name each one by the **second at
   which it should appear** (e.g. `0.0.png`, `3.4.png`, `7.1.png` — use the
   suggested timestamps), and upload them to the run's Drive folder. You may add
   extra images at intermediate timestamps.

3. **`/assemble <RUN_ID>`** (web UI button or Telegram) — downloads the images
   (ordered by the timestamp in the filename), builds **karaoke word-by-word
   captions** (.ass) from the saved timings, composes with FFmpeg: per-image
   **Ken Burns** (bright/punchy zoom) lasting until the next image's timestamp,
   concatenated **single narration track** (sync stays correct even with extra
   images), burned captions + a bright title banner. Saves to `clips/` and uploads
   to `ViralClipForge/ai_shorts/finals/`. **No YouTube upload — upload manually.**

Timing note: image filenames = timestamps, decoupled from beats. The suggested
timestamps come from the real TTS durations, so using them gives perfect sync.

### Style & volume defaults
- Bright / punchy "MrBeast-style": yellow title banner, saturated karaoke
  highlight, noticeable Ken Burns zoom. Constants live in `aishorts/composer.py`.
- Target **3 AI Shorts/day** (aligned with the algorithm guide's max 3 Shorts/day):
  the Cowork agent queues 3 entries/day; each `/aishorts` consumes one.
- Duration: no hard target, capped < 60s; the pipeline warns past ~58s.

### Script queue file (`ai_shorts_scripts.json` on Drive)
Produced by the Cowork agent (WebSearch trends → copy a viral structure → write
narration + Leonardo prompts) and uploaded to the `ViralClipForge` Drive folder.
Same full-`drive`-scope OAuth as the ranker (`--setup-gdrive`). Shape:

```json
{"videos": [
  {"theme": "space facts",
   "title": "5 THINGS NASA HID FROM YOU",
   "hook": "What you're about to see will change how you look at the sky.",
   "beats": [
     {"narration": "In 1977, a signal arrived that no one could explain.",
      "leonardo_prompt": "cinematic deep space radio telescope at night, vivid saturated colors, high contrast, dramatic lighting, 9:16"},
     {"narration": "...", "leonardo_prompt": "..."}
   ]}
]}
```
The agent does **not** set timestamps (one prompt per beat is ideal); the pipeline
computes and suggests them after TTS. Each `/aishorts` consumes one entry (queue).
The prompt template lives on Drive as `ViralClipForge_AiShorts_ScriptPrompt.txt`.

### State & success criteria
- `data/aishorts_pending.json` — current session (step, scenes, timeline, mp3 paths).
- `data/aishorts.lock` — PID lock while a step runs.
- `/aishorts` exits 0 with step=`pending_images`, MP3s generated, total <60s.
- `/assemble` exits 0 with the final video in `clips/`, a Drive link, pending deleted.
- `uploads_scheduled` is always 0 (YouTube upload is manual).

### Run commands (then use the web UI / Telegram)
```
C:\Python313\python.exe main.py --aishorts
C:\Python313\python.exe main.py --aishorts-assemble <RUN_ID>
```

## Analytics Feedback Loop

A separate weekly process that reads YouTube Analytics data for uploaded clips, identifies
what's working, and auto-applies config tuning to the next pipeline run.

### Run manually (test first):
```
C:\Python313\python.exe analyze.py
```

### One-time Google Drive setup (lorenzotervel@gmail.com):
```
C:\Python313\python.exe analyze.py --setup-gdrive
```

### Schedule (Windows Task Scheduler — run after testing):
```
schtasks /Create /TN "ViralClipForge\Analytics" /TR "C:\Python313\python.exe \"C:\Users\Utente\Desktop\PROGETTI\Viral Clip Forge\analyze.py\"" /SC WEEKLY /D MON /ST 09:00 /F
```

### What it produces:
- `data/analytics_reports/YYYY-MM-DD_analytics.md` — human-readable report (tables, keyword suggestions)
- `data/analytics_reports/YYYY-MM-DD_context.json` — machine-readable dump (paste into Claude Code for reasoning)
- `data/analytics_insights.json` — written manually after your review; auto-loaded by pipeline at next run
- Telegram message with Google Drive links to both files
- Logs: `logs/analytics_YYYY-MM-DD.log`

### Feedback loop workflow:
1. Run `analyze.py` (manually or via `/analyze` Telegram command)
2. Open the Drive link on mobile or desktop — review `_analytics.md`
3. Optionally paste `_context.json` into a Claude Code session for deeper reasoning
4. Claude Code writes `data/analytics_insights.json` after you validate the suggestions
5. Next `main.py` run auto-loads the insights: overrides `scene_threshold`, `preferred_clip_duration`, keywords, slot priorities

### What auto-applies from analytics_insights.json:
- `config_overrides`: `scene_threshold`, `audio_peak_percentile`, `preferred_clip_duration`, `min_views`, `upload_slot_priorities`
- `keyword_overrides.tech`: completely replaces `search_keywords` and `cc_search_keywords` for the tech niche
- Every override is logged: "Analytics override: scene_threshold 0.40 → 0.35"
- Missing or malformed file → silently skipped, pipeline uses defaults

### Knowledge base (static, human-editable):
- `docs/analytics/strategies.md` — slot optimization rules
- `docs/analytics/clip_scoring.md` — how to tune FFmpeg thresholds from retention data
- `docs/analytics/content_signals.md` — keyword performance benchmarks
- `docs/analytics/data_interpretation.md` — how to read YouTube Analytics metrics
- `docs/analytics/algorithm_signals.md` — how impressions grow into suggested traffic

### Analytics OAuth (separate from YouTube pipeline):
Analytics uses its own OAuth client to keep credentials isolated from the upload pipeline.

Required files:
- `data/analytics_client_secret.json` — new OAuth 2.0 Desktop client from Google Cloud Console
- `data/analytics_token.json` — created by `--setup-analytics`

Setup steps:
1. Google Cloud Console → APIs & Services → Enable **YouTube Analytics API**
2. Credentials → Create OAuth 2.0 Desktop client (or reuse existing) → Download JSON
3. Save as `data/analytics_client_secret.json`
4. Run: `C:\Python313\python.exe analyze.py --setup-analytics`

## Environment requirements

- Python 3.13 at `C:\Python313\python.exe`
- `.env` file with `YOUTUBE_API_KEY` set (copy `.env.example` to get started)
- FFmpeg 8.1.1 installed via winget (`winget install Gyan.FFmpeg`)
- Python packages: `pip install -r requirements.txt`
