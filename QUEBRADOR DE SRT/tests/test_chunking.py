from __future__ import annotations

import unittest

from srt_chunker.chunking import chunk_cues
from srt_chunker.models import ChunkConfig, SubtitleCue


class ChunkingTests(unittest.TestCase):
    def test_splits_by_entry_limit(self) -> None:
        cues = [
            SubtitleCue(index=1, start_ms=0, end_ms=1000, text="one"),
            SubtitleCue(index=2, start_ms=1100, end_ms=2000, text="two"),
            SubtitleCue(index=3, start_ms=2100, end_ms=3000, text="three"),
        ]
        chunks, warnings = chunk_cues(cues, ChunkConfig(max_entries=2, max_words=0, max_chars=0, max_duration_seconds=0))
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].cue_count, 2)
        self.assertEqual(warnings, [])

    def test_splits_by_word_limit(self) -> None:
        cues = [
            SubtitleCue(index=1, start_ms=0, end_ms=1000, text="one two three"),
            SubtitleCue(index=2, start_ms=1100, end_ms=2000, text="four five six"),
            SubtitleCue(index=3, start_ms=2100, end_ms=3000, text="seven eight"),
        ]
        chunks, _ = chunk_cues(cues, ChunkConfig(max_entries=0, max_words=5, max_chars=0, max_duration_seconds=0))
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[1].word_count, 5)

    def test_large_single_cue_is_kept_alone_with_warning(self) -> None:
        cues = [
            SubtitleCue(index=1, start_ms=0, end_ms=1000, text="one two three four five six"),
            SubtitleCue(index=2, start_ms=1100, end_ms=2000, text="small"),
        ]
        chunks, warnings = chunk_cues(cues, ChunkConfig(max_entries=0, max_words=3, max_chars=0, max_duration_seconds=0))
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].cue_count, 1)
        self.assertEqual(len(warnings), 1)


if __name__ == "__main__":
    unittest.main()
