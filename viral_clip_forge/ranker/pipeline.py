"""
Ranking-Shorts pipeline — v3.

Flusso in 3 step via Telegram (comando /pick riusato):

Step 1  /ranker
  → manda 5 temi fissi (a–e) via Telegram
  → salva data/ranker_pending.json (step=pending_theme)
  → esce

Step 2  /pick a   ← scelta tema
  → legge pending.json (step=pending_theme)
  → cerca YouTube CC-BY per la query del tema scelto
  → manda 5 candidati ordinati per views
  → aggiorna pending.json (step=pending_pick)
  → esce

Step 3  /pick b   ← scelta video
  → legge pending.json (step=pending_pick)
  → scarica il video scelto (cache in downloads/ranker/sources/)
  → taglia i 5 migliori momenti da 6s (no overlap)
  → monta con FFmpeg (normalize + concat + musica Pixabay, NIENTE testi)
  → carica su Drive (ViralClipForge/finals/)
  → manda link Telegram
  → cancella pending.json
  → esce

Niente labels, niente titoli nel video.
L'utente aggiunge testi in CapCut e carica su YouTube manualmente.
Modifiche SOLO in viral_clip_forge/ranker/ — il flusso CC-BY non viene toccato.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ..config import AppConfig
from ..notifier import send_telegram
from ..utils import ensure_dirs, generate_run_id, get_logger, setup_logging
from .composer import compose
from .config import build_ranker_config
from . import yt_source, music_source

log = get_logger()

_LETTERS = "abcde"

# Hardcoded theme list — edit names/queries here without touching the logic.
_THEMES: list[tuple[str, str]] = [
    ("Dashcam & Close Calls",   "dashcam close call top 20 compilation"),
    ("Animal Attacks",          "animal attack top 20 compilation"),
    ("Lucky Escapes",           "lucky escape top 20 compilation"),
    ("Engineering Fails",       "engineering fail top 20 compilation"),
    ("Natural Disasters",       "natural disaster top 20 compilation"),
]


# ---------------------------------------------------------------------------
# Drive helpers (only used in step 3)
# ---------------------------------------------------------------------------

def _drive_service(cfg):
    from . import gdrive
    return gdrive.get_service(cfg.gdrive_token_path, cfg.gdrive_client_secret_path)


def _upload_to_drive(cfg, file_path: Path, folder_name: str) -> str:
    """Upload file to ViralClipForge/folder_name on Drive. Returns public URL."""
    from googleapiclient.http import MediaFileUpload
    from ..analytics.uploader_gdrive import _get_or_create_folder

    service = _drive_service(cfg)
    parent_id = _get_or_create_folder(service, "ViralClipForge")
    folder_id = _get_or_create_folder(service, folder_name, parent_id=parent_id)

    mime = "video/mp4" if file_path.suffix.lower() == ".mp4" else "application/octet-stream"
    media = MediaFileUpload(str(file_path), mimetype=mime, resumable=False)
    metadata = {"name": file_path.name, "parents": [folder_id]}
    uploaded = service.files().create(body=metadata, media_body=media, fields="id").execute()
    file_id = uploaded["id"]
    service.permissions().create(fileId=file_id, body={"role": "reader", "type": "anyone"}).execute()
    url = f"https://drive.google.com/file/d/{file_id}/view"
    log.info("[ranker] Uploaded %s → %s", file_path.name, url)
    return url


# ---------------------------------------------------------------------------
# Pending state helpers
# ---------------------------------------------------------------------------

def _load_pending(cfg) -> dict | None:
    if cfg.pending_path.exists():
        try:
            return json.loads(cfg.pending_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _save_pending(cfg, data: dict) -> None:
    tmp = cfg.pending_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, cfg.pending_path)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_views(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n)


def _fmt_duration(sec: int) -> str:
    if not sec:
        return ""
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# Step 1 — Propose themes (or jump straight to search with a custom query)
# ---------------------------------------------------------------------------

def run_ranker_propose(config: AppConfig, custom_query: str = "") -> str:
    """
    If custom_query is provided, skip theme selection and jump directly to step 2
    (search YouTube CC-BY for that query).
    Otherwise send 5 hardcoded themes via Telegram, save pending with step=pending_theme.
    Returns 'pending_theme' | 'pending_pick' | 'blocked' | 'no_results'.
    """
    setup_logging(config.log_dir, "ranker-propose")
    log.info("=== Ranker PROPOSE custom_query=%r ===", custom_query)

    cfg = build_ranker_config(config)
    ensure_dirs(cfg.output_dir, cfg.download_dir, config.state_db_path.parent, config.log_dir)

    # Guard: don't overwrite an existing pending session
    if cfg.pending_path.exists():
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            "⚠️ <b>Ranker</b>: sessione già in corso.\n"
            "Rispondi con <code>/pick a</code>–<code>e</code> per continuare,\n"
            "oppure elimina <code>data/ranker_pending.json</code> per ricominciare.",
        )
        return "blocked"

    run_id = generate_run_id()

    # If a custom query is given, skip step 1 entirely and search immediately
    if custom_query.strip():
        return _search_and_propose(config, cfg, run_id, custom_query.strip(), custom_query.strip())

    lines = ["🎬 <b>Scegli l'argomento:</b>"]
    for i, (theme_name, _) in enumerate(_THEMES):
        lines.append(f"{_LETTERS[i]}) {theme_name}")
    lines.append("\nRispondi: <code>/pick a</code> (o b, c, d, e)")
    lines.append("Oppure: <code>/ranker &lt;query libera&gt;</code> per cercare direttamente")
    send_telegram(config.telegram_bot_token, config.telegram_chat_id, "\n".join(lines))

    _save_pending(cfg, {"run_id": run_id, "step": "pending_theme"})

    log.info("[ranker] Theme proposals sent for run %s", run_id)
    return "pending_theme"


def _search_and_propose(config: AppConfig, cfg, run_id: str, theme_name: str, query: str) -> str:
    """Search YouTube CC-BY for query, send 5 candidates, save pending at step=pending_pick."""
    log.info("[ranker] Searching CC-BY for theme=%r query=%r", theme_name, query)
    send_telegram(
        config.telegram_bot_token, config.telegram_chat_id,
        f"🔍 Cerco video CC-BY per <b>{theme_name}</b>…",
    )

    candidates = yt_source.search_candidates(config.youtube_api_key, query, top_n=5)
    if not candidates:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"❌ Nessun video CC-BY trovato per: <i>{query}</i>\n"
            "Riprova con <code>/ranker</code> per scegliere un altro tema.",
        )
        return "no_results"

    lines = [f"🔍 <b>{theme_name}</b> — scegli il video:"]
    for i, c in enumerate(candidates):
        ltr = _LETTERS[i]
        dur_str = f"  {_fmt_duration(c.get('duration_sec', 0))}" if c.get("duration_sec") else ""
        views_str = _fmt_views(c.get("views", 0))
        title = c["title"][:60] + ("…" if len(c["title"]) > 60 else "")
        lines.append(f"{ltr}) {title}\n   {views_str} views{dur_str}  youtu.be/{c['id']}")
    lines.append("\nRispondi: <code>/pick b</code> (o a, c, d, e)")
    send_telegram(config.telegram_bot_token, config.telegram_chat_id, "\n".join(lines))

    _save_pending(cfg, {
        "run_id": run_id,
        "step": "pending_pick",
        "theme": theme_name,
        "query": query,
        "candidates": candidates,
    })

    log.info("[ranker] Video proposals sent (%d candidates)", len(candidates))
    return "pending_pick"


# ---------------------------------------------------------------------------
# Step 2 — Theme chosen → search YouTube, propose 5 videos
# ---------------------------------------------------------------------------

def run_ranker_pick_theme(config: AppConfig, letter: str) -> str:
    """
    Reads pending (step=pending_theme), picks theme by letter, searches YouTube CC-BY,
    sends 5 candidates, updates pending to step=pending_pick.
    Returns 'pending_pick' | 'error' | 'no_results'.
    """
    setup_logging(config.log_dir, "ranker-pick-theme")
    log.info("=== Ranker PICK THEME letter='%s' ===", letter)

    cfg = build_ranker_config(config)
    pending = _load_pending(cfg)

    letter = letter.strip().lower()[:1]
    if letter not in _LETTERS:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"⚠️ Lettera non valida: <code>{letter}</code>. Usa a, b, c, d o e.",
        )
        return "error"

    idx = _LETTERS.index(letter)
    if idx >= len(_THEMES):
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"⚠️ Lettera <code>{letter}</code> fuori range.",
        )
        return "error"

    theme_name, query = _THEMES[idx]
    return _search_and_propose(config, cfg, pending.get("run_id", generate_run_id()), theme_name, query)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_ts(sec: float) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _parse_single_ts(s: str) -> float:
    """Parse a single timestamp token (M:SS, H:MM:SS, or raw seconds) to float seconds."""
    parts = s.split(":")
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def _parse_timestamps(text: str) -> list[float]:
    """Parse a string of timestamps (M:SS, H:MM:SS or raw seconds) into a list of floats."""
    result = []
    for token in text.split():
        token = token.strip(",;")
        if not token:
            continue
        try:
            result.append(_parse_single_ts(token))
        except ValueError:
            log.warning("[ranker] Could not parse timestamp token: %r", token)
    return result


def _parse_segments(text: str) -> list[tuple[float, float]]:
    """Parse 'START-END START-END ...' into a list of (start, end) tuples (seconds).
    Accepts M:SS, H:MM:SS, or raw seconds for each part.
    Example: '0:32-0:48 1:27-1:45' → [(32.0, 48.0), (87.0, 105.0)]
    """
    result = []
    for token in text.split():
        token = token.strip(",;")
        if not token:
            continue
        # Find the dash separating start from end.
        # Timestamps use ':' not '-', so the only '-' is the separator.
        idx = token.find("-")
        if idx <= 0:
            log.warning("[ranker] No '-' separator found in segment token: %r", token)
            continue
        try:
            start = _parse_single_ts(token[:idx])
            end = _parse_single_ts(token[idx + 1:])
            if end > start:
                result.append((start, end))
            else:
                log.warning("[ranker] Segment end <= start, skipping: %r", token)
        except (ValueError, IndexError):
            log.warning("[ranker] Could not parse segment token: %r", token)
    return result


# ---------------------------------------------------------------------------
# Step 3 — Video chosen → download, analyse, propose timestamps
# ---------------------------------------------------------------------------

def run_ranker_pick_video(config: AppConfig, letter: str, pending: dict) -> str:
    """
    Downloads chosen video, analyses it, proposes N timestamp suggestions via Telegram.
    Does NOT cut or compose — waits for /confirm.
    Returns 'pending_confirm' | 'failed' | 'error'.
    """
    setup_logging(config.log_dir, "ranker-pick-video")
    log.info("=== Ranker PICK VIDEO letter='%s' ===", letter)

    cfg = build_ranker_config(config)

    letter = letter.strip().lower()[:1]
    if letter not in _LETTERS:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"⚠️ Lettera non valida: <code>{letter}</code>. Usa a, b, c, d o e.",
        )
        return "error"

    candidates = pending.get("candidates", [])
    idx = _LETTERS.index(letter)
    if idx >= len(candidates):
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"⚠️ Lettera <code>{letter}</code> fuori range (solo {len(candidates)} candidati).",
        )
        return "error"

    chosen = candidates[idx]
    video_id = chosen["id"]
    theme = pending.get("theme", "ranking")
    query = pending.get("query", theme)

    send_telegram(
        config.telegram_bot_token, config.telegram_chat_id,
        f"⏳ Scarico e analizzo <b>{chosen['title'][:60]}</b>…\n"
        "Riceverai i timestamp suggeriti tra poco.",
    )

    src_path, segments = yt_source.download_and_suggest_timestamps(cfg, config, video_id, n=5)

    if src_path is None:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"❌ Download fallito per <code>{video_id}</code>. Prova un altro video.",
        )
        return "failed"

    if not segments:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"❌ Analisi fallita o video troppo corto (<code>{video_id}</code>). Prova un altro video.",
        )
        return "failed"

    # Build Telegram message with clickable YouTube links
    title_short = chosen['title'][:55] + ("…" if len(chosen['title']) > 55 else "")
    lines = [
        f"🎬 <b>{theme} — {title_short}</b>",
        f"youtu.be/{video_id}",
        "",
        "Segmenti suggeriti (clicca l'inizio per verificare):",
    ]
    for i, (start, end) in enumerate(segments, start=1):
        start_int = int(start)
        lines.append(f"{i}) youtu.be/{video_id}?t={start_int}   →  {_fmt_ts(start)}–{_fmt_ts(end)}")

    lines += [
        "",
        "Rispondi:",
        "<code>/confirm</code>  → usa questi segmenti",
        "<code>/confirm 0:32-0:48 1:27-1:45 3:10-3:25</code>  → coppie personalizzate (quante vuoi)",
    ]
    send_telegram(config.telegram_bot_token, config.telegram_chat_id, "\n".join(lines))

    _save_pending(cfg, {
        "run_id": pending.get("run_id", generate_run_id()),
        "step": "pending_confirm",
        "video_id": video_id,
        "video_title": chosen["title"],
        "src_path": str(src_path),
        "theme": theme,
        "query": query,
        "suggested_timestamps": [s[0] for s in segments],
        "suggested_segments": [[s[0], s[1]] for s in segments],
    })

    log.info("[ranker] Timestamp proposals sent for %s: %s", video_id, segments)
    return "pending_confirm"


# ---------------------------------------------------------------------------
# Step 4 — Confirm timestamps → cut, compose, upload Drive
# ---------------------------------------------------------------------------

def run_ranker_confirm(config: AppConfig, args: str) -> str:
    """
    Cut clips at the given (or suggested) timestamps, compose, upload to Drive.
    args: space-separated timestamps string, or empty to use suggested ones.
    Returns 'completed' | 'failed' | 'error'.
    """
    setup_logging(config.log_dir, "ranker-confirm")
    log.info("=== Ranker CONFIRM args=%r ===", args)

    cfg = build_ranker_config(config)
    pending = _load_pending(cfg)

    if not pending or pending.get("step") != "pending_confirm":
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            "⚠️ Nessuna sessione in attesa di conferma.\nInvia <code>/ranker</code> per iniziare.",
        )
        return "error"

    video_id = pending["video_id"]
    src_path_str = pending.get("src_path", "")
    theme = pending.get("theme", "ranking")
    query = pending.get("query", theme)

    # Parse segments: user-supplied or from pending
    if args.strip():
        segments = _parse_segments(args)
        if not segments:
            send_telegram(
                config.telegram_bot_token, config.telegram_chat_id,
                "⚠️ Nessun segmento valido trovato.\n"
                "Esempio: <code>/confirm 0:32-0:48 1:27-1:45 3:10-3:25</code>",
            )
            return "error"
        log.info("[ranker] Using user segments: %s", segments)
    else:
        raw = pending.get("suggested_segments")
        if raw:
            segments = [(float(s[0]), float(s[1])) for s in raw]
        else:
            # Retrocompatibilità: pending vecchio con solo suggested_timestamps
            cfg_tmp = build_ranker_config(config)
            default_dur = cfg_tmp.clip_seconds
            segments = [(float(ts), float(ts) + default_dur) for ts in pending.get("suggested_timestamps", [])]
        log.info("[ranker] Using suggested segments: %s", segments)

    if not segments:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            "⚠️ Nessun segmento disponibile. Torna indietro con <code>/ranker</code>.",
        )
        return "error"

    # Resolve source path
    from pathlib import Path as _Path
    src_path = _Path(src_path_str) if src_path_str and _Path(src_path_str).exists() else None
    if src_path is None:
        # Try to find it in cache
        src_dir = cfg.download_dir / "sources"
        existing = list(src_dir.glob(f"{video_id}.*"))
        src_path = existing[0] if existing else None

    if src_path is None:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"❌ File sorgente non trovato per <code>{video_id}</code>.\n"
            "Riparti da <code>/ranker</code>.",
        )
        return "failed"

    send_telegram(
        config.telegram_bot_token, config.telegram_chat_id,
        f"✂️ Taglio {len(segments)} clip da <b>{pending.get('video_title', video_id)[:55]}</b>…",
    )

    clips = yt_source.cut_clips_at_timestamps(cfg, config, src_path, segments)
    if not clips:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            "❌ Taglio fallito per tutti i timestamp. Controlla i valori e riprova con /confirm.",
        )
        return "failed"

    log.info("[ranker] %d clips cut", len(clips))

    # Fetch music from Pixabay (optional)
    music = music_source.fetch_background_music(cfg, theme=query)

    # Compose
    comp = compose(cfg, clips, title="", labels=[], music=music)
    if not comp.success or not comp.output_path:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            f"❌ Composizione fallita: {comp.error}",
        )
        return "failed"

    # Copy to clips/
    ensure_dirs(cfg.output_dir)
    local_path = cfg.output_dir / comp.output_path.name
    if comp.output_path != local_path:
        shutil.copy2(comp.output_path, local_path)

    # Upload to Drive
    drive_url = None
    try:
        drive_url = _upload_to_drive(cfg, local_path, "finals")
    except Exception as exc:
        log.error("[ranker] Drive upload failed: %s", exc)

    # Clean up pending
    cfg.pending_path.unlink(missing_ok=True)

    # Notify
    size_mb = local_path.stat().st_size / 1_048_576
    seg_str = "  ".join(f"{_fmt_ts(s)}–{_fmt_ts(e)}" for s, e in segments)
    lines = [
        f"✅ <b>Ranking Short pronto</b>  ({comp.n_segments} clip · {size_mb:.1f} MB)",
        f"Segmenti: {seg_str}",
    ]
    if drive_url:
        lines.append(f"<a href=\"{drive_url}\">▶ Apri su Drive</a>")
    else:
        lines.append(f"Salvato: <code>{local_path.name}</code>")
    lines.append("Aggiungi i testi in CapCut e carica su YouTube quando vuoi.")
    send_telegram(config.telegram_bot_token, config.telegram_chat_id, "\n".join(lines))

    log.info("=== Ranker CONFIRM completed | segments=%d ===", comp.n_segments)
    return "completed"


# ---------------------------------------------------------------------------
# Public dispatcher — main.py --ranker-pick calls this
# ---------------------------------------------------------------------------

def run_ranker_pick(config: AppConfig, letter: str) -> str:
    """
    Dispatcher: reads pending.json to decide which step to run.
    - step=pending_theme  → run_ranker_pick_theme (step 2)
    - step=pending_pick   → run_ranker_pick_video (step 3)
    - no pending          → error message
    """
    cfg = build_ranker_config(config)
    pending = _load_pending(cfg)

    if not pending:
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            "⚠️ Nessuna sessione ranker in corso.\nInvia <code>/ranker</code> per iniziare.",
        )
        return "error"

    step = pending.get("step")
    if step == "pending_theme":
        return run_ranker_pick_theme(config, letter)
    if step == "pending_pick":
        return run_ranker_pick_video(config, letter, pending)
    if step == "pending_confirm":
        send_telegram(
            config.telegram_bot_token, config.telegram_chat_id,
            "⚠️ Stai aspettando di confermare i timestamp.\n"
            "Usa <code>/confirm</code> per procedere con i suggeriti, "
            "o <code>/confirm 0:32 1:45 3:10</code> per personalizzarli.",
        )
        return "error"

    send_telegram(
        config.telegram_bot_token, config.telegram_chat_id,
        f"⚠️ Stato sessione non riconosciuto: <code>{step}</code>.\n"
        "Elimina <code>data/ranker_pending.json</code> e riprova con <code>/ranker</code>.",
    )
    return "error"


# ---------------------------------------------------------------------------
# Legacy entry point — main.py --ranker calls this
# ---------------------------------------------------------------------------

def run_ranker_pipeline(config: AppConfig, custom_query: str = "") -> object:
    """Called by main.py --ranker [query]. Runs step 1 (theme proposal or direct search)."""
    from dataclasses import dataclass, field as _field

    @dataclass
    class _Result:
        status: str
        clips_produced: int = 0
        uploads_scheduled: int = 0
        errors: list = _field(default_factory=list)
        run_id: str = ""
        started_at: str = ""
        finished_at: str = ""

    status = run_ranker_propose(config, custom_query=custom_query)
    return _Result(status=status)
