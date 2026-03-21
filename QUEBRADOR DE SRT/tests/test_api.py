from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from srt_chunker.app import app


SAMPLE_SRT = """1
00:00:01,000 --> 00:00:03,000
Hello there.

2
00:00:03,500 --> 00:00:05,000
General Kenobi.

3
00:00:05,500 --> 00:00:07,000
You are a bold one.
"""


class ApiTests(unittest.TestCase):
    def test_health_endpoint(self) -> None:
        client = TestClient(app)
        response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_chunk_endpoint_returns_downloads_and_chunks(self) -> None:
        client = TestClient(app)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_output = Path(temp_dir)
            with patch("srt_chunker.app.OUTPUT_ROOT", temp_output):
                response = client.post(
                    "/api/chunk",
                    files={"srt_file": ("sample.srt", SAMPLE_SRT.encode("utf-8"), "application/x-subrip")},
                    data={
                        "max_words": "4",
                        "max_entries": "2",
                        "max_chars": "0",
                        "max_duration_seconds": "0",
                        "restart_numbering": "true",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("downloads", payload)
        self.assertGreaterEqual(payload["stats"]["total_chunks"], 2)
        self.assertTrue(payload["preview"]["first_chunk_srt"].startswith("1\n"))


if __name__ == "__main__":
    unittest.main()
