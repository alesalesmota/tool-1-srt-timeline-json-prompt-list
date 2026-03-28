from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import (
    IMAGE_PROMPT_STAGE,
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


class BatchQueueRequest(BaseModel):
    filter_status: str = "draft"


class EpisodeSubmitRequest(BaseModel):
    title: str
    script_text: str


class EpisodeQueueRequest(BaseModel):
    start_stage: str | None = None


class LanguageRetryRequest(BaseModel):
    language_code: str
    stage: str


class ReviewDataUpdateRequest(BaseModel):
    consistency_guide: dict | None = None
    timeline_draft: list | None = None
    prompt_list: str | None = None


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
    service.start_worker()


@app.on_event("shutdown")
async def shutdown() -> None:
    service.stop_worker()
    service.tts_manager.stop_worker()


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
    return service.get_health()


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


@app.post("/api/episodes/{episode_id}/queue")
async def queue_episode(episode_id: str, payload: EpisodeQueueRequest) -> dict[str, Any]:
    try:
        return service.queue_episode(episode_id, payload.start_stage)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QueueBlockedError as exc:
        raise HTTPException(status_code=400, detail=exc.to_detail()) from exc
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
        detail = service.get_episode_detail(episode_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    episode = detail["episode"]
    workspace_dir = episode.get("workspace_dir", "")
    files: list[dict[str, Any]] = []

    if workspace_dir:
        from pathlib import Path
        ws = Path(workspace_dir)
        if ws.exists():
            # Collect key output files
            patterns = ["*.srt", "*.json", "*.txt", "*.wav", "*.mp3"]
            seen: set[str] = set()
            for pattern in patterns:
                for fp in ws.glob(pattern):
                    if fp.name not in seen:
                        seen.add(fp.name)
                        files.append({
                            "name": fp.name,
                            "size": fp.stat().st_size,
                            "ext": fp.suffix,
                            "path": str(fp),
                        })
            # Also check subdirs (runs/, alignment/)
            for fp in ws.rglob("*"):
                if fp.is_file() and fp.suffix in {".srt", ".json", ".txt"} and fp.name not in seen:
                    seen.add(fp.name)
                    rel = str(fp.relative_to(ws)).replace("\\", "/")
                    files.append({
                        "name": rel,
                        "size": fp.stat().st_size,
                        "ext": fp.suffix,
                        "path": str(fp),
                    })
            files.sort(key=lambda f: f["name"])

    return {"files": files}


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
