# Creator Studio: Tool 2 Video Assembly Integration Plan

> **Date:** 2026-04-05 (revised)
> **Status:** In progress — Phases 0-3 completed on 2026-04-05
> **Scope:** Merge Tool 2 (Video Assembly) into Tool 1 (Creator Studio Dashboard)

---

## What This Is

Currently Tool 1 ends at Export (timeline.json, SRT, audio, prompts). The user then:
1. Generates images/videos from prompts (manual, external tools)
2. Opens Tool 2 (separate app on port 8000)
3. Points Tool 2 at a project directory
4. Renders ONE final video (single language only)

**After this integration:** Steps 2-4 happen inside Tool 1. Same app, same port 8020, same database. The user uploads generated assets once, then renders a final video **for each configured language** — same visuals, different voiceover/timing/subtitles per language.

---

## Multilingual Workflow (Core Concept)

This is the most important section. Every task must respect this data flow.

```
                    SHARED (uploaded once)              PER-LANGUAGE (from Tool 1 pipeline)
                    ─────────────────────               ────────────────────────────────────
                    Images (prompt1.png...)              timeline_en.json  ←  scene durations differ
                    Videos (prompt2.mp4...)              timeline_pt-BR.json   per language because
                                                        timeline_es.json      narration length differs
                                                        
                                                        narration_en.wav  ←  TTS output per language
                                                        narration_pt-BR.wav
                                                        narration_es.wav
                                                        
                                                        subtitles_en.srt  ←  alignment output per lang
                                                        subtitles_pt-BR.srt
                                                        subtitles_es.srt
```

**How it renders:**

```
For EACH language:
  1. Create assembly/{lang}/input/  directory
  2. Copy timeline_{lang}.json  →  input/timeline.json
  3. Copy narration_{lang}.wav  →  input/voiceover.wav
  4. Copy subtitles_{lang}.srt  →  input/subtitles.srt
  5. Copy SHARED assets          →  input/assets/   (same files every time)
  6. Run RenderPipeline  →  Tool 2 retimes each asset to fit THIS language's scene durations
  7. Output: final_video_{lang}.mp4
```

**Key insight:** Tool 2's `RenderPipeline` already handles this perfectly. It retimes every video asset (slow/speed/trim/freeze) to match the scene `duration` in the timeline. Since each language has different durations (because narration lengths differ), the same video clip gets retimed differently per language. Image assets get converted to video clips of exactly the right duration. No changes to Tool 2's core rendering logic needed.

**Where per-language data comes from in Tool 1's DB:**

```sql
-- episode_language_status table (already exists)
SELECT language_code, timeline_path, tts_audio_path, srt_path
FROM episode_language_status
WHERE episode_id = ?
-- Returns one row per language with paths to all per-language artifacts
```

---

## Architecture

- Tool 2 core modules → `tool1_dashboard/video_assembly/` subpackage
- Tool 2's web server/CLI/job manager are NOT copied (replaced by Tool 1 infrastructure)
- New `DashboardRenderObserver` bridges `PipelineObserver` protocol → SQLite
- 4 new pipeline stages after Export: `asset_upload` → `assembly_validation` → `video_render` → `final_review`
- **Shared assets** stored once at `{episode_workspace}/assembly/shared_assets/`
- **Per-language assembly dirs** at `{episode_workspace}/assembly/{lang}/` — created at render time
- One `render_jobs` row per language render — tracks state independently

### Stage Behavior

| Stage | Scope | What Happens |
|-------|-------|-------------|
| `asset_upload` | **Shared** (language-agnostic) | User uploads images/videos for scenes. Same assets for all languages. |
| `assembly_validation` | **Per-language** | Checks: timeline exists? voiceover exists? SRT exists? All scene assets uploaded? Run for each language. |
| `video_render` | **Per-language** | Runs `RenderPipeline` once per language. Sequential (hardware constraint). Each render creates assembly/{lang}/ dir, copies shared assets + per-language files, renders. |
| `final_review` | **Per-language** | Play/download rendered videos per language. Re-render if needed. |

---

## Source Files Reference

