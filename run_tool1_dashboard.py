#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import socket
import threading
import time
import traceback
import webbrowser

import uvicorn

from tool1_dashboard.app import app
from tool1_dashboard.config import DEFAULT_HOST, DEFAULT_PORT
from tool1_dashboard.launch_runtime import (
    SingleInstanceLock,
    append_launcher_log,
    clear_runtime_info,
    register_shutdown_callback,
    runtime_url_from_info,
    set_runtime_info,
)
from tool1_dashboard.runtime import find_free_port


class DashboardServer:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.url = f"http://{host}:{port}"
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self, timeout: float = 15.0) -> None:
        self._thread.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_listening():
                return
            if not self._thread.is_alive():
                break
            time.sleep(0.05)
        raise RuntimeError("Creator Studio failed to start.")

    def stop(self, timeout: float = 10.0) -> None:
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def wait(self) -> None:
        self._thread.join()

    def _is_listening(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            return sock.connect_ex((self.host, self.port)) == 0


def _show_message_box(title: str, message: str) -> None:
    try:  # pragma: no cover - native UI only
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
    except Exception:
        append_launcher_log(f"{title}: {message}")


def _handle_existing_instance() -> int:
    from tool1_dashboard.launch_runtime import get_runtime_info

    existing = get_runtime_info(read_from_disk=True)
    url = runtime_url_from_info(existing)
    if url:
        webbrowser.open(url)
    message = "Creator Studio is already running."
    if url:
        message += f"\n\nOpen the existing app at:\n{url}"
    _show_message_box("Creator Studio", message)
    return 0


def _launch_browser(url: str) -> None:
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()


def _launch_desktop(url: str, shutdown_callback) -> bool:
    try:
        import webview
    except Exception as exc:  # pragma: no cover - import depends on host env
        append_launcher_log(f"Desktop shell unavailable, falling back to browser: {exc}")
        return False

    stop_once = threading.Event()
    try:  # pragma: no cover - native UI only
        window = webview.create_window(
            "Creator Studio",
            url,
            width=1480,
            height=960,
            min_size=(1180, 760),
        )

        def request_shutdown() -> None:
            if stop_once.is_set():
                return
            stop_once.set()
            threading.Thread(target=shutdown_callback, daemon=True).start()

        window.events.closed += request_shutdown
        webview.start()
        request_shutdown()
        return True
    except Exception as exc:
        append_launcher_log(f"Desktop shell failed, falling back to browser: {exc}")
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the Creator Studio dashboard.")
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Open the dashboard in a browser tab instead of the desktop shell.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with SingleInstanceLock() as instance_lock:
        if not instance_lock.acquire():
            return _handle_existing_instance()

        port = find_free_port(DEFAULT_PORT)
        server = DashboardServer(DEFAULT_HOST, port)
        shutdown_once = threading.Event()

        def shutdown_runtime() -> None:
            if shutdown_once.is_set():
                return
            shutdown_once.set()
            try:
                server.stop()
            finally:
                register_shutdown_callback(None)
                clear_runtime_info()

        register_shutdown_callback(shutdown_runtime)
        set_runtime_info(
            pid=os.getpid(),
            host=DEFAULT_HOST,
            port=port,
            url=server.url,
            mode="desktop" if not args.browser else "browser",
            window_controls_shutdown=not args.browser,
        )
        try:
            server.start()
            set_runtime_info(
                pid=os.getpid(),
                host=DEFAULT_HOST,
                port=port,
                url=server.url,
                mode="desktop" if not args.browser else "browser",
                window_controls_shutdown=not args.browser,
            )
            if not args.browser and _launch_desktop(server.url, shutdown_runtime):
                return 0

            set_runtime_info(
                pid=os.getpid(),
                host=DEFAULT_HOST,
                port=port,
                url=server.url,
                mode="browser",
                window_controls_shutdown=False,
            )
            _launch_browser(server.url)
            server.wait()
            return 0
        except KeyboardInterrupt:
            shutdown_runtime()
            return 0
        except Exception:
            shutdown_runtime()
            append_launcher_log(traceback.format_exc())
            raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        _show_message_box(
            "Creator Studio",
            "Creator Studio failed to start. Check workspace/app_runtime/launcher.log for details.",
        )
        raise
