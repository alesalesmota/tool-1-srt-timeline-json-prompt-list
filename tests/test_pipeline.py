from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tool1_dashboard.alignment_tool.models import (
    AlignmentArtifacts,
    AlignmentReport,
    AlignmentResult,
    NormalizedAudioInfo,
    ScriptDocument,
    ScriptWord,
    SubtitleSegment,
    WordTiming,
)
from tool1_dashboard.database import Tool1Database
from tool1_dashboard.service import Tool1Service


class FakeCliRunner:
    def __init__(self, malformed_image_prompts: bool = False) -> None:
        self.malformed_image_prompts = malformed_image_prompts
        self.calls: list[dict[str, object]] = []

    def probe(self) -> dict[str, object]:
        return {
            "codex": {"available": True, "logged_in": True},
            "claude": {"available": True, "logged_in": True},
        }

    def run_structured(self, *, provider, model, system_prompt, user_prompt, schema, workdir, artifact_dir):  # type: ignore[no-untyped-def]
        artifact_dir.mkdir(parents=True, exist_ok=True)
        self.calls.append(
            {
                "provider": provider,
                "model": model,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema": schema,
                "workdir": str(workdir),
                "artifact_dir": str(artifact_dir),
            }
        )
        if "world_style" in schema["properties"]:
            parsed = {
                "world_style": {
                    "setting": "Ancient desert kingdom",
                    "look": "Cinematic realism",
                    "palette": "Muted sand and charcoal",
                    "lighting": "Golden dusk and firelight",
                    "camera_language": "Measured documentary framing",
                    "negative_rules": "No text overlays",
                },
                "characters": [
                    {
                        "character_id": "prophet_001",
                        "label": "Desert prophet",
                        "visual_description": "Elderly man with olive skin, deep-set eyes, and a weathered face",
                        "wardrobe": "Rough wool robe and wooden staff",
                        "demeanor": "Calm but urgent",
                        "usage_notes": "Keep him rugged and serious",
                    }
                ],
                "continuity_rules": ["Keep the same prophet face and robe silhouette."],
                "environment_rules": ["Preserve a windblown desert atmosphere."],
            }
        elif "scenes" in schema["properties"]:
            parsed = {
                "scenes": [
                    {"start": 0.0, "end": 3.0, "duration": 3.0, "text": "Nobody really knows."},
                    {"start": 3.0, "end": 6.0, "duration": 3.0, "text": "The story keeps moving."},
                ]
            }
        else:
            batch_payload = json.loads(user_prompt.split("Batch payload:\n", 1)[1])
            prompts = []
            for scene in batch_payload["scenes"]:
                if "action" in schema["properties"]["prompts"]["items"]["properties"]:
                    prompts.append(
                        {
                            "scene_id": scene["scene_id"],
                            "subject": "elderly desert prophet with olive skin and weathered face",
                            "setting": "windswept desert camp at dusk",
                            "action": "he steps out of a dark tent and looks toward the horizon",
                            "camera": "slow push in from wide to medium",
                            "look": "cinematic realism, muted sand palette",
                            "lighting": "dusk backlight with warm fire glow",
                            "rules": "no text, no watermark",
                            "character_refs": ["prophet_001"],
                            "prompt": "SUBJ: elderly desert prophet with olive skin and weathered face; SET: windswept desert camp at dusk; ACT: he steps out of a dark tent and looks toward the horizon; CAM: slow push in from wide to medium; LOOK: cinematic realism, muted sand palette; LIGHT: dusk backlight with warm fire glow; RULES: no text, no watermark",
                        }
                    )
                else:
                    composition = (
                        ""
                        if self.malformed_image_prompts
                        else "medium-wide frame with prophet off-center and tent behind him"
                    )
                    prompt_text = (
                        "bad prompt"
                        if self.malformed_image_prompts
                        else "SUBJ: elderly desert prophet with olive skin and weathered face; SET: barren desert ridge at dusk; COMP: medium-wide frame with prophet off-center and tent behind him; LOOK: cinematic realism, muted sand palette; LIGHT: dusk backlight with warm fire glow; RULES: no text, no watermark"
                    )
                    prompts.append(
                        {
                            "scene_id": scene["scene_id"],
                            "subject": "elderly desert prophet with olive skin and weathered face",
                            "setting": "barren desert ridge at dusk",
                            "composition": composition,
                            "look": "cinematic realism, muted sand palette",
                            "lighting": "dusk backlight with warm fire glow",
                            "rules": "no text, no watermark",
                            "character_refs": ["prophet_001"],
                            "prompt": prompt_text,
                        }
                    )
            parsed = {"prompts": prompts}
        stdout_path = artifact_dir / "stdout.txt"
        stderr_path = artifact_dir / "stderr.txt"
        parsed_path = artifact_dir / "parsed.json"
        stdout_path.write_text(json.dumps(parsed), encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        parsed_path.write_text(json.dumps(parsed), encoding="utf-8")
        return {
            "parsed": parsed,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "parsed_path": str(parsed_path),
            "command_payload": {"provider": provider, "model": model},
        }


def fake_alignment_result(output_root: Path) -> AlignmentResult:
    run_dir = output_root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    final_srt = run_dir / "final.srt"
    final_srt.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\nNobody really knows.\n\n2\n00:00:03,000 --> 00:00:06,000\nThe story keeps moving.\n",
        encoding="utf-8",
    )
    words_json = run_dir / "words.json"
    words_json.write_text("[]", encoding="utf-8")
    segments_json = run_dir / "segments.json"
    segments_json.write_text(
        json.dumps(
            [
                {"segment_id": 1, "start": 0.0, "end": 3.0, "text": "Nobody really knows."},
                {"segment_id": 2, "start": 3.0, "end": 6.0, "text": "The story keeps moving."},
            ]
        ),
        encoding="utf-8",
    )
    report_json = run_dir / "alignment_report.json"
    report_json.write_text("{}", encoding="utf-8")
    normalized = run_dir / "normalized_audio.wav"
    normalized.write_bytes(b"RIFF")
    run_log = run_dir / "run.log"
    run_log.write_text("alignment ok", encoding="utf-8")
    return AlignmentResult(
        run_id="fake",
        output_dir=run_dir,
        script_document=ScriptDocument(
            source_text="Nobody really knows. The story keeps moving.",
            canonical_text="Nobody really knows. The story keeps moving.",
            alignment_text="Nobody really knows The story keeps moving",
            words=[ScriptWord(0, "Nobody", "nobody", 0, 6, 0, 6), ScriptWord(1, "really", "really", 7, 13, 7, 13)],
        ),
        normalized_audio=NormalizedAudioInfo(path=normalized, duration_seconds=6.0, sample_rate=16000, channels=1),
        engine_used="mfa",
        fallback_used=False,
        fallback_reason=None,
        words=[WordTiming(word="Nobody", start=0.0, end=0.5, index=0)],
        segments=[
            SubtitleSegment(segment_id=1, start=0.0, end=3.0, text="Nobody really knows.", line_count=1, char_count=20, word_count=3, reading_cps=6.0),
            SubtitleSegment(segment_id=2, start=3.0, end=6.0, text="The story keeps moving.", line_count=1, char_count=23, word_count=4, reading_cps=7.0),
        ],
        report=AlignmentReport(
            engine="mfa",
            fallback_used=False,
            fallback_reason=None,
            audio_duration=6.0,
            normalized_audio_properties={"sample_rate": 16000},
            script_word_count=7,
            aligned_word_count=7,
            approximate_word_count=0,
            dropped_word_count=0,
            mismatch_count=0,
            segment_count=2,
            average_segment_duration=3.0,
            warnings=[],
            status="success",
        ),
        artifacts=AlignmentArtifacts(
            output_dir=run_dir,
            final_srt=final_srt,
            words_json=words_json,
            segments_json=segments_json,
            alignment_report=report_json,
            normalized_audio=normalized,
            run_log=run_log,
        ),
        logs=["alignment ok"],
    )


