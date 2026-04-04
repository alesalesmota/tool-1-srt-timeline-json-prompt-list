# Implementation Checklist
> Track progress across conversations. Update after each task.

## Multilingual Translation Quality Upgrade (2026-04-04)
- [x] Add shared per-language translation rulepacks for CTA/channel/reference guidance and known bad literal patterns
- [x] Add deterministic translation QA that runs at chunk level and script level
- [x] Add a fixed-model script reviewer gate (`gpt-5.4-mini`) with one repair attempt and fail-closed behavior
- [x] Add readable + spoken script artifacts and persist `spoken_script_path` in language status rows
- [x] Make TTS/alignment prefer spoken scripts and include readable + spoken scripts in export bundles
- [x] Add OpenAI incomplete-response retry for `max_output_tokens`
- [x] Add/refresh regression coverage for prompts, QA, spoken scripts, exports, queue/TTS orchestration, and OpenAI retry behavior
- [x] Run full backend verification with `python -m pytest tests -q` (`198` passing, `4` subtests passing)
- [x] Live-check episode `205` under the new gate, confirm French now fails safely on quality, and restore the last usable French artifacts with a spoken-script sidecar
- [x] Add reviewer-sanity filtering for unsupported source-channel complaints / contradictory `4+` score failures, rerun French with `gpt-5.4-mini`, and regenerate French TTS + alignment + timeline from the accepted script

## Episode 205 Repair Plan (2026-04-04)
- [x] Harden scene-planning prompts, scene merge normalization, and cue-tail coverage checks so chunk-local timestamps cannot silently truncate shared review assets
- [x] Add regression coverage for late chunk timestamp rebasing and final cue coverage validation
- [x] Harden translation prompts/audits/retry behavior for mixed-language leakage, untranslated CTA text, and configured per-language channel names
- [x] Preserve full source-script text during scene-aware translation retries and make `gpt-5.4*` translation requests compatible with the Responses API reasoning contract
- [x] Reprocess episode `205` shared assets through `scene_planning -> video_prompt_generation -> image_prompt_generation -> timeline_mapping` and verify coverage through `3361.6s`
- [x] Regenerate repaired localized outputs for `es`, `fr`, and `it` through `translation -> tts -> alignment -> timeline_mapping`
- [x] Re-audit episode `205` outputs and record the remaining French subtitle-warning risk in `PROJECT_REGISTRY.md`

## Drawbridge Live-Activity Signal Clarity (2026-03-28)
- [x] Expose stdout/stderr preview timestamps and sizes in serialized stage-run payloads
- [x] Rebuild the `Live activity` panel so it separates run age, output source, last preview write, and preview/snapshot content
- [x] Add regression coverage for preview-file metadata on stage runs
- [x] Verify with `node --check tool1_dashboard/ui/app.js`, `python -m unittest discover -s tests -v` (`143` passing), and Playwright smoke on `http://127.0.0.1:8765/#/niche-projects/niche-20260326-133703-religi-o`

## Drawbridge Follow-Up (2026-03-28)
- [x] Remove the episode-overlay sidebar so the modal reads as a single main-column workflow surface
- [x] Move stage-run history inline under the live-activity area and remove duplicate quick actions from the overlay
- [x] Queue TTS jobs for every unresolved configured language before entering `paused_for_tts`
- [x] Reconcile paused-TTS episodes by refreshing running jobs, backfilling legacy pending languages, and failing unrecoverable states explicitly
- [x] Audit workflow status copy so `paused_for_tts` episodes no longer look actively running without real work behind them
- [x] Add pipeline regression coverage for multi-language TTS queueing, paused-TTS completion handoff, and legacy backfill recovery
- [x] Verify with `python -m py_compile tool1_dashboard/service.py`, `node --check tool1_dashboard/ui/app.js`, `python -m pytest tests/test_video_pipeline.py -k "paused_tts or tts_stage_queues_all_pending_languages_before_pause or paused_tts_episode_requeues_alignment_after_all_languages_complete or paused_tts_episode_backfills_missing_jobs_for_pending_languages" -q`, `python -m pytest tests/test_video_pipeline.py -q`, and Playwright smoke on `http://127.0.0.1:8020/#/niche-projects/niche-20260326-133703-religi-o`

