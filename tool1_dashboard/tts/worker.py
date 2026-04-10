"""TTS Worker — runs as a standalone subprocess.

Loads the XTTS-v2 model, polls the tts_jobs table, and processes
latent_precompute / generate / test_voice jobs.

Launch with:  python -m tool1_dashboard.tts.worker
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import threading
import time
import traceback
import wave
from pathlib import Path
from typing import Any

os.environ.setdefault("COQUI_TOS_AGREED", "1")
os.environ.setdefault("TTS_AGREE_TOS", "1")

import torch
import torchaudio
from TTS.api import TTS as CoquiTTS

from tool1_dashboard.database import Tool1Database, WorkerHeartbeat
from tool1_dashboard.tts.constants import (
    AUDIO_SAMPLE_RATE,
    CHUNK_RETRY_ATTEMPTS,
    FAILED_CHUNK_SECONDS,
    GENERATE_PROGRESS_UPDATE_EVERY_CHUNKS,
    GENERATE_PROGRESS_UPDATE_MIN_SECONDS,
    HEARTBEAT_INTERVAL,
    MAX_JOB_HISTORY,
    MAX_REFERENCE_AUDIO_SECONDS,
    SILENCE_GAP_SECONDS,
    STALE_PROCESSING_SECONDS,
    WAV_MERGE_READ_FRAMES,
    WORKER_POLL_INTERVAL,
)
from tool1_dashboard.tts.voice_config import (
    build_xtts_inference_kwargs,
    chunk_text_for_voice_tts,
    normalize_voice_tts_config,
)

# ---------------------------------------------------------------------------
# Configuration from environment (set by TTSManager)
# ---------------------------------------------------------------------------

DB_PATH = os.environ.get("TTS_DB_PATH", "")
PROFILES_DIR = os.environ.get("TTS_PROFILES_DIR", "")
OUTPUT_DIR = os.environ.get("TTS_OUTPUT_DIR", "")
CHUNKS_DIR = os.environ.get("TTS_CHUNKS_DIR", "")
WORKER_ID = os.environ.get("TTS_WORKER_ID", f"worker-{os.getpid()}")
STARTED_AT = time.time()

# ---------------------------------------------------------------------------
# WorkerDB — thin adapter around Tool1Database for the worker process
# ---------------------------------------------------------------------------


class WorkerDB:
    """Provides the job-store operations the worker needs."""

    def __init__(self, db_path: str) -> None:
        self._db = Tool1Database(Path(db_path))
        self._db.initialize()

    def claim_next_job(self, worker_id: str) -> dict[str, Any] | None:
        job = self._db.claim_next_tts_job(worker_id)
        return self._parse(job) if job else None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self._db.get_tts_job(job_id)
        return self._parse(job) if job else None

    def update_job(self, job_id: str, **fields: Any) -> None:
        fields.setdefault("updated_at", time.time())
        self._db.update_tts_job(job_id, **fields)

    def record_heartbeat(self, heartbeat: WorkerHeartbeat) -> None:
        self._db.record_worker_heartbeat(heartbeat)

    def requeue_stale(self, stale_seconds: int) -> None:
        self._db.requeue_stale_tts_jobs(stale_seconds)

    def prune_terminal_jobs(self, max_history: int, job_type: str = "generate") -> None:
        """Remove oldest terminal tts_jobs beyond *max_history*."""
        rows = self._db._fetchall(
            """
            SELECT job_id FROM tts_jobs
            WHERE job_type = ? AND status IN ('completed','error','expired','canceled')
            ORDER BY updated_at DESC
            """,
            (job_type,),
        )
        if len(rows) <= max_history:
            return
        for row in rows[max_history:]:
            self._db.update_tts_job(row["job_id"], status="expired")

    @staticmethod
    def _parse(job: dict[str, Any]) -> dict[str, Any]:
        result = dict(job)
        for src, dst in (("payload_json", "payload"), ("meta_json", "meta")):
            raw = result.get(src, "")
            try:
                result[dst] = json.loads(raw) if raw else {}
            except (json.JSONDecodeError, TypeError):
                result[dst] = {}
        return result


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

HEARTBEAT_STATE_LOCK = threading.Lock()
HEARTBEAT_STATE: dict[str, Any] = {
    "status": "starting",
    "current_job_id": None,
    "last_error": None,
}
STOP_EVENT = threading.Event()


class JobPausedByUser(Exception):
    pass


class JobStoppedByUser(Exception):
    pass


def _set_heartbeat_state(
    status: str,
    current_job_id: str | None = None,
    last_error: str | None = None,
) -> None:
    with HEARTBEAT_STATE_LOCK:
        HEARTBEAT_STATE["status"] = status
        HEARTBEAT_STATE["current_job_id"] = current_job_id
        HEARTBEAT_STATE["last_error"] = last_error


def _get_heartbeat_state() -> dict[str, Any]:
    with HEARTBEAT_STATE_LOCK:
        return dict(HEARTBEAT_STATE)


# ---------------------------------------------------------------------------
# Heartbeat loop
# ---------------------------------------------------------------------------


def heartbeat_loop(db: WorkerDB) -> None:
    while not STOP_EVENT.is_set():
        state = _get_heartbeat_state()
        try:
            db.record_heartbeat(
                WorkerHeartbeat(
                    worker_id=WORKER_ID,
                    status=state["status"],
                    current_job_id=state["current_job_id"],
                    pid=os.getpid(),
                    started_at=STARTED_AT,
                    last_error=state["last_error"],
                )
            )
        except Exception as err:
            print(f"[Worker] Heartbeat write failed: {err}")
        STOP_EVENT.wait(HEARTBEAT_INTERVAL)


# ---------------------------------------------------------------------------
# Audio helpers (torch-dependent, worker-only)
# ---------------------------------------------------------------------------


def save_pcm16_wav(path: str, waveform: torch.Tensor) -> None:
    if waveform.dim() == 1:
        waveform = waveform.unsqueeze(0)
    wav_cpu = waveform.detach().to(device="cpu", dtype=torch.float32).contiguous()
    torchaudio.save(path, wav_cpu, AUDIO_SAMPLE_RATE, encoding="PCM_S", bits_per_sample=16)


def merge_wav_chunks_streaming(chunk_paths: list[str], final_path: str) -> None:
    """Merge WAV chunks via streaming read/write."""
    if not chunk_paths:
        save_pcm16_wav(final_path, torch.zeros(1, 1))
        return

    with wave.open(chunk_paths[0], "rb") as first:
        n_ch = first.getnchannels()
        sw = first.getsampwidth()
        fr = first.getframerate()
        ct = first.getcomptype()
        cn = first.getcompname()

    with wave.open(final_path, "wb") as out:
        out.setnchannels(n_ch)
        out.setsampwidth(sw)
        out.setframerate(fr)
        out.setcomptype(ct, cn)
        for cp in chunk_paths:
            with wave.open(cp, "rb") as cw:
                if cw.getnchannels() != n_ch or cw.getsampwidth() != sw or cw.getframerate() != fr:
                    raise ValueError(f"Incompatible WAV chunk: {cp}")
                while True:
                    frames = cw.readframes(WAV_MERGE_READ_FRAMES)
                    if not frames:
                        break
                    out.writeframesraw(frames)
        out.writeframes(b"")


# ---------------------------------------------------------------------------
# Reference audio & speaker latents
# ---------------------------------------------------------------------------


def _prepare_reference_audio(tts_model_obj: Any, audio_path: str) -> str:
    """Return a path to reference audio capped at MAX_REFERENCE_AUDIO_SECONDS."""
    info = torchaudio.info(audio_path)
    duration = info.num_frames / info.sample_rate
    if duration <= MAX_REFERENCE_AUDIO_SECONDS:
        return audio_path
    max_frames = int(MAX_REFERENCE_AUDIO_SECONDS * info.sample_rate)
    print(f"[Worker] Trimming reference audio from {duration:.1f}s to {MAX_REFERENCE_AUDIO_SECONDS}s")
    waveform, sr = torchaudio.load(audio_path, num_frames=max_frames)
    trimmed_path = audio_path + ".trimmed.wav"
    torchaudio.save(trimmed_path, waveform, sr)
    return trimmed_path


def compute_speaker_latents(tts_obj: Any, audio_path: str) -> tuple:
    ref_path = _prepare_reference_audio(tts_obj, audio_path)
    model = tts_obj.synthesizer.tts_model
    with torch.inference_mode():
        gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(audio_path=[ref_path])
    if ref_path != audio_path and os.path.exists(ref_path):
        os.remove(ref_path)
    return gpt_cond_latent, speaker_embedding


def _load_or_compute_latents(
    tts_obj: Any, db: WorkerDB, profile_id: str, ref_path: str, job_id: str
) -> tuple:
    latent_path = os.path.join(PROFILES_DIR, f"{profile_id}_latents.pt")
    device = next(tts_obj.synthesizer.tts_model.parameters()).device
    if os.path.exists(latent_path):
        db.update_job(job_id, progress="Loading voice profile (cached)...")
        latents = torch.load(latent_path, map_location=device, weights_only=True)
        return latents["gpt_cond_latent"], latents["speaker_embedding"]

    db.update_job(job_id, progress="Analyzing reference voice...")
    if not os.path.exists(ref_path):
        fallback = os.path.join(PROFILES_DIR, os.path.basename(ref_path))
        if os.path.exists(fallback):
            ref_path = fallback
        else:
            raise FileNotFoundError("Reference audio file not found.")
    return compute_speaker_latents(tts_obj, ref_path)


# ---------------------------------------------------------------------------
# Job control check
# ---------------------------------------------------------------------------


def _check_job_control(db: WorkerDB, job_id: str) -> None:
    job = db.get_job(job_id)
    if not job:
        raise JobStoppedByUser("Job removed.")
    control = job.get("control_action")
    status = job.get("status")
    if control == "stop" or status == "canceled":
        raise JobStoppedByUser("Stopped by user.")
    if control == "pause" or status == "paused":
        raise JobPausedByUser("Paused by user.")


# ---------------------------------------------------------------------------
# Resume detection
# ---------------------------------------------------------------------------


def _detect_resume_index(job_chunks_dir: str, total_texts: int) -> int:
    existing = sorted(glob.glob(os.path.join(job_chunks_dir, "chunk_*.wav")))
    if not existing:
        return 0
    while existing:
        last = existing[-1]
        if os.path.getsize(last) > 44:
            break
        os.remove(last)
        existing.pop()
    if not existing:
        return 0
    last_seq = int(os.path.basename(existing[-1]).split("_")[1].split(".")[0])
    resume_from = (last_seq // 2) + 1
    return min(resume_from, total_texts)


def _resolve_job_tts_config(payload: dict[str, Any]) -> dict[str, Any]:
    return normalize_voice_tts_config(payload.get("tts_config"))


def _resolve_job_texts(
    payload: dict[str, Any],
    *,
    job_type: str,
) -> list[str]:
    raw_texts = payload.get("texts")
    if isinstance(raw_texts, list):
        texts = [str(text or "").strip() for text in raw_texts if str(text or "").strip()]
    else:
        texts = []

    if texts and payload.get("chunked"):
        return texts

    if texts:
        combined_text = " ".join(texts).strip()
        return chunk_text_for_voice_tts(combined_text, payload.get("tts_config"))

    raw_text = str(payload.get("text") or "").strip()
    if raw_text:
        return chunk_text_for_voice_tts(raw_text, payload.get("tts_config"))

    if job_type == "generate":
        raise ValueError("No valid text to generate audio.")
    raise ValueError("No valid text to generate voice test.")


def _should_emit_generate_progress(
    *,
    current_chunk: int,
    total_chunks: int,
    last_progress_at: float,
    now: float,
) -> bool:
    if total_chunks <= 1:
        return True
    if current_chunk <= 1 or current_chunk >= total_chunks:
        return True
    if current_chunk % GENERATE_PROGRESS_UPDATE_EVERY_CHUNKS == 0:
        return True
    return (now - last_progress_at) >= GENERATE_PROGRESS_UPDATE_MIN_SECONDS


# ---------------------------------------------------------------------------
# Job processors
# ---------------------------------------------------------------------------


def process_latent_job(tts_obj: Any, db: WorkerDB, job: dict) -> None:
    payload = job["payload"]
    job_id = job["job_id"]
    profile_id = payload["profile_id"]
    audio_path = payload["audio_path"]

    if not os.path.exists(audio_path):
        raise FileNotFoundError("Reference audio not found for latent precompute.")

    db.update_job(job_id, progress="Analyzing reference voice...")
    gpt_cond_latent, speaker_embedding = compute_speaker_latents(tts_obj, audio_path)
    latent_path = os.path.join(PROFILES_DIR, f"{profile_id}_latents.pt")
    torch.save(
        {"gpt_cond_latent": gpt_cond_latent, "speaker_embedding": speaker_embedding},
        latent_path,
    )
    db.update_job(
        job_id,
        status="completed",
        progress="Voice profile ready.",
        result_path=latent_path,
        finished_at=time.time(),
    )


def process_generate_job(tts_obj: Any, db: WorkerDB, job: dict) -> None:
    payload = job["payload"]
    job_id = job["job_id"]
    profile_id = payload["profile_id"]
    ref_path = payload["ref_path"]
    language = payload["language"]
    original_filename = payload.get("original_filename", "output")
    tts_config = _resolve_job_tts_config(payload)
    inference_kwargs = build_xtts_inference_kwargs(tts_config)
    valid_texts = _resolve_job_texts(payload, job_type="generate")

    if not os.path.exists(ref_path):
        fallback = os.path.join(PROFILES_DIR, os.path.basename(ref_path))
        if os.path.exists(fallback):
            ref_path = fallback
        else:
            raise FileNotFoundError("Reference audio file not found.")

    job_chunks_dir = os.path.join(CHUNKS_DIR, job_id)
    os.makedirs(job_chunks_dir, exist_ok=True)

    resume_from = _detect_resume_index(job_chunks_dir, len(valid_texts))
    last_progress_at = 0.0
    if resume_from > 0:
        print(f"[Worker] Resuming job {job_id} from chunk {resume_from + 1}/{len(valid_texts)}")
        db.update_job(job_id, progress=f"Resuming from chunk {resume_from + 1}/{len(valid_texts)}...")
        last_progress_at = time.time()

    model = tts_obj.synthesizer.tts_model
    silence_gap_seconds = float(tts_config.get("silence_gap_seconds", SILENCE_GAP_SECONDS))
    silence_samples = int(AUDIO_SAMPLE_RATE * silence_gap_seconds)
    silence_tensor = torch.zeros(1, silence_samples)
    failed_samples = int(AUDIO_SAMPLE_RATE * FAILED_CHUNK_SECONDS)

    _check_job_control(db, job_id)
    gpt_cond_latent, speaker_embedding = _load_or_compute_latents(
        tts_obj, db, profile_id, ref_path, job_id
    )

    with torch.inference_mode():
        for idx, text_chunk in enumerate(valid_texts):
            seq_audio = idx * 2
            audio_path = os.path.join(job_chunks_dir, f"chunk_{seq_audio:04d}.wav")

            if idx < resume_from:
                continue

            _check_job_control(db, job_id)
            current_chunk = idx + 1
            now = time.time()
            if _should_emit_generate_progress(
                current_chunk=current_chunk,
                total_chunks=len(valid_texts),
                last_progress_at=last_progress_at,
                now=now,
            ):
                db.update_job(job_id, progress=f"Generating chunk {current_chunk}/{len(valid_texts)}...")
                last_progress_at = now

            chunk_audio = None
            for attempt in range(1, CHUNK_RETRY_ATTEMPTS + 1):
                _check_job_control(db, job_id)
                try:
                    result = model.inference(
                        text=text_chunk,
                        language=language,
                        gpt_cond_latent=gpt_cond_latent,
                        speaker_embedding=speaker_embedding,
                        **inference_kwargs,
                    )
                    chunk_audio = torch.tensor(result["wav"]).unsqueeze(0)
                    break
                except Exception as err:
                    print(f"[Worker] Chunk {idx + 1} attempt {attempt}/{CHUNK_RETRY_ATTEMPTS} failed: {err}")
                    if attempt == CHUNK_RETRY_ATTEMPTS:
                        chunk_audio = torch.zeros(1, failed_samples)

            save_pcm16_wav(audio_path, chunk_audio.cpu())

            if idx < len(valid_texts) - 1:
                seq_silence = idx * 2 + 1
                silence_path = os.path.join(job_chunks_dir, f"chunk_{seq_silence:04d}.wav")
                save_pcm16_wav(silence_path, silence_tensor)

    _check_job_control(db, job_id)
    db.update_job(
        job_id,
        progress=f"Finalizing audio after chunk {len(valid_texts)}/{len(valid_texts)}...",
    )
    base_name = os.path.splitext(original_filename)[0]
    final_filename = f"{base_name}_narration.wav"
    final_path = os.path.join(OUTPUT_DIR, f"{job_id}_{final_filename}")

    total_files = len(valid_texts) * 2 - 1
    all_chunk_paths = [
        os.path.join(job_chunks_dir, f"chunk_{i:04d}.wav") for i in range(total_files)
    ]
    merge_wav_chunks_streaming(all_chunk_paths, final_path)
    shutil.rmtree(job_chunks_dir, ignore_errors=True)

    db.update_job(
        job_id,
        status="completed",
        progress="Completed",
        result_path=final_path,
        filename=final_filename,
        finished_at=time.time(),
    )
    db.prune_terminal_jobs(MAX_JOB_HISTORY, job_type="generate")


def process_test_voice_job(tts_obj: Any, db: WorkerDB, job: dict) -> None:
    payload = job["payload"]
    job_id = job["job_id"]
    profile_id = payload["profile_id"]
    ref_path = payload["ref_path"]
    language = payload["language"]
    tts_config = _resolve_job_tts_config(payload)
    inference_kwargs = build_xtts_inference_kwargs(tts_config)
    texts = _resolve_job_texts(payload, job_type="test_voice")

    if not os.path.exists(ref_path):
        fallback = os.path.join(PROFILES_DIR, os.path.basename(ref_path))
        if os.path.exists(fallback):
            ref_path = fallback
        else:
            raise FileNotFoundError("Reference audio file not found.")

    model = tts_obj.synthesizer.tts_model
    _check_job_control(db, job_id)
    gpt_cond_latent, speaker_embedding = _load_or_compute_latents(
        tts_obj, db, profile_id, ref_path, job_id
    )

    job_chunks_dir = os.path.join(CHUNKS_DIR, f"test_{job_id}")
    os.makedirs(job_chunks_dir, exist_ok=True)
    silence_gap_seconds = float(tts_config.get("silence_gap_seconds", SILENCE_GAP_SECONDS))
    silence_samples = int(AUDIO_SAMPLE_RATE * silence_gap_seconds)
    silence_tensor = torch.zeros(1, silence_samples)

    with torch.inference_mode():
        for idx, text_chunk in enumerate(texts):
            _check_job_control(db, job_id)
            db.update_job(job_id, progress=f"Generating sample chunk {idx + 1}/{len(texts)}...")
            result = model.inference(
                text=text_chunk,
                language=language,
                gpt_cond_latent=gpt_cond_latent,
                speaker_embedding=speaker_embedding,
                **inference_kwargs,
            )
            chunk_audio = torch.tensor(result["wav"]).unsqueeze(0)
            audio_path = os.path.join(job_chunks_dir, f"chunk_{idx * 2:04d}.wav")
            save_pcm16_wav(audio_path, chunk_audio.cpu())
            if idx < len(texts) - 1:
                silence_path = os.path.join(job_chunks_dir, f"chunk_{idx * 2 + 1:04d}.wav")
                save_pcm16_wav(silence_path, silence_tensor)

    db.update_job(job_id, progress="Finalizing voice test...")
    test_path = os.path.join(OUTPUT_DIR, f"test_{job_id}.wav")
    chunk_paths = [
        os.path.join(job_chunks_dir, f"chunk_{i:04d}.wav")
        for i in range(len(texts) * 2 - 1)
    ]
    merge_wav_chunks_streaming(chunk_paths, test_path)
    shutil.rmtree(job_chunks_dir, ignore_errors=True)

    db.update_job(
        job_id,
        status="completed",
        progress="Voice test ready.",
        result_path=test_path,
        filename="voice_test.wav",
        finished_at=time.time(),
    )
    db.prune_terminal_jobs(MAX_JOB_HISTORY, job_type="test_voice")


# ---------------------------------------------------------------------------
# Main run loop
# ---------------------------------------------------------------------------


def run() -> None:
    if not DB_PATH:
        print("[Worker] TTS_DB_PATH not set — cannot start.")
        return

    # Ensure output directories exist
    for d in (PROFILES_DIR, OUTPUT_DIR, CHUNKS_DIR):
        if d:
            os.makedirs(d, exist_ok=True)

    db = WorkerDB(DB_PATH)
    db.requeue_stale(STALE_PROCESSING_SECONDS)

    print("[Worker] Loading XTTS-v2 model...")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[Worker] Device: {device}")
    tts_obj = CoquiTTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    print("[Worker] Model loaded successfully.")
    if torch.cuda.is_available():
        torch.set_float32_matmul_precision("high")

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop, args=(db,), daemon=True, name="worker-heartbeat"
    )
    heartbeat_thread.start()
    _set_heartbeat_state("idle")
    print(f"[Worker] Started with id={WORKER_ID}")

    try:
        while True:
            job = db.claim_next_job(WORKER_ID)
            if job is None:
                _set_heartbeat_state("idle")
                time.sleep(WORKER_POLL_INTERVAL)
                continue

            job_id = job["job_id"]
            job_type = job["job_type"]
            _set_heartbeat_state("processing", job_id)
            print(f"[Worker] Processing {job_type} job: {job_id}")

            try:
                if job_type == "generate":
                    process_generate_job(tts_obj, db, job)
                elif job_type == "test_voice":
                    process_test_voice_job(tts_obj, db, job)
                elif job_type == "latent_precompute":
                    process_latent_job(tts_obj, db, job)
                else:
                    raise ValueError(f"Unknown job type: {job_type}")
            except JobPausedByUser:
                print(f"[Worker] Job {job_id} paused by user.")
                db.update_job(
                    job_id, status="paused", progress="Paused by user.", control_action=None
                )
            except JobStoppedByUser:
                print(f"[Worker] Job {job_id} stopped by user.")
                db.update_job(
                    job_id,
                    status="canceled",
                    progress="Stopped by user.",
                    control_action=None,
                    finished_at=time.time(),
                )
            except Exception as err:
                msg = str(err)[:1500]
                print(f"[Worker] Job {job_id} failed: {msg}")
                traceback.print_exc()
                db.update_job(
                    job_id,
                    status="error",
                    progress=msg,
                    control_action=None,
                    error_message=msg,
                    finished_at=time.time(),
                )
                _set_heartbeat_state("processing", job_id, msg)
            finally:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                _set_heartbeat_state("idle")

    except KeyboardInterrupt:
        print("[Worker] Shutdown requested.")
    finally:
        STOP_EVENT.set()
        heartbeat_thread.join(timeout=2)
        _set_heartbeat_state("stopped")
        try:
            db.record_heartbeat(
                WorkerHeartbeat(
                    worker_id=WORKER_ID,
                    status="stopped",
                    current_job_id=None,
                    pid=os.getpid(),
                    started_at=STARTED_AT,
                )
            )
        except Exception:
            pass


if __name__ == "__main__":
    run()
