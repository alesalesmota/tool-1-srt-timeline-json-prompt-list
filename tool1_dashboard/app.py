from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import (
    IMAGE_PROMPT_STAGE,
    MAX_PREVIEW_CHARS,
    SCENE_STAGE,
    UI_DIR,
    VIDEO_PROMPT_STAGE,
    VISUAL_BIBLE_STAGE,
)
from .service import QueueBlockedError, Tool1Service


class SettingsRequest(BaseModel):
    default_scene_planning_provider: str
    default_visual_bible_provider: str
    default_video_prompt_provider: str
    default_image_prompt_provider: str
    default_scene_planning_model: str
    default_visual_bible_model: str
    default_video_prompt_model: str
    default_image_prompt_model: str
    leading_video_scene_count: int
    planning_chunk_seconds: int
    planning_overlap_seconds: int
    prompt_batch_size: int
    stage_provider_openai_api_key: str | None = None


class TemplateRequest(BaseModel):
    body: str


class TranslationProfileRequest(BaseModel):
    name: str
    provider: str
    api_key: str
    model: str


class TranslationProfileUpdateRequest(BaseModel):
    name: str | None = None
    provider: str | None = None
    api_key: str | None = None
    model: str | None = None


class OpenAiModelDiscoveryRequest(BaseModel):
    api_key: str | None = None
    profile_id: str | None = None


class StageProviderOpenAiDiscoveryRequest(BaseModel):
    api_key: str | None = None


class VoiceTestRequest(BaseModel):
    text: str | None = None
    language: str = "en"


class VoiceProfileTtsConfigRequest(BaseModel):
    preset: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    speed: float | None = None
    chunk_max_chars: int | None = None
    silence_gap_seconds: float | None = None


class VoiceProfileUpdateRequest(BaseModel):
    name: str | None = None
    language_code: str | None = None
    tts_config: VoiceProfileTtsConfigRequest | None = None


class NicheProjectRequest(BaseModel):
    name: str
    master_language: str = "en"
    configured_languages: list[str] = []
    language_voice_profiles: dict[str, str] = {}
    language_translation_profiles: dict[str, str] = {}
    scene_planning_provider: str = "claude"
    visual_bible_provider: str = "claude"
    video_prompt_provider: str = "codex"
    image_prompt_provider: str = "codex"
    scene_planning_model: str = "haiku"
    visual_bible_model: str = "haiku"
    video_prompt_model: str = "gpt-5.4"
    image_prompt_model: str = "gpt-5.4"
    leading_video_scene_count: int = 20


class NicheProjectUpdateRequest(BaseModel):
    name: str | None = None
    configured_languages: list[str] | None = None
    language_voice_profiles: dict[str, str] | None = None
    language_translation_profiles: dict[str, str] | None = None
    scene_planning_provider: str | None = None
    visual_bible_provider: str | None = None
    video_prompt_provider: str | None = None
    image_prompt_provider: str | None = None
    scene_planning_model: str | None = None
    visual_bible_model: str | None = None
    video_prompt_model: str | None = None
    image_prompt_model: str | None = None
    leading_video_scene_count: int | None = None
    source_channel_name: str | None = None
    language_channel_names: dict[str, str] | None = None
    channel_replace_prompt: bool | None = None
    channel_replace_post: bool | None = None


class BatchQueueRequest(BaseModel):
    filter_status: str = "draft"


class EpisodeSubmitRequest(BaseModel):
    title: str
    script_text: str


class EpisodeQueueRequest(BaseModel):
    start_stage: str | None = None
    reset_outputs: bool = False


class EpisodePauseRequest(BaseModel):
    pass


class LanguageRetryRequest(BaseModel):
    language_code: str
    stage: str


class ReviewDataUpdateRequest(BaseModel):
    consistency_guide: dict | None = None
    timeline_draft: list | None = None
    prompt_list: str | None = None


