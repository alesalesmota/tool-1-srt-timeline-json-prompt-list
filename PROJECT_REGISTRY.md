# PROJECT REGISTRY — Tool 1: Multilingual Planning & Pre-Generation System

> **This file is the cross-conversation source of truth.** Every Claude session must read this first and update it before ending.

---

## Core Idea

Tool 1 is the **multilingual planning and pre-generation engine** of the Creator Studio project. It takes a single script and produces everything needed to generate videos in multiple languages:

- Translated scripts (per language)
- TTS narrations (per language)
- Subtitle alignment & SRT files (per language)
- Master scene planning (shared across languages)
- Localized timelines (per language, same scenes, different durations)
- Prompt lists for image/video asset generation (shared)

**The fundamental principle:** Scenes are defined ONCE by the master language. All other languages reuse the same scene structure but with timing adjusted to their narration duration.

**Consistency guide is per-episode** (not shared at niche project level).

## Two-Tool Architecture

- **Tool 1** (this project) — Planning & pre-generation: translation → TTS → alignment → scene planning → timeline → prompts
- **Tool 2** (separate) — Final video assembly: takes Tool 1 outputs + shared assets → produces final localized videos

## Current State (as of 2026-03-27)

### What Exists & Works
- **Dashboard app** (`tool1_dashboard/`) — FastAPI-based, Kanban-style pipeline UI
  - Unified backend with service layer + SQLite
  - Dark/light theme, responsive layout
  - Primary workflow is now project-scoped: `Niche Projects -> Project board -> Draft episode -> Episode overlay -> explicit workflow start`
  - Views: Niche Projects, project board/detail, episode overlay/direct episode route, Voice Profiles, Translation Profiles, Settings, Templates
  - Legacy Jobs and Projects/Builds models have been fully removed; the old global Pipeline Board is no longer a primary workflow surface.
  - Drawbridge feedback repair on 2026-03-26: the project-board CTA now says `Create episode`, the Draft column add action is a compact `+` with hover copy, column helper text moved into title hover tooltips, and the create-project modal now uses a searchable target-language picker instead of a checkbox wall
  - Translation Profiles rework on 2026-03-26/27: the setup modal now has dedicated provider-mode cards, OpenAI model discovery, searchable/sortable model selection, masked saved-key edit flow, preview-only `Codex CLI` / `Claude Code CLI` tabs, and compact summary cards that only show name/provider/model/readiness while opening the existing modal for details on click
  - Shell controls polish on 2026-03-27: the global refresh/theme quick actions moved out of the top-right chrome and into the sidebar as compact utility buttons, keeping the page header focused on the current workspace title
  - Episode workflow-launch cleanup on 2026-03-27: queue wording is now explicit workflow wording, board/overlay start controls are compact icon-only actions with hover explanations, readiness panels are state-aware, and overlay feedback now reconciles fast backend failures instead of leaving stale success copy behind
- **TTS module** (`tool1_dashboard/tts/`) — audio, chunker, constants, manager, worker (XTTS-v2)
  - **Runtime Fixed (2026-03-26)**: Resolved compatibility issues with `torch 2.6.0` and `transformers 5.x`. Environment now pinned to `torch 2.3.1` and `transformers 4.39.3`. Missing dependencies (`bangla`, `gruut`, `spacy[ja]`, `umap-learn`) manually restored.
- **Translation module** (`tool1_dashboard/translation/`) — adapter, chunker, prompts, service
- **Alignment tool** (`tool1_dashboard/alignment_tool/`)
- **SRT chunker** (`tool1_dashboard/srt_chunker/`)
- **Episode pipeline** — all 10 stages implemented in `_process_episode()` (service.py)
- **Queue readiness guardrails**
  - Queue/requeue is blocked when the project is not runnable
  - Episodes and project detail payloads now include structured `queue_readiness`
  - Queue blockers are surfaced on project cards and inside the episode overlay
