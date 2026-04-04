"""Tests for the translation module (chunker, prompts, service, profile CRUD)."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from tool1_dashboard.translation.chunker import (
    TranslationChunk,
    build_scene_aware_chunks,
    build_text_chunks,
)
from tool1_dashboard.translation.prompts import build_translation_prompt, build_translation_repair_prompt
from tool1_dashboard.translation.service import TranslationService
from tool1_dashboard.translation.adapter import TranslationAdapter, TranslationError
from tool1_dashboard.translation_profiles import (
    normalize_openai_model,
    recommended_openai_model,
    sanitize_translation_profile,
)
from tool1_dashboard.database import Tool1Database
from tool1_dashboard.service import Tool1Service


class FakeHttpxResponse:

    def __init__(self, status_code: int, payload: dict[str, Any], text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeHttpxClient:

    def __init__(self, response: FakeHttpxResponse, recorder: list[dict[str, Any]]) -> None:
        self.response = response
        self.recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, headers: dict[str, str] | None = None, json: dict[str, Any] | None = None):
        self.recorder.append({
            "url": url,
            "headers": headers or {},
            "json": json or {},
        })
        return self.response


class SceneAwareChunkingTests(unittest.TestCase):

    def _make_scenes(self, word_counts: list[int]) -> list[dict[str, Any]]:
        scenes = []
        for i, wc in enumerate(word_counts):
            scenes.append({
                "scene_id": f"scene-{i + 1}",
                "text": " ".join(f"word{j}" for j in range(wc)),
            })
        return scenes

    def test_single_scene_under_limit(self):
        scenes = self._make_scenes([100])
        chunks = build_scene_aware_chunks(scenes, max_words_per_chunk=800)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].scene_ids, ["scene-1"])
        self.assertEqual(chunks[0].word_count, 100)

    def test_multiple_scenes_grouped(self):
        scenes = self._make_scenes([200, 200, 200])
        chunks = build_scene_aware_chunks(scenes, max_words_per_chunk=800)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].scene_ids, ["scene-1", "scene-2", "scene-3"])
        self.assertEqual(chunks[0].word_count, 600)

    def test_scenes_split_at_boundary(self):
        scenes = self._make_scenes([400, 400, 400])
        chunks = build_scene_aware_chunks(scenes, max_words_per_chunk=800)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].scene_ids, ["scene-1", "scene-2"])
        self.assertEqual(chunks[0].word_count, 800)
        self.assertEqual(chunks[1].scene_ids, ["scene-3"])
        self.assertEqual(chunks[1].word_count, 400)

    def test_large_scene_gets_own_chunk(self):
        scenes = self._make_scenes([100, 1000, 100])
        chunks = build_scene_aware_chunks(scenes, max_words_per_chunk=800)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].scene_ids, ["scene-1"])
        self.assertEqual(chunks[1].scene_ids, ["scene-2"])
        self.assertEqual(chunks[1].word_count, 1000)
        self.assertEqual(chunks[2].scene_ids, ["scene-3"])

    def test_never_splits_scene_across_chunks(self):
        scenes = self._make_scenes([300, 300, 300])
        chunks = build_scene_aware_chunks(scenes, max_words_per_chunk=500)
        # scene-1 (300) fits, scene-2 (300) would make 600 > 500, so new chunk
        self.assertEqual(len(chunks), 3)
        for chunk in chunks:
            self.assertEqual(len(chunk.scene_ids), 1)

    def test_empty_scenes_skipped(self):
        scenes = [
            {"scene_id": "s1", "text": "hello world"},
            {"scene_id": "s2", "text": ""},
            {"scene_id": "s3", "text": "goodbye world"},
        ]
        chunks = build_scene_aware_chunks(scenes, max_words_per_chunk=800)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].scene_ids, ["s1", "s3"])

    def test_source_script_is_preserved_when_scene_text_is_lossy(self):
        source_script = "\n\n".join([
            "This is how it began.",
            "Welcome to True Light.",
            "And if this is your first time here, please subscribe.",
            "Now. Let us go deeper.",
            "The disciples approached Jesus and asked him where to prepare the Passover meal.",
        ])
        scenes = [
            {"scene_id": "scene-1", "text": "This is how it began."},
            {"scene_id": "scene-2", "text": "Welcome to True Light."},
            {
                "scene_id": "scene-3",
                "text": "The disciples approached Jesus and asked him where to prepare the Passover meal.",
            },
        ]

        chunks = build_scene_aware_chunks(
            scenes,
            max_words_per_chunk=200,
            source_script=source_script,
        )

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].scene_ids, ["scene-1", "scene-2", "scene-3"])
        self.assertIn("And if this is your first time here, please subscribe.", chunks[0].text)
        self.assertIn("Now. Let us go deeper.", chunks[0].text)


class TextFallbackChunkingTests(unittest.TestCase):

    def test_short_text_single_chunk(self):
        text = "Hello world. This is a test."
        chunks = build_text_chunks(text, max_words=800)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, text)

    def test_paragraph_splitting(self):
        para1 = " ".join(f"word{i}" for i in range(500))
        para2 = " ".join(f"word{i}" for i in range(500))
        text = f"{para1}\n\n{para2}"
        chunks = build_text_chunks(text, max_words=600)
        self.assertEqual(len(chunks), 2)
        self.assertIn("word0", chunks[0].text)

    def test_large_paragraph_sentence_splitting(self):
        sentences = [f"This is sentence number {i}." for i in range(200)]
        text = " ".join(sentences)
        chunks = build_text_chunks(text, max_words=100)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(chunk.word_count, 200)  # some tolerance


class PromptBuildingTests(unittest.TestCase):

    def test_single_chunk_no_context(self):
        prompt = build_translation_prompt(
            chunk="Hello world",
            context="",
            source_lang="English",
            target_lang="Spanish",
            chunk_index=0,
            total_chunks=1,
        )
        self.assertIn("English", prompt)
        self.assertIn("Spanish", prompt)
        self.assertIn("Hello world", prompt)
        self.assertNotIn("chunk", prompt.lower().split("STRICT RULES")[0])
        self.assertNotIn("CONTEXT from previous", prompt)

    def test_multi_chunk_with_context(self):
        prompt = build_translation_prompt(
            chunk="Second part of text",
            context="last words from previous chunk",
            source_lang="English",
            target_lang="French",
            chunk_index=1,
            total_chunks=3,
        )
        self.assertIn("chunk 2 of 3", prompt)
        self.assertIn("CONTEXT from previous section", prompt)
        self.assertIn("last words from previous chunk", prompt)
        self.assertIn("Second part of text", prompt)

    def test_template_variables_all_replaced(self):
        prompt = build_translation_prompt(
            chunk="test",
            context="ctx",
            source_lang="SRC",
            target_lang="TGT",
            chunk_index=0,
            total_chunks=2,
            channel_name="MyChannel",
        )
        # No unresolved placeholders
        self.assertNotIn("{source_lang}", prompt)
        self.assertNotIn("{target_lang}", prompt)
        self.assertNotIn("{text}", prompt)
        self.assertNotIn("{context_section}", prompt)
        self.assertNotIn("{chunk_note}", prompt)

    def test_prompt_includes_target_language_only_and_no_duplication_rules(self):
        prompt = build_translation_prompt(
            chunk="Subscribe to True Light",
            context="",
            source_lang="English",
            target_lang="Spanish",
            chunk_index=0,
            total_chunks=1,
            source_channel_name="True Light",
            target_channel_name="Biblo Viral",
        )
        self.assertIn("Translate EVERYTHING into Spanish", prompt)
        self.assertIn("Do NOT output both the original text and the translation", prompt)
        self.assertIn("translate those calls to action fully into Spanish", prompt)
        self.assertIn('"True Light"', prompt)
        self.assertIn('"Biblo Viral"', prompt)

    def test_repair_prompt_includes_issues_and_rejected_output(self):
        prompt = build_translation_repair_prompt(
            chunk="Subscribe to True Light",
            invalid_output="Subscribe to Biblo Viral",
            issues=["Output still contains English CTA wording."],
            context="ctx words",
            source_lang="English",
            target_lang="Spanish",
            source_channel_name="True Light",
            target_channel_name="Biblo Viral",
        )
        self.assertIn("[WHY THE PREVIOUS OUTPUT WAS REJECTED]", prompt)
        self.assertIn("Output still contains English CTA wording.", prompt)
        self.assertIn("Subscribe to Biblo Viral", prompt)
        self.assertIn("ctx words", prompt)


class TranslationAdapterOpenAiTests(unittest.TestCase):

    def _run_async(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_gpt5_translation_uses_minimal_reasoning_and_reads_message_text(self):
        recorder: list[dict[str, Any]] = []
        response = FakeHttpxResponse(200, {
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "Hola mundo"},
                    ],
                },
            ],
        })
        with patch(
            "tool1_dashboard.translation.adapter.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: FakeHttpxClient(response, recorder),
        ):
            adapter = TranslationAdapter()
            text = self._run_async(adapter.translate_chunk(
                provider="openai",
                api_key="sk-test",
                model="gpt-5-nano",
                prompt="Translate to Spanish.",
            ))

        self.assertEqual(text, "Hola mundo")
        request_payload = recorder[0]["json"]
        self.assertEqual(request_payload["reasoning"]["effort"], "minimal")
        self.assertEqual(request_payload["text"]["verbosity"], "low")

    def test_gpt54_request_uses_low_reasoning_hint(self):
        request_payload = TranslationAdapter._build_openai_request_body(
            model="gpt-5.4",
            prompt="Translate to Italian.",
        )
        self.assertEqual(request_payload["reasoning"]["effort"], "low")
        self.assertEqual(request_payload["text"]["verbosity"], "low")

    def test_incomplete_openai_response_raises_translation_error(self):
        recorder: list[dict[str, Any]] = []
        response = FakeHttpxResponse(200, {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"type": "reasoning", "summary": []}],
        })
        with patch(
            "tool1_dashboard.translation.adapter.httpx.AsyncClient",
            side_effect=lambda *args, **kwargs: FakeHttpxClient(response, recorder),
        ):
            adapter = TranslationAdapter()
            with self.assertRaises(TranslationError) as ctx:
                self._run_async(adapter.translate_chunk(
                    provider="openai",
                    api_key="sk-test",
                    model="gpt-5-nano",
                    prompt="Translate to Spanish.",
                ))

        self.assertIn("max_output_tokens", str(ctx.exception))


class TranslationServiceTests(unittest.TestCase):

    def _run_async(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def _make_fake_adapter(self, responses: list[str | Exception]) -> TranslationAdapter:
        adapter = TranslationAdapter()
        call_count = 0

        async def fake_translate(provider, api_key, model, prompt):
            nonlocal call_count
            idx = min(call_count, len(responses) - 1)
            call_count += 1
            resp = responses[idx]
            if isinstance(resp, Exception):
                raise resp
            return resp

        adapter.translate_chunk = fake_translate  # type: ignore
        return adapter

    def test_successful_translation(self):
        adapter = self._make_fake_adapter(["Hola mundo", "Adiós mundo"])
        svc = TranslationService(adapter=adapter)
        scenes = [
            {"scene_id": "s1", "text": "Hello world"},
            {"scene_id": "s2", "text": "Goodbye world"},
        ]
        result = self._run_async(svc.translate_script(
            source_script="",
            source_lang="English",
            target_lang="Spanish",
            provider="gemini",
            api_key="fake",
            model="test",
            master_scenes=scenes,
        ))
        self.assertEqual(result.status, "done")
        self.assertEqual(len(result.chunk_results), 1)  # both scenes fit in 1 chunk
        self.assertIn("Hola mundo", result.translated_script)

    def test_context_passing(self):
        """Verify that context from chunk N is passed to chunk N+1."""
        prompts_seen: list[str] = []
        adapter = TranslationAdapter()

        async def capture_translate(provider, api_key, model, prompt):
            prompts_seen.append(prompt)
            return "Translated words " * 50  # 100 words

        adapter.translate_chunk = capture_translate  # type: ignore
        svc = TranslationService(adapter=adapter)

        scenes = [
            {"scene_id": "s1", "text": " ".join(["word"] * 500)},
            {"scene_id": "s2", "text": " ".join(["word"] * 500)},
        ]
        result = self._run_async(svc.translate_script(
            source_script="",
            source_lang="English",
            target_lang="Spanish",
            provider="gemini",
            api_key="fake",
            model="test",
            master_scenes=scenes,
            max_words_per_chunk=600,
            context_tail_words=50,
        ))
        self.assertEqual(len(prompts_seen), 2)
        # First prompt should have no context
        self.assertNotIn("CONTEXT from previous", prompts_seen[0])
        # Second prompt should contain context
        self.assertIn("CONTEXT from previous", prompts_seen[1])

    def test_chunk_error_recovery(self):
        """One chunk failing should not abort the entire translation."""
        adapter = self._make_fake_adapter([
            TranslationError("Gemini", 429, "Rate limited"),
            "Chunk 2 translated ok",
        ])
        svc = TranslationService(adapter=adapter)
        scenes = [
            {"scene_id": "s1", "text": " ".join(["word"] * 500)},
            {"scene_id": "s2", "text": " ".join(["word"] * 500)},
        ]
        result = self._run_async(svc.translate_script(
            source_script="",
            source_lang="English",
            target_lang="Spanish",
            provider="gemini",
            api_key="fake",
            model="test",
            master_scenes=scenes,
            max_words_per_chunk=600,
        ))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.chunk_results[0].status, "error")
        self.assertEqual(result.chunk_results[1].status, "ok")

    def test_empty_chunk_response_is_treated_as_error(self):
        adapter = self._make_fake_adapter([""])
        svc = TranslationService(adapter=adapter)
        result = self._run_async(svc.translate_script(
            source_script="Hello world. This should fail if the model returns nothing.",
            source_lang="English",
            target_lang="Spanish",
            provider="openai",
            api_key="fake",
            model="gpt-5-nano",
        ))
        self.assertEqual(result.status, "error")
        self.assertEqual(result.chunk_results[0].status, "error")
        self.assertIn("empty translation", (result.chunk_results[0].error or "").lower())

    def test_all_chunks_fail(self):
        adapter = self._make_fake_adapter([
            TranslationError("OpenAI", 500, "Server error"),
        ])
        svc = TranslationService(adapter=adapter)
        result = self._run_async(svc.translate_script(
            source_script="Hello world. This is a test.",
            source_lang="English",
            target_lang="Spanish",
            provider="openai",
            api_key="fake",
            model="test",
        ))
        self.assertEqual(result.status, "error")

    def test_empty_input(self):
        adapter = self._make_fake_adapter([])
        svc = TranslationService(adapter=adapter)
        result = self._run_async(svc.translate_script(
            source_script="",
            source_lang="English",
            target_lang="Spanish",
            provider="gemini",
            api_key="fake",
            model="test",
            master_scenes=[],
        ))
        self.assertEqual(result.status, "done")
        self.assertEqual(result.translated_script, "")

    def test_mixed_language_chunk_is_repaired_before_acceptance(self):
        adapter = self._make_fake_adapter([
            "This source paragraph should not remain untranslated.\n\nEste parrafo aun no sirve.",
            "Este parrafo ya fue traducido correctamente.",
        ])
        svc = TranslationService(adapter=adapter)
        result = self._run_async(svc.translate_script(
            source_script="This source paragraph should not remain untranslated.",
            source_lang="English",
            target_lang="Spanish",
            provider="openai",
            api_key="fake",
            model="gpt-5-nano",
        ))
        self.assertEqual(result.status, "done")
        self.assertEqual(result.translated_script, "Este parrafo ya fue traducido correctamente.")
        self.assertEqual(result.chunk_results[0].status, "ok")

    def test_inflated_duplicate_chunk_fails_after_repair_attempt(self):
        duplicate = "Hello world. " * 120
        adapter = self._make_fake_adapter([duplicate, duplicate])
        svc = TranslationService(adapter=adapter)
        result = self._run_async(svc.translate_script(
            source_script="Hello world.",
            source_lang="English",
            target_lang="Spanish",
            provider="openai",
            api_key="fake",
            model="gpt-5-nano",
        ))
        self.assertEqual(result.status, "error")
        self.assertEqual(result.chunk_results[0].status, "error")
        self.assertIn("quality check failed", (result.chunk_results[0].error or "").lower())


class TranslationValidationTests(unittest.TestCase):

    def test_validated_translation_script_repairs_exact_subscribe_prefix(self):
        result = SimpleNamespace(
            translated_script="Subscribe to Biblo Viral if you need a community.",
            status="done",
            chunk_results=[],
        )
        translated = Tool1Service._validated_translation_script(
            result,
            language_code="es",
            source_script="Subscribe to True Light if you need a community.",
            source_channel_name="True Light",
            target_channel_name="Biblo Viral",
        )
        self.assertEqual(translated, "Suscríbete a Biblo Viral if you need a community.")

    def test_validated_translation_script_rejects_leaked_source_paragraphs(self):
        result = SimpleNamespace(
            translated_script="This is a leaked English paragraph from the source.\n\nEste es el texto corregido.",
            status="done",
            chunk_results=[],
        )
        with self.assertRaises(ValueError) as ctx:
            Tool1Service._validated_translation_script(
                result,
                language_code="es",
                source_script="This is a leaked English paragraph from the source.",
            )
        self.assertIn("source paragraphs", str(ctx.exception).lower())


class TranslationProfileCrudTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.db = Tool1Database(Path(self._tmpdir) / "test.db")
        self.db.initialize()

    def test_create_and_list(self):
        from tool1_dashboard.runtime import utc_now
        now = utc_now()
        self.db.create_translation_profile({
            "id": "tp1",
            "name": "Test Profile",
            "provider": "gemini",
            "api_key_ref": "fake-key",
            "model": "gemini-pro",
            "is_default": 0,
            "created_at": now,
            "updated_at": now,
        })
        profiles = self.db.list_translation_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["name"], "Test Profile")
        self.assertEqual(profiles[0]["provider"], "gemini")

    def test_update(self):
        from tool1_dashboard.runtime import utc_now
        now = utc_now()
        self.db.create_translation_profile({
            "id": "tp2",
            "name": "Old Name",
            "provider": "openai",
            "api_key_ref": "key",
            "model": "gpt-4",
            "is_default": 0,
            "created_at": now,
            "updated_at": now,
        })
        self.db.update_translation_profile("tp2", name="New Name", model="gpt-4o")
        profile = self.db.get_translation_profile("tp2")
        self.assertIsNotNone(profile)
        self.assertEqual(profile["name"], "New Name")
        self.assertEqual(profile["model"], "gpt-4o")

    def test_delete(self):
        from tool1_dashboard.runtime import utc_now
        now = utc_now()
        self.db.create_translation_profile({
            "id": "tp3",
            "name": "To Delete",
            "provider": "anthropic",
            "api_key_ref": "key",
            "model": "claude-3",
            "is_default": 0,
            "created_at": now,
            "updated_at": now,
        })
        self.db.delete_translation_profile("tp3")
        self.assertIsNone(self.db.get_translation_profile("tp3"))
        self.assertEqual(len(self.db.list_translation_profiles()), 0)


class TranslationRetryFlowTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.db = Tool1Database(Path(self._tmpdir) / "test.db")
        self.db.initialize()
        self.service = Tool1Service(db=self.db)

    def test_single_language_retry_uses_master_scenes_when_timeline_exists(self):
        from tool1_dashboard.runtime import utc_now

        now = utc_now()
        self.db.create_translation_profile({
            "id": "tp1",
            "name": "Test Profile",
            "provider": "openai",
            "api_key_ref": "sk-test",
            "model": "gpt-5-nano",
            "is_default": 0,
            "created_at": now,
            "updated_at": now,
        })
        project_payload = self.service.create_niche_project(
            name="Retry Project",
            configured_languages=["en", "es"],
            language_translation_profiles={"es": "tp1"},
        )
        project = project_payload["project"]
        episode_payload = self.service.submit_episode(
            project["id"],
            title="Retry Episode",
            script_text="Hello world.",
        )
        episode = episode_payload["episode"]
        workspace = Path(episode["workspace_dir"])
        timeline_path = workspace / "timeline_draft.json"
        timeline_path.write_text(
            '[{"scene_id":"scene_001","start":0.0,"end":3.0,"duration":3.0,"text":"Hello world.","asset_type":"image"}]',
            encoding="utf-8",
        )
        self.db.update_episode(episode["id"], timeline_draft_path=str(timeline_path), updated_at=now)

        mocked_result = SimpleNamespace(
            translated_script="Hola mundo.",
            status="done",
            chunk_results=[],
        )
        with patch(
            "tool1_dashboard.translation.TranslationService.translate_script",
            new=AsyncMock(return_value=mocked_result),
        ) as mocked_translate:
            self.service._episode_retry_single_translation(episode["id"], "es")

        self.assertTrue(mocked_translate.await_args.kwargs["master_scenes"])


class TranslationProfilePresentationTests(unittest.TestCase):

    def test_sanitize_translation_profile_masks_api_key(self):
        sanitized = sanitize_translation_profile({
            "id": "tp-openai",
            "name": "OpenAI Main",
            "provider": "openai",
            "api_key_ref": "sk-test-secret-1234",
            "model": "gpt-5.4-mini",
        })
        self.assertNotIn("api_key_ref", sanitized)
        self.assertTrue(sanitized["has_api_key"])
        self.assertTrue(sanitized["api_key_masked"].startswith("sk-t"))
        self.assertEqual(sanitized["provider_label"], "OpenAI API")
        self.assertEqual(sanitized["provider_mode"], "api")
        self.assertFalse(sanitized["provider_placeholder"])

    def test_sanitize_legacy_translation_profile_keeps_legacy_label(self):
        sanitized = sanitize_translation_profile({
            "id": "tp-legacy",
            "name": "Old Anthropic",
            "provider": "anthropic",
            "api_key_ref": "",
            "model": "claude-3",
        })
        self.assertEqual(sanitized["provider_label"], "Anthropic API (legacy)")
        self.assertEqual(sanitized["provider_mode"], "legacy")
        self.assertFalse(sanitized["provider_editable"])

    def test_normalize_openai_model_filters_non_text_models(self):
        self.assertIsNone(normalize_openai_model({"id": "gpt-image-1"}))
        model = normalize_openai_model({"id": "gpt-4o-mini", "owned_by": "openai"})
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual(model["capability_label"], "Affordable")

    def test_recommended_openai_model_prefers_default_order(self):
        recommended = recommended_openai_model([
            {"id": "gpt-4o", "priority": 40},
            {"id": "gpt-5.4-mini", "priority": 10},
        ])
        self.assertEqual(recommended, "gpt-5.4-mini")


if __name__ == "__main__":
    unittest.main()
