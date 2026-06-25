from __future__ import annotations

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from . import jobs, state_reader

bp = Blueprint("main", __name__)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@bp.get("/")
def dashboard():
    status = state_reader.get_pipeline_status()
    analytics = state_reader.get_analytics_status()
    next_slots = state_reader.get_next_slots(10)
    recent_runs = state_reader.list_recent_runs(5)
    return render_template(
        "dashboard.html",
        status=status,
        analytics=analytics,
        next_slots=next_slots,
        recent_runs=recent_runs,
    )


@bp.get("/ranker")
def ranker_page():
    ranker = state_reader.get_ranker_status()
    themes = state_reader.RANKER_THEMES
    return render_template("ranker.html", ranker=ranker, themes=themes)


@bp.get("/ai-shorts")
def ai_shorts_page():
    aishorts = state_reader.get_aishorts_status()
    return render_template("ai_shorts.html", aishorts=aishorts)


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------

@bp.get("/stream/<job_id>")
def stream(job_id: str):
    return Response(
        stream_with_context(jobs.stream_lines(job_id)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# API — read-only
# ---------------------------------------------------------------------------

@bp.get("/api/status")
def api_status():
    return jsonify(state_reader.get_pipeline_status())


@bp.get("/api/analytics")
def api_analytics():
    return jsonify(state_reader.get_analytics_status())


@bp.get("/api/next")
def api_next():
    return jsonify(state_reader.get_next_slots(10))


@bp.get("/api/runs")
def api_runs():
    return jsonify(state_reader.list_recent_runs(10))


@bp.get("/api/ranker/pending")
def api_ranker_pending():
    return jsonify(state_reader.get_ranker_pending())


@bp.get("/api/ranker/status")
def api_ranker_status():
    return jsonify(state_reader.get_ranker_status())


@bp.get("/api/pool")
def api_pool():
    theme = request.args.get("theme", "")
    return jsonify(state_reader.get_ranker_pool(theme or None))


@bp.get("/api/ai-shorts/pending")
def api_aishorts_pending():
    return jsonify(state_reader.get_aishorts_status())


@bp.get("/api/ai-shorts/images")
def api_aishorts_images():
    return jsonify({"count": state_reader.get_aishorts_image_count()})


# ---------------------------------------------------------------------------
# API — actions (spawn subprocesses)
# ---------------------------------------------------------------------------

def _check_running(lock_name: str) -> Response | None:
    """Return a 409 response if the named process is already running."""
    from pathlib import Path
    lock = Path(__file__).parent.parent / "data" / f"{lock_name}.lock"
    if state_reader.is_running(lock):
        return jsonify({"error": "already_running", "lock": lock_name}), 409
    return None


@bp.post("/api/run")
def api_run():
    err = _check_running("pipeline")
    if err:
        return err
    job_id = jobs.spawn_pipeline()
    return jsonify({"job_id": job_id})


@bp.post("/api/analyze")
def api_analyze():
    err = _check_running("analytics")
    if err:
        return err
    job_id = jobs.spawn_analyze()
    return jsonify({"job_id": job_id})


@bp.post("/api/ranker/start")
def api_ranker_start():
    data = request.get_json(silent=True) or {}
    custom_query = str(data.get("query", "")).strip()
    err = _check_running("ranker")
    if err:
        return err
    job_id = jobs.spawn_ranker(custom_query=custom_query)
    return jsonify({"job_id": job_id})


@bp.post("/api/ranker/pick")
def api_ranker_pick():
    data = request.get_json(silent=True) or {}
    letter = str(data.get("letter", "")).strip().lower()[:1]
    if letter not in "abcde":
        return jsonify({"error": "invalid_letter"}), 400
    err = _check_running("ranker")
    if err:
        return err
    job_id = jobs.spawn_ranker_pick(letter)
    return jsonify({"job_id": job_id})


@bp.post("/api/ranker/confirm")
def api_ranker_confirm():
    data = request.get_json(silent=True) or {}
    timestamps = str(data.get("timestamps", "")).strip()
    err = _check_running("ranker")
    if err:
        return err
    job_id = jobs.spawn_ranker_confirm(timestamps)
    return jsonify({"job_id": job_id})


@bp.post("/api/ranker/reset")
def api_ranker_reset():
    """Delete ranker_pending.json to abort the current session."""
    pending = state_reader._RANKER_PENDING
    if pending.exists():
        pending.unlink()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "reason": "no pending session"})


@bp.post("/api/ai-shorts/start")
def api_aishorts_start():
    err = _check_running("aishorts")
    if err:
        return err
    job_id = jobs.spawn_aishorts()
    return jsonify({"job_id": job_id})


@bp.post("/api/ai-shorts/assemble")
def api_aishorts_assemble():
    data = request.get_json(silent=True) or {}
    run_id = str(data.get("run_id", "")).strip()
    if not run_id:
        return jsonify({"error": "missing_run_id"}), 400
    err = _check_running("aishorts")
    if err:
        return err
    job_id = jobs.spawn_aishorts_assemble(run_id)
    return jsonify({"job_id": job_id})


@bp.post("/api/ai-shorts/reset")
def api_aishorts_reset():
    """Delete aishorts_pending.json to abort the current session."""
    pending = state_reader._AISHORTS_PENDING
    if pending.exists():
        pending.unlink()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "reason": "no pending session"})