- **TTS runtime guardrails**
  - Voice profile creation now skips latent precompute when XTTS runtime is unavailable instead of queuing dead jobs
  - Voice-test submission now fails fast with an actionable runtime error instead of leaving jobs permanently queued
  - Voice-engine health now surfaces missing XTTS dependencies and failed auto-start attempts directly in the UI
  - Voice profiles are now language-agnostic, create with only name + reference audio, and expose a one-click `Play test` action that generates a fresh inline sample without manual text entry
  - Voice-profile auto-refresh now pauses while a preview clip is actively playing, so inline playback is not interrupted by the 5-second dashboard refresh loop
  - Voice/TTS worker lifecycle is now automatic: interactive voice-profile actions wake the engine on demand and let it sleep shortly after profile/test work finishes, while pipeline `generate` work keeps the engine warm until the queued TTS batch drains
  - TTS pacing is now configured per voice profile with saved presets plus advanced controls, and both `Play test` and production narration use the same resolved config path
  - The repo TTS chunker is now the authoritative narration split layer; queued `generate` and `test_voice` jobs carry explicit chunk text plus a frozen `tts_config` snapshot, and XTTS internal `enable_text_splitting` is disabled
  - The voice-profile card action row is now fully compact: play, tuning, and delete use icon-only controls with hover tooltips, and active play-test states stay visually tight instead of expanding into large text-button blocks
- **Stage-run logging for provider stages**
  - consistency guide, scene planning, and prompt generation now preserve structured stage runs and full failure details
- **Template/settings reads**
  - template and settings reads are now side-effect free; reads no longer upsert template rows
- **Interaction responsiveness**
  - route changes and episode overlay opens now render immediately with explicit loading states instead of waiting for the full refresh cycle
  - dashboard refreshes now reuse short-lived frontend cache windows for health/settings metadata and skip legacy board fetches unless the board view is active
  - CLI provider probe calls are now cached briefly inside `CliRunner`, avoiding repeated `codex`/`claude` subprocess checks on every click
- **AI agent configs** (`config/agents/`) — scene planning, visual bible, video/image prompts (Claude + Codex)
- **Test suite** — 124 tests passing (chunking, cli_runner, translation, tts, video_pipeline, API/service coverage)

### What's Being Worked On
See `IMPLEMENTATION_PLAN.md` and `IMPLEMENTATION_CHECKLIST.md` for the 10-phase plan.

**Currently:** Phases 1-10 are complete, the 2026-03-26 workflow repair is complete, the Drawbridge feedback pass is complete, per-voice TTS pacing control is shipped, the Translation Profiles flow now combines OpenAI API discovery with compact summary cards that open the existing editor modal on click, the global refresh/theme controls now live in the sidebar instead of the topbar, the niche-project `Language setup` / `Provider setup` controls now use explicit button-driven disclosures that preserve open state across rerenders while pausing auto-refresh during active config-field focus, and the episode start path now uses explicit workflow wording with icon-only actions plus inline overlay feedback that stays correct through quick backend failures. The Tool 1 pipeline is aligned with the intended project-board-first episode workflow and long-form narration now uses explicit app-side TTS chunking plus per-profile pacing presets.

### What Is Still Fragile
- No dedicated frontend test harness yet; browser verification is still manual/smoke based
- Intermittent `/api/board` 404 noise appeared in local smoke logs, but no current source call was found in `tool1_dashboard/ui/app.js`
- Live translation-model discovery depends on a valid OpenAI API key; local browser smoke still uses a mocked discovery route when a real key is unavailable
- Live workflow smoke can fail immediately when Claude quota is exhausted; on 2026-03-27 the provider returned `Claude limit reached. You've hit your limit · resets Mar 28, 5pm (America/Sao_Paulo)`, and the frontend now surfaces that inline correctly but cannot resolve the quota issue itself
- XTTS runtime availability still matters operationally, but the UI no longer asks the user to manually start or restart the worker
- Fresh Windows environments still need the XTTS runtime installed manually; Coqui TTS may require Microsoft C++ Build Tools before voice cloning can work

### Git State
- Branch: `codex/episode-start-ux-cleanup` (active)
- Responsiveness/latency fixes for route changes, overlay opens, repeated provider health probes, and the latest Drawbridge tooltip polish all live in codex feature branches
- Remote: `https://github.com/alesalesmota/tool-1-srt-timeline-json-prompt-list.git`

## Architecture Decisions

| Decision | Why | Date |
|----------|-----|------|
| TTS only, no human audio upload | All narration generated via TTS for full automation | 2026-03-24 |
| One master language defines scene structure | Enables shared assets across all languages | 2026-03-24 |
| Duration mismatch MVP: images stretch, videos hold last frame | Keep MVP simple | 2026-03-24 |
| Pre-configured voice/translation profiles per Niche Project | Minimize per-video setup | 2026-03-24 |
| Sequential processing (no parallel TTS/translation) | Local machine constraints, GPU-bound TTS | 2026-03-24 |
| Consistency guide per-episode (not per-niche) | More flexibility per episode | 2026-03-25 |
| Remove all legacy code (Jobs, Projects/Builds) | Episodes is the final model, legacy is ~4000 lines of dead weight | 2026-03-25 |
| Archive outdated docs instead of deleting | Preserves reference material | 2026-03-25 |
| Project board is the primary workflow surface | The user works project-first, not from a global board | 2026-03-26 |
| Creating an episode only creates a Draft card | Queueing must be explicit and user-controlled | 2026-03-26 |
| Queue/requeue is blocked by server-side readiness checks | Missing voices, translations, provider auth, or languages must fail fast before work starts | 2026-03-26 |
| Provider failures stay on the failed stage with full logs | Failures must be actionable; no silent Claude->Codex fallback | 2026-03-26 |
| Template/settings reads are side-effect free | GET requests should not mutate template state | 2026-03-26 |
| Voice profiles are language-agnostic | XTTS profiles can be reused across languages, so the UI should not force or validate a language tag | 2026-03-26 |
| Voice engine lifecycle is automatic and demand-driven | Users should trigger voice actions, not manage worker processes; interactive work can cool down quickly while pipeline TTS stays warm until the queue drains | 2026-03-26 |
| Narration pacing is tuned per voice profile and snapped into each TTS job | Different voices need different stability/variation bands, and queued jobs must stay reproducible even if the profile is edited later | 2026-03-26 |
| App-side TTS chunking is authoritative; XTTS internal splitting stays off | Long-form narration must be resumable and predictable, so the repo controls chunk boundaries instead of leaving them to model-side splitting | 2026-03-26 |
| Translation Profiles use a dedicated provider-mode setup flow | Translation execution currently runs through API providers, so the setup UI must distinguish runnable OpenAI API profiles from future CLI preview modes instead of reusing the stage-provider catalog | 2026-03-26 |

## User Observations & Insights

- **2026-03-26**: The project page should be the real workspace: project Kanban first, not a flat episode list and not a global board
- **2026-03-26**: Adding an episode should leave it in Draft; queueing must be explicit instead of automatic
- **2026-03-26**: The primary project CTA should say `Create episode`; the Draft state is secondary detail and should be explained in helper copy instead of the main button label
- **2026-03-26**: In the Draft column, the add action can be just a `+`; the explanation should appear on hover instead of taking permanent header space
- **2026-03-26**: Column explanations should appear when hovering the column title instead of staying visible and occupying board space
- **2026-03-26**: Target-language selection in the create-project modal should be a searchable picker that adds languages into a list below, not a large checkbox grid
- **2026-03-26**: Episode details should open as an overlay on top of the project board, not force a full navigation away from the workflow
- **2026-03-26**: Queueing and requeueing should be blocked when setup is incomplete (missing voice profiles, translation profiles, provider login, or languages)
- **2026-03-26**: Provider failures must remain explicit and controllable; do not add automatic Claude->Codex fallback
- **2026-03-26**: App feedback feels too slow; clicking pages or any action should respond immediately instead of feeling delayed by background refresh work
- **2026-03-26**: Voice clone/test feedback must stay inside each voice-profile card; the global worker badge alone is too abstract, `stale` is unclear copy, and the user needs an inline way to hear the generated preview clip
- **2026-03-26**: Voice profile creation should only ask for a name and an audio file; language should not be required because these XTTS voices can handle multiple languages
- **2026-03-26**: The voice-profile card should be much simpler, show less technical information, and use one click to generate a fresh test sample instead of asking for manual test text
- **2026-03-26**: A manual `Start` button does not make sense for voice cloning/TTS; the app should start and stop the voice engine intelligently based on actual usage
- **2026-03-26**: `Needs restart` is misleading copy when the user is actively inside the app; normal sleeping/offline engine state should not be presented as a user task
- **2026-03-26**: Voice rhythm can vary between generations, but production narration needs tighter pacing guardrails; very slow takes feel unreal and retries become too expensive on long workflows
- **2026-03-26**: Automatic quality retries are not desirable for long-form narration because they add too much runtime; the system should keep natural variation but constrain it to a believable rhythm band and expose manual tuning when presets are not enough
- **2026-03-26**: Long-form narration should not rely on XTTS internal text splitting; explicit app-side TTS chunking should stay in control so resumability and pacing are predictable, and voice tuning should live on each voice profile rather than in global settings
- **2026-03-26**: The voice-profile play-testing controls should stay minimal across all states; play, tuning, and delete should read as compact icon actions with hover explanations instead of bulky text buttons
- **2026-03-26**: Translation profile setup needs a clear distinction between CLI use and API use; if an API key is pasted the app should recognize it, show the available models, and let the user sort/filter them with simple relative cost/speed guidance shown on hover
- **2026-03-27**: Project setup disclosures cannot auto-close during background refresh; the user must be able to open `Language setup`, interact with dropdowns, and keep working without the page collapsing the section underneath them
- **2026-03-27**: Translation-profile cards should stay compact in the default view; the page should not repeat `Translation Profiles`, the card should only show language/provider/model/readiness, and deeper details should appear only after clicking the card
- **2026-03-27**: The two global shell controls in the top-right corner can live in the lateral menu instead; the topbar should stay focused on the current page title and notices
- **2026-03-27**: `Queue` is unclear on the episode workflow surface; the action should say `Start workflow` / `Restart workflow` / `Run again`, and the controls should stay minimal icon buttons with hover explanations instead of large permanent text buttons
- **2026-03-25**: Lost 10+ phase plan between conversations → created IMPLEMENTATION_PLAN.md + IMPLEMENTATION_CHECKLIST.md in repo + updated CLAUDE.md behavior to always persist plans
- **2026-03-25**: Standalone tools (TRADUTOR, TTS, SRT chunker, Whisper UI) all duplicated integrated modules → deleted
- **2026-03-24**: Niche Project hierarchy — each niche has pre-configured languages, voice profiles, translation profiles
- **2026-03-24**: Future: multiple Niche Projects (Religion, Sports, etc.)

## Future Improvements

- Add automated browser regression coverage for the project board and episode overlay workflow
- Trace and eliminate the stray `/api/board` 404 log source if it reappears in future smoke runs
- Add richer provider health diagnostics so readiness can distinguish login, quota, and binary availability more precisely
- Expand Translation Profiles beyond OpenAI once the CLI execution path and persistence contract for `Codex CLI` / `Claude Code CLI` are ready
- Add chunk-level narration diagnostics so long-form TTS can report pacing anomalies and per-chunk timings without introducing automatic retries
- Add a clearer “production safe” vs “more expressive” explanation in the tuning modal so preset choice is more obvious before users touch the advanced controls
- Consider inline stage-run diffing/retry tools in the episode overlay once the current workflow remains stable

## Phase Plan

**See `IMPLEMENTATION_PLAN.md` for full details and `IMPLEMENTATION_CHECKLIST.md` for progress.**

| Phase | Goal | Status |
|-------|------|--------|
| Pre-Implementation | Continuity setup (plan docs, checklist, CLAUDE.md) | DONE |
| Phase 1 | Cleanup & Git Hygiene | DONE |
| Phase 2 | Database Consolidation | DONE |
| Phase 3 | Service Layer — Remove Legacy | DONE |
| Phase 4 | API Layer Consolidation | DONE |
| Phase 5 | Frontend — Remove Legacy Views | DONE |
| Phase 6 | Episode Pipeline Board Enhancement | DONE |
| Phase 7 | Niche Project Detail Enhancement | DONE |
| Phase 8 | TTS & Translation Polish | DONE |
| Phase 9 | Review & Export Phase | DONE |
| Phase 10 | Final Cleanup & Documentation | DONE |