## Post-Phase Refinements (2026-03-28)
- [x] Add workflow-control backend support for pause/resume and selected-step reruns
- [x] Add stage-specific start guards so mid-pipeline restarts only run when prerequisite assets exist
- [x] Add episode detail/overlay workflow-control UI with step selector and pause action
- [x] Verify workflow-control changes (`python -m py_compile`, `node --check`, `python -m pytest tests/test_video_pipeline.py`)

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

## Automatic Voice Engine Lifecycle (2026-03-26)
- [x] Remove first-party manual `Start Worker` / `Stop` controls from Voice Profiles and episode TTS views
- [x] Stop auto-starting the XTTS worker at app startup
- [x] Auto-start and auto-recover the voice engine on interactive profile/test actions and pipeline TTS generation
- [x] Add smart idle shutdown with short interactive cooldowns and longer pipeline drain cooldowns
- [x] Hide normal sleeping/offline worker state from the UI and only surface runtime/startup failures
- [x] Extend backend and pipeline tests for sleeping vs unavailable worker states and lifecycle behavior
- [x] Verify with `node --check`, `python -m unittest discover -s tests -v` (`108` passing)

## Per-Voice TTS Pacing Control (2026-03-26)
- [x] Add `tts_config_json` to voice profiles and resolve it into typed per-profile `tts_config` payloads
- [x] Seed new and legacy voice profiles with the `natural_stable` preset automatically
- [x] Snapshot resolved `tts_config` into queued `generate` and `test_voice` jobs
- [x] Make the repo TTS chunker the authoritative narration split layer for production and tests
- [x] Disable XTTS internal `enable_text_splitting` and pass explicit decoding controls (`do_sample`, `num_beams`, `temperature`, `top_p`, `top_k`, `speed`)
- [x] Add compact per-profile `Tuning` modal with presets, collapsed advanced controls, `Save`, and `Save and play test`
- [x] Keep the voice-profile card minimal while exposing tuning as a secondary action
- [x] Extend backend, worker, UI, and pipeline tests for tuning persistence, chunked payloads, and explicit XTTS kwargs
- [x] Verify with `node --check`, `python -m unittest discover -s tests -v` (`115` passing), and browser smoke for preset switching plus `Save and play test`

## Drawbridge Voice-Card Control Cleanup (2026-03-26)
- [x] Convert voice-profile play, tuning, and delete controls into compact icon-only tooltip actions
- [x] Keep starting and generating play-test states visually compact with a small status pill plus short copy
- [x] Tighten title/action alignment so long profile names do not force awkward button wrapping
- [x] Preserve inline audio playback and inline error messaging while cleaning the control row
- [x] Update Drawbridge task `29eb90f2-dd37-4ab3-bff5-4c680c72abd1` from `to do` -> `doing` -> `done` and sync `.moat/moat-tasks.md`
- [x] Verify with `node --check`, `python -m unittest discover -s tests -v` (`115` passing), and browser smoke for idle plus generating card states

## Drawbridge Translation Profile Setup Rework (2026-03-26)
- [x] Split translation-profile provider metadata away from the stage-provider catalog
- [x] Add OpenAI model discovery endpoint with inline-key and saved-key flows
- [x] Sanitize translation-profile API responses so raw key refs do not reach the browser
- [x] Rebuild Translation Profiles into a shared create/edit modal with provider tabs
- [x] Keep `Codex CLI` and `Claude Code CLI` visible as placeholder-only tabs with disabled save
- [x] Add model search, sort, selection, and hover-detail metadata to the OpenAI picker
- [x] Add edit support with saved-key masking and rediscovery
- [x] Render translation-profile cards with provider mode, selected model, and saved-key state
- [x] Reject placeholder provider ids if they are posted directly to the backend
- [x] Update Drawbridge task `9d96f21b-ec71-4952-8c5a-bce15f8b75fc` from `to do` -> `doing` -> `done` and sync `.moat/moat-tasks.md`
- [x] Verify with `node --check`, `python -m unittest discover -s tests -v` (`124` passing), and browser smoke for create, placeholder tabs, edit rediscovery, model filtering, and update save

