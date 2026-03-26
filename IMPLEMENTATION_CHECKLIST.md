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
- [x] Fix TTS pause/resume for multi-language (backend already working, now wired to UI with worker health indicator)
- [x] TTS progress display in episode detail (per-language TTS job progress in status table)
- [x] TTS worker health indicator (active/stale/offline badge + start worker button)
- [x] Translation preview (side-by-side original vs translated with chunk log)
- [x] Per-language retry mechanism (retry failed translation or TTS per language)
- [x] Error handling (API keys, rate limits) — errors display in per-language table, retry available

## Phase 9: Review & Export Phase
- [x] Timeline editor in review view
- [x] Consistency guide editor
- [x] Prompt list editor
- [x] Per-language timeline viewer
- [x] Export zip packaging
- [x] "Finalize" action

## Phase 10: Final Cleanup & Documentation
- [x] Remove all remaining legacy references
- [x] Update `PROJECT_REGISTRY.md` with final architecture
- [x] Update agent configs in `config/agents/`
- [x] Clean up `requirements.txt`
- [x] Full test suite pass
- [x] E2E test: submit → all stages → export

## Frontend UI Overhaul (2026-03-26)
- [x] Phase 1: Collapsible Icon Sidebar (implemented toggle rail, localstorage persistence)
- [x] Phase 2: Streamline Pipeline Board (removed redundant helper text, badging moved to header)
- [x] Phase 3: Widen Kanban Columns (set explicitly to min 320px for better data density, fixed media query squash bug)
- [x] Phase 4: Clean Episode Cards (compact cards, icon-only buttons, hidden niche label on the main board)
- [x] Phase 5: Reduce Topbar Text Noise (removed sub-headers and eyebrows for cleaner pages)
- [x] Phase 6: Improve Episode Detail Layout (removed heavy boxes, unified grid layout, cleaner language table)
- [x] Phase 7: Polish Niche Project & Settings (densified stats bar and setup form cards into a cleaner view)
- [x] Phase 8: Micro-animations & Polish (added hover lift on cards and tactile click down-states on buttons)

## Workflow Repair (2026-03-26)
- [x] Make `#/niche-projects` the landing flow and `#/niche-projects/:id` the main workspace
- [x] Redirect legacy `#/pipeline-board` usage into the project-scoped workflow
- [x] Replace the flat project episode list with a true project Kanban
- [x] Rename the first column to `Draft`
- [x] Move episode creation into the Draft column and stop auto-queueing new episodes
- [x] Open episode details as an overlay on top of the project board
- [x] Add shared queue-readiness validation for queue and requeue
- [x] Return structured readiness blockers in project/episode payloads and queue 400 responses
- [x] Disable blocked queue actions in the UI and render blocker/warning details
- [x] Preserve provider-stage failure logs and keep failures explicit without provider fallback
- [x] Make template/settings reads side-effect free
- [x] Verify with 93 passing tests, JS syntax check, and browser smoke

## Voice Profile Flow Simplification (2026-03-26)
- [x] Remove language from the create voice-profile flow
- [x] Make voice profiles language-agnostic in queue readiness and project assignment
- [x] Replace manual voice-test text entry with a backend-generated default English sample
- [x] Simplify voice-profile cards to one-click `Play test`, compact state text, and inline sample playback
- [x] Verify with `node --check`, `python -m unittest discover -s tests -v` (`101` passing), and browser smoke
