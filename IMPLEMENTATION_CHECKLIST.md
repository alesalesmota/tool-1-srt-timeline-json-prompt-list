# Implementation Checklist
> Track progress across conversations. Update after each task.

## Pre-Implementation: Continuity Setup
- [x] Save plan as `IMPLEMENTATION_PLAN.md` in repo
- [x] Create this checklist (`IMPLEMENTATION_CHECKLIST.md`)
- [x] Update `CLAUDE.md` global behavior with plan persistence rule
- [x] Save feedback memory about plan persistence
- [x] Archive outdated docs to `archive/`
- [x] Update `PROJECT_REGISTRY.md` with new plan

## Phase 1: Cleanup & Git Hygiene
- [x] Delete `TRADUTOR/` directory
- [x] Delete `TTS -NARRAÇAO/` directory
- [x] Delete `QUEBRADOR DE SRT/` directory
- [x] Delete `UI for Open AI Whisper/` directory
- [x] Archive `tool_1_multilingual_implementation.md` to `archive/`
- [x] Archive `tool_1_prd_readme_revised.md` to `archive/`
- [x] Delete `WORKFLOW_STATUS.md`
- [x] Create/update `.gitignore`
- [x] Commit all uncommitted work on `feat/cleanup-and-consolidation`
- [x] Push to remote
- [x] Verify: app starts
- [x] Verify: existing tests pass (119 passed, 0 failures)

## Phase 2: Database Consolidation
- [x] Create `niche_projects` table in database.py
- [x] Add `episode_id` column to `stage_runs`
- [x] Drop legacy tables (`jobs`, `projects`, `builds`)
- [x] Update all DB methods for niche projects
- [x] Verify: tests pass (87 passed)

## Phase 3: Service Layer — Remove Legacy Processing
- [x] Remove `_process_job()` + legacy job stage methods
- [x] Remove `_process_build()` + build stage methods
- [x] Remove legacy CRUD methods (jobs, projects, builds)
- [x] Update `_worker_loop()` for episodes only
- [x] Rename niche methods to primary
- [x] Update/remove legacy test files (deleted test_api.py, test_build_pipeline.py, test_pipeline.py)
- [x] Verify: episode pipeline tests pass (87 passed)

## Phase 4: API Layer Consolidation
- [x] Remove `/api/jobs/*` endpoints
- [x] Remove `/api/projects/*` legacy endpoints
- [x] Remove `/api/builds/*` endpoints
- [ ] Rename `/api/niche-projects` → `/api/projects` (deferred — keeping niche-projects path for now)
- [x] Remove legacy Pydantic models
- [x] Remove legacy config constants (BUILD_TYPES, MASTER/LOCALIZATION stages, etc.)
- [x] Verify: 87 tests pass

## Phase 5: Frontend — Remove Legacy Views
- [x] Remove legacy sidebar items ("Projects (legacy)", "Board (legacy)")
- [x] Remove job/project/build render functions (~2100 lines removed)
- [x] Remove legacy constants, state fields, event handlers, data fetching
- [x] Set default route to pipeline board
- [x] Clean sidebar (removed workflow steps section)
- [x] Verify: manual browser test (no JS errors, all nav works)

## Phase 6: Episode Pipeline Board Enhancement
- [x] Per-language progress indicators on episode cards
- [x] Current stage + elapsed time display
- [x] Quick actions (queue, restart, delete)
- [x] Episode detail: per-language status table
- [x] Stage run history view
- [x] Output file previews
- [x] Pipeline progress bar

## Phase 7: Niche Project Detail Enhancement
- [x] Language config with voice/translation profile assignments
- [x] Episode list with status summary (per-language mini-dots)
- [x] "Submit New Episode" form (existed from Phase 5, verified working)
- [x] Inline language config editing (add/remove languages, assign profiles)
- [x] Batch operations (queue all drafts, re-run failed)
- [x] Project statistics (stats bar with totals, by-status, completion rate)
- [x] AI provider/model configuration per project (inline editing)
- [x] Missing /api/target-languages endpoint added

## Phase 8: TTS & Translation Integration Polish
- [ ] Fix TTS pause/resume for multi-language
- [ ] TTS progress display in episode detail
- [ ] TTS worker health indicator
- [ ] Translation preview (side-by-side)
- [ ] Per-language retry mechanism
- [ ] Error handling (API keys, rate limits)

## Phase 9: Review & Export Phase
- [ ] Timeline editor in review view
- [ ] Consistency guide editor
- [ ] Prompt list editor
- [ ] Per-language timeline viewer
- [ ] Export zip packaging
- [ ] "Finalize" action

## Phase 10: Final Cleanup & Documentation
- [ ] Remove all remaining legacy references
- [ ] Update `PROJECT_REGISTRY.md` with final architecture
- [ ] Update agent configs in `config/agents/`
- [ ] Clean up `requirements.txt`
- [ ] Full test suite pass
- [ ] E2E test: submit → all stages → export
