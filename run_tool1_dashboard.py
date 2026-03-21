#!/usr/bin/env python3
from __future__ import annotations

import threading
import webbrowser

import uvicorn

from tool1_dashboard.app import app
from tool1_dashboard.config import DEFAULT_HOST, DEFAULT_PORT
from tool1_dashboard.runtime import find_free_port


def main() -> None:
    port = find_free_port(DEFAULT_PORT)
    url = f"http://{DEFAULT_HOST}:{port}"
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=DEFAULT_HOST, port=port)


if __name__ == "__main__":
    main()
