# Tool 1 Creator Studio — Reconstruction Plan
> Last updated: 2026-03-26

## Context

Tool 1 is the multilingual video planning pipeline for Creator Studio. The user (Blue) creates niche-based YouTube content (e.g., Religion) and produces the **same episode in multiple languages** for different YouTube channels.

**The problem:** A previous comprehensive implementation plan was lost between conversations. The project has accumulated three overlapping workflow models (legacy Jobs, legacy Projects/Builds, and the target Episodes model), standalone tools that duplicate integrated modules, and uncommitted work across the entire codebase. We need to clean up, consolidate, and finish what was started.

**Intended outcome:** A clean, episode-first pipeline where submitting a script to a Niche Project creates a Draft episode on the project board, queueing is explicit, failures are visible, and the Kanban shows every stage without hiding provider/configuration problems.

---

## Current State Summary

### What works (backend)
- FastAPI app with ~50+ endpoints, SQLite database, service layer with worker loop
- Translation module (`tool1_dashboard/translation/`) — adapter, chunker, prompts, service
- TTS module (`tool1_dashboard/tts/`) — manager, worker subprocess, XTTS-v2
- Alignment tool (`tool1_dashboard/alignment_tool/`)
- Episode pipeline (`_process_episode()` in service.py) — all 10 stages implemented
- 8 agent prompt templates (scene planning, visual bible, video/image prompts)
- Validators, CLI runner, template store

### What works (frontend)
- Kanban board with pipeline columns, dark/light theme
- Episode views, Niche Project views, Voice/Translation profiles, Settings, Templates
- But: ~2000 lines of legacy views still present (Jobs, Projects/Builds)

### Three coexisting workflow models (problem)
1. **Jobs** (legacy v1) — single-language, requires audio upload
2. **Projects/Builds** (legacy v2) — master + localization builds
3. **Niche Projects/Episodes** (target v3) — TTS-first, multi-language, script-only input

### Key decisions
- **Consistency guide is per-episode** (not shared at niche project level)
- **All legacy code (Jobs, Projects/Builds) will be removed** — Episodes is the final model
- **4 standalone tools deleted** — all duplicated by integrated modules

---

## Unified Pipeline Flow (Target)

```
Submit Script to Niche Project → Episode Created (Draft)
  │
  ├─ 1. CONSISTENCY GUIDE    (LLM, per episode — each episode gets its own guide)
  ├─ 2. TRANSLATION           (per target language, sequential)
  ├─ 3. TTS                   (per language incl. master, sequential, GPU-bound)
  ├─ 4. ALIGNMENT             (per language, generates SRT from audio+script)
  ├─ 5. CHUNKING              (master language SRT → planning chunks)
  ├─ 6. SCENE PLANNING        (LLM, master language, chunk by chunk)
  ├─ 7. VIDEO PROMPT GEN      (LLM, batch by batch, leading scenes)
  ├─ 8. IMAGE PROMPT GEN      (LLM, batch by batch, remaining scenes)
  ├─ 9. TIMELINE MAPPING      (per language, stretch scenes to fit narration)
  │
  └─ REVIEW → EXPORT (zip for Tool 2 handoff)
```

---

## Phase Plan

See `IMPLEMENTATION_CHECKLIST.md` for progress tracking.

### Phase 1: Cleanup & Git Hygiene
**Goal:** Remove dead files, set up .gitignore, commit clean baseline

- Delete all 4 standalone tools: `TRADUTOR/`, `TTS -NARRAÇAO/`, `QUEBRADOR DE SRT/`, `UI for Open AI Whisper/`
- Archive outdated docs to `archive/`: `tool_1_multilingual_implementation.md`, `tool_1_prd_readme_revised.md`
- Delete `WORKFLOW_STATUS.md`
- Add to `.gitignore`: `.playwright-cli/`, `__pycache__/`, `*.db`, `workspace/`, `venv/`
- Commit all uncommitted work (feature branch `feat/cleanup-and-consolidation`)

### Phase 2: Database Consolidation
**Goal:** Dedicated `niche_projects` table, drop legacy tables

- Create proper `niche_projects` table
- Add `episode_id` column to `stage_runs`
- Drop legacy tables (`jobs`, `projects`, `builds`)
- Update all DB methods

### Phase 3: Service Layer — Remove Legacy Processing
**Goal:** Remove ~2000 lines of dead code from service.py