### Tool 2 modules to copy into `tool1_dashboard/video_assembly/`
All from `TOOL 2-VIDEO ASSEMBLY/AUTO VIDEO/app/`:
- `pipeline.py` — RenderPipeline class + PipelineObserver protocol
- `models.py` — ProjectConfig, SceneSpec, SceneRenderResult, etc.
- `timeline.py` — load_timeline() with Tool 1 flat-array auto-conversion
- `validation.py` — validate_or_raise()
- `asset_resolver.py` — scan assets dir, match by number
- `probe_assets.py` — ffprobe wrapper
- `render_image_scene.py` — image → video (static/zoom motion)
- `render_video_scene.py` — video retiming (slow/speed/trim/freeze)
- `concat_scenes.py` — FFmpeg concat demuxer
- `mux_voiceover.py` — add narration audio track
- `burn_subtitles.py` — optional SRT/ASS burning
- `ffmpeg_utils.py` — FFmpeg helpers
- `utils.py` — format_seconds, write_json, utc_now
- `exceptions.py` — custom exceptions

### Tool 1 files to modify
- `tool1_dashboard/config.py` — add 4 new pipeline stages
- `tool1_dashboard/database.py` — add 3 new tables (render_jobs, scene_assets, render_logs)
- `tool1_dashboard/service.py` — add assembly methods (prepare project, stage assets, start render, validate)
- `tool1_dashboard/app.py` — add ~15 new API endpoints
- `tool1_dashboard/ui/app.js` — add asset upload grid, validation panel, per-language render progress (SSE), video player
- `tool1_dashboard/ui/app.css` — add assembly UI styles

### Tool 1 existing structures used (do NOT modify)
- `episode_language_status` table — provides per-language `timeline_path`, `tts_audio_path`, `srt_path`
- `finalize_export()` in service.py — sets stage to export, status to done
- `_episode_workspace()` in service.py — returns episode workspace dir

---

## Known Concerns & Mitigations (reviewed 2026-04-05)

| # | Concern | Risk | Mitigation |
|---|---------|------|------------|
| C1 | Tool 1 timeline scenes lack `asset_file`/`asset_id` fields | LOW | Tool 2's `_convert_tool1_timeline()` auto-generates these from `asset_resolver.resolve_assets()`. Ensure assets are staged before timeline is loaded. |
| C2 | Asset naming must match scene numbering for bulk upload | MEDIUM | `_extract_number()` matches `prompt1`, `scene_001`, `asset_3`, or bare `1`. Document in UI help text. |
| C3 | Shared assets copied per-language wastes disk | LOW | Accepted. Symlinks unreliable on Windows without admin. Cleanup in Task 10.1. |
| C4 | SQLite thread safety during render | LOW | Tool 1 DB wrapper already thread-safe. Observer should batch logs. |
| C5 | Windows paths with spaces in FFmpeg subprocess calls | LOW | Tool 2's `ffmpeg_utils.py` uses list args (not shell=True). Verify in Task 0.2. |
| C6 | Export sets `board_status="Done"` — assembly transition must work from Done | MEDIUM | Guard checks `current_stage` + `pipeline_status`, not board_status. |
| C7 | Disk space for per-language renders | MEDIUM | Cleanup `temp/scenes/` after success. Warn about disk in UI. |
| C8 | Some languages may have failed TTS/alignment — no voiceover or SRT | MEDIUM | Validation (Task 4.1) checks per-language prerequisites. Skip languages with missing artifacts, show clear status in UI. |

---

## Pre-Flight Check Protocol (for ALL agents)

**BEFORE executing any task**, the assigned agent MUST:

