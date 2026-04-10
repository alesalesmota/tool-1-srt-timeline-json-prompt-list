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

from tool1_dashboard.database import Tool1Database, WorkerHeartbeat
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
            WorkerHeartbeat(
                worker_id="w1",
                status="idle",
                current_job_id=None,
                pid=1234,
                started_at=time.time(),
            )
        )
        hb = self.db.get_latest_worker_heartbeat()
        self.assertIsNotNone(hb)
        self.assertEqual(hb["worker_id"], "w1")
        self.assertEqual(hb["status"], "idle")
        self.assertEqual(hb["pid"], 1234)

    def test_heartbeat_upsert(self):
        now = time.time()
        self.db.record_worker_heartbeat(
            WorkerHeartbeat(
                worker_id="w1", status="idle", current_job_id=None, pid=100, started_at=now,
            )
        )
        self.db.record_worker_heartbeat(
            WorkerHeartbeat(
                worker_id="w1", status="processing", current_job_id="j1", pid=100, started_at=now,
            )
        )
        hb = self.db.get_latest_worker_heartbeat()
        self.assertEqual(hb["status"], "processing")
        self.assertEqual(hb["current_job_id"], "j1")

    def test_list_worker_heartbeats_newest_first(self):
        now = time.time()
        self.db.record_worker_heartbeat(
            WorkerHeartbeat(
                worker_id="older",
                status="idle",
                current_job_id=None,
                pid=100,
                started_at=now - 10,
            )
        )
        time.sleep(0.01)
        self.db.record_worker_heartbeat(
            WorkerHeartbeat(
                worker_id="newer",
                status="processing",
                current_job_id="job-1",
                pid=200,
                started_at=now,
            )
        )
        rows = self.db.list_worker_heartbeats()
        self.assertEqual(rows[0]["worker_id"], "newer")
        self.assertEqual(rows[1]["worker_id"], "older")


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

    def test_requeue_orphaned_processing_job_without_heartbeat(self):
        now = time.time()
        self.db.create_tts_job({
            "job_id": "orphan-1",
            "job_type": "generate",
            "profile_id": "p1",
            "status": "processing",
            "payload_json": "{}",
            "meta_json": "{}",
            "queue_priority": 10,
            "worker_id": "missing-worker",
            "created_at": now,
            "updated_at": now,
        })
        count = self.db.requeue_orphaned_processing_tts_jobs(90)
        self.assertEqual(count, 1)
        job = self.db.get_tts_job("orphan-1")
        self.assertEqual(job["status"], "queued")
        self.assertIsNone(job["worker_id"])

    def test_requeue_orphaned_processing_job_keeps_fresh_worker(self):
        now = time.time()
        self.db.create_tts_job({
            "job_id": "fresh-worker-job",
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
        self.db.record_worker_heartbeat(
            WorkerHeartbeat(
                worker_id="active-worker",
                status="processing",
                current_job_id="fresh-worker-job",
                pid=1234,
                started_at=now,
            )
        )
        count = self.db.requeue_orphaned_processing_tts_jobs(90)
        self.assertEqual(count, 0)
        job = self.db.get_tts_job("fresh-worker-job")
        self.assertEqual(job["status"], "processing")
        self.assertEqual(job["worker_id"], "active-worker")

    def test_requeue_tts_job_resets_job_and_language_status(self):
        now = time.time()
        self.db.create_episode({
            "id": "ep-1",
            "niche_project_id": "np-1",
            "title": "Episode",
            "script_text": "hello",
            "workspace_dir": str(Path(self._tmpdir) / "ep-1"),
            "created_at": utc_now(),
            "updated_at": utc_now(),
        })
        self.db.create_episode_language_status({
            "id": "els-1",
            "episode_id": "ep-1",
            "language_code": "en",
            "translation_status": "done",
            "tts_status": "running",
            "srt_status": "pending",
            "timeline_status": "pending",
            "tts_job_id": "job-1",
            "updated_at": utc_now(),
        })
        self.db.create_tts_job({
            "job_id": "job-1",
            "job_type": "generate",
            "profile_id": "p1",
            "status": "processing",
            "progress": "Generating chunk 3/10...",
            "payload_json": "{}",
            "meta_json": "{}",
            "queue_priority": 10,
            "worker_id": "worker-a",
            "created_at": now,
            "updated_at": now,
        })

        changed = self.db.requeue_tts_job(
            "job-1",
            progress="Requeued after duplicate worker shutdown.",
        )

        self.assertTrue(changed)
        job = self.db.get_tts_job("job-1")
        self.assertEqual(job["status"], "queued")
        self.assertIsNone(job["worker_id"])
        lang_status = self.db.get_episode_language_status("ep-1", "en")
        self.assertEqual(lang_status["tts_status"], "queued")

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
        self.assertEqual(config["chunk_max_chars"], 250)
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

    def test_worker_health_includes_runtime_and_queue_counts(self):
        from tool1_dashboard.tts.manager import TTSManager, WorkerRuntimeStatus

        now = time.time()
        self.db.create_tts_job({
            "job_id": "job-processing",
            "job_type": "generate",
            "profile_id": "p1",
            "status": "processing",
            "payload_json": json.dumps({"texts": ["hello"]}),
            "meta_json": "{}",
            "queue_priority": 10,
            "worker_id": "worker-1",
            "created_at": now,
            "updated_at": now,
        })
        self.db.create_tts_job({
            "job_id": "job-queued",
            "job_type": "generate",
            "profile_id": "p1",
            "status": "queued",
            "payload_json": json.dumps({"texts": ["hello"]}),
            "meta_json": "{}",
            "queue_priority": 20,
            "created_at": now,
            "updated_at": now,
        })

        mgr = TTSManager(self.db)
        with patch.object(
            mgr,
            "get_runtime_status",
            return_value=WorkerRuntimeStatus(
                available=True,
                missing_dependencies=[],
                error=None,
                device="cuda",
                torch_version="2.3.1",
                torch_build="cu121",
                cuda_available=True,
                gpu_name="RTX 3050",
            ),
        ):
            health = mgr.get_worker_health()
        self.assertEqual(health.device, "cuda")
        self.assertEqual(health.torch_version, "2.3.1")
        self.assertEqual(health.torch_build, "cu121")
        self.assertTrue(health.cuda_available)
        self.assertEqual(health.gpu_name, "RTX 3050")
        self.assertEqual(health.active_generate_jobs, 1)
        self.assertEqual(health.queued_generate_jobs, 1)

    def test_start_worker_skips_duplicate_spawn_when_fresh_heartbeat_exists(self):
        from tool1_dashboard.tts.manager import TTSManager

        now = time.time()
        self.db.record_worker_heartbeat(
            WorkerHeartbeat(
                worker_id="worker-live",
                status="idle",
                current_job_id=None,
                pid=4321,
                started_at=now,
            )
        )
        mgr = TTSManager(self.db)
        with patch.object(mgr, "_pid_is_alive", return_value=True):
            started = mgr.start_worker()
        self.assertFalse(started)
        self.assertEqual(mgr._active_worker_id, "worker-live")

    def test_ensure_worker_ready_stops_duplicate_worker_and_requeues_its_job(self):
        from tool1_dashboard.tts.manager import TTSManager, WorkerRuntimeStatus

        now = time.time()
        self.db.create_episode({
            "id": "ep-1",
            "niche_project_id": "np-1",
            "title": "Episode",
            "script_text": "hello",
            "workspace_dir": str(Path(self._tmpdir) / "ep-1"),
            "created_at": utc_now(),
            "updated_at": utc_now(),
        })
        self.db.create_episode_language_status({
            "id": "els-1",
            "episode_id": "ep-1",
            "language_code": "es",
            "translation_status": "done",
            "tts_status": "running",
            "srt_status": "pending",
            "timeline_status": "pending",
            "tts_job_id": "job-dup",
            "updated_at": utc_now(),
        })
        self.db.create_tts_job({
            "job_id": "job-dup",
            "job_type": "generate",
            "profile_id": "p1",
            "status": "processing",
            "progress": "Generating chunk 10/100...",
            "payload_json": json.dumps({"texts": ["hello"]}),
            "meta_json": "{}",
            "queue_priority": 10,
            "worker_id": "worker-dup",
            "created_at": now,
            "updated_at": now,
        })
        self.db.record_worker_heartbeat(
            WorkerHeartbeat(
                worker_id="worker-main",
                status="processing",
                current_job_id="job-main",
                pid=1111,
                started_at=now - 5,
            )
        )
        self.db.record_worker_heartbeat(
            WorkerHeartbeat(
                worker_id="worker-dup",
                status="processing",
                current_job_id="job-dup",
                pid=2222,
                started_at=now - 4,
            )
        )

        mgr = TTSManager(self.db)
        mgr._active_worker_id = "worker-main"
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
            "_pid_is_alive",
            return_value=True,
        ), patch.object(
            mgr,
            "_terminate_pid",
            return_value=True,
        ) as terminate_mock, patch.object(
            mgr,
            "_schedule_shutdown_check",
        ) as schedule_mock:
            mgr.ensure_worker_ready(intent="pipeline")

        terminate_mock.assert_called_once_with(2222)
        job = self.db.get_tts_job("job-dup")
        self.assertEqual(job["status"], "queued")
        self.assertIsNone(job["worker_id"])
        lang_status = self.db.get_episode_language_status("ep-1", "es")
        self.assertEqual(lang_status["tts_status"], "queued")
        duplicate_hb = self.db.get_worker_heartbeat("worker-dup")
        self.assertEqual(duplicate_hb["status"], "stopped")
        self.assertEqual(mgr._active_worker_id, "worker-main")
        schedule_mock.assert_called_once()

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

    def test_create_voice_profile_surfaces_cpu_runtime_warning(self):
        from tool1_dashboard.tts.manager import WorkerRuntimeStatus

        with patch.object(
            self.service.tts_manager,
            "get_runtime_status",
            return_value=WorkerRuntimeStatus(
                available=True,
                missing_dependencies=[],
                error=None,
                device="cpu",
                torch_version="2.3.1",
                torch_build="cpu",
                cuda_available=False,
                gpu_name=None,
            ),
        ), patch.object(self.service.tts_manager, "ensure_worker_ready") as ensure_mock, patch.object(
            self.service.tts_manager,
            "submit_tts_job",
            return_value="latent-cpu",
        ):
            profile = self.service.create_voice_profile(
                name="CPU Voice",
                audio_bytes=b"RIFF....WAVEfmt ",
                audio_filename="cpu.wav",
            )

        ensure_mock.assert_called_once_with(intent="interactive")
        self.assertEqual(profile["latent_job_id"], "latent-cpu")
        self.assertEqual(profile["runtime_warning"], self.service._tts_cpu_runtime_warning())

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

    def test_generate_payload_uses_production_chunk_override_but_voice_test_keeps_profile_setting(self):
        self.service.update_voice_profile(
            "voice-1",
            tts_config={
                "preset": "natural_stable",
                "chunk_max_chars": 180,
            },
        )
        profile = self.service.get_voice_profile("voice-1")
        long_text = "Sentence one. " * 120

        generate_payload = self.service._build_generate_tts_payload(
            profile=profile,
            language="en",
            script_text=long_text,
        )
        voice_test_payload = self.service._build_voice_test_payload(
            profile=profile,
            language="en",
            text=long_text,
        )

        self.assertEqual(generate_payload["tts_config"]["chunk_max_chars"], 180)
        self.assertEqual(voice_test_payload["tts_config"]["chunk_max_chars"], 180)
        self.assertEqual(generate_payload["texts"], voice_test_payload["texts"])

    def test_completed_job_payload_keeps_final_chunk_totals_and_percent(self):
        job_payload = self.service._build_tts_job_client_payload({
            "job_id": "job-final",
            "status": "completed",
            "progress": "Completed",
            "payload_json": json.dumps({"texts": ["a", "b", "c"]}),
            "updated_at": time.time(),
            "finished_at": time.time(),
        })

        self.assertEqual(job_payload["current_chunk"], 3)
        self.assertEqual(job_payload["total_chunks"], 3)
        self.assertEqual(job_payload["percent"], 100)

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


class EpisodeTtsQueueStatusTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.workspace = Path(self._tmpdir) / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.audio_path = self.workspace / "voice.wav"
        self.audio_path.write_bytes(b"RIFF....WAVEfmt ")
        self.service = Tool1Service(db=Tool1Database(Path(self._tmpdir) / "test.db"))
        now = utc_now()

        self.service.db.create_niche_project({
            "id": "niche-1",
            "title": "Test Project",
            "master_language": "en",
            "configured_languages": json.dumps(["en", "es"]),
            "language_voice_profiles": json.dumps({"en": "voice-en", "es": "voice-es"}),
            "language_translation_profiles": "{}",
            "board_status": "Draft",
            "workspace_dir": str(self.workspace),
            "created_at": now,
            "updated_at": now,
        })
        self.service.db.create_episode({
            "id": "ep-1",
            "niche_project_id": "niche-1",
            "title": "Episode 1",
            "script_text": "Hello world. This is a test script for queued narration.",
            "board_status": "Running",
            "pipeline_status": "paused_for_tts",
            "current_stage": "tts",
            "queued_from_stage": "tts",
            "master_language": "en",
            "configured_languages": json.dumps(["en", "es"]),
            "workspace_dir": str(self.workspace),
            "created_at": now,
            "updated_at": now,
        })
        for language_code in ("en", "es"):
            self.service.db.create_episode_language_status({
                "id": f"ep-1-{language_code}",
                "episode_id": "ep-1",
                "language_code": language_code,
                "translation_status": "done",
                "tts_status": "pending",
                "srt_status": "pending",
                "timeline_status": "pending",
                "updated_at": now,
            })
        for profile_id in ("voice-en", "voice-es"):
            self.service.db.create_voice_profile({
                "id": profile_id,
                "name": profile_id,
                "language_code": "en" if profile_id.endswith("en") else "es",
                "audio_file": self.audio_path.name,
                "audio_path": str(self.audio_path),
                "latents_path": None,
                "has_latents": 0,
                "tts_config_json": "",
                "created_at": now,
                "updated_at": now,
            })

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _create_generate_job(self, *, job_id: str, profile_id: str, status: str, progress: str) -> None:
        now = time.time()
        self.service.db.create_tts_job({
            "job_id": job_id,
            "build_id": "ep-1",
            "job_type": "generate",
            "profile_id": profile_id,
            "status": status,
            "progress": progress,
            "payload_json": json.dumps({"texts": ["hello", "world"]}),
            "meta_json": "{}",
            "queue_priority": 10,
            "created_at": now,
            "updated_at": now,
        })

    def test_queue_episode_tts_jobs_marks_new_submissions_as_queued(self):
        with patch.object(self.service.tts_manager, "ensure_worker_ready") as ensure_mock, patch.object(
            self.service.tts_manager,
            "submit_tts_job",
            side_effect=["job-en", "job-es"],
        ):
            result = self.service._queue_episode_tts_jobs("ep-1", allow_resubmit_failed=True)

        ensure_mock.assert_called_once_with(intent="pipeline")
        self.assertEqual(result["submitted_jobs"], 2)
        self.assertEqual(result["active_jobs"], 2)
        self.assertEqual(
            self.service.db.get_episode_language_status("ep-1", "en")["tts_status"],
            "queued",
        )
        self.assertEqual(
            self.service.db.get_episode_language_status("ep-1", "es")["tts_status"],
            "queued",
        )

    def test_queue_episode_tts_jobs_only_marks_processing_job_as_running(self):
        self._create_generate_job(
            job_id="job-en",
            profile_id="voice-en",
            status="processing",
            progress="Generating chunk 1/2...",
        )
        self._create_generate_job(
            job_id="job-es",
            profile_id="voice-es",
            status="queued",
            progress="Queued...",
        )
        self.service.db.update_episode_language_status(
            "ep-1",
            "en",
            tts_status="queued",
            tts_job_id="job-en",
        )
        self.service.db.update_episode_language_status(
            "ep-1",
            "es",
            tts_status="running",
            tts_job_id="job-es",
        )

        result = self.service._queue_episode_tts_jobs("ep-1", allow_resubmit_failed=False)

        self.assertEqual(result["submitted_jobs"], 0)
        self.assertEqual(result["active_jobs"], 2)
        self.assertEqual(
            self.service.db.get_episode_language_status("ep-1", "en")["tts_status"],
            "running",
        )
        self.assertEqual(
            self.service.db.get_episode_language_status("ep-1", "es")["tts_status"],
            "queued",
        )

    def test_retry_single_tts_marks_job_as_queued(self):
        with patch.object(self.service.tts_manager, "ensure_worker_ready") as ensure_mock, patch.object(
            self.service.tts_manager,
            "submit_tts_job",
            return_value="job-retry",
        ):
            self.service._episode_retry_single_tts("ep-1", "es")

        ensure_mock.assert_called_once_with(intent="pipeline")
        status = self.service.db.get_episode_language_status("ep-1", "es")
        self.assertEqual(status["tts_status"], "queued")
        self.assertEqual(status["tts_job_id"], "job-retry")

    def test_recover_paused_tts_queue_requeues_orphaned_processing_job_while_worker_is_healthy(self):
        from tool1_dashboard.tts.manager import WorkerHealth

        now = time.time()
        self.service.db.create_tts_job({
            "job_id": "job-en",
            "build_id": "ep-1",
            "job_type": "generate",
            "profile_id": "voice-en",
            "status": "processing",
            "progress": "Generating chunk 1/2...",
            "payload_json": json.dumps({"texts": ["hello", "world"]}),
            "meta_json": "{}",
            "queue_priority": 10,
            "worker_id": "dead-worker",
            "created_at": now,
            "updated_at": now,
        })
        self.service.db.update_episode_language_status(
            "ep-1",
            "en",
            tts_status="running",
            tts_job_id="job-en",
        )
        self.service.db.record_worker_heartbeat(
            WorkerHeartbeat(
                worker_id="healthy-worker",
                status="idle",
                current_job_id=None,
                pid=5555,
                started_at=now,
            )
        )

        with patch.object(
            self.service.tts_manager,
            "get_worker_health",
            return_value=WorkerHealth(
                running=True,
                worker_id="healthy-worker",
                status="idle",
                current_job_id=None,
                last_heartbeat=now,
                is_stale=False,
                pid=5555,
                startup_error=None,
                missing_dependencies=[],
                lifecycle_state="sleeping",
            ),
        ), patch.object(self.service.tts_manager, "ensure_worker_ready") as ensure_mock:
            error = self.service._recover_paused_tts_queue([self.service.db.get_episode("ep-1")])

        self.assertIsNone(error)
        ensure_mock.assert_called_once_with(intent="pipeline")
        requeued = self.service.db.get_tts_job("job-en")
        self.assertEqual(requeued["status"], "queued")
        self.assertIsNone(requeued["worker_id"])


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