## Drawbridge Language Setup Disclosure Fix (2026-03-27)
- [x] Replace native project-config `<details>` panels with explicit button-driven disclosure controls in `tool1_dashboard/ui/app.js`
- [x] Preserve `Language setup` and `Provider setup` open state across background `refreshData().then(renderApp)` rerenders
- [x] Pause auto-refresh while project-config controls are actively focused so dropdown edits are not interrupted mid-interaction
- [x] Reset disclosure state when leaving the project so a fresh visit still starts collapsed
- [x] Update Drawbridge task `a17fdd50-b72a-4df2-9a4e-5ef2f914fbb2` from `to do` -> `doing` -> `done` and sync `.moat/moat-tasks.md`
- [x] Update Drawbridge task `1b7ec1b8-bfe3-4325-9172-d27a81296d94` from `to do` -> `doing` -> `done` and sync `.moat/moat-tasks.md`
- [x] Verify with `node --check tool1_dashboard/ui/app.js`, `python -m unittest discover -s tests -v` (`124` passing), and browser smoke on `http://127.0.0.1:8020/#/niche-projects/niche-20260326-133703-religi-o`

## Drawbridge Translation Profile Card Simplification (2026-03-27)
- [x] Remove the repeated `Translation Profiles` section copy and the long helper paragraph from the translation-profiles page chrome
- [x] Simplify each translation-profile card to show only profile name, provider, model, and readiness in the default view
- [x] Reuse the existing translation-profile editor modal as the details surface when the summary area is clicked
- [x] Keep edit/delete icon actions separate from the summary click target
- [x] Update Drawbridge task `07a5ddf5-14fc-45f8-89fb-2bbdd8ced2b1` from `to do` -> `doing` -> `done` and sync `.moat/moat-tasks.md`
- [x] Verify with `node --check tool1_dashboard/ui/app.js` and Playwright smoke on `http://127.0.0.1:8020/#/translation-profiles`

## Drawbridge Sidebar Utility Relocation (2026-03-27)
- [x] Remove the refresh and theme-toggle buttons from the top-right topbar chrome
- [x] Rehome both shell controls into the left sidebar as compact quick actions that still work in collapsed mode
- [x] Keep the topbar focused on page title and notices while preserving hover labels for the sidebar actions
- [x] Update Drawbridge task `ee8b37dd-c563-495b-8ffc-0ef2a80d1603` from `to do` -> `doing` -> `done` and sync `.moat/moat-tasks.md`
- [x] Verify with Playwright smoke on `http://127.0.0.1:8021/#/translation-profiles`

## Drawbridge Episode Start UX Cleanup (2026-03-27)
- [x] Rename episode queue/requeue wording into `Start workflow`, `Restart workflow`, and `Run again`
- [x] Convert board-card and overlay workflow controls into compact icon-only actions with hover explanations and disabled-state tooltips
- [x] Make project and episode readiness panels state-aware for ready, blocked, queued, and running cases
- [x] Keep the episode overlay open after workflow start and add inline optimistic pending plus success/error feedback
- [x] Reconcile inline workflow feedback against later refreshes so fast provider failures replace stale success copy
- [x] Normalize readiness blocker copy from queue wording into workflow wording on the frontend without changing the API contract
- [x] Update Drawbridge task `50815060-5efc-4251-8a89-61129d92331b` from `to do` -> `doing` -> `done` and sync `.moat/moat-tasks.md`
- [x] Verify with `node --check tool1_dashboard/ui/app.js`, `python -m unittest discover -s tests -v` (`124` passing), and Playwright smoke on the seeded ready/blocked niche projects plus a mobile-width overlay pass

