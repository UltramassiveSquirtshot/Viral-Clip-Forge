"""
Local web dashboard for Viral Clip Forge.
Run with:  C:\Python313\python.exe run_web.py
Then open: http://localhost:5000
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import pip_system_certs.wrapt_requests  # noqa: F401
except ImportError:
    pass

from web import create_app

app = create_app()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    print(f"Viral Clip Forge dashboard → http://localhost:{port}")
    app.run(
        host="127.0.0.1",
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )
