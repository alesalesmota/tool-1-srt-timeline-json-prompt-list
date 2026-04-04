# Tool 1 Creator Studio — Reconstruction Plan
> Last updated: 2026-04-04

## Context

Tool 1 is the multilingual video planning pipeline for Creator Studio. The user (Blue) creates niche-based YouTube content (e.g., Religion) and produces the **same episode in multiple languages** for different YouTube channels.

**The problem:** A previous comprehensive implementation plan was lost between conversations. The project has accumulated three overlapping workflow models (legacy Jobs, legacy Projects/Builds, and the target Episodes model), standalone tools that duplicate integrated modules, and uncommitted work across the entire codebase. We need to clean up, consolidate, and finish what was started.

**Intended outcome:** A clean, episode-first pipeline where submitting a script to a Niche Project creates a Draft episode on the project board, queueing is explicit, failures are visible, and the Kanban shows every stage without hiding provider/configuration problems.

---

## Current State Summary

### Drawbridge Live-Activity Follow-Up (2026-03-28)
- Stage-run payloads now expose preview-file timestamps and sizes for both stdout and stderr.
- The episode live-activity surface now distinguishes real preview-file output from fallback execution snapshots using separate `Run age`, `Output source`, and `Last preview write` cards plus dedicated preview blocks.
- Regression coverage now protects the preview-file metadata contract that feeds this UI.

### Post-Phase Refinement (2026-03-28)
- Workflow controls now support resume-from-stop, pause-at-safe-boundary, and selected-step reruns from the episode detail/overlay UI.
- Backend queueing now defaults failed/paused episodes to their actual stopped stage instead of always falling back to `consistency_guide`.
- Selected-step reruns can reset downstream outputs safely, and mid-pipeline starts now validate that prerequisite assets exist before queueing.

### Episode 205 Repair Pass (2026-04-04)
- Scene-planning now requires absolute episode seconds, rebases late chunk-local timestamps when they slip through, and fails fast when merged coverage stops materially before the final master cue.
- Translation retries now preserve the original source script text instead of relying only on lossy `master_scenes` text, so CTA/opening paragraphs are not dropped during single-language repairs.
- Translation audits now explicitly reject mixed-language CTA leakage while preserving configured per-language channel names such as `Biblo Viral` and `Orizzonte`.
- Episode `205` was reprocessed through shared scene planning plus localized ES/IT regeneration. Shared review assets now cover `378` scenes through `3361.6s`.
- French-specific outcome changed after the multilingual QA upgrade: a fresh French rerun is now rejected by the stricter deterministic + reviewer gate under the current `openai / gpt-5-nano` profile, so the previous French review artifacts were preserved and backfilled with a spoken-script sidecar instead of being overwritten by low-quality output.

### Multilingual Translation Quality Upgrade (2026-04-04)
- Translation now uses shared language rulepacks to carry per-language CTA/channel/reference guidance, protected terms, and known bad literal patterns.
- Deterministic translation QA now runs at chunk level and script level before output is accepted.
- Script-level quality review now uses a fixed OpenAI reviewer model (`gpt-5.4-mini`) and fails the language after one repair attempt if the script is still weak.
- Readable and spoken scripts are now distinct artifacts. TTS/alignment prefer the spoken script; review/export keep the readable script primary.
- OpenAI translation calls now retry once with a larger output budget when `Responses API` returns `status = incomplete` because of `max_output_tokens`.
- Operational implication: lower-quality translation profiles that previously slipped through can now fail closed. Episode `205` French is the first confirmed case.
- Reviewer follow-up on 2026-04-04: the judge path now prunes unsupported source-channel complaints and treats all-`4+` score outputs as pass-with-suggestions rather than hard failures, which was necessary to keep French acceptance focused on real issues instead of reviewer hallucinations.

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

## 2026-03-26 Per-Voice TTS Pacing Control Addendum

This addendum captures the narration-stability pass that followed the voice-profile UX simplification and automatic voice-engine lifecycle work.

### Goals

- keep natural voice variation while reducing obviously slow or unrealistic narration takes
- make the app-owned TTS chunker the single authoritative split layer for long-form narration
- expose pacing controls per voice profile so preview behavior matches production behavior
- avoid automatic quality retries, which are too expensive for hour-long narration workflows

### Implemented shape

- Voice profiles now store a resolved `tts_config` seeded from the `natural_stable` preset and editable per profile
- The Voice Profiles UI keeps the card minimal and exposes pacing controls through a compact `Tuning` modal with presets plus advanced fields
- `Play test` and production `generate` jobs both snapshot the same per-profile `tts_config` at queue time
- Production narration is pre-chunked with the repo TTS chunker before queueing, and XTTS internal text splitting is disabled
- XTTS inference is now called with explicit sampling controls (`do_sample=True`, `num_beams=1`, `temperature`, `top_p`, `top_k`, `speed`)
- Job payloads now preserve explicit chunk text plus the original filename needed for resumable long-form output assembly

### Preset baseline

- `natural_stable`: safe default narration band
- `balanced`: modestly looser variation
- `expressive`: widest allowed variation in the first pass

### Guardrails

- Per-profile tuning stays within constrained narration-safe ranges for sampling, speed, chunk size, and inter-chunk silence
- No automatic pacing retry is introduced in this pass
- Scene chunking remains separate; TTS chunking controls narration generation before downstream planning/alignment stages

## 2026-03-29 TTS Throughput Stabilization Addendum

This addendum captures the throughput and stuck-queue repair after the user reported that the current TTS step was much slower than the preview TTS tool.

### Goals

- move the dashboard runtime back onto CUDA without changing the pinned `torch==2.3.1` / `torchaudio==2.3.1` versions
- make long-form `generate` work materially cheaper than preview/test mode without changing the downstream alignment/output contract
- recover orphaned `processing` narration jobs even when another worker heartbeat is still fresh
- make CPU-vs-GPU state and queue depth visible in the existing worker-health surfaces

### Decisions

- `test_voice` stays on the saved per-profile chunk sizing so preview tuning remains representative
- production `generate` keeps the same per-profile tuning path but enforces `chunk_max_chars >= 260`
- worker health is extended instead of adding new endpoints
- throttled progress updates reduce SQLite churn, but final completed job payloads must still resolve to `current_chunk == total_chunks` and `percent == 100`

### Verification

- targeted backend regression: `python -m pytest tests/test_tts.py -q`
- runtime verification: `torch 2.3.1+cu121`, `torchaudio 2.3.1+cu121`, `cuda_available = True`, `gpu_name = NVIDIA GeForce RTX 3050 Laptop GPU`
- worker smoke on a temporary DB: one `test_voice` job and one multi-chunk `generate` job both completed on CUDA

### Verification baseline

- `python -m unittest discover -s tests -v` -> 115 passing tests
- `node --check tool1_dashboard/ui/app.js`
- Browser smoke for:
  - opening the `Tuning` modal from a voice-profile card
  - switching presets and seeing advanced controls rewrite to preset values
  - `Save and play test` closing the modal and moving the card into inline `Generating sample`

---

## 2026-03-26 Translation Profile Setup Rework Addendum

This addendum captures the Drawbridge pass that rebuilt Translation Profiles around the actual runnable provider path instead of the shared CLI stage-provider catalog.

### Goals

- clarify API vs CLI provider modes inside Translation Profiles
- make OpenAI API the only runnable/savable translation profile in this pass
- load the available OpenAI models from the pasted or saved API key instead of asking for a free-text model id
- expose model sorting, filtering, and hover metadata so model choice is understandable at setup time
- keep future CLI modes visible as placeholders without allowing invalid persistence

### Implemented shape

- Translation Profiles now use a dedicated provider catalog with:
  - `OpenAI API` as the live runnable mode
  - `Codex CLI` and `Claude Code CLI` as placeholder preview tabs
- Added `POST /api/translation-profiles/openai/discover`
  - accepts a pasted `api_key` for create flow or `profile_id` for edit flow
  - calls OpenAI `GET /v1/models`
  - filters for text-capable models
  - merges live ids with local metadata for labels, price/speed scores, capability labels, and `best for` hover copy
  - returns a normalized model list plus a recommended default
- Translation-profile API payloads are now sanitized for the frontend
  - raw key refs are no longer returned
  - responses expose `has_api_key`, `api_key_masked`, `provider_label`, `provider_mode`, and `provider_placeholder`
- The Translation Profiles UI now uses a shared create/edit modal with:
  - provider mode tabs/cards
  - explicit OpenAI key check / model refresh action
  - searchable and sortable discovered model picker
  - hover detail tooltips for model cost/speed/capability hints
  - saved-key masking for edit flow
  - disabled save path for placeholder CLI tabs
- Existing legacy providers remain visible and deletable, but the new editor only supports OpenAI updates in this pass

### Guardrails

- Only `openai` can be created or updated through the current setup flow
- Placeholder CLI provider ids are rejected server-side if posted directly
- Edit-mode model discovery can reuse the stored secret without exposing it back to the browser

### Verification baseline

- `python -m unittest discover -s tests -v` -> 124 passing tests
- `node --check tool1_dashboard/ui/app.js`
- Browser smoke on `http://127.0.0.1:8032/#/translation-profiles` covering:
  - placeholder tab rendering and disabled save
  - mocked OpenAI model discovery during create flow
  - saved-key rediscovery during edit flow
  - model search/filter interaction
  - editing a profile and saving a changed model

---

## 2026-03-27 Translation Profile Card Simplification Addendum

This addendum captures the follow-up Drawbridge pass that trimmed the translation-profiles page down to a compact default view.

### Goals

- stop repeating `Translation Profiles` in both the page title and section body
- remove the long helper and key/meta copy that made the cards visually noisy
- keep the default card focused on the few details the user needs to scan quickly
- reuse the existing modal for deeper details instead of introducing a second inline expansion pattern

### Implemented shape

- The translation-profiles page header now keeps only a compact count plus the `Create profile` action
- The default card summary now shows:
  - profile name
  - provider label
  - selected model
  - readiness badge derived from `provider_runnable`
- The masked API-key badge, provider-description paragraph, and extra meta line are removed from the default card view
- The card summary is now the primary details trigger and reuses the existing translation-profile editor modal on click
- Edit/delete icon actions remain available as independent controls beside the summary region

### Verification baseline

- `node --check tool1_dashboard/ui/app.js`
- Playwright smoke on `http://127.0.0.1:8020/#/translation-profiles` covering:
  - compact card summaries for all profiles
  - summary click opening the existing edit modal
  - delete icon opening only the confirm dialog
  - mobile-width snapshot confirming the compact cards remain readable

---

## 2026-03-27 Sidebar Utility Relocation Addendum

This addendum captures the next Drawbridge pass that moved the two global shell controls out of the top-right chrome and into the lateral menu.

### Goals

- remove the refresh/theme buttons from the page header so the topbar stays focused on title and notices
- keep both controls easy to reach even when the sidebar is collapsed
- preserve hover labels and existing `data-refresh` / `data-theme-toggle` behavior without introducing a second control path

### Implemented shape

- The topbar now renders only the page title plus sync/notice meta
- The sidebar includes a new compact quick-actions group with:
  - `Refresh data`
  - `Light mode` / `Dark mode`
- The quick-action buttons reuse the existing sidebar visual language so they work in both collapsed icon-only mode and expanded labeled mode

### Verification baseline

- `node --check tool1_dashboard/ui/app.js`
- Playwright smoke on `http://127.0.0.1:8021/#/translation-profiles` covering:
  - no refresh/theme buttons in the topbar
  - both controls present in the sidebar
  - translation-profile page still opening and rendering correctly after the shell move

---

## 2026-03-27 Episode Start UX Cleanup Addendum

This addendum captures the workflow-launch cleanup that followed the earlier project-board repair.

### Goals

- replace ambiguous `Queue` / `Requeue` wording with explicit workflow language
- keep episode start controls compact and icon-only while preserving hover explanations and `aria-label`s
- make readiness panels reflect ready, blocked, queued, and running states instead of always reading like blockers
- keep the episode overlay open after a start attempt and show inline local feedback immediately
- preserve the existing `/api/episodes/{id}/queue` and `queue_readiness` backend contract

### Implemented shape

- Episode workflow actions now use state-specific labels:
  - `Start workflow`
  - `Restart workflow`
  - `Run again`
- Board cards and episode overlay/detail actions now share compact icon-only controls with tooltip shells that still explain disabled states
- Project and episode readiness panels now switch between:
  - `Ready to start`
  - `Ready to restart`
  - `Ready to run again`
  - `Workflow blockers`
  - `Workflow in progress`
- Starting a workflow now applies optimistic local episode state, disables repeated clicks, and shows inline overlay feedback
- Frontend action state is reconciled on refresh, so if the workflow fails moments after a start request the overlay swaps the optimistic success copy for an inline error
- Readiness blocker copy is normalized on the frontend from queue language into workflow language without changing the backend payload schema

### Verification baseline

- `node --check tool1_dashboard/ui/app.js`
- `python -m unittest discover -s tests -v` -> 124 passing tests
- Playwright smoke on:
  - `http://127.0.0.1:8020/#/niche-projects/niche-20260327-160221-bridge-smoke-ready-638141`
  - `http://127.0.0.1:8020/#/niche-projects/niche-20260327-160221-bridge-smoke-blocked-638141`
- Verified:
  - ready/review/done/failed cards expose the new workflow wording
  - blocked episodes keep disabled start controls with blocker tooltips and blocker panels
  - mobile-width overlay and card controls remain icon-only with accessible labels
  - the real restart flow keeps the overlay open and flips the inline message to `Workflow failed in Consistency Guide.` after the backend failure refresh
- Live environment note:
  - the smoke restart hit a provider-side Claude quota error: `Claude limit reached. You've hit your limit · resets Mar 28, 5pm (America/Sao_Paulo)`
  - this is an environment/runtime limitation, not a frontend regression; the UI now surfaces it correctly inline

---

## 2026-03-27 Real Workflow Feedback On Project Kanban Addendum

This addendum captures the follow-up Drawbridge pass that made the project board itself explain live workflow state instead of relying on hover-only controls or detail overlays.

### Goals

- move started cards out of `Draft` immediately on the board
- keep card column placement and card copy derived from the same real workflow state
- show a compact, always-visible in-card status line that reflects actual backend progress
- tighten active-board polling so queue/running states feel live instead of delayed
- avoid API-contract changes unless absolutely necessary

### Implemented shape

- Added a compact inline status row on every episode card that derives its copy from:
  - `pipeline_status`
  - `current_stage`
  - `updated_at`
  - `language_statuses`
- Added a shared computed display-stage helper for cards and kanban placement
  - if an episode is active and backend `current_stage` is still `draft`, the board now falls back to the real queued start stage so the card no longer appears stuck
- Fixed per-language stage summaries to use the actual backend keys:
  - `translation -> translation_status`
  - `tts -> tts_status`
  - `alignment -> srt_status`
  - `timeline_mapping -> timeline_status`
- Added active-board refresh throttling:
  - `1000ms` while any episode is `queued`, `running`, or `paused_for_tts`
  - `5000ms` otherwise
- Synced the refresh throttle immediately after optimistic workflow start so the faster poll rate begins as soon as the user starts a workflow
- Styled the inline workflow status and error surfaces directly on the card so critical state is readable without hover

### Verification baseline

- `node --check tool1_dashboard/ui/app.js`
- `python -m pytest tests/test_video_pipeline.py -k "queue_episode and not missing and not provider and not translation_profile and not voice_profile" -q`
- `python -m pytest tests/test_video_pipeline.py -k "requeue_after_provider_config_change_restarts_from_failed_stage" -q`
- Playwright smoke on `http://127.0.0.1:8021/#/niche-projects/niche-20260326-133703-religi-o`
- Live browser verification confirmed:
  - a fresh draft card initially rendered `Ready to start workflow.` in the `Draft` column
  - after workflow start, that same card moved out of `Draft` within roughly 100ms
  - the card rendered inline running state in `Consistency Guide` instead of looking idle

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
