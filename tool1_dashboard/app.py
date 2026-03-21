from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    IMAGE_PROMPT_STAGE,
    SCENE_STAGE,
    UI_DIR,
    VIDEO_PROMPT_STAGE,
    VISUAL_BIBLE_STAGE,
)
from .service import Tool1Service


class MoveRequest(BaseModel):
    board_status: str


class QueueRequest(BaseModel):
    start_stage: str = "alignment"


class JobConfigRequest(BaseModel):
    scene_planning_provider: str
    visual_bible_provider: str
    video_prompt_provider: str
    image_prompt_provider: str
    leading_video_scene_count: int


class SettingsRequest(BaseModel):
    default_scene_planning_provider: str
    default_visual_bible_provider: str
    default_video_prompt_provider: str
    default_image_prompt_provider: str
    leading_video_scene_count: int
    planning_chunk_seconds: int
    planning_overlap_seconds: int
    prompt_batch_size: int


class TemplateRequest(BaseModel):
    body: str


class TimelineRequest(BaseModel):
    scenes: list[dict[str, Any]]


class VisualBibleRequest(BaseModel):
    visual_bible: dict[str, Any]


class PromptsRequest(BaseModel):
    prompts: list[str]


service = Tool1Service()
app = FastAPI(title="Tool 1 CLI-First Dashboard")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


@app.on_event("startup")
async def startup() -> None:
    service.start_worker()


@app.on_event("shutdown")
async def shutdown() -> None:
    service.stop_worker()


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    index_path = UI_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="UI is missing.")
    return index_path.read_text(encoding="utf-8")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return service.get_health()


@app.post("/api/languages/{language_code}/prepare")
async def prepare_language(language_code: str) -> dict[str, Any]:
    try:
        return service.prepare_language(language_code)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/board")
async def board() -> dict[str, Any]:
    return {"jobs": service.list_jobs()}


@app.get("/api/settings")
async def settings() -> dict[str, Any]:
    return service.get_settings_payload()


@app.post("/api/settings")
async def save_settings(payload: SettingsRequest) -> dict[str, Any]:
    try:
        return service.save_settings(payload.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs")
async def create_job(
    title: str = Form(...),
    language_code: str = Form("en"),
    scene_planning_provider: str = Form("claude"),
    visual_bible_provider: str = Form("claude"),
    video_prompt_provider: str = Form("codex"),
    image_prompt_provider: str = Form("codex"),
    leading_video_scene_count: int = Form(20),
    audio_file: UploadFile = File(...),
    script_file: UploadFile = File(...),
) -> dict[str, Any]:
    try:
        return service.create_job(
            title=title,
            audio_name=audio_file.filename or "audio.wav",
            audio_bytes=await audio_file.read(),
            script_name=script_file.filename or "script.txt",
            script_bytes=await script_file.read(),
            language_code=language_code,
            scene_planning_provider=scene_planning_provider,
            visual_bible_provider=visual_bible_provider,
            video_prompt_provider=video_prompt_provider,
            image_prompt_provider=image_prompt_provider,
            leading_video_scene_count=leading_video_scene_count,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}")
async def job_detail(job_id: str) -> dict[str, Any]:
    try:
        return service.get_job_detail(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/move")
async def move_job(job_id: str, payload: MoveRequest) -> dict[str, Any]:
    try:
        return service.move_job(job_id, payload.board_status)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/queue")
async def queue_job(job_id: str, payload: QueueRequest) -> dict[str, Any]:
    try:
        return service.queue_job(job_id, payload.start_stage)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/config")
async def save_job_config(job_id: str, payload: JobConfigRequest) -> dict[str, Any]:
    try:
        return service.update_job_config(job_id, **payload.model_dump())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/review/timeline")
async def save_timeline(job_id: str, payload: TimelineRequest) -> dict[str, Any]:
    try:
        return service.save_review_timeline(job_id, payload.scenes)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/review/visual-bible")
async def save_visual_bible(job_id: str, payload: VisualBibleRequest) -> dict[str, Any]:
    try:
        return service.save_review_visual_bible(job_id, payload.visual_bible)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/review/prompts")
async def save_prompts(job_id: str, payload: PromptsRequest) -> dict[str, Any]:
    try:
        return service.save_review_prompts(job_id, payload.prompts)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/finalize")
async def finalize_job(job_id: str) -> dict[str, Any]:
    try:
        return service.finalize_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/templates")
async def templates() -> dict[str, Any]:
    return {"templates": service.get_settings_payload()["templates"]}


@app.post("/api/templates/{stage}/{provider}")
async def save_template(stage: str, provider: str, payload: TemplateRequest) -> dict[str, Any]:
    if stage not in {SCENE_STAGE, VISUAL_BIBLE_STAGE, VIDEO_PROMPT_STAGE, IMAGE_PROMPT_STAGE}:
        raise HTTPException(status_code=400, detail="Invalid stage.")
    try:
        return service.save_template(stage, provider, payload.body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}/artifacts/{artifact_key}")
async def artifact(job_id: str, artifact_key: str) -> FileResponse:
    try:
        path = service.get_artifact_path(job_id, artifact_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(str(path), filename=Path(path).name)
