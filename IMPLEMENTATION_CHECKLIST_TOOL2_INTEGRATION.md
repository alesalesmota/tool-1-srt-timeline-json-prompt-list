# Tool 2 Integration — Progress Checklist

> Track progress here. Mark tasks as they complete.

## PHASE 0: Preparation
- [ ] 0.1 `[EASY][CODEX]` FFmpeg startup check in app.py
- [ ] 0.2 `[EASY][CODEX]` Copy Tool 2 modules → video_assembly/ subpackage
- [ ] 0.3 `[EASY][CODEX]` Add jinja2 to requirements.txt

## PHASE 1: Database Schema
- [ ] 1.1 `[MEDIUM][CODEX]` render_jobs table + CRUD
- [ ] 1.2 `[MEDIUM][CODEX]` scene_assets table + CRUD
- [ ] 1.3 `[EASY][CODEX]` render_logs table + insert/query

## PHASE 2: Config & Frontend Stages
- [ ] 2.1 `[EASY][CODEX]` Add 4 stages to EPISODE_PIPELINE_STAGES
- [ ] 2.2 `[EASY][GEMINI]` Add 4 Kanban columns to app.js

## PHASE 3: Asset Upload Backend
- [ ] 3.1 `[MEDIUM][CODEX]` _prepare_assembly_project() method
- [ ] 3.2 `[MEDIUM][CODEX]` GET /api/episodes/{id}/scenes endpoint
- [ ] 3.3 `[MEDIUM][CODEX]` POST single-scene asset upload
- [ ] 3.4 `[MEDIUM][CODEX]` POST bulk asset upload with auto-match
- [ ] 3.5 `[EASY][CODEX]` DELETE scene asset endpoint
- [ ] 3.6 `[EASY][CODEX]` GET asset preview endpoint

## PHASE 4: Assembly Validation
- [ ] 4.1 `[MEDIUM][CODEX]` POST validate endpoint
- [ ] 4.2 `[MEDIUM][CODEX]` Stage transition methods + endpoints

## PHASE 5: Render Pipeline
- [ ] 5.1 `[HARD][CLAUDE]` DashboardRenderObserver (PipelineObserver → SQLite)
- [ ] 5.2 `[HARD][CODEX]` POST render start endpoint (threaded)
- [ ] 5.3 `[HARD][CLAUDE]` SSE render progress endpoint
- [ ] 5.4 `[EASY][CODEX]` Video/scene serving endpoints

## PHASE 6: Frontend — Asset Upload
- [ ] 6.1 `[HARD][GEMINI]` Scene grid + upload zones in episode overlay
- [ ] 6.2 `[MEDIUM][GEMINI]` Drag-drop + file picker + bulk upload
- [ ] 6.3 `[EASY][GEMINI]` Asset upload CSS

## PHASE 7: Frontend — Validation & Render
- [ ] 7.1 `[MEDIUM][GEMINI]` Validation panel UI
- [ ] 7.2 `[HARD][GEMINI]` Render progress panel with SSE
- [ ] 7.3 `[MEDIUM][GEMINI]` Final review video player
- [ ] 7.4 `[EASY][GEMINI]` Render/validation CSS

## PHASE 8: Workflow Integration
- [ ] 8.1 `[EASY][GEMINI]` "Start Video Assembly" button
- [ ] 8.2 `[EASY][GEMINI]` Stage strip for 16 stages
- [ ] 8.3 `[MEDIUM][CODEX]` Conditional assembly UI loader

## PHASE 9: Asset Staging
- [ ] 9.1 `[MEDIUM][CODEX]` _stage_assets_for_render() method
- [ ] 9.2 `[EASY][CODEX]` Wire staging into start_render()

## PHASE 10: Polish & Safety
- [ ] 10.1 `[EASY][CODEX]` Re-render + cleanup
- [ ] 10.2 `[EASY][CODEX]` FFmpeg guard on endpoints
- [ ] 10.3 `[MEDIUM][GEMINI]` Per-language render status column
- [ ] 10.4 `[EASY][CODEX]` Concurrent operation prevention

## PHASE 11: Testing
- [ ] 11.1 `[HARD][CLAUDE]` End-to-end integration test
- [ ] 11.2 `[MEDIUM][CODEX]` Unit tests for video_assembly

---

**Total: 38 tasks** | EASY: 17 | MEDIUM: 15 | HARD: 6
**Agents:** CODEX: 24 | GEMINI: 11 | CLAUDE: 3
