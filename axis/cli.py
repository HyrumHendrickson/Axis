"""`axis` starts the local web app and opens it in the browser."""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from typing import List, Optional

from . import __version__, server

DEFAULT_PORT = 8765


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="axis",
        description="Start the Axis web app on this computer and open it in your browser. "
        "Axis uses a model you have already started with Ollama or llama.cpp.",
    )
    p.add_argument("--version", action="version", version=f"axis {__version__}")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port for the web app (default {DEFAULT_PORT})")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    p.add_argument("--url", help="model server address (default: auto-detect; env: AXIS_URL)")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        print("Error: --port must be between 0 and 65535.", file=sys.stderr)
        return 2

    if args.port and server.find_running(args.port):
        url = f"http://127.0.0.1:{args.port}"
        print(f"Axis is already running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    try:
        httpd = server.make_server(args.port, args.url)
    except OSError as exc:
        print(f"Error: could not start the web app: {exc}", file=sys.stderr)
        return 1

    print(f"Axis is running at {httpd.url}")
    print("Keep this window open while you use Axis. Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(httpd.url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nAxis stopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
