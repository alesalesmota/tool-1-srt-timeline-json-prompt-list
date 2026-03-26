"""Tests for the TTS module (chunker, audio, constants, DB, manager)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import time
import unittest
import wave
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from tool1_dashboard.database import Tool1Database
from tool1_dashboard.runtime import utc_now
from tool1_dashboard.service import Tool1Service
from tool1_dashboard.tts.audio import generate_silence_wav, merge_wav_chunks_streaming
from tool1_dashboard.tts.chunker import TTSChunk, chunk_text_for_tts
from tool1_dashboard.tts.constants import (
    CHUNK_MAX_CHARS,
    INTERACTIVE_IDLE_SHUTDOWN_SECONDS,
    WORKER_IDLE_RECHECK_SECONDS,
    XTTS_LANG_MAP,
    map_language_code,
)
from tool1_dashboard.tts.voice_config import (
    build_xtts_inference_kwargs,
    normalize_voice_tts_config,
)


class ChunkerTests(unittest.TestCase):

    def test_empty_text(self):
        self.assertEqual(chunk_text_for_tts(""), [])
        self.assertEqual(chunk_text_for_tts("   "), [])

    def test_short_text_single_chunk(self):
        chunks = chunk_text_for_tts("Hello world.")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, "Hello world.")
        self.assertEqual(chunks[0].index, 0)

    def test_sentence_splitting(self):
        text = "First sentence. " * 20 + "Second sentence. " * 20
        chunks = chunk_text_for_tts(text, max_chars=200)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(chunk.char_count, 200)

    def test_hard_limit_no_punctuation(self):
        text = " ".join(["word"] * 100)  # ~500 chars, no sentence endings
        chunks = chunk_text_for_tts(text, max_chars=100)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(chunk.char_count, 100)

    def test_long_single_word(self):
        text = "a" * 500
        chunks = chunk_text_for_tts(text, max_chars=200)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(chunk.char_count, 200)

    def test_chunk_indices_sequential(self):
        text = "Sentence one. Sentence two. Sentence three. Sentence four. Sentence five."
        chunks = chunk_text_for_tts(text, max_chars=30)
        for i, chunk in enumerate(chunks):
            self.assertEqual(chunk.index, i)

    def test_preserves_all_text(self):
        text = "The quick brown fox jumps over the lazy dog. " * 10
        chunks = chunk_text_for_tts(text, max_chars=100)
        reassembled = " ".join(c.text for c in chunks)
        # All words present
        for word in ["quick", "brown", "fox", "lazy", "dog"]:
            self.assertIn(word, reassembled)


class LanguageMappingTests(unittest.TestCase):

    def test_direct_codes(self):
        self.assertEqual(map_language_code("en"), "en")
        self.assertEqual(map_language_code("pt"), "pt")
        self.assertEqual(map_language_code("zh"), "zh-cn")

    def test_creator_studio_codes(self):
        self.assertEqual(map_language_code("pt-BR"), "pt")
        self.assertEqual(map_language_code("en-US"), "en")
        self.assertEqual(map_language_code("zh-CN"), "zh-cn")

    def test_unknown_defaults_to_en(self):
        self.assertEqual(map_language_code("xx"), "en")
        self.assertEqual(map_language_code(""), "en")

    def test_case_insensitive(self):
        self.assertEqual(map_language_code("PT-BR"), "pt")
        self.assertEqual(map_language_code("EN"), "en")


class AudioTests(unittest.TestCase):

    def test_generate_silence_wav(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "silence.wav"
            generate_silence_wav(path, duration_seconds=0.5, sample_rate=24000)
            self.assertTrue(path.exists())
            with wave.open(str(path), "rb") as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getframerate(), 24000)
                expected_frames = int(24000 * 0.5)
                self.assertEqual(wf.getnframes(), expected_frames)

    def test_merge_wav_chunks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create 3 silence WAV files
            paths = []
            for i in range(3):
                p = Path(tmpdir) / f"chunk_{i}.wav"
                generate_silence_wav(p, duration_seconds=0.1, sample_rate=24000)
                paths.append(p)

            merged_path = Path(tmpdir) / "merged.wav"
            merge_wav_chunks_streaming(paths, merged_path)

            self.assertTrue(merged_path.exists())
            with wave.open(str(merged_path), "rb") as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getframerate(), 24000)
                expected = int(24000 * 0.1) * 3
                self.assertEqual(wf.getnframes(), expected)

    def test_merge_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            merged_path = Path(tmpdir) / "empty_merge.wav"
            merge_wav_chunks_streaming([], merged_path)
            self.assertTrue(merged_path.exists())
            with wave.open(str(merged_path), "rb") as wf:
                self.assertGreater(wf.getnframes(), 0)


class WorkerHeartbeatDbTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.db = Tool1Database(Path(self._tmpdir) / "test.db")
        self.db.initialize()

    def test_record_and_get_heartbeat(self):
        self.db.record_worker_heartbeat(
            worker_id="w1",
            status="idle",
            current_job_id=None,
            pid=1234,
            started_at=time.time(),
        )
        hb = self.db.get_latest_worker_heartbeat()
        self.assertIsNotNone(hb)
        self.assertEqual(hb["worker_id"], "w1")
        self.assertEqual(hb["status"], "idle")
        self.assertEqual(hb["pid"], 1234)

    def test_heartbeat_upsert(self):
        now = time.time()
        self.db.record_worker_heartbeat(
            worker_id="w1", status="idle", current_job_id=None, pid=100, started_at=now,
        )
        self.db.record_worker_heartbeat(
            worker_id="w1", status="processing", current_job_id="j1", pid=100, started_at=now,
        )
        hb = self.db.get_latest_worker_heartbeat()
        self.assertEqual(hb["status"], "processing")
        self.assertEqual(hb["current_job_id"], "j1")


class TTSJobDbTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.db = Tool1Database(Path(self._tmpdir) / "test.db")
        self.db.initialize()

    def _make_job(self, job_id: str, status: str = "queued", priority: int = 10) -> None:
        now = time.time()
        self.db.create_tts_job({
            "job_id": job_id,
            "job_type": "generate",
            "profile_id": "p1",
            "status": status,
            "payload_json": json.dumps({"texts": ["hello"]}),
            "meta_json": "{}",
            "queue_priority": priority,
            "created_at": now,
            "updated_at": now,
        })

    def test_claim_next_tts_job(self):
        self._make_job("j1")
        self._make_job("j2")
        claimed = self.db.claim_next_tts_job("worker-1")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["job_id"], "j1")
        self.assertEqual(claimed["status"], "processing")
        self.assertEqual(claimed["worker_id"], "worker-1")

        # j1 is now processing, so next claim returns j2
        claimed2 = self.db.claim_next_tts_job("worker-1")
        self.assertIsNotNone(claimed2)
        self.assertEqual(claimed2["job_id"], "j2")

    def test_claim_empty_queue(self):
        self.assertIsNone(self.db.claim_next_tts_job("w1"))

    def test_claim_respects_priority(self):
        self._make_job("low", priority=20)
        self._make_job("high", priority=5)
        claimed = self.db.claim_next_tts_job("w1")
        self.assertEqual(claimed["job_id"], "high")

    def test_requeue_stale(self):
        now = time.time()
        self.db.create_tts_job({
            "job_id": "stale1",
            "job_type": "generate",
            "profile_id": "p1",
            "status": "processing",
            "payload_json": "{}",
            "meta_json": "{}",
            "queue_priority": 10,
            "worker_id": "dead-worker",
            "created_at": now - 200,
            "updated_at": now - 200,
        })
        count = self.db.requeue_stale_tts_jobs(90)
        self.assertEqual(count, 1)
        job = self.db.get_tts_job("stale1")
        self.assertEqual(job["status"], "queued")

    def test_requeue_does_not_touch_fresh(self):
        now = time.time()
        self.db.create_tts_job({
            "job_id": "fresh1",
            "job_type": "generate",
            "profile_id": "p1",
            "status": "processing",
            "payload_json": "{}",
            "meta_json": "{}",
            "queue_priority": 10,
            "worker_id": "active-worker",
            "created_at": now,
            "updated_at": now,
        })
        count = self.db.requeue_stale_tts_jobs(90)
        self.assertEqual(count, 0)

    def test_latest_latent_job_for_profile(self):
        now = time.time()
        self.db.create_tts_job({
            "job_id": "lat1",
            "job_type": "latent_precompute",
            "profile_id": "prof1",
            "status": "completed",
            "payload_json": "{}",
            "meta_json": "{}",
            "queue_priority": 5,
            "created_at": now,
            "updated_at": now,
        })
        job = self.db.get_latest_latent_job_for_profile("prof1")
        self.assertIsNotNone(job)
        self.assertEqual(job["job_id"], "lat1")

        self.assertIsNone(self.db.get_latest_latent_job_for_profile("nonexistent"))


class VoiceTtsConfigTests(unittest.TestCase):

    def test_defaults_to_natural_stable(self):
        config = normalize_voice_tts_config(None)
        self.assertEqual(config["preset"], "natural_stable")
        self.assertEqual(config["temperature"], 0.55)
        self.assertEqual(config["chunk_max_chars"], 180)

    def test_clamps_manual_values(self):
        config = normalize_voice_tts_config({
            "preset": "balanced",
            "temperature": 2,
            "top_p": 0.1,
            "top_k": 999,
            "speed": 0.1,
            "chunk_max_chars": 999,
            "silence_gap_seconds": -1,
        })
        self.assertEqual(config["temperature"], 0.85)
        self.assertEqual(config["top_p"], 0.6)
        self.assertEqual(config["top_k"], 80)
        self.assertEqual(config["speed"], 0.96)
        self.assertEqual(config["chunk_max_chars"], 260)
        self.assertEqual(config["silence_gap_seconds"], 0.0)

    def test_xtts_inference_kwargs_are_explicit_and_disable_internal_split(self):
        kwargs = build_xtts_inference_kwargs({
            "preset": "expressive",
            "temperature": 0.77,
            "top_p": 0.9,
            "top_k": 55,
            "speed": 1.01,
        })
        self.assertTrue(kwargs["do_sample"])
        self.assertEqual(kwargs["num_beams"], 1)
        self.assertFalse(kwargs["enable_text_splitting"])
        self.assertEqual(kwargs["temperature"], 0.77)
        self.assertEqual(kwargs["top_p"], 0.9)
        self.assertEqual(kwargs["top_k"], 55)
        self.assertEqual(kwargs["speed"], 1.01)


class DatabaseMigrationTests(unittest.TestCase):

    def test_initialize_adds_voice_tts_config_column_to_existing_db(self):
        tmpdir = Path(tempfile.mkdtemp())
        try:
            db_path = tmpdir / "legacy.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE voice_profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    language_code TEXT NOT NULL DEFAULT '',
                    audio_file TEXT NOT NULL,
                    audio_path TEXT NOT NULL,
                    latents_path TEXT,
                    has_latents INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
            connection.close()

            db = Tool1Database(db_path)
            db.initialize()
            del db

            with sqlite3.connect(db_path) as check:
                columns = {
                    row[1]
                    for row in check.execute("PRAGMA table_info(voice_profiles)").fetchall()
                }
            self.assertIn("tts_config_json", columns)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class VoiceProfileDbTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.db = Tool1Database(Path(self._tmpdir) / "test.db")
        self.db.initialize()

    def test_create_list_get(self):
        from tool1_dashboard.runtime import utc_now
        now = utc_now()
        self.db.create_voice_profile({
            "id": "vp1",
            "name": "Test Voice",
            "language_code": "pt",
            "audio_file": "vp1.wav",
            "audio_path": "/tmp/vp1.wav",
            "latents_path": None,
            "has_latents": 0,
            "created_at": now,
            "updated_at": now,
        })
        profiles = self.db.list_voice_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["name"], "Test Voice")

        profile = self.db.get_voice_profile("vp1")
        self.assertIsNotNone(profile)
        self.assertEqual(profile["language_code"], "pt")
        self.assertEqual(profile["tts_config_json"], "")

    def test_update_and_delete(self):
        from tool1_dashboard.runtime import utc_now
        now = utc_now()
        self.db.create_voice_profile({
            "id": "vp2",
            "name": "Old Name",
            "language_code": "en",
            "audio_file": "vp2.wav",
            "audio_path": "/tmp/vp2.wav",
            "latents_path": None,
            "has_latents": 0,
            "created_at": now,
            "updated_at": now,
        })
        self.db.update_voice_profile("vp2", name="New Name")
        profile = self.db.get_voice_profile("vp2")
        self.assertEqual(profile["name"], "New Name")

        self.db.delete_voice_profile("vp2")
        self.assertIsNone(self.db.get_voice_profile("vp2"))


class ManagerJobSubmissionTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.db = Tool1Database(Path(self._tmpdir) / "test.db")
        self.db.initialize()

    def test_submit_tts_job(self):
        from tool1_dashboard.tts.manager import TTSManager

        mgr = TTSManager(self.db)
        job_id = mgr.submit_tts_job(
            job_type="generate",
            profile_id="p1",
            payload={"texts": ["hello world"]},
            build_id="build-1",
            queue_priority=20,
            filename="test_narration.wav",
        )
        self.assertIsNotNone(job_id)

        job = self.db.get_tts_job(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job["job_type"], "generate")
        self.assertEqual(job["profile_id"], "p1")
        self.assertEqual(job["build_id"], "build-1")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["queue_priority"], 20)

        payload = json.loads(job["payload_json"])
        self.assertEqual(payload["texts"], ["hello world"])
        self.assertEqual(payload["original_filename"], "test_narration.wav")

    def test_set_job_control(self):
        from tool1_dashboard.tts.manager import TTSManager

        mgr = TTSManager(self.db)
        job_id = mgr.submit_tts_job(
            job_type="test_voice",
            profile_id="p1",
            payload={"text": "test"},
        )
        self.assertTrue(mgr.set_job_control(job_id, "pause"))
        job = self.db.get_tts_job(job_id)
        self.assertEqual(job["control_action"], "pause")

        self.assertTrue(mgr.set_job_control(job_id, None))
        job = self.db.get_tts_job(job_id)
        self.assertIsNone(job["control_action"])

    def test_set_job_control_missing_job(self):
        from tool1_dashboard.tts.manager import TTSManager

        mgr = TTSManager(self.db)
        self.assertFalse(mgr.set_job_control("nonexistent", "pause"))

    def test_get_job_status_parses_json(self):
        from tool1_dashboard.tts.manager import TTSManager

        mgr = TTSManager(self.db)
        job_id = mgr.submit_tts_job(
            job_type="generate",
            profile_id="p1",
            payload={"texts": ["a", "b"]},
            meta={"total_chunks": 2},
        )
        status = mgr.get_job_status(job_id)
        self.assertIsNotNone(status)
        self.assertEqual(status["payload"]["texts"], ["a", "b"])
        self.assertEqual(status["meta"]["total_chunks"], 2)

    def test_worker_health_no_heartbeat(self):
        from tool1_dashboard.tts.manager import TTSManager, WorkerRuntimeStatus

        mgr = TTSManager(self.db)
        with patch.object(
            mgr,
            "get_runtime_status",
            return_value=WorkerRuntimeStatus(
                available=True,
                missing_dependencies=[],
                error=None,
            ),
        ):
            health = mgr.get_worker_health()
        self.assertFalse(health.running)
        self.assertEqual(health.status, "sleeping")
        self.assertEqual(health.lifecycle_state, "sleeping")
        self.assertFalse(health.is_stale)

    def test_worker_health_surfaces_runtime_error(self):
        from tool1_dashboard.tts.manager import TTSManager, WorkerRuntimeStatus

        mgr = TTSManager(self.db)
        with patch.object(
            mgr,
            "get_runtime_status",
            return_value=WorkerRuntimeStatus(
                available=False,
                missing_dependencies=["Coqui TTS"],
                error="TTS runtime unavailable.",
            ),
        ):
            health = mgr.get_worker_health()
        self.assertEqual(health.status, "unavailable")
        self.assertEqual(health.lifecycle_state, "unavailable")
        self.assertEqual(health.startup_error, "TTS runtime unavailable.")
        self.assertEqual(health.missing_dependencies, ["Coqui TTS"])

    def test_ensure_worker_ready_restarts_stale_worker(self):
        from tool1_dashboard.tts.manager import TTSManager, WorkerHealth, WorkerRuntimeStatus

        mgr = TTSManager(self.db)
        with patch.object(
            mgr,
            "get_runtime_status",
            return_value=WorkerRuntimeStatus(
                available=True,
                missing_dependencies=[],
                error=None,
            ),
        ), patch.object(
            mgr,
            "get_worker_health",
            return_value=WorkerHealth(
                running=True,
                worker_id="stale-worker",
                status="idle",
                current_job_id=None,
                last_heartbeat=time.time() - 300,
                is_stale=True,
                pid=1234,
                startup_error=None,
                missing_dependencies=[],
                lifecycle_state="sleeping",
            ),
        ), patch.object(mgr, "is_worker_alive", return_value=True), patch.object(
            mgr, "stop_worker"
        ) as stop_mock, patch.object(
            mgr, "start_worker"
        ) as start_mock, patch.object(
            mgr, "_schedule_shutdown_check"
        ) as schedule_mock:
            mgr.ensure_worker_ready(intent="interactive")

        stop_mock.assert_called_once()
        start_mock.assert_called_once()
        schedule_mock.assert_called_once()

    def test_interactive_shutdown_stops_when_idle(self):
        from tool1_dashboard.tts.manager import TTSManager

        mgr = TTSManager(self.db)
        mgr._lifecycle_intent = "interactive"
        mgr._last_activity_at = time.time() - (INTERACTIVE_IDLE_SHUTDOWN_SECONDS + 1)

        with patch.object(mgr, "is_worker_alive", return_value=True), patch.object(
            mgr, "stop_worker"
        ) as stop_mock:
            mgr._evaluate_worker_shutdown()

        stop_mock.assert_called_once()

    def test_pipeline_shutdown_waits_for_active_generate_jobs(self):
        from tool1_dashboard.tts.manager import TTSManager

        mgr = TTSManager(self.db)
        mgr._lifecycle_intent = "interactive"
        with patch.object(mgr, "is_worker_alive", return_value=True), patch.object(
            mgr._db,
            "list_active_tts_jobs",
            return_value=[{"job_type": "generate", "job_id": "job-1"}],
        ), patch.object(mgr, "_schedule_shutdown_check") as schedule_mock, patch.object(
            mgr, "stop_worker"
        ) as stop_mock:
            mgr._evaluate_worker_shutdown()

        stop_mock.assert_not_called()
        schedule_mock.assert_called_once_with(WORKER_IDLE_RECHECK_SECONDS)
        self.assertEqual(mgr._lifecycle_intent, "pipeline")


class ServiceVoiceRuntimeTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.service = Tool1Service(db=Tool1Database(Path(self._tmpdir) / "test.db"))
        audio_path = Path(self._tmpdir) / "voice.wav"
        audio_path.write_bytes(b"RIFF....WAVEfmt ")
        now = utc_now()
        self.service.db.create_voice_profile({
            "id": "voice-1",
            "name": "Voice One",
            "language_code": "en",
            "audio_file": audio_path.name,
            "audio_path": str(audio_path),
            "latents_path": None,
            "has_latents": 0,
            "created_at": now,
            "updated_at": now,
        })

    def test_existing_profiles_without_tuning_resolve_default_config(self):
        profile = self.service.get_voice_profile("voice-1")
        self.assertEqual(profile["tts_config"]["preset"], "natural_stable")
        self.assertEqual(profile["tts_config"]["chunk_max_chars"], 180)

    def test_submit_voice_test_fails_fast_when_runtime_unavailable(self):
        with patch.object(
            self.service.tts_manager,
            "ensure_worker_ready",
            side_effect=RuntimeError("TTS runtime unavailable."),
        ):
            with self.assertRaisesRegex(RuntimeError, "TTS runtime unavailable"):
                self.service.submit_voice_test("voice-1", "hello world")
        self.assertEqual(self.service.db.list_active_tts_jobs(), [])

    def test_create_voice_profile_skips_latent_job_when_runtime_unavailable(self):
        from tool1_dashboard.tts.manager import WorkerRuntimeStatus

        with patch.object(
            self.service.tts_manager,
            "get_runtime_status",
            return_value=WorkerRuntimeStatus(
                available=False,
                missing_dependencies=["Coqui TTS"],
                error="TTS runtime unavailable.",
            ),
        ):
            profile = self.service.create_voice_profile(
                name="Fresh Voice",
                audio_bytes=b"RIFF....WAVEfmt ",
                audio_filename="fresh.wav",
            )
        self.assertIsNone(profile["latent_job_id"])
        self.assertEqual(profile["runtime_warning"], "TTS runtime unavailable.")
        self.assertEqual(profile["language_code"], "")
        self.assertEqual(profile["tts_config"]["preset"], "natural_stable")
        self.assertEqual(self.service.db.list_active_tts_jobs(), [])

    def test_create_voice_profile_auto_starts_interactive_latent_prep(self):
        from tool1_dashboard.tts.manager import WorkerRuntimeStatus

        with patch.object(
            self.service.tts_manager,
            "get_runtime_status",
            return_value=WorkerRuntimeStatus(
                available=True,
                missing_dependencies=[],
                error=None,
            ),
        ), patch.object(self.service.tts_manager, "ensure_worker_ready") as ensure_mock, patch.object(
            self.service.tts_manager,
            "submit_tts_job",
            return_value="latent-123",
        ) as submit_mock:
            profile = self.service.create_voice_profile(
                name="Fresh Voice",
                audio_bytes=b"RIFF....WAVEfmt ",
                audio_filename="fresh.wav",
            )

        ensure_mock.assert_called_once_with(intent="interactive")
        self.assertEqual(submit_mock.call_args.kwargs["job_type"], "latent_precompute")
        self.assertEqual(profile["latent_job_id"], "latent-123")

    def test_submit_voice_test_uses_default_sample_when_text_missing(self):
        with patch.object(self.service.tts_manager, "ensure_worker_ready") as ensure_mock, patch.object(
            self.service.tts_manager,
            "submit_tts_job",
            return_value="job-123",
        ) as submit_mock:
            result = self.service.submit_voice_test("voice-1", None)

        ensure_mock.assert_called_once_with(intent="interactive")
        payload = submit_mock.call_args.kwargs["payload"]
        self.assertEqual(result["job_id"], "job-123")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["language"], "en")
        self.assertEqual(payload["language"], "en")
        self.assertEqual(
            payload["text"],
            "This is Voice One. I am ready for the TTS workflow. Here is a calm line, a brighter line, and a softer ending so you can hear my range.",
        )
        self.assertEqual(result["text"], payload["text"])
        self.assertEqual(payload["texts"], [payload["text"]])
        self.assertEqual(payload["tts_config"]["preset"], "natural_stable")
        self.assertEqual(result["tts_config"]["preset"], "natural_stable")

    def test_update_voice_profile_persists_tts_config(self):
        updated = self.service.update_voice_profile(
            "voice-1",
            tts_config={
                "preset": "expressive",
                "temperature": 0.78,
                "top_p": 0.9,
                "top_k": 55,
                "speed": 1.02,
                "chunk_max_chars": 230,
                "silence_gap_seconds": 0.18,
            },
        )

        self.assertEqual(updated["tts_config"]["preset"], "expressive")
        self.assertEqual(updated["tts_config"]["temperature"], 0.78)
        stored = self.service.db.get_voice_profile("voice-1")
        self.assertIn('"preset": "expressive"', stored["tts_config_json"])

    def test_list_voice_profiles_includes_latest_voice_jobs(self):
        now = time.time()
        audio_output = Path(self._tmpdir) / "voice-test.wav"
        audio_output.write_bytes(b"RIFF....WAVEfmt ")

        self.service.db.create_tts_job({
            "job_id": "latent-1",
            "job_type": "latent_precompute",
            "profile_id": "voice-1",
            "status": "processing",
            "progress": "Preparing cache...",
            "payload_json": json.dumps({"profile_id": "voice-1"}),
            "meta_json": "{}",
            "queue_priority": 1,
            "created_at": now - 10,
            "updated_at": now - 5,
        })
        self.service.db.create_tts_job({
            "job_id": "test-1",
            "job_type": "test_voice",
            "profile_id": "voice-1",
            "status": "completed",
            "progress": "Voice test ready.",
            "result_path": str(audio_output),
            "filename": "voice_test.wav",
            "payload_json": json.dumps({"text": "Hello from the cloned voice.", "language": "en"}),
            "meta_json": "{}",
            "queue_priority": 0,
            "created_at": now - 2,
            "updated_at": now - 1,
            "finished_at": now,
        })

        profiles = self.service.list_voice_profiles()
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]

        self.assertEqual(profile["latest_latent_job"]["job_id"], "latent-1")
        self.assertEqual(profile["latest_latent_job"]["status"], "processing")
        self.assertEqual(profile["latest_test_job"]["job_id"], "test-1")
        self.assertEqual(profile["latest_test_job"]["payload"]["text"], "Hello from the cloned voice.")
        self.assertEqual(profile["tts_config"]["preset"], "natural_stable")
        self.assertTrue(profile["latest_test_job"]["result_available"])
        self.assertEqual(profile["latest_test_job"]["download_url"], "/api/tts-jobs/test-1/download")


class VoiceProfileUiCopyTests(unittest.TestCase):

    def test_voice_profile_ui_hides_manual_worker_controls(self):
        app_js = (
            Path(__file__).resolve().parents[1]
            / "tool1_dashboard"
            / "ui"
            / "app.js"
        ).read_text(encoding="utf-8")
        app_css = (
            Path(__file__).resolve().parents[1]
            / "tool1_dashboard"
            / "ui"
            / "app.css"
        ).read_text(encoding="utf-8")

        self.assertNotIn("Needs restart", app_js)
        self.assertNotIn("Start Worker", app_js)
        self.assertIn("Starting voice engine", app_js)
        self.assertIn("data-open-voice-tuning", app_js)
        self.assertIn("voice-profile-tuning-form", app_js)
        self.assertIn("Save and play test", app_js)
        self.assertIn("tooltip-anchor profile-card-action", app_js)
        self.assertIn("profile-card-status", app_js)
        self.assertIn("button-icon-spin", app_css)
        self.assertIn(".profile-card-action.button.icon-only", app_css)


class PausedTtsEpisodeTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.db = Tool1Database(Path(self._tmpdir) / "test.db")
        self.db.initialize()

    def test_list_paused_tts_episodes(self):
        from tool1_dashboard.runtime import utc_now
        now = utc_now()

        # Create a niche project
        self.db.create_niche_project({
            "id": "niche-1",
            "title": "Test Project",
            "master_language": "en",
            "configured_languages": "[]",
            "language_voice_profiles": "{}",
            "language_translation_profiles": "{}",
            "board_status": "Draft",
            "workspace_dir": self._tmpdir,
            "created_at": now,
            "updated_at": now,
        })

        # Create an episode paused for TTS
        self.db.create_episode({
            "id": "ep-1",
            "niche_project_id": "niche-1",
            "title": "Episode 1",
            "script_text": "Test script",
            "board_status": "Running",
            "pipeline_status": "paused_for_tts",
            "current_stage": "tts",
            "workspace_dir": self._tmpdir,
            "created_at": now,
            "updated_at": now,
        })

        episodes = self.db.list_paused_tts_episodes()
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["id"], "ep-1")


if __name__ == "__main__":
    unittest.main()
