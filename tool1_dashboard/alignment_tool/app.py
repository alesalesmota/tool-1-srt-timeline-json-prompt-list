from __future__ import annotations

import re
import tempfile
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from .config import FRONTEND_DIR, LANGUAGE_PROFILES, OUTPUT_ROOT, TEMP_ROOT
from .mfa_resources import mfa_resource_status, prepare_mfa_language_resources_async
from .models import EngineConfig, SegmentationConfig
from .orchestrator import run_alignment_job
from .runtime import ensure_dir, make_run_id, probe_health, project_dirs, runtime_profile

project_dirs()

app = FastAPI(title="SRT Alignment Tool")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

INDEX_HTML = FRONTEND_DIR / "index.html"
ALLOWED_DOWNLOADS = {
    "final.srt",
    "words.json",
    "segments.json",
    "alignment_report.json",
    "normalized_audio.wav",
    "run.log",
}
JOB_LOCK = threading.Lock()
JOBS: dict[str, dict[str, object]] = {}


def _serialize_result(result) -> dict[str, object]:
    return {
        "run_id": result.run_id,
        "engine_used": result.engine_used,
        "fallback_used": result.fallback_used,
        "fallback_reason": result.fallback_reason,
        "output_dir": str(result.output_dir),
        "runtime": runtime_profile(),
        "report": result.report.to_dict(),
        "logs": result.logs,
        "previews": {
            "script_text": result.script_document.canonical_text,
            "srt_text": result.artifacts.final_srt.read_text(encoding="utf-8"),
            "segments": [segment.to_dict() for segment in result.segments[:20]],
        },
        "downloads": {
            "final_srt": _artifact_url(result.run_id, "final.srt"),
            "words_json": _artifact_url(result.run_id, "words.json"),
            "segments_json": _artifact_url(result.run_id, "segments.json"),
            "alignment_report": _artifact_url(result.run_id, "alignment_report.json"),
            "normalized_audio": _artifact_url(result.run_id, "normalized_audio.wav"),
            "run_log": _artifact_url(result.run_id, "run.log"),
        },
    }


def _job_snapshot(job_id: str) -> dict[str, object]:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return {
            "job_id": job_id,
            "status": job["status"],
            "progress": job["progress"],
            "step": job["step"],
            "logs": list(job["logs"]),
            "error": job["error"],
            "result": job["result"],
        }


def _set_job(job_id: str, **updates: object) -> None:
    with JOB_LOCK:
        JOBS[job_id].update(updates)


def _append_job_log(job_id: str, line: str) -> None:
    with JOB_LOCK:
        JOBS[job_id]["logs"].append(line)


def _queue_job(job_id: str) -> None:
    with JOB_LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "progress": 0,
            "step": "Waiting to start",
            "logs": [],
            "error": None,
            "result": None,
        }


def _execute_job(
    job_id: str,
    audio_path: Path,
    script_path: Path,
    language_code: str,
    engine_config: EngineConfig,
    segmentation_config: SegmentationConfig,
) -> None:
    _set_job(job_id, status="running", progress=3, step="Starting")
    try:
        result = run_alignment_job(
            audio_path=audio_path,
            script_path=script_path,
            language_code=language_code,
            engine_config=engine_config,
            segmentation_config=segmentation_config,
            logger=lambda line: _append_job_log(job_id, line),
            progress_callback=lambda step, percent: _set_job(job_id, step=step, progress=percent),
        )
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        _append_job_log(job_id, f"[error] {detail}")
        _set_job(job_id, status="failed", progress=100, step="Failed", error=detail)
        return
    _set_job(
        job_id,
        status="completed",
        progress=100,
        step="Done",
        result=_serialize_result(result),
    )


def _safe_run_id(run_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", run_id):
        raise HTTPException(status_code=400, detail="Invalid run id.")
    return run_id


def _artifact_url(run_id: str, filename: str) -> str:
    return f"/api/runs/{run_id}/{filename}"


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    if not INDEX_HTML.exists():
        raise HTTPException(status_code=500, detail="Alignment UI is missing.")
    return INDEX_HTML.read_text(encoding="utf-8")


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        **probe_health(),
        "languages": [
            {
                "code": profile.code,
                "label": profile.label,
                "mfa_resources": mfa_resource_status(profile.code),
            }
            for profile in LANGUAGE_PROFILES
        ],
    }


@app.get("/api/languages/{language_code}/prepare")
async def language_prepare_status(language_code: str) -> dict[str, object]:
    return mfa_resource_status(language_code)


