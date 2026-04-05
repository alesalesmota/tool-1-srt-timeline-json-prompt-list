from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tool1_dashboard.alignment_tool import orchestrator
from tool1_dashboard.alignment_tool.guided_chunking import (
    build_guided_chunks,
    build_script_subset,
    stitch_chunk_word_timings,
)
from tool1_dashboard.alignment_tool.models import NormalizedAudioInfo, SubtitleSegment, WordTiming
from tool1_dashboard.alignment_tool.normalize_script import normalize_script, normalize_word
from tool1_dashboard.alignment_tool.parse_alignment import RawAlignedWord, map_raw_words_to_script
from tool1_dashboard.alignment_tool.segment_subtitles import segment_words
from tool1_dashboard.translation.language_rules import build_spoken_script, component_aliases_for_token


class AlignmentNormalizationTests(unittest.TestCase):

    def test_language_aware_token_normalization_supports_elisions_and_join_aliases(self) -> None:
        self.assertEqual(normalize_word("C'est", "fr"), "cest")
        self.assertIn(("l", "amour"), component_aliases_for_token("l'amour", "fr"))
        self.assertIn(("de", "el"), component_aliases_for_token("del", "es"))
        self.assertIn(("zu", "dem"), component_aliases_for_token("zum", "de"))

    def test_spoken_script_expands_abbreviations_and_references(self) -> None:
        spoken = build_spoken_script("St. Jean 18:2", "fr")
        self.assertIn("Saint Jean chapitre 18 verset 2", spoken)


class AlignmentMappingTests(unittest.TestCase):

    def test_merge_rescue_handles_single_token_against_two_raw_tokens(self) -> None:
        script_document = normalize_script("C'est bien.", "fr")
        raw_words = [
            RawAlignedWord("c", 0.0, 0.10),
            RawAlignedWord("est", 0.10, 0.30),
            RawAlignedWord("bien", 0.35, 0.60),
        ]
        mapped, mismatch_count, approximate_count, dropped_count, warnings, diagnostics = map_raw_words_to_script(
            raw_words,
            script_document,
            source="mfa",
            audio_duration=1.0,
            language_code="fr",
        )
        self.assertEqual(mismatch_count, 0)
        self.assertEqual(approximate_count, 0)
        self.assertEqual(dropped_count, 0)
        self.assertEqual(diagnostics["merge_rescues"], 1)
        self.assertEqual(mapped[0].start, 0.0)
        self.assertAlmostEqual(mapped[0].end, 0.30, places=3)
        self.assertEqual(warnings, [])

    def test_split_rescue_handles_joined_article_forms(self) -> None:
        script_document = normalize_script("de el camino.", "es")
        raw_words = [
            RawAlignedWord("del", 0.0, 0.20),
            RawAlignedWord("camino", 0.25, 0.55),
        ]
        mapped, mismatch_count, approximate_count, dropped_count, warnings, diagnostics = map_raw_words_to_script(
            raw_words,
            script_document,
            source="mfa",
            audio_duration=1.0,
            language_code="es",
        )
        self.assertEqual(mismatch_count, 0)
        self.assertEqual(approximate_count, 0)
        self.assertEqual(dropped_count, 0)
        self.assertEqual(diagnostics["split_rescues"], 1)
        self.assertLess(mapped[0].end, mapped[1].end)
        self.assertEqual(warnings, [])

    def test_fuzzy_rescue_handles_minor_token_drift(self) -> None:
        script_document = normalize_script("magnifique lumiere.", "fr")
        raw_words = [
            RawAlignedWord("magnifiqe", 0.0, 0.25),
            RawAlignedWord("lumiere", 0.30, 0.55),
        ]
        mapped, mismatch_count, approximate_count, dropped_count, warnings, diagnostics = map_raw_words_to_script(
            raw_words,
            script_document,
            source="mfa",
            audio_duration=1.0,
            language_code="fr",
        )
        self.assertEqual(mismatch_count, 0)
        self.assertEqual(approximate_count, 0)
        self.assertEqual(dropped_count, 0)
        self.assertGreaterEqual(diagnostics["fuzzy_rescues"], 1)
        self.assertEqual(len(mapped), 2)
        self.assertEqual(warnings, [])


class GuidedChunkingTests(unittest.TestCase):

    def test_chunk_stitching_preserves_core_indices_and_offsets(self) -> None:
        paragraphs = [
            " ".join(f"word{i}" for i in range(5)),
            " ".join(f"word{i}" for i in range(5, 10)),
            " ".join(f"word{i}" for i in range(10, 15)),
        ]
        document = normalize_script("\n\n".join(paragraphs), "en")
        plans = build_guided_chunks(document, target_words=6, hard_max=8, overlap_words=2)
        self.assertEqual(len(plans), 3)
        middle = plans[1]
        subset = build_script_subset(document, middle.align_start, middle.align_end)
        local_words = [
            WordTiming(
                word=word.word,
                start=index * 0.10,
                end=(index + 1) * 0.10,
                index=index,
                normalized=word.normalized,
                text_start=word.text_start,
                text_end=word.text_end,
                render_start=word.render_start,
                render_end=word.render_end,
            )
            for index, word in enumerate(subset.words)
        ]
        stitched = stitch_chunk_word_timings(document, middle, local_words, 10.0)
        self.assertEqual(sorted(stitched.keys()), list(range(middle.core_start, middle.core_end)))
        self.assertAlmostEqual(stitched[middle.core_start].start, 10.20, places=2)


