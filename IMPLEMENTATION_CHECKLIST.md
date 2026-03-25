# Implementation Checklist
> Track progress across conversations. Update after each task.

## Pre-Implementation: Continuity Setup
- [x] Save plan as `IMPLEMENTATION_PLAN.md` in repo
- [x] Create this checklist (`IMPLEMENTATION_CHECKLIST.md`)
- [x] Update `CLAUDE.md` global behavior with plan persistence rule
- [x] Save feedback memory about plan persistence
- [ ] Archive outdated docs to `archive/`
- [ ] Update `PROJECT_REGISTRY.md` with new plan

## Phase 1: Cleanup & Git Hygiene
- [ ] Delete `TRADUTOR/` directory
- [ ] Delete `TTS -NARRAÇAO/` directory
- [ ] Delete `QUEBRADOR DE SRT/` directory
- [ ] Delete `UI for Open AI Whisper/` directory
- [ ] Archive `tool_1_multilingual_implementation.md` to `archive/`
- [ ] Archive `tool_1_prd_readme_revised.md` to `archive/`
- [ ] Delete `WORKFLOW_STATUS.md`
- [ ] Create/update `.gitignore`
- [ ] Commit all uncommitted work on `feat/cleanup-and-consolidation`
- [ ] Push to remote
- [ ] Verify: app starts
- [ ] Verify: existing tests pass

## Phase 2: Database Consolidation
- [ ] Create `niche_projects` table in database.py
- [ ] Add `episode_id` column to `stage_runs`
- [ ] Write data migration
- [ ] Drop legacy tables (`jobs`, `projects`, `builds`)
- [ ] Update all DB methods for niche projects
- [ ] Verify: tests pass

## Phase 3: Service Layer — Remove Legacy Processing
- [ ] Remove `_process_job()` + legacy job stage methods
- [ ] Remove `_process_build()` + build stage methods
- [ ] Remove legacy CRUD methods (jobs, projects, builds)
- [ ] Update `_worker_loop()` for episodes only
- [ ] Rename niche methods to primary
- [ ] Update/remove legacy test files
- [ ] Verify: episode pipeline tests pass

## Phase 4: API Layer Consolidation
- [ ] Remove `/api/jobs/*` endpoints
- [ ] Remove `/api/projects/*` legacy endpoints
- [ ] Remove `/api/builds/*` endpoints
- [ ] Rename `/api/niche-projects` → `/api/projects`
- [ ] Remove legacy Pydantic models
- [ ] Write `test_api_consolidated.py`
- [ ] Verify: new API tests pass

## Phase 5: Frontend — Remove Legacy Views
- [ ] Remove legacy sidebar items
- [ ] Remove job/project/build render functions
- [ ] Remove legacy constants and state fields
- [ ] Set default route to pipeline board
- [ ] Rename sidebar items
- [ ] Verify: manual browser test

## Phase 6: Episode Pipeline Board Enhancement
- [ ] Per-language progress indicators on episode cards
- [ ] Current stage + elapsed time display
- [ ] Quick actions (queue, restart, delete)
- [ ] Episode detail: per-language status table
- [ ] Stage run history view
- [ ] Output file previews
- [ ] Pipeline progress bar

## Phase 7: Niche Project Detail Enhancement
- [ ] Language config with voice/translation profile assignments
- [ ] Episode list with status summary
- [ ] "Submit New Episode" form
- [ ] Inline language config editing
- [ ] Batch operations
- [ ] Project statistics

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