@app.post("/api/languages/{language_code}/prepare")
async def start_language_prepare(language_code: str) -> dict[str, object]:
    try:
        return prepare_mfa_language_resources_async(language_code)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str) -> dict[str, object]:
    return _job_snapshot(job_id)


@app.get("/api/runs/{run_id}/{filename}")
async def download_artifact(run_id: str, filename: str) -> FileResponse:
    run_id = _safe_run_id(run_id)
    if filename not in ALLOWED_DOWNLOADS:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    candidate = (OUTPUT_ROOT / run_id / filename).resolve()
    output_root = OUTPUT_ROOT.resolve()
    if candidate.parent != (OUTPUT_ROOT / run_id).resolve() or output_root not in candidate.parents:
        raise HTTPException(status_code=400, detail="Invalid artifact path.")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return FileResponse(str(candidate), filename=filename)


@app.post("/api/align")
async def align(
    audio_file: UploadFile = File(...),
    script_file: UploadFile = File(...),
    language_code: str = Form("en"),
    primary_engine: str = Form("mfa"),
    fallback_engine: str = Form("whisperx"),
    whisperx_model: str = Form("small"),
    min_duration: float = Form(0.9),
    preferred_duration: float = Form(3.0),
    max_duration: float = Form(6.0),
    max_chars_per_line: int = Form(42),
    max_lines_per_block: int = Form(2),
    max_chars_per_block: int = Form(84),
    max_reading_cps: float = Form(18.0),
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        audio_path = temp_path / (audio_file.filename or "audio.wav")
        script_path = temp_path / (script_file.filename or "script.txt")
        audio_path.write_bytes(await audio_file.read())
        script_path.write_bytes(await script_file.read())

        fallback_value = None if fallback_engine.lower() == "none" else fallback_engine
        engine_config = EngineConfig(
            primary_engine=primary_engine,
            fallback_engine=fallback_value,
            whisperx_model=whisperx_model,
        )
        segmentation_config = SegmentationConfig(
            min_duration=min_duration,
            preferred_duration=preferred_duration,
            max_duration=max_duration,
            max_chars_per_line=max_chars_per_line,
            max_lines_per_block=max_lines_per_block,
            max_chars_per_block=max_chars_per_block,
            max_reading_cps=max_reading_cps,
        )

        try:
            result = run_alignment_job(
                audio_path=audio_path,
                script_path=script_path,
                language_code=language_code,
                engine_config=engine_config,
                segmentation_config=segmentation_config,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        **_serialize_result(result),
    }


@app.post("/api/jobs")
async def start_job(
    audio_file: UploadFile = File(...),
    script_file: UploadFile = File(...),
    language_code: str = Form("en"),
    primary_engine: str = Form("mfa"),
    fallback_engine: str = Form("whisperx"),
    whisperx_model: str = Form("small"),
    min_duration: float = Form(0.9),
    preferred_duration: float = Form(3.0),
    max_duration: float = Form(6.0),
    max_chars_per_line: int = Form(42),
    max_lines_per_block: int = Form(2),
    max_chars_per_block: int = Form(84),
    max_reading_cps: float = Form(18.0),
) -> dict[str, object]:
    job_id = make_run_id("alignment-job")
    job_dir = ensure_dir(TEMP_ROOT / "jobs" / job_id)
    audio_path = job_dir / (audio_file.filename or "audio.wav")
    script_path = job_dir / (script_file.filename or "script.txt")
    audio_path.write_bytes(await audio_file.read())
    script_path.write_bytes(await script_file.read())

    fallback_value = None if fallback_engine.lower() == "none" else fallback_engine
    engine_config = EngineConfig(
        primary_engine=primary_engine,
        fallback_engine=fallback_value,
        whisperx_model=whisperx_model,
    )
    segmentation_config = SegmentationConfig(
        min_duration=min_duration,
        preferred_duration=preferred_duration,
        max_duration=max_duration,
        max_chars_per_line=max_chars_per_line,
        max_lines_per_block=max_lines_per_block,
        max_chars_per_block=max_chars_per_block,
        max_reading_cps=max_reading_cps,
    )

    _queue_job(job_id)
    _append_job_log(job_id, "[queued] Files received. Waiting for worker thread.")
    threading.Thread(
        target=_execute_job,
        args=(job_id, audio_path, script_path, language_code, engine_config, segmentation_config),
        daemon=True,
    ).start()
    return {
        "job_id": job_id,
        "status": "queued",
    }
