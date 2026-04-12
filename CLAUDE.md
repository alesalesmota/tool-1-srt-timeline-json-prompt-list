# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Tool 1 is the multilingual planning + video-assembly engine for Creator Studio. A single script yields, per target language: translated scripts, XTTS narration, aligned SRT, a shared master scene plan, localized timelines, asset prompt lists, and finally a rendered video. Tool 2's assembly flow was absorbed into this repo — there is no separate Tool 2 project anymore.

Primary workflow: `Niche Projects -> Project Kanban -> Draft Episode -> Episode Overlay -> Explicit Queue`. `#/pipeline-board` is legacy and redirects.

**Before starting work, read `PROJECT_REGISTRY.md`** — it is the cross-conversation source of truth (current state, recent fixes, known fragilities). It must be updated at the end of any session with meaningful changes.

## Commands

```bash
# Run the dashboard (desktop pywebview shell by default, FastAPI+SQLite backend)
python run_tool1_dashboard.py

# Full test suite (baseline: ~293 tests + 4 subtests passing)
python -m pytest tests -q

# Single test file / test
python -m pytest tests/test_video_pipeline.py -q
python -m pytest tests/test_video_pipeline.py::test_name -q

# JS syntax check (no frontend test harness exists)
node --check tool1_dashboard/ui/app.js
```

There is no lint/format config checked in; match existing style.

## Architecture

**FastAPI + SQLite monolith.** All backend state lives in `tool1_dashboard.db`; UI is a single-page vanilla JS app under `tool1_dashboard/ui/`.

Key modules in `tool1_dashboard/`:

- `app.py` — FastAPI routes, API error shaping, startup health (`ffmpeg_available`, whisperx/faster-whisper/ctranslate2/pyannote module visibility).
- `service.py` — **Episode orchestration is centered here.** `_process_episode()` implements all 10 pipeline stages (translation → tts → alignment → scene_planning → consistency_guide → video_prompts → image_prompts → timeline_mapping → export → then the post-export assembly stages `asset_upload`, `assembly_validation`, `video_render`, `final_review`). Worker loop, queue readiness, paused-TTS reconciliation, shared-asset flows, and stage-run logging all live here.
- `database.py` — SQLite schema + CRUD for projects, episodes, stage_runs, `render_jobs`, `scene_assets`, `render_logs`, tts_jobs, worker_heartbeats, templates, settings. `Tool1Database` serializes writes via `self._lock`.
- `config.py` — Canonical `EPISODE_PIPELINE_STAGES`, `VIDEO_ASSEMBLY_STAGES`, default host/port. New stages added here must also be reflected in `ui/app.js` `EPISODE_PIPELINE_COLUMNS` and `EPISODE_RUNNABLE_STAGE_IDS`.
- `providers.py` — `CliRunner` wrapping `codex` / `claude` CLIs plus OpenAI Responses API execution for structured stage runs. Has a subprocess-stall guard and short-lived probe caching.
- `validators.py` — Timeline validation (overlap normalization vs. hard errors); used in `scene_planning`, `timeline_mapping`, and review read/write paths.
- `translation/` — Adapter (OpenAI + `openai_compatible` / LM Studio), chunker, prompts, service. Fail-stop: partial failure halts the episode at `translation` and writes `translation_report_<lang>.json` + `translation_diagnostics_<lang>.json`. Per-language runs go up to 4 concurrently via `asyncio.Semaphore(4)`; chunks within a language stay sequential. AI reviewer is off by default (`translation_ai_review_enabled`).
- `tts/` — XTTS-v2 runtime. **Pinned runtime: `torch==2.3.1` / `torchaudio==2.3.1` (CUDA 121 build strongly preferred).** Repo chunker is authoritative; XTTS `enable_text_splitting` is disabled. Exactly one live worker globally — duplicates are terminated and their jobs requeued. Worker auto-starts/stops based on demand.
- `alignment_tool/` — MFA-first subtitle alignment with WhisperX fallback. Retry order: `single_pass_mfa` → WhisperX-guided `guided_chunked_mfa` → `estimated_chunked_mfa` (proportional script-position chunk windows) → whole-audio WhisperX. Deterministic DP readability-first segmenter; outputs include `alignment_report.json` density diagnostics.
- `srt_chunker/` — SRT-facing chunking utilities.
- `video_assembly/` — Ported Tool 2 render core: `pipeline.py`, `timeline.py`, `validation.py`, `asset_resolver.py`, `probe_assets.py` (uses `input/cached_probes.json` written by service when staging assets), `render_image_scene.py`, `render_video_scene.py`, `concat_scenes.py` (stream-copies normalized scenes), `mux_voiceover.py`, `burn_subtitles.py`, `ffmpeg_utils.py`, `dashboard_observer.py` (writes to `render_jobs` / `render_logs`). Scene renders use `libx264 -preset fast -crf 20`.
- `launch_runtime.py`, `runtime.py` — Single-instance lock, pywebview desktop shell wiring, free-port selection, runtime metadata surfaced under `/api/app-runtime`.
- `templates.py`, `translation_profiles.py` — Read-side-effect-free template/settings/profile storage.

