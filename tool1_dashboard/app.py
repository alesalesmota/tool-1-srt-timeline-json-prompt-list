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
    scene_planning_model: str
    visual_bible_model: str
    video_prompt_model: str
    image_prompt_model: str
    leading_video_scene_count: int


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


class TemplateRequest(BaseModel):
    body: str


class TimelineRequest(BaseModel):
    scenes: list[dict[str, Any]]


class VisualBibleRequest(BaseModel):
    visual_bible: dict[str, Any]


class PromptsRequest(BaseModel):
    prompts: list[str] | None = None
    video_prompts: list[str] | None = None
    image_prompts: list[str] | None = None


class BuildQueueRequest(BaseModel):
    start_stage: str | None = None


class LocalizationRequest(BaseModel):
    target_language: str
    voice_profile_id: str | None = None
    translation_profile_id: str | None = None


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


class TranslateRequest(BaseModel):
    translation_profile_id: str


class TTSTriggerRequest(BaseModel):
    voice_profile_id: str


class VoiceTestRequest(BaseModel):
    text: str
    language: str = "en"


class VoiceProfileUpdateRequest(BaseModel):
    name: str | None = None
    language_code: str | None = None


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


class EpisodeSubmitRequest(BaseModel):
    title: str
    script_text: str


class EpisodeQueueRequest(BaseModel):
    start_stage: str | None = None


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
    settings = service.get_settings_payload()
    if settings.get("tts_auto_start_worker"):
        service.tts_manager.start_worker()


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
    scene_planning_model: str = Form("haiku"),
    visual_bible_model: str = Form("haiku"),
    video_prompt_model: str = Form("gpt-5.4"),
    image_prompt_model: str = Form("gpt-5.4"),
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
            scene_planning_model=scene_planning_model,
            visual_bible_model=visual_bible_model,
            video_prompt_model=video_prompt_model,
            image_prompt_model=image_prompt_model,
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


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> dict[str, Any]:
    try:
        return service.delete_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        return service.save_review_prompts(
            job_id,
            payload.prompts,
            video_prompts=payload.video_prompts,
            image_prompts=payload.image_prompts,
        )
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


@app.get("/api/jobs/{job_id}/live-log")
async def live_log(job_id: str) -> dict[str, Any]:
    try:
        return service.get_live_log(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}/artifacts/{artifact_key}")
async def artifact(job_id: str, artifact_key: str) -> FileResponse:
    try:
        path = service.get_artifact_path(job_id, artifact_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(str(path), filename=Path(path).name)


# ── Creator Studio: Project & Build routes ─────────────────────────


@app.get("/api/projects")
async def list_projects() -> dict[str, Any]:
    return {"projects": service.list_projects()}


@app.post("/api/projects")
async def create_project(
    title: str = Form(...),
    source_language: str = Form("en"),
    scene_planning_provider: str = Form("claude"),
    visual_bible_provider: str = Form("claude"),
    video_prompt_provider: str = Form("codex"),
    image_prompt_provider: str = Form("codex"),
    scene_planning_model: str = Form("haiku"),
    visual_bible_model: str = Form("haiku"),
    video_prompt_model: str = Form("gpt-5.4"),
    image_prompt_model: str = Form("gpt-5.4"),
    leading_video_scene_count: int = Form(20),
    audio_file: UploadFile = File(...),
    script_file: UploadFile = File(...),
) -> dict[str, Any]:
    try:
        return service.create_project(
            title=title,
            source_language=source_language,
            audio_name=audio_file.filename or "audio.wav",
            audio_bytes=await audio_file.read(),
            script_name=script_file.filename or "script.txt",
            script_bytes=await script_file.read(),
            scene_planning_provider=scene_planning_provider,
            visual_bible_provider=visual_bible_provider,
            video_prompt_provider=video_prompt_provider,
            image_prompt_provider=image_prompt_provider,
            scene_planning_model=scene_planning_model,
            visual_bible_model=visual_bible_model,
            video_prompt_model=video_prompt_model,
            image_prompt_model=image_prompt_model,
            leading_video_scene_count=leading_video_scene_count,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}")
async def project_detail(project_id: str) -> dict[str, Any]:
    try:
        return service.get_project_detail(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str) -> dict[str, Any]:
    try:
        return service.delete_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/localize")
async def create_localization(project_id: str, payload: LocalizationRequest) -> dict[str, Any]:
    try:
        return service.create_localization_build(
            project_id,
            target_language=payload.target_language,
            voice_profile_id=payload.voice_profile_id,
            translation_profile_id=payload.translation_profile_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/builds")
async def list_builds(project_id: str) -> dict[str, Any]:
    try:
        project = service.get_project_detail(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "master_build": project["master_build"],
        "localizations": project["localizations"],
    }


@app.get("/api/builds/{build_id}")
async def build_detail(build_id: str) -> dict[str, Any]:
    try:
        return service.get_build_detail(build_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/builds/{build_id}")
async def delete_build(build_id: str) -> dict[str, Any]:
    try:
        return service.delete_build_record(build_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/builds/{build_id}/queue")
async def queue_build(build_id: str, payload: BuildQueueRequest) -> dict[str, Any]:
    try:
        return service.queue_build(build_id, payload.start_stage)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/target-languages")
async def target_languages() -> dict[str, Any]:
    return {"languages": service.get_target_languages()}


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
        return service.delete_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
    return {"profiles": service.list_translation_profiles()}


@app.post("/api/translation-profiles")
async def create_translation_profile(payload: TranslationProfileRequest) -> dict[str, Any]:
    try:
        return service.create_translation_profile(
            name=payload.name,
            provider=payload.provider,
            api_key=payload.api_key,
            model=payload.model,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/translation-profiles/{profile_id}")
async def translation_profile_detail(profile_id: str) -> dict[str, Any]:
    try:
        return service.get_translation_profile(profile_id)
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
        return service.update_translation_profile(profile_id, **fields)
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


# ── Translation execution ──────────────────────────────────────────


@app.post("/api/builds/{build_id}/translate")
async def trigger_translation(build_id: str, payload: TranslateRequest) -> dict[str, Any]:
    try:
        return service.translate_build(build_id, payload.translation_profile_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
            language_code=language_code,
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
            profile_id, **payload.dict(exclude_none=True)
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


@app.post("/api/builds/{build_id}/tts")
async def trigger_tts(build_id: str, payload: TTSTriggerRequest) -> dict[str, Any]:
    try:
        return service.submit_build_tts(build_id, payload.voice_profile_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/voice-profiles/{profile_id}/test")
async def test_voice(profile_id: str, payload: VoiceTestRequest) -> dict[str, Any]:
    try:
        return service.submit_voice_test(profile_id, payload.text, payload.language)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
    started = service.tts_manager.start_worker()
    return {"started": started}


@app.post("/api/worker/stop")
async def stop_tts_worker() -> dict[str, Any]:
    service.tts_manager.stop_worker()
    return {"stopped": True}
