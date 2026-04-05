# Creator Studio: Tool 2 Video Assembly Integration Plan

> **Date:** 2026-04-05
> **Status:** Planning — awaiting approval
> **Scope:** Merge Tool 2 (Video Assembly) into Tool 1 (Creator Studio Dashboard)

---

## What This Is

Currently Tool 1 ends at Export (timeline.json, SRT, audio, prompts). The user then:
1. Generates images/videos from prompts (manual, external tools)
2. Opens Tool 2 (separate app on port 8000)
3. Points Tool 2 at a project directory
4. Renders final video

**After this integration:** Steps 2-4 happen inside Tool 1. Same app, same port 8020, same database. The user uploads generated assets, validates, renders, and reviews — all in one place.

---

## Architecture

- Tool 2 core modules → `tool1_dashboard/video_assembly/` subpackage
- Tool 2's web server/CLI/job manager are NOT copied (replaced by Tool 1 infrastructure)
- New `DashboardRenderObserver` bridges `PipelineObserver` protocol → SQLite
- 4 new pipeline stages after Export: `asset_upload` → `assembly_validation` → `video_render` → `final_review`
- Assembly directories: `{episode_workspace}/assembly/{language_code}/`
- Rendering is per-language (each has own voiceover + timeline)

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
- `tool1_dashboard/app.py` — add ~12 new API endpoints
- `tool1_dashboard/ui/app.js` — add asset upload grid, validation panel, render progress (SSE), video player
- `tool1_dashboard/ui/app.css` — add assembly UI styles

---

## Known Concerns & Mitigations (reviewed 2026-04-05)

These were identified during critical review. Each is addressed in the relevant task notes.

| # | Concern | Risk | Mitigation |
|---|---------|------|------------|
| C1 | Tool 1 timeline scenes lack `asset_file`/`asset_id` fields | LOW | Tool 2's `_convert_tool1_timeline()` auto-generates these from `asset_resolver.resolve_assets()`. No action needed — just ensure assets are staged before timeline is loaded. |
| C2 | Asset naming must match scene numbering for bulk upload | MEDIUM | `asset_resolver._extract_number()` matches `prompt1`, `scene_001`, `asset_3`, or bare `1`. User must name generated files with sequential numbers. Document this in the UI help text. |
| C3 | Shared assets copied per-language wastes disk | LOW | Accepted tradeoff. Symlinks unreliable on Windows without admin. Images are small. Video assets are larger but typically <20 per episode. Cleanup in Task 10.1. |
| C4 | SQLite thread safety during render | LOW | Tool 1 already has thread-safe DB wrapper with lock. Observer should batch logs, not write per-FFmpeg-line. Noted in Task 5.1. |
| C5 | Windows paths with spaces in FFmpeg subprocess calls | LOW | Tool 2's `ffmpeg_utils.py` passes paths as list args (not shell strings), which handles spaces correctly. Verify during Task 0.2. |
| C6 | Export sets `board_status="Done"` — assembly transition must work from Done | MEDIUM | Task 4.2 guard checks `current_stage="export"` AND `pipeline_status="done"`, not board_status. |
| C7 | Disk space for per-language renders (temp scenes + final videos) | MEDIUM | Task 10.1 adds cleanup of `temp/scenes/` after successful render. User should be warned about disk usage in the UI. |

---

## Pre-Flight Check Protocol (for ALL agents)

**BEFORE executing any task in a phase**, the assigned agent MUST:

1. **Read this plan file** to understand context and the specific task
2. **Read the Concerns table above** — check if any concern applies to your task
3. **Read the files you will modify** — understand existing code patterns, naming conventions, imports
4. **Flag blockers** — If you discover something that contradicts the plan (a function doesn't exist, an import path changed, a table was already added), STOP and report the issue instead of guessing
5. **Run existing tests** after your changes: `python -m pytest tests/ -q`
6. **Do not add unplanned features** — implement exactly what the task describes, nothing more

**After completing a task:** Update `IMPLEMENTATION_CHECKLIST_TOOL2_INTEGRATION.md` by checking off the completed task.

---

## Tasks (38 total)

### Agent Legend
- `[CODEX]` — Complex backend logic, needs detailed instructions
- `[GEMINI]` — Frontend UI, small focused implementations
- `[CLAUDE]` — Complex architecture, protocol bridging, integration tests

### Difficulty Legend
- `[EASY]` — Single file, straightforward, <30 min
- `[MEDIUM]` — 1-2 files, some logic, ~1 hour
- `[HARD]` — Multiple files, complex logic, architecture decisions

---

### PHASE 0: Preparation

| # | Task | Difficulty | Agent | Files |
|---|------|-----------|-------|-------|
| 0.1 | FFmpeg startup check — `shutil.which("ffmpeg")` at startup, expose in `/api/health` | EASY | CODEX | `app.py` |
| 0.2 | Copy Tool 2 modules → `tool1_dashboard/video_assembly/` subpackage. **[C5]** After copying, verify `ffmpeg_utils.py` passes paths as list args (not shell=True). Run `python -c "from tool1_dashboard.video_assembly.pipeline import RenderPipeline; print('OK')"` to verify imports. | EASY | CODEX | new package |
| 0.3 | Add `jinja2>=3.1,<4.0` to requirements.txt | EASY | CODEX | `requirements.txt` |

### PHASE 1: Database Schema

| # | Task | Difficulty | Agent | Files |
|---|------|-----------|-------|-------|
| 1.1 | Add `render_jobs` table + CRUD methods | MEDIUM | CODEX | `database.py` |
| 1.2 | Add `scene_assets` table + CRUD methods | MEDIUM | CODEX | `database.py` |
| 1.3 | Add `render_logs` table + insert/query methods | EASY | CODEX | `database.py` |

### PHASE 2: Config & Frontend Stages

| # | Task | Difficulty | Agent | Files |
|---|------|-----------|-------|-------|
| 2.1 | Add 4 stages to `EPISODE_PIPELINE_STAGES` in config.py | EASY | CODEX | `config.py` |
| 2.2 | Add 4 Kanban columns to `EPISODE_PIPELINE_COLUMNS` in app.js | EASY | GEMINI | `app.js` |

### PHASE 3: Asset Upload Backend

| # | Task | Difficulty | Agent | Files |
|---|------|-----------|-------|-------|
| 3.1 | Assembly project directory builder `_prepare_assembly_project()`. **[C1]** Copy timeline BEFORE assets are staged. **[C3]** Assets stored once in `assembly/shared_assets/`, copied per-language during render staging (Task 9.1). | MEDIUM | CODEX | `service.py` |
| 3.2 | `GET /api/episodes/{id}/scenes` — scene list + upload status | MEDIUM | CODEX | `app.py`, `service.py` |
| 3.3 | `POST /api/episodes/{id}/scenes/{sid}/asset` — single upload | MEDIUM | CODEX | `app.py`, `service.py` |
| 3.4 | `POST /api/episodes/{id}/scenes/bulk-upload` — auto-match by number. **[C2]** Uses `_extract_number()` which matches `prompt1`, `scene_001`, `asset_3`, or bare `1` prefixes. Return clear `unmatched` list so UI can show what failed. | MEDIUM | CODEX | `app.py`, `service.py` |
| 3.5 | `DELETE /api/episodes/{id}/scenes/{sid}/asset` — delete asset | EASY | CODEX | `app.py`, `service.py` |
| 3.6 | `GET /api/episodes/{id}/scenes/{sid}/asset/preview` — serve file | EASY | CODEX | `app.py` |

### PHASE 4: Assembly Validation

| # | Task | Difficulty | Agent | Files |
|---|------|-----------|-------|-------|
| 4.1 | `POST /api/episodes/{id}/assembly/validate` — run Tool 2 validation | MEDIUM | CODEX | `app.py`, `service.py` |
| 4.2 | Stage transition methods (export→asset_upload→validation→render→review). **[C6]** Guard checks `current_stage="export"` AND `pipeline_status="done"`, NOT board_status (which is "Done" after export). | MEDIUM | CODEX | `app.py`, `service.py` |

### PHASE 5: Render Pipeline

| # | Task | Difficulty | Agent | Files |
|---|------|-----------|-------|-------|
| 5.1 | `DashboardRenderObserver` — PipelineObserver → SQLite bridge. **[C4]** Batch log writes where possible. Don't write a DB row per FFmpeg stderr line — aggregate into stage-level entries. Tool 1's DB wrapper is already thread-safe (uses lock). | HARD | CLAUDE | `video_assembly/dashboard_observer.py` |
| 5.2 | `POST /api/episodes/{id}/assembly/render` — start render in thread | HARD | CODEX | `app.py`, `service.py` |
| 5.3 | `GET .../render/{job_id}/events` — SSE progress stream | HARD | CLAUDE | `app.py` |
| 5.4 | `GET .../render/{job_id}/video` + `GET .../scene/{sid}` — serve files | EASY | CODEX | `app.py` |

### PHASE 6: Frontend — Asset Upload

| # | Task | Difficulty | Agent | Files |
|---|------|-----------|-------|-------|
| 6.1 | Scene grid in episode overlay (cards + upload zones + progress) | HARD | GEMINI | `app.js` |
| 6.2 | Drag-drop + file picker + bulk upload interactions | MEDIUM | GEMINI | `app.js` |
| 6.3 | Asset upload CSS (grid, cards, dropzone, thumbnails) | EASY | GEMINI | `app.css` |

### PHASE 7: Frontend — Validation & Render

| # | Task | Difficulty | Agent | Files |
|---|------|-----------|-------|-------|
| 7.1 | Validation panel (validate button, green/red result, start render) | MEDIUM | GEMINI | `app.js` |
| 7.2 | Render progress panel (SSE, progress bar, log scroll, complete/fail) | HARD | GEMINI | `app.js` |
| 7.3 | Final review video player (inline player, download, re-render) | MEDIUM | GEMINI | `app.js` |
| 7.4 | Render/validation CSS styles | EASY | GEMINI | `app.css` |

### PHASE 8: Workflow Integration

| # | Task | Difficulty | Agent | Files |
|---|------|-----------|-------|-------|
| 8.1 | "Start Video Assembly" button on export stage | EASY | GEMINI | `app.js` |
| 8.2 | Stage strip renders 16 stages with correct coloring | EASY | GEMINI | `app.js` |
| 8.3 | Conditional assembly UI panel loader based on current stage | MEDIUM | CODEX | `app.js` |

### PHASE 9: Asset Staging

| # | Task | Difficulty | Agent | Files |
|---|------|-----------|-------|-------|
| 9.1 | `_stage_assets_for_render()` — copy assets into assembly input/assets/ | MEDIUM | CODEX | `service.py` |
| 9.2 | Wire staging into `start_render()` flow | EASY | CODEX | `service.py` |

### PHASE 10: Polish & Safety

| # | Task | Difficulty | Agent | Files |
|---|------|-----------|-------|-------|
| 10.1 | Re-render support + cleanup old jobs. **[C7]** After successful render, delete `temp/scenes/` to free disk. Keep `final_video` and `visual_master`. Warn: 50-min episode × 5 languages ≈ 10-15GB temp files if not cleaned. | EASY | CODEX | `service.py` |
| 10.2 | FFmpeg availability guard on validate/render endpoints | EASY | CODEX | `service.py` |
| 10.3 | Per-language render status column in episode overlay | MEDIUM | GEMINI | `app.js` |
| 10.4 | Prevent concurrent renders + TTS conflict guard | EASY | CODEX | `service.py` |

### PHASE 11: Testing

| # | Task | Difficulty | Agent | Files |
|---|------|-----------|-------|-------|
| 11.1 | End-to-end integration test (full assembly flow) | HARD | CLAUDE | `tests/test_video_assembly_integration.py` |
| 11.2 | Unit tests for video_assembly subpackage | MEDIUM | CODEX | `tests/test_video_assembly/` |

---

## Execution Order (Sprints)

| Sprint | Tasks | Focus |
|--------|-------|-------|
| 1 | 0.1, 0.2, 0.3 | Setup (all parallel) |
| 2 | 1.1, 1.2, 1.3, 2.1, 2.2 | Schema + config (all parallel) |
| 3 | 3.1 → 3.2 → 3.3, 3.4, 3.5, 3.6 | Asset backend (3.1 first) |
| 4 | 5.1, 4.1, 6.3, 7.4 | Observer + validation + CSS (parallel) |
| 5 | 6.1, 6.2, 4.2 | Asset frontend + transitions |
| 6 | 5.2, 9.1, 9.2 | Render backend + staging |
| 7 | 5.3, 5.4, 7.1 | SSE + video serving + validation UI |
| 8 | 7.2, 7.3, 8.1, 8.2, 8.3 | Render UI + workflow |
| 9 | 10.1, 10.2, 10.3, 10.4 | Polish |
| 10 | 11.1, 11.2 | Testing |

---

## Agent Summary

| Agent | Count | Task IDs |
|-------|-------|----------|
| CODEX | 24 | 0.1-0.3, 1.1-1.3, 2.1, 3.1-3.6, 4.1-4.2, 5.2, 5.4, 8.3, 9.1-9.2, 10.1-10.2, 10.4, 11.2 |
| GEMINI | 11 | 2.2, 6.1-6.3, 7.1-7.4, 8.1-8.2, 10.3 |
| CLAUDE | 3 | 5.1, 5.3, 11.1 |

---

## End-to-End Verification

After all phases:
1. Create niche project (EN + target language)
2. Run episode through full pipeline to Export
3. Click "Start Video Assembly" → asset_upload stage
4. Bulk upload test images
5. Validate → green
6. Render master language → watch SSE progress
7. Final video plays in review panel
8. `pytest tests/ -v` → all tests pass