class SubtitleOptimizationTests(unittest.TestCase):

    def test_segment_words_uses_gap_extension_to_reduce_cps_without_losing_text(self) -> None:
        document = normalize_script("Alpha beta gamma delta epsilon. Zeta.", "en")
        words = [
            WordTiming("Alpha", 0.00, 0.20, 0, normalized="alpha", text_start=0, text_end=5, render_start=0, render_end=5),
            WordTiming("beta", 0.22, 0.42, 1, normalized="beta", text_start=6, text_end=10, render_start=6, render_end=10),
            WordTiming("gamma", 0.44, 0.64, 2, normalized="gamma", text_start=11, text_end=16, render_start=11, render_end=16),
            WordTiming("delta", 0.66, 0.86, 3, normalized="delta", text_start=17, text_end=22, render_start=17, render_end=22),
            WordTiming("epsilon", 0.88, 1.28, 4, normalized="epsilon", text_start=23, text_end=30, render_start=23, render_end=31, trailing_text="."),
            WordTiming("Zeta", 2.60, 2.90, 5, normalized="zeta", text_start=32, text_end=36, render_start=32, render_end=37, trailing_text="."),
        ]
        segments, warnings = segment_words(document, words, orchestrator.SegmentationConfig())
        self.assertEqual(len(segments), 2)
        self.assertLess(segments[0].reading_cps, 18.0)
        combined_text = " ".join(segment.text.replace("\n", " ") for segment in segments)
        self.assertIn("Alpha beta gamma delta epsilon.", combined_text)
        self.assertEqual(warnings, [])


class AlignmentOrchestratorTests(unittest.TestCase):

    def test_run_alignment_job_selects_best_candidate_and_writes_report_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            audio_path = temp_path / "audio.wav"
            script_path = temp_path / "script.txt"
            audio_path.write_bytes(b"RIFF....WAVEfmt ")
            script_path.write_text("Hello world.", encoding="utf-8")

            fake_audio = NormalizedAudioInfo(
                path=audio_path,
                duration_seconds=10.0,
                sample_rate=16000,
                channels=1,
            )
            fake_words = [
                WordTiming("Hello", 0.0, 0.4, 0, normalized="hello", text_start=0, text_end=5, render_start=0, render_end=5),
                WordTiming("world", 0.5, 1.0, 1, normalized="world", text_start=6, text_end=11, render_start=6, render_end=12, trailing_text="."),
            ]
            primary_segment = SubtitleSegment(1, 0.0, 1.0, "Hello world.", 1, 11, 2, 22.0)
            fallback_segment = SubtitleSegment(1, 0.0, 1.3, "Hello world.", 1, 11, 2, 12.0)

            primary_candidate = orchestrator._AlignmentCandidate(
                strategy="single_pass_mfa",
                engine="mfa",
                mapped_words=fake_words,
                segments=[primary_segment],
                warnings=["mfa mismatch near script words 1-2; timings were approximated."],
                mismatch_count=40,
                approximate_word_count=40,
                dropped_word_count=0,
                mapping_diagnostics={"mismatch_blocks": 4},
                raw_words=None,
                chunk_count=1,
            )
            whisperx_candidate = orchestrator._AlignmentCandidate(
                strategy="whisperx_fallback",
                engine="whisperx",
                mapped_words=fake_words,
                segments=[fallback_segment],
                warnings=[],
                mismatch_count=12,
                approximate_word_count=12,
                dropped_word_count=0,
                mapping_diagnostics={"mismatch_blocks": 2},
                raw_words=[RawAlignedWord("hello", 0.0, 0.4), RawAlignedWord("world", 0.5, 1.0)],
                chunk_count=1,
            )
            guided_segment = SubtitleSegment(1, 0.0, 1.4, "Hello world.", 1, 11, 2, 11.0)

            with patch("tool1_dashboard.alignment_tool.orchestrator.ensure_mfa_language_resources"), patch(
                "tool1_dashboard.alignment_tool.orchestrator.extract_script_text",
                return_value="Hello world.",
            ), patch(
                "tool1_dashboard.alignment_tool.orchestrator.normalize_audio_file",
                return_value=fake_audio,
            ), patch(
                "tool1_dashboard.alignment_tool.orchestrator._run_engine_candidate",
                side_effect=[primary_candidate, whisperx_candidate],
            ), patch(
                "tool1_dashboard.alignment_tool.orchestrator.run_guided_chunked_mfa",
                return_value=(
                    fake_words,
                    [],
                    {"mismatch_blocks": 1, "merge_rescues": 1},
                    {"mismatch_count": 2, "approximate_word_count": 2, "dropped_word_count": 0},
                    2,
                ),
            ), patch(
                "tool1_dashboard.alignment_tool.orchestrator.segment_words",
                return_value=([guided_segment], []),
            ):
                result = orchestrator.run_alignment_job(
                    audio_path=audio_path,
                    script_path=script_path,
                    language_code="fr",
                    output_root=temp_path / "output",
                )

            self.assertEqual(result.report.strategy, "guided_chunked_mfa")
            self.assertEqual(result.report.chunk_count, 2)
            self.assertEqual(len(result.report.candidate_metrics), 3)
            report_payload = json.loads(result.artifacts.alignment_report.read_text(encoding="utf-8"))
            self.assertEqual(report_payload["strategy"], "guided_chunked_mfa")
            self.assertIn("candidate_metrics", report_payload)
            self.assertEqual(report_payload["chunk_count"], 2)


if __name__ == "__main__":
    unittest.main()
