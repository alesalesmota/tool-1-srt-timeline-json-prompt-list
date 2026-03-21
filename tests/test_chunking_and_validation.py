from __future__ import annotations

import unittest

from tool1_dashboard.chunking import build_planning_chunks, build_prompt_batches
from tool1_dashboard.srt_chunker.models import SubtitleCue
from tool1_dashboard.validators import (
    apply_default_asset_types,
    merge_scene_chunks,
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
        self.assertIn("LOOK: Painterly documentary-drama, detailed textiles", prompts[0])
        self.assertNotIn("same", prompts[0].lower())


if __name__ == "__main__":
    unittest.main()