1. **Read this plan file** — understand context, multilingual workflow, and the specific task
2. **Read the Concerns table** — check if any concern applies to your task
3. **Read the files you will modify** — understand existing patterns, imports, naming
4. **Flag blockers** — If something contradicts the plan (function doesn't exist, path changed, table already added), STOP and report instead of guessing
5. **Run existing tests** after your changes: `python -m pytest tests/ -q`
6. **Do not add unplanned features** — implement exactly what the task describes

**After completing a task:** Update `IMPLEMENTATION_CHECKLIST_TOOL2_INTEGRATION.md`.

---

## Tasks (38 total)

### Agent Legend
- `[CODEX]` — Backend logic, needs detailed instructions
- `[GEMINI]` — Frontend UI, small focused implementations
- `[CLAUDE]` — Complex architecture, protocol bridging, integration tests

### Difficulty Legend
- `[EASY]` — Single file, straightforward
- `[MEDIUM]` — 1-2 files, moderate logic
- `[HARD]` — Multiple files, complex logic, architecture decisions

---

### PHASE 0: Preparation

| # | Task | Diff | Agent | Files |
|---|------|------|-------|-------|
| 0.1 | FFmpeg startup check — `shutil.which("ffmpeg")` + `shutil.which("ffprobe")` at startup, expose in `GET /api/health` as `ffmpeg_available: bool` | EASY | CODEX | `app.py` |
| 0.2 | Copy Tool 2 modules → `tool1_dashboard/video_assembly/` subpackage. Copy all 14 files listed in Source Files Reference. Do NOT copy `main.py`, `cli.py`, `jobs.py`, `ui/`. **[C5]** Verify `ffmpeg_utils.py` uses list args not shell=True. **Verify:** `python -c "from tool1_dashboard.video_assembly.pipeline import RenderPipeline; print('OK')"` | EASY | CODEX | new package |
| 0.3 | Add `jinja2>=3.1,<4.0` to requirements.txt | EASY | CODEX | `requirements.txt` |

### PHASE 1: Database Schema

| # | Task | Diff | Agent | Files |
|---|------|------|-------|-------|
| 1.1 | Add `render_jobs` table. Schema: `id TEXT PK, episode_id TEXT FK, language_code TEXT NOT NULL, state TEXT DEFAULT 'idle', stage TEXT DEFAULT 'idle', current_scene_id TEXT, total_scenes INT DEFAULT 0, completed_scenes INT DEFAULT 0, project_dir TEXT, error_message TEXT, validation_json TEXT, outputs_json TEXT DEFAULT '{}', started_at TEXT, finished_at TEXT, created_at TEXT, updated_at TEXT`. CRUD: `create_render_job`, `get_render_job`, `list_render_jobs(episode_id)`, `get_render_job_for_language(episode_id, language_code)`, `update_render_job`, `delete_render_jobs_for_episode`. Add CASCADE in `delete_episode()`. | MEDIUM | CODEX | `database.py` |
| 1.2 | Add `scene_assets` table (episode-level, NOT per-language — assets are shared). Schema: `id TEXT PK, episode_id TEXT FK, scene_id TEXT, asset_type TEXT DEFAULT 'image', original_filename TEXT, stored_filename TEXT, file_path TEXT, file_size INT DEFAULT 0, width INT, height INT, duration_seconds REAL, uploaded_at TEXT, updated_at TEXT, UNIQUE(episode_id, scene_id)`. CRUD: `create_scene_asset`, `get_scene_asset(episode_id, scene_id)`, `list_scene_assets(episode_id)`, `update_scene_asset`, `delete_scene_asset`, `delete_scene_assets_for_episode`, `count_scene_assets(episode_id)`. Add CASCADE in `delete_episode()`. | MEDIUM | CODEX | `database.py` |
| 1.3 | Add `render_logs` table. Schema: `id INTEGER PK AUTOINCREMENT, render_job_id TEXT FK, timestamp TEXT, level TEXT, stage TEXT, message TEXT, scene_id TEXT, created_at TEXT`. Index: `idx_render_logs_job ON render_logs(render_job_id)`. Methods: `append_render_log`, `list_render_logs(render_job_id, limit=200)`. | EASY | CODEX | `database.py` |

### PHASE 2: Config & Frontend Stages

| # | Task | Diff | Agent | Files |
|---|------|------|-------|-------|
| 2.1 | Add to `EPISODE_PIPELINE_STAGES` after "export": `"asset_upload"`, `"assembly_validation"`, `"video_render"`, `"final_review"`. Add `VIDEO_ASSEMBLY_STAGES = ("asset_upload", "assembly_validation", "video_render")`. Do NOT add to `EPISODE_RUNNABLE_STAGES`. | EASY | CODEX | `config.py` |
| 2.2 | Add 4 Kanban columns to `EPISODE_PIPELINE_COLUMNS` in app.js (after export): `asset_upload/"Asset Upload"`, `assembly_validation/"Assembly Check"`, `video_render/"Video Render"`, `final_review/"Final Review"`. | EASY | GEMINI | `app.js` |

### PHASE 3: Asset Upload Backend (shared assets, language-agnostic)

**Context for agents:** Assets are SHARED across all languages. The user uploads one set of images/videos that will be reused for every language render. Store them at `{workspace}/assembly/shared_assets/`.

| # | Task | Diff | Agent | Files |
|---|------|------|-------|-------|
| 3.1 | `_prepare_assembly_project(episode_id, language_code) -> Path`. Creates `{workspace}/assembly/{lang}/input/` and `input/assets/`. Reads `episode_language_status` for this language to get `timeline_path`, `tts_audio_path`, `srt_path`. Copies: timeline → `input/timeline.json`, voiceover → `input/voiceover.wav`, SRT → `input/subtitles.srt` (if exists). Does NOT copy assets yet (that's Task 9.1). Returns project dir. **[C1]** Timeline must be in place before `load_timeline()` runs. **[C8]** If `tts_audio_path` is None/missing for this language, raise clear error "Language {lang} has no TTS audio — run TTS first". | MEDIUM | CODEX | `service.py` |
| 3.2 | `GET /api/episodes/{id}/scenes` — Returns scene list from master timeline (`timeline_draft_path` on episode). Join with `scene_assets` table to show upload status per scene. Response: `{ scenes: [{scene_id, start, end, duration, text, asset_type, asset: null or {filename, file_size, asset_type}}], total_scenes: N, uploaded_count: N }`. Note: scenes come from MASTER timeline (shared), not per-language. | MEDIUM | CODEX | `app.py`, `service.py` |
| 3.3 | `POST /api/episodes/{id}/scenes/{scene_id}/asset` — Single file upload (multipart). Validate extension (png/jpg/jpeg/webp for images, mp4/mov/mkv/webm for videos). Store at `{workspace}/assembly/shared_assets/{scene_id}_{original_filename}`. Create/update `scene_assets` DB row. Return asset record. | MEDIUM | CODEX | `app.py`, `service.py` |
| 3.4 | `POST /api/episodes/{id}/scenes/bulk-upload` — Multiple files (multipart). Auto-match filenames to scenes by number using same logic as `asset_resolver._extract_number()` (matches `prompt1`, `scene_001`, `asset_3`, or bare `1` prefix). **[C2]** Return `{ matched: [{scene_id, filename}], unmatched: [filenames], total_uploaded: N }` so UI shows what failed. | MEDIUM | CODEX | `app.py`, `service.py` |
| 3.5 | `DELETE /api/episodes/{id}/scenes/{scene_id}/asset` — Delete file from disk + DB row. | EASY | CODEX | `app.py`, `service.py` |
| 3.6 | `GET /api/episodes/{id}/scenes/{scene_id}/asset/preview` — Serve uploaded file as `FileResponse`. | EASY | CODEX | `app.py` |

### PHASE 4: Assembly Validation (per-language)

**Context for agents:** Validation checks TWO things: (A) shared assets — are all scenes covered? (B) per-language prerequisites — does this language have a timeline, voiceover, and (optionally) SRT?

| # | Task | Diff | Agent | Files |
|---|------|------|-------|-------|
| 4.1 | `POST /api/episodes/{id}/assembly/validate?language_code=XX` — Two-part validation: (1) Check `scene_assets` count matches total scenes from master timeline. (2) Check `episode_language_status` for this language: `timeline_path` must exist, `tts_audio_path` must exist, `srt_path` is optional. If language_code omitted, validate ALL configured languages and return per-language results. Return `{ languages: { "en": {passed, errors, warnings, scene_count, total_duration}, "pt-BR": {...} }, shared: {all_assets_uploaded: bool, missing_scenes: [...]} }`. **[C8]** Languages with `tts_status != "done"` or `timeline_status != "done"` should show clear error like "TTS not completed for pt-BR". | MEDIUM | CODEX | `app.py`, `service.py` |
| 4.2 | Stage transition methods + endpoints. `POST /api/episodes/{id}/assembly/start` → moves to `asset_upload`. Guard: `current_stage="export"` AND `pipeline_status="done"` **[C6]**. `POST /api/episodes/{id}/assembly/advance` body `{target_stage}` → moves to next stage if prerequisites met. For `assembly_validation`: all scenes must have assets. For `video_render`: validation must pass for at least one language. For `final_review`: at least one render completed. | MEDIUM | CODEX | `app.py`, `service.py` |

### PHASE 5: Render Pipeline (per-language)

**Context for agents:** Each render is ONE language. The user picks a language (or "Render All"), each spawns a `render_jobs` row. Only ONE render runs at a time (hardware constraint). If "Render All" is requested, queue them and process sequentially.

| # | Task | Diff | Agent | Files |
|---|------|------|-------|-------|
| 5.1 | `DashboardRenderObserver` — Implements `PipelineObserver` protocol. Constructor: `(db, render_job_id)`. `set_state` → `db.update_render_job(state, stage, current_scene_id)` + `db.append_render_log(...)`. `add_scene_result` → increment `completed_scenes`. `complete` → state="completed", store output paths in `outputs_json`. `fail` → state="failed", store error. **[C4]** Batch: only write one log per `set_state` call, not per FFmpeg line. | HARD | CLAUDE | `video_assembly/dashboard_observer.py` |
| 5.2 | `POST /api/episodes/{id}/assembly/render` body `{language_code}` OR `{language_code: "all"}`. If single language: create one `render_jobs` row, prepare assembly project (Task 3.1), stage assets (Task 9.1), launch `RenderPipeline` in `threading.Thread(daemon=True)`. If "all": create render_jobs rows for all valid languages, queue them, process sequentially in one thread. Return `{ render_job_id(s), status: "started" }`. `GET /api/episodes/{id}/assembly/render-status` → returns all render_jobs for episode grouped by language. `GET /api/episodes/{id}/assembly/render-jobs` → returns all render_jobs. Hardware constraint: only ONE render at a time (use `_render_lock`). | HARD | CODEX | `app.py`, `service.py` |
| 5.3 | `GET /api/episodes/{id}/assembly/render/{job_id}/events` — SSE stream. Poll `render_jobs` + `render_logs` every 1s. Yield `event: update` with `{job: {...}, new_logs: [...]}`. Close when state is completed/failed. Return `StreamingResponse(media_type="text/event-stream")`. | HARD | CLAUDE | `app.py` |
| 5.4 | `GET /api/episodes/{id}/assembly/render/{job_id}/video` — serve final_video.mp4 from `outputs_json.final_video` path. `GET /api/episodes/{id}/assembly/render/{job_id}/scene/{scene_id}` — serve scene clip from `{project_dir}/temp/scenes/{scene_id}.mp4`. | EASY | CODEX | `app.py` |

### PHASE 6: Frontend — Asset Upload (shared, language-agnostic)

**Context for agents:** This UI section is NOT per-language. The user uploads ONE set of assets used for ALL language renders. The scene list comes from the master timeline.

| # | Task | Diff | Agent | Files |
|---|------|------|-------|-------|
| 6.1 | Scene grid in episode overlay. New section `<div id="episode-assembly-section">` in `renderEpisodeDetailOverlay()`. New function `renderAssemblySection(episodeId)`: calls `GET /api/episodes/{id}/scenes`, renders CSS grid of scene cards. Each card: scene number + time range, text excerpt (60 chars), asset_type badge (image/video), upload dropzone or thumbnail preview if uploaded, "Replace"/"Remove" buttons if uploaded. Top bar: "12/45 assets uploaded" progress + "Bulk Upload" button. | HARD | GEMINI | `app.js` |
| 6.2 | Upload interactions. Drag-and-drop on empty scene cards → `POST .../scenes/{id}/asset`. Bulk upload button → hidden `<input type="file" multiple>` → `POST .../scenes/bulk-upload`. Show unmatched files in a toast/alert after bulk upload. Re-fetch + re-render grid after each upload. | MEDIUM | GEMINI | `app.js` |
| 6.3 | Asset upload CSS. `.scene-grid` (3-4 col responsive), `.scene-card`, `.scene-card-dropzone` (dashed border, highlight on dragover), `.scene-card-thumbnail`, `.asset-upload-progress`, `.asset-badge`, `.assembly-stats-bar`. Match existing dark theme. | EASY | GEMINI | `app.css` |

### PHASE 7: Frontend — Validation & Render (per-language)

**Context for agents:** Validation and render are PER-LANGUAGE. The UI must show a language selector/tabs and per-language status. The user can render languages one at a time or "Render All."

| # | Task | Diff | Agent | Files |
|---|------|------|-------|-------|
| 7.1 | Validation panel. Shows AFTER all assets uploaded. "Validate All Languages" button → calls `POST .../assembly/validate` (no language_code = validate all). Shows per-language results: each language row with green check or red X + error list. Shared check: "All scenes have assets: Yes/No". Per-language check: "Timeline: OK, Voiceover: OK, SRT: OK/Missing(optional)". Languages that passed get a "Render" button. Global "Render All" button if ≥1 language passes. | MEDIUM | GEMINI | `app.js` |
| 7.2 | Render progress panel. Language tabs/dropdown at top showing render state per language (idle/rendering/completed/failed). Active render shows: progress bar (completed_scenes/total_scenes), stage badge, current scene ID, scrolling log (via SSE `EventSource`). Completed render shows: green badge + "Play" link. Failed shows: red badge + error + "Retry" button. "Render All" queues all valid languages — show queue position for waiting languages. | HARD | GEMINI | `app.js` |
| 7.3 | Final review panel. Per-language video gallery. Each completed language: inline `<video>` player sourced from `/api/.../render/{job_id}/video`, download button, file size, render duration. "Re-render" button per language. Summary: "3/5 languages rendered". | MEDIUM | GEMINI | `app.js` |
| 7.4 | Render/validation CSS. `.render-progress-panel`, `.render-progress-bar` (animated), `.render-log-output` (monospace, scrollable, max-height), `.render-stage-badge`, `.video-player-container` (responsive), `.validation-result`, `.validation-error-item`, `.language-tab`, `.language-tab-active`, `.render-queue-badge`. | EASY | GEMINI | `app.css` |

### PHASE 8: Workflow Integration

| # | Task | Diff | Agent | Files |
|---|------|------|-------|-------|
| 8.1 | "Start Video Assembly" button. In `renderReviewSectionContent()`, next to "Download ZIP", show button when `current_stage="export"` and `pipeline_status="done"`. Click → `POST /api/episodes/{id}/assembly/start` → refresh overlay. | EASY | GEMINI | `app.js` |
| 8.2 | Stage strip for 16 stages. Verify `renderEpisodeDetailOverlay()` stage strip works with 4 new stages. Fix `currentIdx` calculation and progress % if needed (was 12 stages, now 16). | EASY | GEMINI | `app.js` |
| 8.3 | Conditional assembly UI loader. `loadAssemblyUI(episodeId)` called from episode detail render. Shows correct panel based on `current_stage`: `asset_upload` → scene grid + upload. `assembly_validation` → assets (read-only thumbnails) + validation panel. `video_render` → render progress panel. `final_review` → video gallery. Hide section entirely for stages before export. | MEDIUM | CODEX | `app.js` |

### PHASE 9: Asset Staging for Render

**Context for agents:** This is where shared assets get copied INTO the per-language assembly directory right before rendering. This runs once per language at render start.

| # | Task | Diff | Agent | Files |
|---|------|------|-------|-------|
| 9.1 | `_stage_assets_for_render(episode_id, language_code) -> Path`. Get all `scene_assets` from DB. Get assembly project dir from `_prepare_assembly_project()`. Clear `input/assets/` dir. For each scene_asset: copy file to `input/assets/{NNN}_{original_filename}` where NNN = scene number (zero-padded 3 digits) — this naming ensures `asset_resolver._extract_number()` matches correctly. **[C3]** Yes, assets get copied per language — accepted tradeoff for Windows compatibility. Return project dir. | MEDIUM | CODEX | `service.py` |
| 9.2 | Wire into `start_render()`. In `start_render()` (Task 5.2), before launching pipeline thread: (1) `_prepare_assembly_project(episode_id, lang)` — creates dir + copies timeline/voiceover/SRT. (2) `_stage_assets_for_render(episode_id, lang)` — copies shared assets. (3) Create render_job row. (4) Launch thread. | EASY | CODEX | `service.py` |

### PHASE 10: Polish & Safety

| # | Task | Diff | Agent | Files |
|---|------|------|-------|-------|
| 10.1 | Re-render + cleanup. On re-render: create NEW render_job (preserve history). `DELETE /api/episodes/{id}/assembly/render/{job_id}` deletes job + temp files. **[C7]** After successful render, auto-delete `temp/scenes/` for that language (keep final_video + visual_master). Add `POST /api/episodes/{id}/assembly/cleanup` to delete ALL temp files for all languages. | EASY | CODEX | `service.py`, `app.py` |
| 10.2 | FFmpeg guard. In `validate_assembly()` and `start_render()`: `if not shutil.which("ffmpeg") or not shutil.which("ffprobe"): raise RuntimeError("FFmpeg required")`. | EASY | CODEX | `service.py` |
| 10.3 | Per-language render status in episode overlay. In the existing per-language table (langRows in episode detail), add "Render" column. Badge: idle (gray), rendering (blue pulse), completed (green + play icon), failed (red + retry). Extend episode detail API to include render_jobs grouped by language. | MEDIUM | GEMINI | `app.js` |
| 10.4 | Concurrent operation prevention. `_render_lock = threading.Lock()` — reject if locked. Also check TTS worker not processing: `if heartbeat and heartbeat["status"] == "processing": raise ValueError("TTS running")`. Release lock in thread's `finally` block. | EASY | CODEX | `service.py` |

### PHASE 11: Testing

| # | Task | Diff | Agent | Files |
|---|------|------|-------|-------|
| 11.1 | Integration test. Create episode with mock export artifacts (master timeline, 2 per-language timelines with different durations, 2 voiceovers, 2 SRTs). Upload small test PNGs as assets. Validate both languages. Render both. Verify 2 final_video files exist with different durations. | HARD | CLAUDE | `tests/test_video_assembly_integration.py` |
| 11.2 | Unit tests for video_assembly subpackage. Port from Tool 2 tests. Test timeline loading (flat array conversion), validation, asset resolver matching, dashboard observer DB writes. | MEDIUM | CODEX | `tests/test_video_assembly/` |

---

## Execution Order (Sprints)

| Sprint | Tasks | Focus |
|--------|-------|-------|
| 1 | 0.1, 0.2, 0.3 | Setup (all parallel) |
| 2 | 1.1, 1.2, 1.3, 2.1, 2.2 | Schema + config (all parallel) |
| 3 | 3.1 → 3.2 → 3.3, 3.4, 3.5, 3.6 | Asset backend (3.1 first, rest parallel) |
| 4 | 5.1, 4.1, 6.3, 7.4 | Observer + validation + CSS (parallel) |
| 5 | 6.1, 6.2, 4.2 | Asset frontend + stage transitions |
| 6 | 5.2, 9.1, 9.2 | Render backend + asset staging |
| 7 | 5.3, 5.4, 7.1 | SSE + video serving + validation UI |
| 8 | 7.2, 7.3, 8.1, 8.2, 8.3 | Render UI + workflow integration |
| 9 | 10.1, 10.2, 10.3, 10.4 | Polish + safety |
| 10 | 11.1, 11.2 | Testing |

---

## Agent Summary

| Agent | Count | Task IDs |
|-------|-------|----------|
| CODEX | 24 | 0.1-0.3, 1.1-1.3, 2.1, 3.1-3.6, 4.1-4.2, 5.2, 5.4, 8.3, 9.1-9.2, 10.1-10.2, 10.4, 11.2 |
| GEMINI | 11 | 2.2, 6.1-6.3, 7.1-7.4, 8.1-8.2, 10.3 |
| CLAUDE | 3 | 5.1, 5.3, 11.1 |

---

## End-to-End Verification (Multilingual)

After all phases:
1. Create niche project with EN (master) + PT-BR + ES
2. Run episode through full pipeline to Export
3. Click "Start Video Assembly" → asset_upload stage
4. Bulk upload test images (one per scene)
5. Validate all languages → EN green, PT-BR green, ES green
6. "Render All" → watch EN render, then PT-BR queued, then ES queued
7. Each language produces a different-length final video (because narration durations differ)
8. Play each language video inline in final review
9. Download EN video, re-render PT-BR, verify both work
10. `pytest tests/ -v` → all tests pass (existing 208 + new)