## Change Log

| Date | What Changed |
|------|-------------|
| 2026-03-27 | **Drawbridge episode start UX cleanup complete**: renamed the queue/requeue surface into explicit workflow language, replaced board and overlay start buttons with compact icon-only actions plus hover explanations, made readiness panels state-aware, added optimistic inline overlay feedback, and reconciled that feedback against later refreshes so fast provider failures replace stale success copy with `Workflow failed in Consistency Guide.`. Verified with `node --check tool1_dashboard/ui/app.js`, `python -m unittest discover -s tests -v` (`124` passing), and Playwright smoke on seeded ready/blocked project boards plus a mobile-width overlay pass. The live restart smoke hit a real provider limitation (`Claude limit reached. You've hit your limit · resets Mar 28, 5pm (America/Sao_Paulo)`), which the UI now surfaces correctly inline. |
| 2026-03-27 | **Drawbridge sidebar utility relocation complete**: moved the global `Refresh data` and theme-toggle controls from the top-right topbar into the left sidebar as compact quick-action buttons, so the header now shows only the active page title plus notices while the shell actions stay in the lateral menu. Verified with Playwright smoke on `http://127.0.0.1:8021/#/translation-profiles`, confirming the topbar no longer contains those controls and the sidebar exposes both actions in collapsed mode. |
| 2026-03-27 | **Drawbridge translation-profile card simplification complete**: removed the repeated translation-profile section copy and long helper text from the page, rebuilt the default cards into compact summary buttons that show only name/provider/model/readiness, and reused the existing translation-profile editor modal as the details surface on click while keeping edit/delete actions separate. Verified with `node --check tool1_dashboard/ui/app.js` and Playwright smoke on `http://127.0.0.1:8020/#/translation-profiles`, including summary-click modal open, delete-dialog isolation, and mobile-width snapshot checks. |
| 2026-03-27 | **Drawbridge project-config disclosure stabilization complete**: replaced the niche-project config panels' native `<details>` behavior with explicit button-driven disclosure state in the frontend, kept `Language setup` and `Provider setup` stable across rerenders, and paused the 5-second auto-refresh loop while project-config controls are focused so dropdown work is not interrupted mid-edit. Verified with `node --check tool1_dashboard/ui/app.js`, `python -m unittest discover -s tests -v` (`124` passing), and Playwright smoke on `http://127.0.0.1:8020/#/niche-projects/niche-20260326-133703-religi-o`, confirming both panels stay open across >10 seconds of background refresh, remain stable while selects are focused, and toggle correctly via keyboard `Space` / `Enter` with `aria-expanded` updates. |
| 2026-03-26 | **Drawbridge translation-profile setup rework complete**: split Translation Profiles off the shared stage-provider catalog, added OpenAI model discovery plus sanitized API payloads, rebuilt the modal into a provider-mode create/edit flow with searchable/sortable discovered models and saved-key masking, and exposed preview-only `Codex CLI` / `Claude Code CLI` tabs with disabled save. Verified with `node --check tool1_dashboard/ui/app.js`, `python -m unittest discover -s tests -v` (`124` passing), and browser smoke on `http://127.0.0.1:8032/#/translation-profiles` covering create, placeholder tabs, edit rediscovery, model filtering, and saving an updated model via a mocked discovery route. |
| 2026-03-26 | **Drawbridge voice-card control cleanup complete**: compacted the voice-profile action row so play, tuning, and delete are all icon-only tooltip actions, tightened title/action alignment for long names, and kept `Starting voice engine` / `Generating sample` states compact with a small status pill plus short copy instead of bulky text buttons. Verified with `node --check tool1_dashboard/ui/app.js`, `python -m unittest discover -s tests -v` (`115` passing), and browser smoke on `http://127.0.0.1:8765/#/voice-profiles` confirming the new idle and generating states. |
| 2026-03-26 | **Per-voice TTS pacing control shipped**: added saved per-profile `tts_config` presets plus advanced tuning, exposed a compact `Tuning` modal on voice-profile cards, made the repo TTS chunker the single authoritative split layer for both `Play test` and production narration, disabled XTTS internal text splitting, and snapshotted the resolved tuning into queued TTS jobs so live jobs are stable across later profile edits. Verified with `node --check tool1_dashboard/ui/app.js`, `python -m unittest discover -s tests -v` (`115` passing), and browser smoke on `http://127.0.0.1:8765/#/voice-profiles` covering preset switching, advanced-value rewriting, and `Save and play test` driving the inline `Generating sample` state. |
| 2026-03-26 | **Automatic voice-engine lifecycle shipped**: removed first-party manual worker controls from Voice Profiles and episode TTS views, stopped auto-launching XTTS at app boot, added on-demand auto-start plus stale-worker recovery, and introduced smart idle shutdown with short interactive cooldowns and longer pipeline drain cooldowns. Voice Profiles now show `Starting voice engine` inline only while a user action wakes the engine, while episode views only surface true startup/runtime failures. Verified with `node --check tool1_dashboard/ui/app.js` and `python -m unittest discover -s tests -v` (`108` passing). |
| 2026-03-26 | **Voice sample playback stabilized**: paused the Voice Profiles auto-refresh loop whenever an inline preview clip is actively playing, preventing the 5-second dashboard rerender from replacing the audio element mid-playback. Verified with `node --check tool1_dashboard/ui/app.js` and `python -m unittest discover -s tests -v` (`101` passing). |
| 2026-03-26 | **Voice profile flow simplified**: removed language from the create-profile UI, stopped filtering/validating voice profiles by language in project assignment, moved default sample text generation to the backend, and rebuilt the voice-profile card around a one-click `Play test` action with inline fresh-sample playback. Verified with `node --check tool1_dashboard/ui/app.js`, `python -m unittest discover -s tests -v` (`101` passing), and browser smoke confirming create modal simplification, fresh-test generation state, and project language dropdowns showing the same voice profile for every language. |
| 2026-03-26 | **TTS Runtime Fixed**: Resolved multiple environment issues preventing `TTS.api` imports and worker startup. Pinned `torch` to `2.3.1`, `transformers` to `4.39.3`, and manually installed missing binary dependencies (`bangla`, `gruut`, `spacy[ja]`, `umap-learn`). Verified worker status as `idle` and healthy on port 8020. |
| 2026-03-26 | **Drawbridge voice-profile UX pass complete**: enriched voice-profile payloads with the latest latent-precompute and voice-test job metadata, moved voice testing into an inline card form, added per-card clone/test status messaging plus preview playback/download, and replaced the raw worker `stale` label with clearer restart guidance. Verified with `node --check tool1_dashboard/ui/app.js`, `python -m unittest discover -s tests -v` (`99` passing), and browser smoke on `http://127.0.0.1:8765/#/voice-profiles` confirming the inline test form, the new worker/card copy, and only the existing `favicon.ico` 404 in the console. |
| 2026-03-26 | **Drawbridge hover affordance polish complete**: collapsed the Draft-column add CTA into an icon-only `+` button with a hover tooltip, and moved column guidance copy into hover tooltips on the column titles so the kanban headers stay compact. Verified with `node --check tool1_dashboard/ui/app.js` plus browser smoke on `http://127.0.0.1:8765/#/niche-projects/niche-20260325-215207-audit-project`, including screenshots for the Draft `+` tooltip and the Draft-column title tooltip. |
| 2026-03-26 | **Drawbridge UI feedback pass complete**: renamed the project-board episode CTA and the create-episode modal copy from `Create draft` to `Create episode` while keeping the Draft-only helper text, and replaced the create-project target-language checkbox wall with a searchable picker that adds selected languages into removable pills below the input. Verified with `node --check tool1_dashboard/ui/app.js`, `python -m unittest discover -s tests -v` (`98` passing), and browser smoke on `http://127.0.0.1:8031` covering the project-board CTA plus the new create-project language picker flow. |
| 2026-03-26 | **Interaction latency pass complete**: route changes and episode overlay opens now paint an immediate loading state before background data hydration finishes, refreshes no longer pull every heavyweight endpoint on every interaction, and provider health probes are cached briefly inside `CliRunner` to avoid repeated CLI subprocess checks. Verified with `node --check tool1_dashboard/ui/app.js`, the full `python -m unittest discover -s tests -v` suite (`98` passing), and browser smoke showing the episode overlay loading shell appears instantly on click before the full detail payload arrives. |
| 2026-03-26 | Added TTS runtime preflight and fail-fast UX: the worker health endpoint now reports missing XTTS dependencies, voice profile creation no longer queues dead latent jobs when the runtime is missing, and voice tests return an actionable error instead of sitting in `queued` forever. Verified with targeted TTS tests and an API smoke call returning `503` plus the startup error payload. |
| 2026-03-26 | **Project-Scoped Workflow Repair complete**: the project page is now the primary Kanban surface, new episodes stay in Draft until explicitly queued, episode details open as an overlay, queue/requeue is blocked by structured readiness checks, provider-stage failures preserve full actionable logs, and template/settings reads no longer mutate template state. Verified with 93 passing tests, `node --check tool1_dashboard/ui/app.js`, and browser smoke covering project board, draft creation, overlay routing, and blocked queue UI. |
| 2026-03-26 | **Frontend UI Overhaul Complete**: Executed an 8-phase densification and cleanup of the dashboard. Replaced heavy \`.detail-section\` wrappers with clean \`.surface\` grids. Added collapsible icon sidebar. Widen Kanban columns. Stripped redundant "eyebrows" and helper text. Added tactile micro-animations to cards and buttons. Merged Settings and Niche Project configuration cards into tighter grids. |
| 2026-03-25 | Phase 10 complete: Final Cleanup & Documentation — verified agent configs and dependencies, removed all remaining references to legacy architecture, all tests pass. Tool 1 is fully transitioned to the episode-first pipeline. |
| 2026-03-25 | Phase 9 complete: Review & Export Phase — timeline editor, consistency guide editor, prompt list editor, per-language timeline read-only view, and fully wired UI handlers for saving review data and finalizing/downloading export zip. |
| 2026-03-25 | Phase 8 complete: TTS & translation polish — per-language retry (translation/TTS), translation preview (side-by-side), TTS worker health indicator, TTS job progress in episode detail, retry buttons on failed languages. 87 tests passing. |
| 2026-03-25 | Phase 7 complete: niche project detail enhancement — stats bar, inline language config with voice/translation profile dropdowns, AI provider/model config, enhanced episode cards with per-language mini-dots, batch operations (queue drafts, re-run failed), added missing `/api/target-languages` endpoint. 87 tests passing. |
| 2026-03-25 | Phase 6 complete: enhanced Episode Pipeline Board with elapsed timers, progress bars, quick actions (Queue, Delete), output file previews, and expandable stage run details with stdout/error logs. Added `/api/episodes/{id}/files` endpoint. |
| 2026-03-25 | Phase 5 complete: removed ~2100 lines of legacy frontend code from app.js (3854→1717 lines). Removed legacy sidebar items, routing, render functions, state, event handlers. Default route changed to pipeline-board. 87 tests pass, 0 JS errors |
| 2026-03-25 | Phases 2-4 complete: removed all legacy code (jobs/projects/builds). database.py rewritten with niche_projects table, service.py reduced from 4500→1737 lines, app.py from 849→496 lines, 87 tests passing |
| 2026-03-25 | Phase 1 complete: deleted 4 standalone tools, archived outdated docs, committed all uncommitted code, 119 tests passing |
| 2026-03-25 | Reconstructed 10-phase implementation plan (saved to IMPLEMENTATION_PLAN.md) |
| 2026-03-25 | Created PROJECT_REGISTRY.md for cross-conversation continuity |
| Pre-2026-03-25 | Built multilingual episode pipeline, translation module, TTS module, UI |