- Remove all legacy job/project/build processing methods
- Update `_worker_loop()` to only process episodes
- Rename niche methods to primary

### Phase 4: API Layer Consolidation
**Goal:** Clean API surface — episodes and niche projects only

- Remove ~30 legacy endpoints
- Rename routes: `/api/niche-projects` → `/api/projects`
- Write consolidated API tests

### Phase 5: Frontend — Remove Legacy Views
**Goal:** Episode-first navigation, remove ~2000 lines of dead frontend code

- Remove all job/project/build render functions
- Target sidebar: Board | Projects | Voice Profiles | Translation Profiles | Settings | Templates

### Phase 6: Episode Pipeline Board Enhancement
**Goal:** Rich, transparent Kanban showing real-time progress

- Per-language progress indicators
- Quick actions, stage run history, output previews
- Pipeline progress bar

### Phase 7: Niche Project Detail Enhancement
**Goal:** Complete project management

- Language config, episode list, batch ops
- "Submit New Episode" form
- Project stats

### Phase 8: TTS & Translation Integration Polish
**Goal:** Smooth TTS + Translation workflow

- Fix pause/resume logic, progress display
- Per-language retry, error handling

### Phase 9: Review & Export Phase
**Goal:** Complete review-to-export workflow

- Timeline editor, prompt list editor
- Export zip for Tool 2 handoff

### Phase 10: Final Cleanup & Documentation
**Goal:** Final polish, full test pass, E2E validation

---

## 2026-03-26 Workflow Repair Addendum

This addendum supersedes the remaining board/queue UX assumptions from the original reconstruction plan.

### Workflow shape

The intended flow is now:

`Niche Projects -> Project Kanban -> Draft episode -> Episode Details overlay -> explicit queue`

### Completed repair items

- Made `#/niche-projects` the landing flow and `#/niche-projects/:id` the primary workspace
- Redirected legacy `#/pipeline-board` access back into the project-scoped flow
- Replaced the flat per-project episode list with a real project Kanban grouped by pipeline stage
- Renamed the first column to `Draft` and moved episode creation into that column
- Removed frontend auto-queueing from draft submission
- Switched episode details to an overlay on top of the project board, while keeping direct `#/episodes/:id` routing working
- Added shared queue readiness validation for queue and requeue
- Blocked queueing when languages, voice profiles, translation profiles, or provider auth/config are missing
- Returned structured `queue_readiness` in project and episode payloads and structured 400 errors from queue attempts
- Preserved full provider-stage failure logs without adding automatic Claude-to-Codex fallback
- Made template/settings reads side-effect free

### Verification baseline

- `python -m unittest discover -s tests -v` -> 93 passing tests
- `node --check tool1_dashboard/ui/app.js`
- Browser smoke for:
  - project board rendering
  - draft episode creation
  - direct episode route opening the overlay over the board
  - blocked queue actions showing readiness blockers

---

## Files to Modify (Critical)

| File | Action |
|------|--------|
| `tool1_dashboard/service.py` (~4500 lines) | Remove ~2000 lines legacy, keep episode pipeline |
| `tool1_dashboard/database.py` | New `niche_projects` table, drop legacy |
| `tool1_dashboard/app.py` | Remove ~25 legacy endpoints, rename routes |
| `tool1_dashboard/ui/app.js` (~3900 lines) | Remove ~2000 lines legacy views, enhance episode board |
| `tool1_dashboard/config.py` | Remove legacy pipeline constants |
| `PROJECT_REGISTRY.md` | Update throughout |

## Reuse (Don't Rebuild)

| Module | Path | Status |
|--------|------|--------|
| Translation service | `tool1_dashboard/translation/` | Complete, keep as-is |
| TTS module | `tool1_dashboard/tts/` | Complete, minor fixes in Phase 8 |
| Alignment tool | `tool1_dashboard/alignment_tool/` | Complete, keep as-is |
| SRT chunker | `tool1_dashboard/srt_chunker/` | Complete, keep as-is |
| Validators | `tool1_dashboard/validators.py` | Complete, keep as-is |
| CLI runner | `tool1_dashboard/providers.py` | Complete, keep as-is |
| Agent prompts | `config/agents/` | Complete, keep as-is |
| Template store | `tool1_dashboard/templates.py` | Complete, keep as-is |