Other roots:

- `config/agents/` — Claude + OpenAI prompt templates for scene planning, visual bible, video/image prompts. Edit these when changing LLM behavior for those stages.
- `tests/test_video_assembly/` — Assembly-specific coverage (flat-array timeline conversion, filename/type matching, `DashboardRenderObserver` DB writes).
- `workspace/` — Per-episode runtime artifacts (scripts, narration chunks, SRTs, timelines, render outputs). Not a code surface; safe to inspect.

### Scene timing invariant

Master language defines canonical scenes. Non-master timelines are derived by total-duration ratio and then snapped to that language's subtitle cue boundaries. Scene planning realigns LLM-proposed scene timings onto the real master-language subtitle cues before merge — the LLM is not trusted for timestamps. If you touch scene/timeline code, preserve this flow.

### Hardware / concurrency constraints

Local single-machine deployment. TTS runs one language at a time (GPU-bound). Translation is sequential per chunk but parallel across up to 4 languages. Video renders hold a process-wide render lock and are blocked while the TTS worker heartbeat reports `processing`. Do not add parallelism that violates these.

### Assembly cleanup

Successful renders auto-clean `workspace/.../temp/scenes/`. Episode-level cleanup is exposed at `POST /api/episodes/{id}/assembly/cleanup`, render-job deletion at `DELETE /api/episodes/{id}/assembly/render/{job_id}`.

## UI conventions

`tool1_dashboard/ui/app.js` is vanilla JS, no build step. Conventions worth knowing:

- `renderApp()` is the passive full-refresh path; specific surfaces (assembly section, running-elapsed timer, SSE render-log stream, per-scene cards) intentionally persist across rerenders to avoid flicker, stuck timers, and lost scroll state. Don't reintroduce `clearInterval` / full-section reassignment in those paths.
- `episodeAssemblyCache` is a bounded LRU `Map` (cap 5). Use `Map.set` / `Map.delete` / `Map.clear` semantics.
- Render SSE uses explicit `event: update` channel with a 5-minute inactivity cap; streams are closed on episode switch.
- Polling: 10s idle / 3s active; active project boards drop to 1s while any episode is `queued`, `running`, or `paused_for_tts`.

## Drawbridge

This repo is connected to Drawbridge for browser-to-code UI annotations. `bridge`, `drawbridge`, `step bridge`, `batch bridge`, `yolo bridge` are action commands — process `.moat/moat-tasks-detail.json` then `.moat/moat-tasks.md`, resolve screenshots from `.moat/screenshots/`, advance each task `to do` → `doing` → `done|failed` and keep the `.md` checkbox in sync. Default to step mode.

## Side files archive

Historical runtime artifacts may live in the sibling workspace `C:\Users\Blue_\Desktop\PROJETOS\CREATOR STUDIO\TOOL 1 - SIDE FILES ARCHIVE` under `from-repo\...`. The app does not read from it. Check it only when looking for archived logs/benchmarks/old episode folders — not as a live dependency.

## Git workflow

Per user global rules: commit frequently with clear messages, push to remote, use feature branches (`feat/...`, `fix/...`), offer PRs when work is ready. Don't ask permission for git or doc updates; do ask before destructive operations (force push, branch delete, hard reset). Update `PROJECT_REGISTRY.md` whenever meaningful work or direction changes, and before session end.
