"""python -m web — a local UI server on 127.0.0.1:8765 and a browser tab."""
import argparse
import os
import threading
import webbrowser

import uvicorn

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--port", type=int, default=8765)
# in a container — 0.0.0.0 (the port is published only on 127.0.0.1, see docker-compose.yml)
parser.add_argument("--host", default=os.environ.get("JOBHUNTER_HOST", "127.0.0.1"))
parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
args = parser.parse_args()
if not args.no_browser:
    threading.Timer(1.5, webbrowser.open, [f"http://127.0.0.1:{args.port}/"]).start()
# local address only: reports contain mail, applications and contacts
uvicorn.run("web.app:app", host=args.host, port=args.port, log_level="warning")
