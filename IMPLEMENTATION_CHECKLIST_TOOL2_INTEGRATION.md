# Tool 2 Integration — Progress Checklist

> Track progress here. Mark tasks as they complete.
> See IMPLEMENTATION_PLAN_TOOL2_INTEGRATION.md for full task details.

## PHASE 0: Preparation
- [x] 0.1 `[EASY][CODEX]` FFmpeg startup check in app.py
- [x] 0.2 `[EASY][CODEX]` Copy Tool 2 modules → video_assembly/ subpackage
- [x] 0.3 `[EASY][CODEX]` Add jinja2 to requirements.txt

## PHASE 1: Database Schema
- [x] 1.1 `[MEDIUM][CODEX]` render_jobs table + CRUD (per-language tracking)
- [x] 1.2 `[MEDIUM][CODEX]` scene_assets table + CRUD (shared, episode-level)
- [x] 1.3 `[EASY][CODEX]` render_logs table + insert/query

## PHASE 2: Config & Frontend Stages
- [x] 2.1 `[EASY][CODEX]` Add 4 stages to EPISODE_PIPELINE_STAGES
- [x] 2.2 `[EASY][GEMINI]` Add 4 Kanban columns to app.js

## PHASE 3: Asset Upload Backend (shared assets)
- [x] 3.1 `[MEDIUM][CODEX]` _prepare_assembly_project() — per-language dir builder
- [x] 3.2 `[MEDIUM][CODEX]` GET /api/episodes/{id}/scenes — master scene list + upload status
- [x] 3.3 `[MEDIUM][CODEX]` POST single-scene asset upload
- [x] 3.4 `[MEDIUM][CODEX]` POST bulk asset upload with auto-match
- [x] 3.5 `[EASY][CODEX]` DELETE scene asset endpoint
- [x] 3.6 `[EASY][CODEX]` GET asset preview endpoint

## PHASE 4: Assembly Validation (per-language)
- [ ] 4.1 `[MEDIUM][CODEX]` POST validate — shared assets + per-language prerequisites
- [ ] 4.2 `[MEDIUM][CODEX]` Stage transition methods + endpoints

## PHASE 5: Render Pipeline (per-language, sequential)
- [ ] 5.1 `[HARD][CLAUDE]` DashboardRenderObserver (PipelineObserver → SQLite)
- [ ] 5.2 `[HARD][CODEX]` POST render — single lang or "all" (sequential queue)
- [ ] 5.3 `[HARD][CLAUDE]` SSE render progress endpoint
- [ ] 5.4 `[EASY][CODEX]` Video/scene serving endpoints

## PHASE 6: Frontend — Asset Upload (shared)
- [ ] 6.1 `[HARD][GEMINI]` Scene grid + upload zones in episode overlay
- [ ] 6.2 `[MEDIUM][GEMINI]` Drag-drop + file picker + bulk upload
- [ ] 6.3 `[EASY][GEMINI]` Asset upload CSS

## PHASE 7: Frontend — Validation & Render (per-language)
- [ ] 7.1 `[MEDIUM][GEMINI]` Validation panel — per-language results
- [ ] 7.2 `[HARD][GEMINI]` Render progress panel — language tabs + SSE + queue
- [ ] 7.3 `[MEDIUM][GEMINI]` Final review — per-language video gallery
- [ ] 7.4 `[EASY][GEMINI]` Render/validation CSS

## PHASE 8: Workflow Integration
- [ ] 8.1 `[EASY][GEMINI]` "Start Video Assembly" button
- [ ] 8.2 `[EASY][GEMINI]` Stage strip for 16 stages
- [ ] 8.3 `[MEDIUM][CODEX]` Conditional assembly UI loader

## PHASE 9: Asset Staging
- [ ] 9.1 `[MEDIUM][CODEX]` _stage_assets_for_render() — copy shared → per-lang
- [ ] 9.2 `[EASY][CODEX]` Wire staging into start_render()

## PHASE 10: Polish & Safety
- [ ] 10.1 `[EASY][CODEX]` Re-render + cleanup temp files
- [ ] 10.2 `[EASY][CODEX]` FFmpeg guard on endpoints
- [ ] 10.3 `[MEDIUM][GEMINI]` Per-language render status column
- [ ] 10.4 `[EASY][CODEX]` Sequential-only: prevent concurrent renders + TTS conflict

## PHASE 11: Testing
- [ ] 11.1 `[HARD][CLAUDE]` Integration test (multilingual render)
- [ ] 11.2 `[MEDIUM][CODEX]` Unit tests for video_assembly

---

**Total: 38 tasks** | EASY: 17 | MEDIUM: 15 | HARD: 6
**Agents:** CODEX: 24 | GEMINI: 11 | CLAUDE: 3