## Drawbridge Real Workflow Feedback On Project Kanban (2026-03-27)
- [x] Add an always-visible inline workflow status row on episode cards using real episode payload fields instead of hover-only or overlay-only feedback
- [x] Use the same computed workflow display stage for card status and kanban column placement so a started episode cannot remain visually stuck in `Draft`
- [x] Map per-language card summaries to the real backend language status keys (`translation_status`, `tts_status`, `srt_status`, `timeline_status`)
- [x] Shorten board auto-refresh to 1 second while any episode is `queued`, `running`, or `paused_for_tts`, with the existing 5-second idle fallback
- [x] Make card error/status surfaces readable without hover by styling the inline status row and concise error block directly on the card
- [x] Update Drawbridge task `59eb5c0d-3001-4ab3-97c4-566f5968fbfb` from `to do` -> `doing` -> `done` and sync `.moat/moat-tasks.md`
- [x] Verify with `node --check tool1_dashboard/ui/app.js`, `python -m pytest tests/test_video_pipeline.py -k "queue_episode and not missing and not provider and not translation_profile and not voice_profile" -q`, `python -m pytest tests/test_video_pipeline.py -k "requeue_after_provider_config_change_restarts_from_failed_stage" -q`, and Playwright smoke on `http://127.0.0.1:8021/#/niche-projects/niche-20260326-133703-religi-o`

## Drawbridge Stale Paused-TTS Recovery (2026-03-28)
- [x] Route pipeline TTS submission through the shared `Tool1Service.tts_manager` so worker health and restart behavior reflect the real active worker
- [x] Requeue stale `processing` TTS jobs for paused episodes instead of treating dead workers as indefinitely active narration
- [x] Wake the shared TTS worker back up when paused episodes still have queued narration jobs after a restart
- [x] Fail paused-TTS recovery explicitly if queued narration cannot resume, instead of leaving episodes frozen in `paused_for_tts`
- [x] Add regression coverage for a paused episode whose Spanish narration job is stale `processing`
- [x] Verify with `python -m pytest tests/test_video_pipeline.py -k "paused_tts_episode or live_tts_progress"` (`5` passing)

## Drawbridge TTS Queue-State Clarity (2026-03-28)
- [x] Stop marking newly submitted narration jobs as `running` before the XTTS worker actually claims them
- [x] Reconcile paused-episode TTS rows from real job states so queued jobs stay `queued` and only `processing` jobs become `running`
- [x] Update the per-language TTS table, compact summaries, and badge tones so queued narration is visible as queued instead of looking concurrent
- [x] Keep retry actions disabled while `paused_for_tts` is still active
- [x] Add regression coverage for queued submission, queued-vs-processing reconciliation, and retry submission
- [x] Verify with `python -m pytest tests/test_tts.py` (`49` passing)

## TTS Throughput Stabilization (2026-03-29)
- [x] Extend worker/runtime health payloads with `device`, `torch_version`, `torch_build`, `cuda_available`, `gpu_name`, and active/queued `generate` counts
- [x] Surface CPU-only runtime warnings in queue readiness, voice-profile creation, and paused-TTS / voice-profile worker-health UI
- [x] Requeue orphaned `processing` narration jobs whose worker has no fresh heartbeat, even if another worker heartbeat is healthy
- [x] Block duplicate worker starts when a fresh shared-worker heartbeat already exists
- [x] Keep `test_voice` chunk sizing unchanged while enforcing `chunk_max_chars >= 260` for production `generate`
- [x] Throttle `generate` progress writes to reduce SQLite churn while preserving final chunk totals in the client payload
- [x] Repair the dashboard Python environment to use `torch 2.3.1+cu121` and `torchaudio 2.3.1+cu121`
- [x] Verify with `python -m compileall tool1_dashboard run_tool1_dashboard.py tests/test_tts.py`
- [x] Verify with `python -m pytest tests/test_tts.py -q` (`57` passing)
- [x] Verify runtime metadata shows `device = cuda`, `torch_build = cu121`, and `gpu_name = NVIDIA GeForce RTX 3050 Laptop GPU`
- [x] Verify a temporary-db XTTS smoke run completes one `test_voice` job and one multi-chunk `generate` job on CUDA
