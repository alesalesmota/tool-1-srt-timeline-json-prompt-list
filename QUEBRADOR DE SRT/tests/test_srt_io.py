from __future__ import annotations

import unittest

from srt_chunker.srt_io import cues_to_srt, format_timestamp_ms, parse_srt_text


SAMPLE_SRT = """1
00:00:01,000 --> 00:00:03,000
Hello there.

2
00:00:03,500 --> 00:00:05,000
General Kenobi.
"""


class SrtIoTests(unittest.TestCase):
    def test_parse_srt_text(self) -> None:
        cues = parse_srt_text(SAMPLE_SRT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].start_ms, 1000)
        self.assertEqual(cues[1].text, "General Kenobi.")

    def test_format_timestamp_ms(self) -> None:
        self.assertEqual(format_timestamp_ms(3723004), "01:02:03,004")

    def test_cues_to_srt_restarts_numbering(self) -> None:
        cues = parse_srt_text(SAMPLE_SRT)
        rendered = cues_to_srt(cues[:1], restart_numbering=True)
        self.assertTrue(rendered.startswith("1\n00:00:01,000 --> 00:00:03,000"))


if __name__ == "__main__":
    unittest.main()
