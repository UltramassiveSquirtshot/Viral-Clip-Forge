# Viral Clip Forge — Pipeline Instructions

## Execution Environment

This pipeline runs **locally on the Windows machine**, scheduled via the
Claude Code desktop app's local task scheduler. It cannot run in a cloud or
remote sandbox — it requires local Python 3.13, local FFmpeg, a local `.env`
file, and local disk for downloads and clips.

## Run Command

```
cd "C:\Users\Utente\Desktop\PROGETTI\Viral Clip Forge" && C:\Python313\python.exe main.py
```

## Schedule

Registered in the Claude Code **desktop app** local scheduler.
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
- Nothing is published to `clips/` until manually approved.

After a run finishes, check the manifest in `data/manifests/` to review what
was selected (title, channel, URL, view count, license, clip timestamps). Then:

- Approve → `C:\Python313\python.exe main.py --approve <run_id>`
- Reject  → `C:\Python313\python.exe main.py --reject <run_id>`
- List pending → `C:\Python313\python.exe main.py --list-pending`

When `REQUIRE_APPROVAL=false`, clips go straight to `clips/` and
`approval_status` is `"not_required"` — no manual step needed.

The plan is to keep `REQUIRE_APPROVAL=true` for the first several runs to
validate video/clip selection quality, then switch to `false` once satisfied.

## Success criteria

- Exit code 0
- `status` in latest manifest is `"completed"` or `"partial"`
- `clips_produced > 0` (may be 0 if no CC-BY videos found that day — normal)
- If `REQUIRE_APPROVAL=true` and `clips_produced > 0`, `approval_status` will
  be `"pending"` until approved/rejected — this is expected, not a failure.

## On failure

- **Quota exhausted**: API has 10,000 units/day. Next run will pick up where things stand.
- **No CC videos found**: Normal and expected. Logged as `skipped_license` in manifest.
- **FFmpeg error**: Clip is skipped, others continue. Check `logs/` for details.
- **Download error**: Video skipped, pipeline continues.

## Environment requirements

- Python 3.13 at `C:\Python313\python.exe`
- `.env` file with `YOUTUBE_API_KEY` set (copy `.env.example` to get started)
- FFmpeg 8.1.1 installed via winget (`winget install Gyan.FFmpeg`)
- Python packages: `pip install -r requirements.txt`
