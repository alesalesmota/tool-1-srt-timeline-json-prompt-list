from __future__ import annotations

import unittest

from tool1_dashboard.chunking import build_gap_fill_batches, build_planning_chunks, build_prompt_batches
from tool1_dashboard.srt_chunker.models import SubtitleCue
from tool1_dashboard.validators import (
    apply_default_asset_types,
    merge_scene_chunks,
    normalize_scene_payload,
    normalize_visual_bible,
    validate_prompt_payloads,
    validate_timeline,
)


class ChunkingValidationTests(unittest.TestCase):
    def test_overlap_chunking_creates_multiple_chunks(self) -> None:
        cues = [
            SubtitleCue(index=1, start_ms=0, end_ms=120000, text="A"),
            SubtitleCue(index=2, start_ms=120000, end_ms=240000, text="B"),
            SubtitleCue(index=3, start_ms=240000, end_ms=360000, text="C"),
            SubtitleCue(index=4, start_ms=360000, end_ms=480000, text="D"),
        ]
        chunks, manifest = build_planning_chunks(cues, chunk_seconds=300, overlap_seconds=30)
        self.assertEqual(manifest["mode"], "overlap_chunks")
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual(chunks[0].cue_start_index, 1)

    def test_merge_scene_chunks_dedupes_overlap(self) -> None:
        scenes, report = merge_scene_chunks(
            [
                [
                    {"start": 0.0, "end": 5.0, "duration": 5.0, "text": "One", "asset_type": "image"},
                    {"start": 5.0, "end": 10.0, "duration": 5.0, "text": "Two", "asset_type": "video"},
                ],
                [
                    {"start": 5.0, "end": 10.0, "duration": 5.0, "text": "Two", "asset_type": "video"},
                    {"start": 10.0, "end": 15.0, "duration": 5.0, "text": "Three", "asset_type": "image"},
                ],
            ]
        )
        self.assertEqual(len(scenes), 3)
        self.assertEqual(report["deduped_duplicates"], 1)
        self.assertEqual(scenes[0]["scene_id"], "scene_001")

    def test_merge_scene_chunks_uses_chunk_ownership_windows(self) -> None:
        scenes, report = merge_scene_chunks(
            [
                [
                    {"start": 320.0, "end": 350.0, "duration": 30.0, "text": "Edge A", "asset_type": "image", "_source_chunk_id": 1},
                    {"start": 350.0, "end": 380.0, "duration": 30.0, "text": "Core A", "asset_type": "image", "_source_chunk_id": 1},
                ],
                [
                    {"start": 330.0, "end": 360.0, "duration": 30.0, "text": "Edge B", "asset_type": "video", "_source_chunk_id": 2},
                    {"start": 360.0, "end": 390.0, "duration": 30.0, "text": "Core B", "asset_type": "video", "_source_chunk_id": 2},
                ],
            ],
            chunk_metadata=[
                {"chunk_id": 1, "start_seconds": 0.0, "end_seconds": 360.0},
                {"chunk_id": 2, "start_seconds": 330.0, "end_seconds": 690.0},
            ],
            overlap_seconds=30.0,
        )
        self.assertEqual([scene["text"] for scene in scenes], ["Edge A", "Edge B", "Core B"])
        self.assertEqual(report["ownership_dropped"], 1)

    def test_merge_scene_chunks_splits_long_scenes_using_cues(self) -> None:
        scenes, report = merge_scene_chunks(
            [
                [
                    {
                        "start": 0.0,
                        "end": 24.0,
                        "duration": 24.0,
                        "text": "One two three",
                        "asset_type": "image",
                        "_source_chunk_id": 1,
                    }
                ]
            ],
            cues=[
                SubtitleCue(index=1, start_ms=0, end_ms=8000, text="One."),
                SubtitleCue(index=2, start_ms=8000, end_ms=16000, text="Two."),
                SubtitleCue(index=3, start_ms=16000, end_ms=24000, text="Three."),
            ],
        )
        self.assertEqual(len(scenes), 2)
        self.assertTrue(all(scene["duration"] <= 18.0 for scene in scenes))
        self.assertEqual(report["split_insertions"], 1)

    def test_prompt_validation_rejects_mismatch(self) -> None:
        scenes = [
            {"scene_id": "scene_001", "start": 0.0, "end": 4.0, "duration": 4.0, "text": "One", "asset_type": "image"},
            {"scene_id": "scene_002", "start": 4.0, "end": 8.0, "duration": 4.0, "text": "Two", "asset_type": "video"},
        ]
        prompts, report = validate_prompt_payloads(scenes, [{"prompts": ["only one"]}])
        self.assertEqual(prompts, ["only one"])
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(report["errors"])

    def test_timeline_validation_flags_overlap(self) -> None:
        report = validate_timeline(
            [
                {"scene_id": "scene_001", "start": 0.0, "end": 5.0, "duration": 5.0, "text": "One", "asset_type": "image"},
                {"scene_id": "scene_002", "start": 4.0, "end": 8.0, "duration": 4.0, "text": "Two", "asset_type": "video"},
            ]
        )
        self.assertEqual(report["status"], "valid")
        self.assertTrue(report["warnings"])

    def test_prompt_batches_group_by_size(self) -> None:
        scenes = [{"scene_id": f"scene_{index:03d}"} for index in range(1, 8)]
        batches = build_prompt_batches(scenes, batch_size=3)
        self.assertEqual(len(batches), 3)
        self.assertEqual(batches[0]["scene_start"], 1)
        self.assertEqual(batches[-1]["scene_end"], 7)

    def test_default_video_allocation_marks_first_n_scenes(self) -> None:
        scenes = [
            {"scene_id": "scene_001", "asset_type": "image"},
            {"scene_id": "scene_002", "asset_type": "image"},
            {"scene_id": "scene_003", "asset_type": "image"},
        ]
        assigned = apply_default_asset_types(scenes, 2)
        self.assertEqual([scene["asset_type"] for scene in assigned], ["video", "video", "image"])

    def test_normalize_scene_payload_discards_zero_length_gap_marker(self) -> None:
        scenes, warnings = normalize_scene_payload(
            {
                "scenes": [
                    {"start": 0.0, "end": 7.0, "duration": 7.0, "text": "Opening line", "notes": "real scene"},
                    {
                        "start": 7.0,
                        "end": 7.0,
                        "duration": 0.0,
                        "text": "",
                        "notes": "SKIP - gap absorbed into adjacent scenes",
                    },
                    {"start": 7.0, "end": 12.0, "duration": 5.0, "text": "Next line", "notes": "real scene"},
                ]
            },
            1,
        )
        self.assertEqual([scene["text"] for scene in scenes], ["Opening line", "Next line"])
        self.assertTrue(any("gap marker" in warning for warning in warnings))

    def test_normalize_scene_payload_trims_malformed_trailing_suffix(self) -> None:
        scenes, warnings = normalize_scene_payload(
            {
                "scenes": [
                    {"start": 0.0, "end": 6.0, "duration": 6.0, "text": "Opening line", "notes": "real scene"},
                    {"start": 6.0, "end": 12.0, "duration": 6.0, "text": "Next line", "notes": "real scene"},
                    {"start": 12.0, "end": 10.0, "duration": -2.0, "text": "Broken tail", "notes": ""},
                    {"start": 10.0, "end": 10.0, "duration": 0.0, "text": "More broken tail", "notes": ""},
                    {"start": 10.0, "end": 10.0, "duration": 0.0, "text": "", "notes": "gap absorbed"},
                ]
            },
            5,
        )
        self.assertEqual([scene["text"] for scene in scenes], ["Opening line", "Next line"])
        self.assertTrue(any("malformed trailing scenes" in warning for warning in warnings))

    def test_normalize_scene_payload_rebases_chunk_local_timestamps(self) -> None:
        scenes, warnings = normalize_scene_payload(
            {
                "scenes": [
                    {
                        "start": 0.0,
                        "end": 12.0,
                        "duration": 12.0,
                        "text": "Late section opens",
                        "notes": "real scene",
                    },
                    {
                        "start": 12.0,
                        "end": 28.0,
                        "duration": 16.0,
                        "text": "Late section continues",
                        "notes": "real scene",
                    },
                ]
            },
            10,
            chunk_window={"chunk_id": 10, "start_seconds": 2970.0, "end_seconds": 3330.0, "duration_seconds": 360.0},
        )
        self.assertEqual((scenes[0]["start"], scenes[0]["end"]), (2970.0, 2982.0))
        self.assertEqual((scenes[1]["start"], scenes[1]["end"]), (2982.0, 2998.0))
        self.assertTrue(any("rebased" in warning for warning in warnings))

    def test_merge_scene_chunks_rebases_relative_late_chunk_and_reaches_last_cue(self) -> None:
        scenes, report = merge_scene_chunks(
            [
                [
                    {
                        "start": 3300.0,
                        "end": 3330.0,
                        "duration": 30.0,
                        "text": "Late section",
                        "asset_type": "image",
                        "_source_chunk_id": 10,
                    }
                ],
                [
                    {
                        "start": 30.0,
                        "end": 61.6,
                        "duration": 31.6,
                        "text": "Final section",
                        "asset_type": "image",
                        "_source_chunk_id": 11,
                    }
                ],
            ],
            chunk_metadata=[
                {"chunk_id": 10, "start_seconds": 2970.0, "end_seconds": 3330.0, "duration_seconds": 360.0},
                {"chunk_id": 11, "start_seconds": 3300.0, "end_seconds": 3361.6, "duration_seconds": 61.6},
            ],
            overlap_seconds=30.0,
            cues=[
                SubtitleCue(index=1, start_ms=3300000, end_ms=3330000, text="Late section."),
                SubtitleCue(index=2, start_ms=3330000, end_ms=3361600, text="Final section."),
            ],
        )
        self.assertEqual(scenes[-1]["end"], 3361.6)
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["chunk_timestamp_rebases"], 1)
        self.assertEqual(report["cue_tail_gap_seconds"], 0.0)

    def test_timeline_validation_fails_when_tail_cues_are_uncovered(self) -> None:
        report = validate_timeline(
            [
                {
                    "scene_id": "scene_001",
                    "start": 0.0,
                    "end": 8.0,
                    "duration": 8.0,
                    "text": "Opening",
                    "asset_type": "image",
                }
            ],
            cues=[
                SubtitleCue(index=1, start_ms=0, end_ms=8000, text="Opening."),
                SubtitleCue(index=2, start_ms=8000, end_ms=16000, text="Missing tail."),
            ],
        )
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("before the last cue" in error for error in report["errors"]))

    def test_visual_bible_normalization_requires_core_sections(self) -> None:
        normalized, report = normalize_visual_bible(
            {
                "world_style": {
                    "setting": "Ancient desert kingdom",
                    "look": "Cinematic realism",
                    "palette": "Muted sand and charcoal",
                    "lighting": "Golden dusk mixed with firelight",
                    "camera_language": "Steady documentary camera language",
                    "negative_rules": "No text overlays",
                },
                "characters": [
                    {
                        "character_id": "prophet_001",
                        "label": "Desert prophet",
                        "visual_description": "Elderly man with olive skin and deep-set eyes",
                        "wardrobe": "Rough wool robe and wooden staff",
                        "demeanor": "Calm but urgent",
                        "usage_notes": "Keep face rugged and weathered",
                    }
                ],
                "continuity_rules": ["Keep the same robe silhouette."],
                "environment_rules": ["Preserve a windblown desert atmosphere."],
            }
        )
        self.assertEqual(report["status"], "valid")
        self.assertEqual(normalized["characters"][0]["character_id"], "prophet_001")

    def test_prompt_validation_rejects_cross_reference_wording(self) -> None:
        scenes = [
            {"scene_id": "scene_001", "start": 0.0, "end": 4.0, "duration": 4.0, "text": "One", "asset_type": "video"},
        ]
        _, report = validate_prompt_payloads(
            scenes,
            [{"prompts": [{"scene_id": "scene_001", "prompt": "SUBJ: same prophet; SET: desert tent; ACT: he walks out; CAM: slow push in; LOOK: cinematic realism; LIGHT: dusk firelight"}]}],
        )
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("banned content" in error for error in report["errors"]))

    def test_prompt_validation_rebuilds_structured_prompt_lines(self) -> None:
        scenes = [
            {"scene_id": "scene_001", "start": 0.0, "end": 4.0, "duration": 4.0, "text": "One", "asset_type": "video"},
        ]
        prompts, report = validate_prompt_payloads(
            scenes,
            [
                {
                    "prompts": [
                        {
                            "scene_id": "scene_001",
                            "subject": "Abram in Ur",
                            "setting": "same refined urban home in ancient Ur",
                            "action": "He pauses during a calm daily routine",
                            "camera": "Slow push in",
                            "look": "Painterly documentary-drama; detailed textiles",
                            "lighting": "Warm late-afternoon light",
                            "rules": "No modern cues",
                            "character_refs": ["abraham"],
                            "prompt": "bad raw prompt",
                        }
                    ]
                }
            ],
        )
        self.assertEqual(report["status"], "valid")
        self.assertIn("Painterly documentary-drama, detailed textiles", prompts[0])
        self.assertNotIn("LOOK:", prompts[0])
        self.assertIn("no text", prompts[0].lower())
        self.assertIn("no split-screen", prompts[0].lower())
        self.assertNotIn("same", prompts[0].lower())