class AssemblyAdvanceRequest(BaseModel):
    target_stage: str


class AssemblyRenderRequest(BaseModel):
    language_code: str


service = Tool1Service()
app = FastAPI(title="Creator Studio")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


TEXT_PREVIEW_EXTENSIONS = {".csv", ".json", ".jsonl", ".log", ".md", ".srt", ".txt"}
AUDIO_PREVIEW_EXTENSIONS = {".m4a", ".mp3", ".ogg", ".wav"}
OUTPUT_FILE_EXTENSIONS = TEXT_PREVIEW_EXTENSIONS | AUDIO_PREVIEW_EXTENSIONS | {".zip"}
_ffmpeg_available = False


def _check_ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _ui_asset_version() -> str:
    css_path = UI_DIR / "app.css"
    js_path = UI_DIR / "app.js"
    css_stamp = css_path.stat().st_mtime_ns if css_path.exists() else 0
    js_stamp = js_path.stat().st_mtime_ns if js_path.exists() else 0
    return str(max(css_stamp, js_stamp))


def _apply_no_store_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.middleware("http")
async def disable_ui_caching(request: Request, call_next) -> Response:
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/ui/"):
        return _apply_no_store_headers(response)
    return response


@app.on_event("startup")
async def startup() -> None:
    global _ffmpeg_available
    _ffmpeg_available = _check_ffmpeg_available()
    service.start_worker()


@app.on_event("shutdown")
async def shutdown() -> None:
    service.stop_worker()
    service.tts_manager.stop_worker()


def _episode_workspace_dir(episode_id: str) -> Path | None:
    detail = service.get_episode_detail(episode_id)
    workspace_dir = str(detail["episode"].get("workspace_dir", "")).strip()
    if not workspace_dir:
        return None
    workspace = Path(workspace_dir)
    return workspace if workspace.exists() else None


def _resolve_episode_file_path(workspace_dir: Path, relative_path: str) -> Path:
    rel_path = Path(str(relative_path or "").strip())
    if not rel_path.parts or rel_path.is_absolute():
        raise HTTPException(status_code=400, detail="Invalid file path.")

    workspace_root = workspace_dir.resolve()
    candidate = (workspace_root / rel_path).resolve()
    try:
        candidate.relative_to(workspace_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path.") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Output file not found.")
    return candidate


def _episode_file_preview_type(file_path: Path, *, size: int | None = None) -> str:
    file_size = file_path.stat().st_size if size is None else size
    if file_size <= 0:
        return "empty"
    ext = file_path.suffix.lower()
    if ext in AUDIO_PREVIEW_EXTENSIONS:
        return "audio"
    if ext == ".json":
        return "json"
    if ext in TEXT_PREVIEW_EXTENSIONS:
        return "text"
    return "binary"


def _serialize_episode_file(workspace_dir: Path, file_path: Path) -> dict[str, Any]:
    stat = file_path.stat()
    relative_path = file_path.relative_to(workspace_dir).as_posix()
    parent = Path(relative_path).parent.as_posix()
    return {
        "relative_path": relative_path,
        "name": file_path.name,
        "directory": "" if parent in {"", "."} else parent,
        "parent_label": "workspace root" if parent in {"", "."} else parent,
        "size": stat.st_size,
        "ext": file_path.suffix.lower(),
        "modified_at": stat.st_mtime,
        "preview_type": _episode_file_preview_type(file_path, size=stat.st_size),
        "is_empty": stat.st_size <= 0,
    }


def _episode_file_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    ext_priority = {
        ".srt": 0,
        ".json": 1,
        ".jsonl": 2,
        ".txt": 3,
        ".log": 4,
        ".md": 5,
        ".csv": 6,
        ".wav": 7,
        ".mp3": 8,
        ".m4a": 9,
        ".ogg": 10,
        ".zip": 11,
    }
    return (
        item["is_empty"],
        item["directory"] != "",
        ext_priority.get(item["ext"], 99),
        item["relative_path"].lower(),
    )


