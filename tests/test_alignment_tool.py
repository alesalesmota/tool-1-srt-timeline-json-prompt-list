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
from tool1_dashboard.alignment_tool.models import (
    NormalizedAudioInfo,
    SegmentationDiagnostics,
    SubtitleSegment,
    WordTiming,
)
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

    def test_segment_words_respects_line_limits_and_uses_two_lines(self) -> None:
        document = normalize_script("Alpha beta gamma delta epsilon zeta eta theta.", "en")
        words = [
            WordTiming("Alpha", 0.00, 0.35, 0, normalized="alpha", text_start=0, text_end=5, render_start=0, render_end=5),
            WordTiming("beta", 0.36, 0.68, 1, normalized="beta", text_start=6, text_end=10, render_start=6, render_end=10),
            WordTiming("gamma", 0.69, 1.05, 2, normalized="gamma", text_start=11, text_end=16, render_start=11, render_end=16),
            WordTiming("delta", 1.06, 1.40, 3, normalized="delta", text_start=17, text_end=22, render_start=17, render_end=22),
            WordTiming("epsilon", 1.41, 1.90, 4, normalized="epsilon", text_start=23, text_end=30, render_start=23, render_end=30),
            WordTiming("zeta", 1.91, 2.24, 5, normalized="zeta", text_start=31, text_end=35, render_start=31, render_end=35),
            WordTiming("eta", 2.25, 2.50, 6, normalized="eta", text_start=36, text_end=39, render_start=36, render_end=39),
            WordTiming("theta", 2.51, 2.95, 7, normalized="theta", text_start=40, text_end=45, render_start=40, render_end=46, trailing_text="."),
        ]
        result = segment_words(document, words, orchestrator.SegmentationConfig())
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.segments[0].line_count, 2)
        for line in result.segments[0].text.split("\n"):
            self.assertLessEqual(len(line), 42)

    def test_segment_words_does_not_break_after_non_breaking_abbreviation(self) -> None:
        document = normalize_script("St. Jean parla doucement, puis continua avec calme.", "fr")
        words = [
            WordTiming("St", 0.00, 0.18, 0, normalized="st", text_start=0, text_end=2, render_start=0, render_end=3, trailing_text="."),
            WordTiming("Jean", 0.19, 0.48, 1, normalized="jean", text_start=4, text_end=8, render_start=4, render_end=8),
            WordTiming("parla", 0.49, 0.76, 2, normalized="parla", text_start=9, text_end=14, render_start=9, render_end=14),
            WordTiming("doucement", 0.77, 1.18, 3, normalized="doucement", text_start=15, text_end=24, render_start=15, render_end=25, trailing_text=","),
            WordTiming("puis", 1.55, 1.82, 4, normalized="puis", text_start=26, text_end=30, render_start=26, render_end=30),
            WordTiming("continua", 1.83, 2.22, 5, normalized="continua", text_start=31, text_end=39, render_start=31, render_end=39),
            WordTiming("avec", 2.23, 2.48, 6, normalized="avec", text_start=40, text_end=44, render_start=40, render_end=44),
            WordTiming("calme", 2.49, 2.92, 7, normalized="calme", text_start=45, text_end=50, render_start=45, render_end=51, trailing_text="."),
        ]
        result = segment_words(document, words, orchestrator.SegmentationConfig(max_duration=2.5))
        self.assertTrue(result.segments[0].text.startswith("St. Jean"))

    def test_segment_words_avoids_leading_short_tokens_when_a_better_break_exists(self) -> None:
        document = normalize_script("La lumiere apparut et la foule resta en silence.", "fr")
        words = [
            WordTiming("La", 0.00, 0.16, 0, normalized="la", text_start=0, text_end=2, render_start=0, render_end=2),
            WordTiming("lumiere", 0.17, 0.56, 1, normalized="lumiere", text_start=3, text_end=10, render_start=3, render_end=10),
            WordTiming("apparut", 0.57, 0.98, 2, normalized="apparut", text_start=11, text_end=18, render_start=11, render_end=18),
            WordTiming("et", 0.99, 1.15, 3, normalized="et", text_start=19, text_end=21, render_start=19, render_end=21),
            WordTiming("la", 1.16, 1.30, 4, normalized="la", text_start=22, text_end=24, render_start=22, render_end=24),
            WordTiming("foule", 1.31, 1.68, 5, normalized="foule", text_start=25, text_end=30, render_start=25, render_end=30),
            WordTiming("resta", 1.69, 2.00, 6, normalized="resta", text_start=31, text_end=36, render_start=31, render_end=36),
            WordTiming("en", 2.01, 2.16, 7, normalized="en", text_start=37, text_end=39, render_start=37, render_end=39),
            WordTiming("silence", 2.17, 2.62, 8, normalized="silence", text_start=40, text_end=47, render_start=40, render_end=48, trailing_text="."),
        ]
        result = segment_words(document, words, orchestrator.SegmentationConfig(max_chars_per_line=18, max_chars_per_block=36))
        self.assertEqual(len(result.segments), 2)
        self.assertFalse(result.segments[1].text.split()[0].lower().startswith("la"))

    def test_segment_words_uses_gap_extension_to_reduce_cps_without_losing_text(self) -> None:
        document = normalize_script("Alpha beta gamma delta epsilon. Zeta eta theta.", "en")
        words = [
            WordTiming("Alpha", 0.00, 0.20, 0, normalized="alpha", text_start=0, text_end=5, render_start=0, render_end=5),
            WordTiming("beta", 0.22, 0.42, 1, normalized="beta", text_start=6, text_end=10, render_start=6, render_end=10),
            WordTiming("gamma", 0.44, 0.64, 2, normalized="gamma", text_start=11, text_end=16, render_start=11, render_end=16),
            WordTiming("delta", 0.66, 0.86, 3, normalized="delta", text_start=17, text_end=22, render_start=17, render_end=22),
            WordTiming("epsilon", 0.88, 1.28, 4, normalized="epsilon", text_start=23, text_end=30, render_start=23, render_end=31, trailing_text="."),
            WordTiming("Zeta", 2.60, 2.90, 5, normalized="zeta", text_start=32, text_end=36, render_start=32, render_end=36),
            WordTiming("eta", 2.92, 3.12, 6, normalized="eta", text_start=37, text_end=40, render_start=37, render_end=40),
            WordTiming("theta", 3.14, 3.46, 7, normalized="theta", text_start=41, text_end=46, render_start=41, render_end=47, trailing_text="."),
        ]
        result = segment_words(document, words, orchestrator.SegmentationConfig(max_chars_per_line=18, max_chars_per_block=36))
        self.assertEqual(len(result.segments), 2)
        raw_duration = words[4].end - words[0].start
        raw_cps = len("Alpha beta gamma delta epsilon.") / raw_duration
        self.assertLess(result.segments[0].reading_cps, raw_cps)
        combined_text = " ".join(segment.text.replace("\n", " ") for segment in result.segments)
        self.assertIn("Alpha beta gamma delta epsilon.", combined_text)
        self.assertEqual(result.warnings, [])

    def test_segment_words_shifts_one_word_to_reduce_boundary_density(self) -> None:
        document = normalize_script("Alpha beta gamma delta. Epsilon zeta eta theta iota.", "en")
        words = [
            WordTiming("Alpha", 0.00, 0.22, 0, normalized="alpha", text_start=0, text_end=5, render_start=0, render_end=5),
            WordTiming("beta", 0.23, 0.44, 1, normalized="beta", text_start=6, text_end=10, render_start=6, render_end=10),
            WordTiming("gamma", 0.45, 0.66, 2, normalized="gamma", text_start=11, text_end=16, render_start=11, render_end=16),
            WordTiming("delta", 0.67, 0.92, 3, normalized="delta", text_start=17, text_end=22, render_start=17, render_end=23, trailing_text="."),
            WordTiming("Epsilon", 1.60, 1.92, 4, normalized="epsilon", text_start=24, text_end=31, render_start=24, render_end=31),
            WordTiming("zeta", 1.93, 2.14, 5, normalized="zeta", text_start=32, text_end=36, render_start=32, render_end=36),
            WordTiming("eta", 2.15, 2.34, 6, normalized="eta", text_start=37, text_end=40, render_start=37, render_end=40),
            WordTiming("theta", 2.35, 2.58, 7, normalized="theta", text_start=41, text_end=46, render_start=41, render_end=46),
            WordTiming("iota", 2.59, 2.82, 8, normalized="iota", text_start=47, text_end=51, render_start=47, render_end=52, trailing_text="."),
        ]
        result = segment_words(document, words, orchestrator.SegmentationConfig(max_chars_per_line=20, max_chars_per_block=40))
        self.assertEqual(len(result.segments), 2)
        self.assertLessEqual(max(segment.reading_cps for segment in result.segments), 18.0)

    def test_segment_words_prefers_punctuation_split_over_mid_clause_split(self) -> None:
        document = normalize_script("Alpha beta gamma, delta epsilon zeta eta.", "en")
        words = [
            WordTiming("Alpha", 0.00, 0.18, 0, normalized="alpha", text_start=0, text_end=5, render_start=0, render_end=5),
            WordTiming("beta", 0.19, 0.36, 1, normalized="beta", text_start=6, text_end=10, render_start=6, render_end=10),
            WordTiming("gamma", 0.37, 0.58, 2, normalized="gamma", text_start=11, text_end=16, render_start=11, render_end=17, trailing_text=","),
            WordTiming("delta", 0.98, 1.20, 3, normalized="delta", text_start=18, text_end=23, render_start=18, render_end=23),
            WordTiming("epsilon", 1.21, 1.52, 4, normalized="epsilon", text_start=24, text_end=31, render_start=24, render_end=31),
            WordTiming("zeta", 1.53, 1.72, 5, normalized="zeta", text_start=32, text_end=36, render_start=32, render_end=36),
            WordTiming("eta", 1.73, 1.90, 6, normalized="eta", text_start=37, text_end=40, render_start=37, render_end=41, trailing_text="."),
        ]
        result = segment_words(document, words, orchestrator.SegmentationConfig(max_chars_per_line=20, max_chars_per_block=40))
        self.assertTrue(result.segments[0].text.split("\n")[0].rstrip().endswith(","))

    def test_segment_words_does_not_merge_when_merge_worsens_readability(self) -> None:
        document = normalize_script("Alpha beta gamma delta epsilon. Zeta eta theta iota kappa lambda.", "en")
        words = [
            WordTiming("Alpha", 0.00, 0.18, 0, normalized="alpha", text_start=0, text_end=5, render_start=0, render_end=5),
            WordTiming("beta", 0.19, 0.36, 1, normalized="beta", text_start=6, text_end=10, render_start=6, render_end=10),
            WordTiming("gamma", 0.37, 0.56, 2, normalized="gamma", text_start=11, text_end=16, render_start=11, render_end=16),
            WordTiming("delta", 0.57, 0.76, 3, normalized="delta", text_start=17, text_end=22, render_start=17, render_end=22),
            WordTiming("epsilon", 0.77, 1.02, 4, normalized="epsilon", text_start=23, text_end=30, render_start=23, render_end=31, trailing_text="."),
            WordTiming("Zeta", 1.60, 1.78, 5, normalized="zeta", text_start=32, text_end=36, render_start=32, render_end=36),
            WordTiming("eta", 1.79, 1.95, 6, normalized="eta", text_start=37, text_end=40, render_start=37, render_end=40),
            WordTiming("theta", 1.96, 2.17, 7, normalized="theta", text_start=41, text_end=46, render_start=41, render_end=46),
            WordTiming("iota", 2.18, 2.36, 8, normalized="iota", text_start=47, text_end=51, render_start=47, render_end=51),
            WordTiming("kappa", 2.37, 2.58, 9, normalized="kappa", text_start=52, text_end=57, render_start=52, render_end=57),
            WordTiming("lambda", 2.59, 2.86, 10, normalized="lambda", text_start=58, text_end=64, render_start=58, render_end=65, trailing_text="."),
        ]
        result = segment_words(document, words, orchestrator.SegmentationConfig(max_chars_per_line=22, max_chars_per_block=44))
        self.assertEqual(len(result.segments), 2)


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
                segmentation_diagnostics=SegmentationDiagnostics(
                    segment_profile={"ok": 0, "short_fast": 0, "long_text_fast": 1, "other_fast": 0},
                    segments_over_18_cps=1,
                    segments_over_24_cps=0,
                    segments_over_30_cps=0,
                    short_fast_segment_count=0,
                    long_text_fast_segment_count=1,
                    average_chars_per_segment=11.0,
                    average_words_per_segment=2.0,
                    optimization_passes=2,
                ),
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
                segmentation_diagnostics=SegmentationDiagnostics(),
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
                return_value=SimpleNamespace(
                    segments=[guided_segment],
                    warnings=[],
                    diagnostics=SegmentationDiagnostics(
                        segment_profile={"ok": 1, "short_fast": 0, "long_text_fast": 0, "other_fast": 0},
                        segments_over_18_cps=0,
                        segments_over_24_cps=0,
                        segments_over_30_cps=0,
                        short_fast_segment_count=0,
                        long_text_fast_segment_count=0,
                        average_chars_per_segment=11.0,
                        average_words_per_segment=2.0,
                        optimization_passes=2,
                    ),
                ),
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
            self.assertIn("segment_profile", result.report.to_dict())
            report_payload = json.loads(result.artifacts.alignment_report.read_text(encoding="utf-8"))
            self.assertEqual(report_payload["strategy"], "guided_chunked_mfa")
            self.assertIn("candidate_metrics", report_payload)
            self.assertEqual(report_payload["chunk_count"], 2)
            self.assertIn("segment_profile", report_payload)
            self.assertIn("segments_over_24_cps", report_payload)
            self.assertIn("optimization_passes", report_payload)


if __name__ == "__main__":
    unittest.main()
