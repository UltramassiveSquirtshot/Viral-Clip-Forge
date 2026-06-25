"""
In-process job registry for subprocess execution and SSE streaming.
Thread-safe: deque.append is atomic under the GIL; dict only receives new keys.
"""

from __future__ import annotations

import collections
import subprocess
import threading
import time
import uuid
from pathlib import Path

_jobs: dict[str, dict] = {}

PROJECT_ROOT = Path(__file__).parent.parent
PYTHON = r"C:\Python313\python.exe"


def spawn(cmd: list[str]) -> str:
    job_id = str(uuid.uuid4())
    buf: collections.deque[str] = collections.deque(maxlen=500)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    _jobs[job_id] = {"proc": proc, "lines": buf, "done": False, "returncode": None}
    t = threading.Thread(target=_reader, args=(job_id, proc, buf), daemon=True)
    t.start()
    return job_id


def _reader(job_id: str, proc: subprocess.Popen, buf: collections.deque) -> None:
    for line in proc.stdout:
        buf.append(line.rstrip())
    proc.wait()
    _jobs[job_id]["done"] = True
    _jobs[job_id]["returncode"] = proc.returncode


def stream_lines(job_id: str):
    """Generator that yields SSE-formatted lines until the job is done."""
    if job_id not in _jobs:
        yield "data: [ERROR: unknown job]\n\n"
        return

    sent = 0
    while True:
        lines = list(_jobs[job_id]["lines"])
        for line in lines[sent:]:
            yield f"data: {line}\n\n"
            sent += 1

        if _jobs[job_id]["done"] and sent >= len(_jobs[job_id]["lines"]):
            rc = _jobs[job_id].get("returncode", 0)
            yield f"data: [DONE rc={rc}]\n\n"
            break
        time.sleep(0.2)


def get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def spawn_pipeline() -> str:
    return spawn([PYTHON, str(PROJECT_ROOT / "main.py")])


def spawn_analyze() -> str:
    return spawn([PYTHON, str(PROJECT_ROOT / "analyze.py")])


def spawn_ranker(custom_query: str = "") -> str:
    cmd = [PYTHON, str(PROJECT_ROOT / "main.py"), "--ranker"]
    if custom_query.strip():
        cmd.append(custom_query.strip())
    return spawn(cmd)


def spawn_ranker_pick(letter: str) -> str:
    return spawn([PYTHON, str(PROJECT_ROOT / "main.py"), "--ranker-pick", letter])


def spawn_ranker_confirm(timestamps: str = "") -> str:
    cmd = [PYTHON, str(PROJECT_ROOT / "main.py"), "--ranker-confirm"]
    if timestamps.strip():
        cmd.append(timestamps.strip())
    return spawn(cmd)


def spawn_aishorts() -> str:
    return spawn([PYTHON, str(PROJECT_ROOT / "main.py"), "--aishorts"])


def spawn_aishorts_assemble(run_id: str) -> str:
    return spawn([PYTHON, str(PROJECT_ROOT / "main.py"), "--aishorts-assemble", run_id])
