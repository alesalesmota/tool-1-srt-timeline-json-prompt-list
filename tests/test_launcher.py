from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tool1_dashboard.launch_runtime import (
    SingleInstanceLock,
    clear_runtime_info,
    get_runtime_info,
    register_shutdown_callback,
    request_runtime_shutdown,
    runtime_url_from_info,
    set_runtime_info,
)


class LaunchRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.runtime_dir = Path(self._tmpdir.name)
        self._patches = [
            patch("tool1_dashboard.launch_runtime.APP_RUNTIME_DIR", self.runtime_dir),
            patch("tool1_dashboard.launch_runtime.APP_INSTANCE_LOCK_PATH", self.runtime_dir / "instance.lock"),
            patch("tool1_dashboard.launch_runtime.APP_RUNTIME_STATE_PATH", self.runtime_dir / "runtime.json"),
            patch("tool1_dashboard.launch_runtime.APP_LAUNCHER_LOG_PATH", self.runtime_dir / "launcher.log"),
        ]
        for patcher in self._patches:
            patcher.start()

    def tearDown(self) -> None:
        register_shutdown_callback(None)
        clear_runtime_info()
        for patcher in reversed(self._patches):
            patcher.stop()
        self._tmpdir.cleanup()

    def test_runtime_info_roundtrip_and_url(self) -> None:
        payload = set_runtime_info(
            pid=999,
            host="127.0.0.1",
            port=8020,
            url="http://127.0.0.1:8020",
            mode="desktop",
            window_controls_shutdown=True,
            started_at="2026-04-03T14:00:00+00:00",
        )

        self.assertEqual(payload["pid"], 999)
        disk_payload = get_runtime_info(read_from_disk=True)
        self.assertEqual(disk_payload["mode"], "desktop")
        self.assertEqual(runtime_url_from_info(disk_payload), "http://127.0.0.1:8020")

    def test_request_runtime_shutdown_invokes_callback(self) -> None:
        triggered = threading.Event()

        def callback() -> None:
            triggered.set()

        register_shutdown_callback(callback)
        self.assertTrue(request_runtime_shutdown())
        self.assertTrue(triggered.wait(timeout=1.0))

    def test_single_instance_lock_blocks_duplicate_handle(self) -> None:
        lock_path = self.runtime_dir / "instance.lock"
        first = SingleInstanceLock(lock_path)
        second = SingleInstanceLock(lock_path)

        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())

        first.release()
        self.assertTrue(second.acquire())
        second.release()
