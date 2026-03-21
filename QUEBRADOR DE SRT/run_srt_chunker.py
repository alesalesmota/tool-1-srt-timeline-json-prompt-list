#!/usr/bin/env python3
from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from srt_chunker.app import app
from srt_chunker.config import DEFAULT_HOST, DEFAULT_PORT
from srt_chunker.runtime import find_free_port


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local SRT chunking tool.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    port = find_free_port(args.port)
    url = f"http://{args.host}:{port}"
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=port)


if __name__ == "__main__":
    main()