class GapFillBatchTests(unittest.TestCase):
    def _scenes(self, ids: list[str]) -> list[dict[str, object]]:
        return [{"scene_id": sid, "asset_type": "image"} for sid in ids]

    def test_no_missing_returns_empty(self) -> None:
        scenes = self._scenes(["s1", "s2", "s3"])
        result = build_gap_fill_batches(scenes, {"s1", "s2", "s3"}, batch_size=4)
        self.assertEqual(result, [])

    def test_all_missing_returns_one_batch(self) -> None:
        scenes = self._scenes(["s1", "s2", "s3"])
        result = build_gap_fill_batches(scenes, set(), batch_size=10)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["scenes"]), 3)

    def test_partial_missing_returns_only_gaps(self) -> None:
        scenes = self._scenes(["s1", "s2", "s3", "s4", "s5"])
        result = build_gap_fill_batches(scenes, {"s1", "s3", "s5"}, batch_size=10)
        self.assertEqual(len(result), 1)
        ids = [s["scene_id"] for s in result[0]["scenes"]]
        self.assertEqual(ids, ["s2", "s4"])

    def test_gap_fill_respects_batch_size(self) -> None:
        scenes = self._scenes([f"s{i}" for i in range(10)])
        result = build_gap_fill_batches(scenes, set(), batch_size=3)
        self.assertEqual(len(result), 4)  # 10 / 3 = 4 batches (3+3+3+1)
        self.assertEqual(len(result[-1]["scenes"]), 1)

    def test_batch_id_offset(self) -> None:
        scenes = self._scenes(["s1", "s2"])
        result = build_gap_fill_batches(scenes, set(), batch_size=10, batch_id_offset=5)
        self.assertEqual(result[0]["batch_id"], 6)


if __name__ == "__main__":
    unittest.main()