def _collect_episode_output_files(workspace_dir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for file_path in workspace_dir.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in OUTPUT_FILE_EXTENSIONS:
            continue
        files.append(_serialize_episode_file(workspace_dir, file_path))
    files.sort(key=_episode_file_sort_key)
    return files


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    index_path = UI_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="UI is missing.")
    version = _ui_asset_version()
    html = index_path.read_text(encoding="utf-8")
    html = re.sub(r'/ui/app\.css(?:\?v=[^"]+)?', f'/ui/app.css?v={version}', html)
    html = re.sub(r'/ui/app\.js(?:\?v=[^"]+)?', f'/ui/app.js?v={version}', html)
    return html


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        **service.get_health(),
        "ffmpeg_available": _ffmpeg_available,
    }


@app.post("/api/languages/{language_code}/prepare")
async def prepare_language(language_code: str) -> dict[str, Any]:
    try:
        return service.prepare_language(language_code)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/target-languages")
async def target_languages() -> dict[str, Any]:
    return {"languages": service.get_target_languages()}


@app.get("/api/settings")
async def settings() -> dict[str, Any]:
    return service.get_settings_payload()


@app.post("/api/settings")
async def save_settings(payload: SettingsRequest) -> dict[str, Any]:
    try:
        return service.save_settings(payload.model_dump())
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


# ── Niche project routes ────────────────────────────────────────────