class PipelineTests(unittest.TestCase):
    def test_visual_bible_uses_clean_source_script_as_primary_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            runner = FakeCliRunner()
            with patch("tool1_dashboard.service.EPISODES_ROOT", temp_path / "videos"), \
                 patch("tool1_dashboard.service.AGENTS_ROOT", temp_path / "config" / "agents"), \
                 patch("tool1_dashboard.templates.AGENTS_ROOT", temp_path / "config" / "agents"), \
                 patch("tool1_dashboard.service.run_alignment_job", side_effect=lambda **kwargs: fake_alignment_result(kwargs["output_root"])):
                service = Tool1Service(db=Tool1Database(temp_path / "db.sqlite"), cli_runner=runner)
                created = service.create_job(
                    title="Bible Context Test",
                    audio_name="voice.wav",
                    audio_bytes=b"RIFF",
                    script_name="script.txt",
                    script_bytes=b"Abraham waits in the desert.\n\nIshmael watches the horizon.",
                    language_code="en",
                    leading_video_scene_count=1,
                )
                service._process_job(created["job"])

                visual_bible_calls = [call for call in runner.calls if "world_style" in call["schema"]["properties"]]  # type: ignore[index]
                self.assertEqual(len(visual_bible_calls), 1)
                user_prompt = str(visual_bible_calls[0]["user_prompt"])
                self.assertIn("Create a consistency guide for this video project.", user_prompt)
                self.assertIn("Source script payload:", user_prompt)
                self.assertNotIn("Approved timeline payload:", user_prompt)
                self.assertIn("Abraham waits in the desert.", user_prompt)
                self.assertIn("Ishmael watches the horizon.", user_prompt)
                self.assertEqual(visual_bible_calls[0]["provider"], "claude")
                self.assertEqual(visual_bible_calls[0]["model"], "haiku")

    def test_prompt_enrichment_normalizes_character_refs_and_infers_missing_refs(self) -> None:
        visual_bible = {
            "characters": [
                {
                    "character_id": "abraham",
                    "label": "Abraham",
                    "visual_description": "Elderly Middle Eastern man with weathered olive-brown skin and a full white beard",
                },
                {
                    "character_id": "ishmael",
                    "label": "Ishmael",
                    "visual_description": "Young desert traveler with medium-brown skin and lean features",
                },
            ]
        }
        entries = [
            {
                "scene_id": "scene_001",
                "asset_type": "image",
                "subject": "comparison of Ishmael's place in Islamic and Hebrew traditions",
                "setting": "paired manuscript pages",
                "composition": "clear left-right comparison built around Ishmael's placement and scale",
                "character_refs": ["Ishmael: medium-brown skin, strong features"],
            },
            {
                "scene_id": "scene_002",
                "asset_type": "image",
                "subject": "A father facing an impossible choice",
                "setting": "Dim tent threshold stripped of distraction",
                "composition": "Tight close-up focused on Abraham's face and gripping hand, with Ishmael blurred just beyond",
                "character_refs": [],
            },
            {
                "scene_id": "scene_003",
                "asset_type": "image",
                "subject": "Abraham Accords signing at the White House",
                "setting": "White House South Lawn",
                "composition": "Wide symmetrical view centered on desk and flag line",
                "character_refs": [],
            },
        ]

        enriched = Tool1Service._enrich_prompt_entries(entries, visual_bible)

        self.assertIn("Ishmael (young desert traveler with medium-brown skin", enriched[0]["subject"])
        self.assertIn("Abraham (elderly Middle Eastern man with weathered olive-brown skin", enriched[1]["subject"])
        self.assertIn("Ishmael (young desert traveler with medium-brown skin", enriched[1]["subject"])
        self.assertEqual(enriched[2]["subject"], "Abraham Accords signing at the White House")

    def test_successful_pipeline_reaches_review_with_visual_bible_and_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("tool1_dashboard.service.EPISODES_ROOT", temp_path / "videos"), \
                 patch("tool1_dashboard.service.AGENTS_ROOT", temp_path / "config" / "agents"), \
                 patch("tool1_dashboard.templates.AGENTS_ROOT", temp_path / "config" / "agents"), \
                 patch("tool1_dashboard.service.run_alignment_job", side_effect=lambda **kwargs: fake_alignment_result(kwargs["output_root"])):
                service = Tool1Service(db=Tool1Database(temp_path / "db.sqlite"), cli_runner=FakeCliRunner())
                created = service.create_job(
                    title="Test Video",
                    audio_name="voice.wav",
                    audio_bytes=b"RIFF",
                    script_name="script.txt",
                    script_bytes=b"hello",
                    language_code="en",
                    leading_video_scene_count=1,
                )
                service._process_job(created["job"])
                detail = service.get_job_detail(created["job"]["id"])
                self.assertEqual(detail["job"]["board_status"], "Review")
                self.assertTrue(detail["job"]["visual_bible_path"])
                self.assertTrue(detail["job"]["visual_bible_path"].endswith("consistency_guide.json"))
                self.assertTrue(detail["job"]["prompt_list_draft_path"])
                self.assertTrue(detail["job"]["prompt_blueprint_path"])
                self.assertIsNotNone(detail["artifacts"]["consistency_guide"])
                self.assertTrue((temp_path / "videos" / created["job"]["id"] / "review" / "video_prompt_list_draft.txt").exists())
                self.assertTrue((temp_path / "videos" / created["job"]["id"] / "review" / "image_prompt_list_draft.txt").exists())
                self.assertIn("elderly desert prophet", detail["artifacts"]["video_prompt_list_draft"].lower())
                self.assertIn("elderly desert prophet", detail["artifacts"]["image_prompt_list_draft"].lower())
                self.assertNotIn("SUBJ:", detail["artifacts"]["video_prompt_list_draft"])
                self.assertNotIn("SUBJ:", detail["artifacts"]["image_prompt_list_draft"])
                self.assertEqual([scene["asset_type"] for scene in detail["artifacts"]["timeline"]], ["video", "image"])
                self.assertEqual(detail["job"]["scene_planning_model"], "haiku")
                self.assertEqual(detail["job"]["visual_bible_model"], "haiku")
                self.assertEqual(detail["job"]["video_prompt_model"], "gpt-5.4")
                self.assertEqual(detail["job"]["image_prompt_model"], "gpt-5.4")

                finalized = service.finalize_job(created["job"]["id"])
                self.assertEqual(finalized["job"]["board_status"], "Done")
                self.assertTrue(finalized["job"]["export_video_prompt_list_path"])
                self.assertTrue(finalized["job"]["export_image_prompt_list_path"])
                video_prompts = Path(finalized["job"]["export_video_prompt_list_path"]).read_text(encoding="utf-8").splitlines()
                image_prompts = Path(finalized["job"]["export_image_prompt_list_path"]).read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(video_prompts), 1)
                self.assertEqual(len(image_prompts), 1)
                self.assertTrue(any(call["provider"] == "claude" and call["model"] == "haiku" for call in service.cli_runner.calls))
                self.assertTrue(any(call["provider"] == "codex" and call["model"] == "gpt-5.4" for call in service.cli_runner.calls))

    def test_invalid_split_prompt_output_moves_job_to_attention(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("tool1_dashboard.service.EPISODES_ROOT", temp_path / "videos"), \
                 patch("tool1_dashboard.service.AGENTS_ROOT", temp_path / "config" / "agents"), \
                 patch("tool1_dashboard.templates.AGENTS_ROOT", temp_path / "config" / "agents"), \
                 patch("tool1_dashboard.service.run_alignment_job", side_effect=lambda **kwargs: fake_alignment_result(kwargs["output_root"])):
                service = Tool1Service(db=Tool1Database(temp_path / "db.sqlite"), cli_runner=FakeCliRunner(malformed_image_prompts=True))
                created = service.create_job(
                    title="Broken Video",
                    audio_name="voice.wav",
                    audio_bytes=b"RIFF",
                    script_name="script.txt",
                    script_bytes=b"hello",
                    language_code="en",
                    leading_video_scene_count=1,
                )
                service._process_job(created["job"])
                detail = service.get_job_detail(created["job"]["id"])
                self.assertEqual(detail["job"]["board_status"], "Needs Attention")
                self.assertIn("Prompt 1", detail["job"]["last_error"])


if __name__ == "__main__":
    unittest.main()
