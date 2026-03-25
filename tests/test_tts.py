"""Tests for the TTS module (chunker, audio, constants, DB, manager)."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
import wave
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from tool1_dashboard.database import Tool1Database
from tool1_dashboard.tts.audio import generate_silence_wav, merge_wav_chunks_streaming
from tool1_dashboard.tts.chunker import TTSChunk, chunk_text_for_tts
from tool1_dashboard.tts.constants import (
    CHUNK_MAX_CHARS,
    XTTS_LANG_MAP,
    map_language_code,
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
        from tool1_dashboard.tts.manager import TTSManager

        mgr = TTSManager(self.db)
        health = mgr.get_worker_health()
        self.assertFalse(health.running)
        self.assertEqual(health.status, "unknown")
        self.assertTrue(health.is_stale)


class PausedTtsBuildTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.db = Tool1Database(Path(self._tmpdir) / "test.db")
        self.db.initialize()

    def test_list_paused_tts_builds(self):
        from tool1_dashboard.runtime import utc_now
        now = utc_now()

        # Create a project
        self.db.create_project({
            "id": "proj1",
            "title": "Test Project",
            "source_language": "en",
            "script_path": "",
            "workspace_dir": self._tmpdir,
            "created_at": now,
            "updated_at": now,
        })

        # Create a localization build paused for TTS
        self.db.create_build({
            "id": "b1",
            "project_id": "proj1",
            "build_type": "localization",
            "language_code": "pt-BR",
            "board_status": "Draft",
            "pipeline_status": "paused_for_tts",
            "current_stage": "translation",
            "tts_job_id": "j1",
            "workspace_dir": self._tmpdir,
            "created_at": now,
            "updated_at": now,
        })

        builds = self.db.list_paused_tts_builds()
        self.assertEqual(len(builds), 1)
        self.assertEqual(builds[0]["id"], "b1")
        self.assertEqual(builds[0]["tts_job_id"], "j1")


if __name__ == "__main__":
    unittest.main()