@app.post("/api/niche-projects")
async def create_niche_project(payload: NicheProjectRequest) -> dict[str, Any]:
    try:
        return service.create_niche_project(
            name=payload.name,
            master_language=payload.master_language,
            configured_languages=payload.configured_languages,
            language_voice_profiles=payload.language_voice_profiles,
            language_translation_profiles=payload.language_translation_profiles,
            scene_planning_provider=payload.scene_planning_provider,
            visual_bible_provider=payload.visual_bible_provider,
            video_prompt_provider=payload.video_prompt_provider,
            image_prompt_provider=payload.image_prompt_provider,
            scene_planning_model=payload.scene_planning_model,
            visual_bible_model=payload.visual_bible_model,
            video_prompt_model=payload.video_prompt_model,
            image_prompt_model=payload.image_prompt_model,
            leading_video_scene_count=payload.leading_video_scene_count,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/niche-projects")
async def list_niche_projects() -> dict[str, Any]:
    return {"projects": service.list_niche_projects()}


@app.get("/api/niche-projects/{project_id}")
async def niche_project_detail(project_id: str) -> dict[str, Any]:
    try:
        return service.get_niche_project_detail(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/niche-projects/{project_id}")
async def update_niche_project(project_id: str, payload: NicheProjectUpdateRequest) -> dict[str, Any]:
    try:
        fields = {k: v for k, v in payload.model_dump().items() if v is not None}
        return service.update_niche_project(project_id, **fields)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/niche-projects/{project_id}")
async def delete_niche_project(project_id: str) -> dict[str, Any]:
    try:
        return service.delete_niche_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/niche-projects/{project_id}/batch-queue")
async def batch_queue_episodes(project_id: str, payload: BatchQueueRequest) -> dict[str, Any]:
    try:
        return service.batch_queue_episodes(project_id, payload.filter_status)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Episode routes ──────────────────────────────────────────────────


@app.post("/api/niche-projects/{project_id}/episodes")
async def submit_episode(project_id: str, payload: EpisodeSubmitRequest) -> dict[str, Any]:
    try:
        return service.submit_episode(
            project_id,
            title=payload.title,
            script_text=payload.script_text,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/episodes/{episode_id}")
async def episode_detail(episode_id: str) -> dict[str, Any]:
    try:
        return service.get_episode_detail(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/episodes/{episode_id}/scenes")
async def episode_scenes(episode_id: str) -> dict[str, Any]:
    try:
        return service.list_episode_scenes(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/episodes/{episode_id}/scenes/{scene_id}/asset")
async def upload_scene_asset(
    episode_id: str,
    scene_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    try:
        await file.seek(0)
        return service.upload_scene_asset(
            episode_id,
            scene_id,
            source_file=file.file,
            original_filename=file.filename or "asset",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()


@app.post("/api/episodes/{episode_id}/scenes/bulk-upload")
async def bulk_upload_scene_assets(
    episode_id: str,
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    uploads: list[tuple[str, Any]] = []
    try:
        for upload in files:
            await upload.seek(0)
            uploads.append((upload.filename or "asset", upload.file))
        return service.bulk_upload_scene_assets(episode_id, uploads)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        for upload in files:
            await upload.close()


@app.delete("/api/episodes/{episode_id}/scenes/{scene_id}/asset")
async def delete_scene_asset(episode_id: str, scene_id: str) -> dict[str, Any]:
    try:
        return service.delete_scene_asset(episode_id, scene_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/episodes/{episode_id}/scenes/{scene_id}/asset/preview")
async def preview_scene_asset(episode_id: str, scene_id: str) -> FileResponse:
    try:
        asset_path = service.get_scene_asset_preview_path(episode_id, scene_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    media_type = mimetypes.guess_type(asset_path.name)[0]
    return FileResponse(str(asset_path), filename=asset_path.name, media_type=media_type)


@app.post("/api/episodes/{episode_id}/assembly/validate")
async def validate_episode_assembly(
    episode_id: str,
    language_code: str | None = None,
) -> dict[str, Any]:
    try:
        return service.validate_assembly(episode_id, language_code=language_code)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/episodes/{episode_id}/assembly/start")
async def start_episode_assembly(episode_id: str) -> dict[str, Any]:
    try:
        return service.start_assembly(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/episodes/{episode_id}/assembly/advance")
async def advance_episode_assembly(
    episode_id: str,
    payload: AssemblyAdvanceRequest,
) -> dict[str, Any]:
    try:
        return service.advance_assembly_stage(episode_id, payload.target_stage)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/episodes/{episode_id}/assembly/render")
async def start_episode_render(
    episode_id: str,
    payload: AssemblyRenderRequest,
) -> dict[str, Any]:
    try:
        return service.start_render(episode_id, payload.language_code)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/episodes/{episode_id}/assembly/render-status")
async def episode_render_status(episode_id: str) -> dict[str, Any]:
    try:
        return service.get_render_status(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/episodes/{episode_id}/assembly/render-jobs")
async def episode_render_jobs(episode_id: str) -> dict[str, Any]:
    try:
        return service.list_render_jobs_payload(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/episodes/{episode_id}/assembly/cleanup")
async def cleanup_episode_assembly(episode_id: str) -> dict[str, Any]:
    try:
        return service.cleanup_assembly_temp_files(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/episodes/{episode_id}/assembly/render/{render_job_id}")
async def delete_episode_render_job(
    episode_id: str,
    render_job_id: str,
) -> dict[str, Any]:
    try:
        return service.delete_render_job(episode_id, render_job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/episodes/{episode_id}/assembly/render/{render_job_id}/events")
async def render_job_events(episode_id: str, render_job_id: str) -> StreamingResponse:
    """SSE stream of render progress for a specific render job."""

    job = service.db.get_render_job(render_job_id)
    if job is None or job["episode_id"] != episode_id:
        raise HTTPException(status_code=404, detail="Render job not found.")

    async def event_stream():
        last_log_id = 0
        while True:
            job = service.db.get_render_job(render_job_id)
            if job is None:
                break

            logs = service.db.list_render_logs(render_job_id)
            new_logs = [log for log in logs if log["id"] > last_log_id]
            if new_logs:
                last_log_id = max(log["id"] for log in new_logs)

            payload = json.dumps(
                {
                    "job": {
                        "id": job["id"],
                        "state": job["state"],
                        "stage": job["stage"],
                        "current_scene_id": job["current_scene_id"],
                        "total_scenes": job["total_scenes"],
                        "completed_scenes": job["completed_scenes"],
                        "error_message": job["error_message"],
                        "started_at": job["started_at"],
                        "finished_at": job["finished_at"],
                    },
                    "new_logs": [
                        {
                            "id": log["id"],
                            "level": log["level"],
                            "stage": log["stage"],
                            "message": log["message"],
                            "scene_id": log["scene_id"],
                            "timestamp": log["timestamp"],
                        }
                        for log in new_logs
                    ],
                },
                ensure_ascii=False,
            )
            yield f"event: update\ndata: {payload}\n\n"

            if job["state"] in ("completed", "failed"):
                break

            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/episodes/{episode_id}/assembly/render/{render_job_id}/video")
async def render_job_video(episode_id: str, render_job_id: str) -> FileResponse:
    try:
        video_path = service.get_render_job_video_path(episode_id, render_job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(str(video_path), filename=video_path.name, media_type="video/mp4")


@app.get("/api/episodes/{episode_id}/assembly/render/{render_job_id}/scene/{scene_id}")
async def render_job_scene_clip(
    episode_id: str,
    render_job_id: str,
    scene_id: str,
) -> FileResponse:
    try:
        scene_path = service.get_render_job_scene_path(episode_id, render_job_id, scene_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(str(scene_path), filename=scene_path.name, media_type="video/mp4")


@app.post("/api/episodes/{episode_id}/queue")
async def queue_episode(episode_id: str, payload: EpisodeQueueRequest) -> dict[str, Any]:
    try:
        return service.queue_episode(
            episode_id,
            payload.start_stage,
            reset_outputs=payload.reset_outputs,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QueueBlockedError as exc:
        raise HTTPException(status_code=400, detail=exc.to_detail()) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/episodes/{episode_id}/pause")
async def pause_episode(episode_id: str, payload: EpisodePauseRequest | None = None) -> dict[str, Any]:
    try:
        return service.pause_episode(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/episodes/{episode_id}")
async def delete_episode(episode_id: str) -> dict[str, Any]:
    try:
        return service.delete_episode(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/board/episodes")
async def board_episodes() -> dict[str, Any]:
    return {"episodes": service.list_all_episodes_for_board()}


@app.get("/api/episodes/{episode_id}/files")
async def episode_files(episode_id: str) -> dict[str, Any]:
    """List output files in the episode workspace for preview."""
    try:
        workspace_dir = _episode_workspace_dir(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not workspace_dir:
        return {"files": []}
    return {"files": _collect_episode_output_files(workspace_dir)}


@app.get("/api/episodes/{episode_id}/files/content")
async def episode_file_content(episode_id: str, path: str) -> dict[str, Any]:
    try:
        workspace_dir = _episode_workspace_dir(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not workspace_dir:
        raise HTTPException(status_code=404, detail="Episode workspace not found.")

    file_path = _resolve_episode_file_path(workspace_dir, path)
    file_meta = _serialize_episode_file(workspace_dir, file_path)
    preview_type = file_meta["preview_type"]
    response: dict[str, Any] = {
        "file": file_meta,
        "preview_type": preview_type,
    }

    if preview_type == "empty":
        response["summary"] = "This file exists but does not contain data yet."
        return response

    if preview_type == "audio":
        response["summary"] = "Audio preview available."
        return response

    if preview_type == "binary":
        response["summary"] = "Preview is not available for this file type."
        return response

    raw_text = file_path.read_text(encoding="utf-8", errors="replace")
    text = raw_text
    if file_path.suffix.lower() == ".json":
        try:
            text = json.dumps(json.loads(raw_text), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            text = raw_text
    truncated = len(text) > MAX_PREVIEW_CHARS
    if truncated:
        text = text[:MAX_PREVIEW_CHARS].rstrip() + "\n\n... (preview truncated)"

    response["text"] = text
    response["truncated"] = truncated
    return response


@app.get("/api/episodes/{episode_id}/files/download")
async def download_episode_file(episode_id: str, path: str) -> FileResponse:
    try:
        workspace_dir = _episode_workspace_dir(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not workspace_dir:
        raise HTTPException(status_code=404, detail="Episode workspace not found.")

    file_path = _resolve_episode_file_path(workspace_dir, path)
    media_type = mimetypes.guess_type(file_path.name)[0]
    return FileResponse(
        str(file_path),
        filename=file_path.name,
        media_type=media_type,
    )


@app.post("/api/episodes/{episode_id}/retry-language")
async def retry_episode_language(episode_id: str, payload: LanguageRetryRequest) -> dict[str, Any]:
    try:
        return service.retry_episode_language(episode_id, payload.language_code, payload.stage)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/episodes/{episode_id}/translation-preview/{language_code}")
async def translation_preview(episode_id: str, language_code: str) -> dict[str, Any]:
    try:
        return service.get_translation_preview(episode_id, language_code)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── Review & Export routes ─────────────────────────────────────────


@app.get("/api/episodes/{episode_id}/review-data")
async def review_data(episode_id: str) -> dict[str, Any]:
    try:
        return service.get_review_data(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/episodes/{episode_id}/review-data")
async def update_review_data(episode_id: str, payload: ReviewDataUpdateRequest) -> dict[str, Any]:
    try:
        return service.update_review_data(
            episode_id,
            consistency_guide=payload.consistency_guide,
            timeline_draft=payload.timeline_draft,
            prompt_list=payload.prompt_list,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/episodes/{episode_id}/finalize-export")
async def finalize_export(episode_id: str) -> dict[str, Any]:
    try:
        return service.finalize_export(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/episodes/{episode_id}/export/download")
async def download_export(episode_id: str) -> FileResponse:
    try:
        detail = service.get_episode_detail(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    episode = detail["episode"]
    workspace = Path(episode.get("workspace_dir", ""))
    zip_path = workspace / f"export_{episode_id}.zip"
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="Export zip not found. Run finalize first.")
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=f"{episode.get('title', episode_id)}_export.zip",
    )


# ── Voice & Translation profile routes ─────────────────────────────


@app.get("/api/voice-profiles")
async def list_voice_profiles() -> dict[str, Any]:
    return {"profiles": service.list_voice_profiles()}


@app.get("/api/voice-profiles/{profile_id}")
async def voice_profile_detail(profile_id: str) -> dict[str, Any]:
    try:
        return service.get_voice_profile(profile_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/translation-profiles")
async def list_translation_profiles() -> dict[str, Any]:
    return {"profiles": service.list_translation_profiles_public()}


@app.post("/api/translation-profiles")
async def create_translation_profile(payload: TranslationProfileRequest) -> dict[str, Any]:
    try:
        profile = service.create_translation_profile(
            name=payload.name,
            provider=payload.provider,
            api_key=payload.api_key,
            model=payload.model,
        )
        return service.get_translation_profile_public(profile["id"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/translation-profiles/openai/discover")
async def discover_openai_translation_models(payload: OpenAiModelDiscoveryRequest) -> dict[str, Any]:
    try:
        return await service.discover_openai_translation_models(
            api_key=payload.api_key or "",
            profile_id=payload.profile_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/providers/openai/discover")
async def discover_openai_stage_provider_models(
    payload: StageProviderOpenAiDiscoveryRequest,
) -> dict[str, Any]:
    try:
        return await service.discover_openai_stage_provider_models(
            api_key=payload.api_key or "",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/translation-profiles/{profile_id}")
async def translation_profile_detail(profile_id: str) -> dict[str, Any]:
    try:
        return service.get_translation_profile_public(profile_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/translation-profiles/{profile_id}")
async def update_translation_profile(
    profile_id: str,
    payload: TranslationProfileUpdateRequest,
) -> dict[str, Any]:
    try:
        fields = payload.model_dump(exclude_none=True)
        if "api_key" in fields:
            fields["api_key_ref"] = fields.pop("api_key")
        service.update_translation_profile(profile_id, **fields)
        return service.get_translation_profile_public(profile_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/translation-profiles/{profile_id}")
async def delete_translation_profile(profile_id: str) -> dict[str, Any]:
    try:
        return service.delete_translation_profile(profile_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── Voice profile CRUD ─────────────────────────────────────────────


@app.post("/api/voice-profiles")
async def create_voice_profile(
    name: str = Form(...),
    language_code: str = Form(""),
    audio_file: UploadFile = File(...),
) -> dict[str, Any]:
    from .tts.constants import MAX_PROFILE_AUDIO_BYTES

    audio_bytes = await audio_file.read()
    if len(audio_bytes) > MAX_PROFILE_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file too large (max 25 MB).")
    try:
        return service.create_voice_profile(
            name=name,
            language_code=(language_code or "").strip(),
            audio_bytes=audio_bytes,
            audio_filename=audio_file.filename or "upload.wav",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/voice-profiles/{profile_id}")
async def update_voice_profile(
    profile_id: str, payload: VoiceProfileUpdateRequest
) -> dict[str, Any]:
    try:
        return service.update_voice_profile(
            profile_id, **payload.model_dump(exclude_none=True)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/voice-profiles/{profile_id}")
async def delete_voice_profile(profile_id: str) -> dict[str, Any]:
    try:
        return service.delete_voice_profile(profile_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── TTS execution ──────────────────────────────────────────────────


@app.post("/api/voice-profiles/{profile_id}/test")
async def test_voice(profile_id: str, payload: VoiceTestRequest) -> dict[str, Any]:
    try:
        return service.submit_voice_test(profile_id, payload.text, payload.language)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── TTS job control ────────────────────────────────────────────────


@app.get("/api/tts-jobs/{job_id}")
async def tts_job_status(job_id: str) -> dict[str, Any]:
    try:
        return service.get_tts_job_status(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/tts-jobs/{job_id}/pause")
async def pause_tts_job(job_id: str) -> dict[str, Any]:
    try:
        return service.pause_tts_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/tts-jobs/{job_id}/resume")
async def resume_tts_job(job_id: str) -> dict[str, Any]:
    try:
        return service.resume_tts_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/tts-jobs/{job_id}/stop")
async def stop_tts_job(job_id: str) -> dict[str, Any]:
    try:
        return service.stop_tts_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/tts-jobs/{job_id}/download")
async def download_tts_output(job_id: str) -> FileResponse:
    job = service.get_tts_job_status(job_id)
    result_path = job.get("result_path")
    if not result_path or not Path(result_path).exists():
        raise HTTPException(status_code=404, detail="Output file not found.")
    return FileResponse(
        path=result_path,
        media_type="audio/wav",
        filename=job.get("filename", "output.wav"),
    )


# ── Worker health ──────────────────────────────────────────────────


@app.get("/api/worker-health")
async def worker_health() -> dict[str, Any]:
    return service.get_worker_health()


@app.get("/api/app-runtime")
async def app_runtime() -> dict[str, Any]:
    return service.get_app_runtime()


@app.post("/api/worker/start")
async def start_tts_worker() -> dict[str, Any]:
    # Deprecated compatibility route. The first-party UI now auto-starts on demand.
    try:
        service.tts_manager.ensure_worker_ready(intent="interactive")
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"started": True, "health": service.get_worker_health()}


@app.post("/api/worker/stop")
async def stop_tts_worker() -> dict[str, Any]:
    # Deprecated compatibility route. The first-party UI no longer exposes manual worker control.
    service.tts_manager.stop_worker()
    return {"stopped": True}
