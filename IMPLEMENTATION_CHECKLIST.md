# Implementation Checklist — Assembly Continuation Flow

Companion to `IMPLEMENTATION_PLAN.md`. Tick each box as the task is completed and verified. **Tags:** `[BACKEND]` = Python only, `[FRONTEND]` = JS/CSS only, `[FRONTEND-DESIGN]` = visual UI work — activate the design skill before starting.

Status on 2026-04-08: implementation complete for Phases 1-7. Episode `205` timeline artifacts are repaired and stale non-completed render jobs were pruned. Manual live browser/render verification is still pending.

## Phase 8 — Fail-stop multilingual translation/TTS safety

- [x] **Task 8.1 [BACKEND]** — Make translation fail-stop for mixed per-language outcomes (`tool1_dashboard/service.py`)
  - Verify: any required non-master translation ending failed/skipped/missing-assets blocks the episode in `translation`.
- [x] **Task 8.2 [BACKEND]** — Remove non-master TTS fallback to the master script and block batch submission on invalid translation inputs (`tool1_dashboard/service.py`)
  - Verify: missing translated scripts raise before any TTS job is submitted; non-master payload text never falls back to the master English script.
- [x] **Task 8.3 [BACKEND]** — Preserve translation error feedback during downstream TTS churn and paused-TTS recovery (`tool1_dashboard/service.py`, `tool1_dashboard/database.py`)
  - Verify: requeues/running-state transitions no longer clear translation errors; paused episode recovery cancels invalid TTS work and fails back to `translation`.
- [x] **Task 8.4 [BACKEND]** — Add structured translation preview/report support (`tool1_dashboard/service.py`)
  - Verify: preview returns `error_message` plus `translation_report`; `translation_log_{lang}.json` stays an array.
- [x] **Task 8.5 [FRONTEND]** — Surface preserved translation errors in the preview modal (`tool1_dashboard/ui/app.js`)
  - Verify: failed-language preview shows the error notice above the existing chunk log.
- [x] **Task 8.6 [BACKEND]** — Regression coverage for fail-stop translation, TTS fallback prevention, error persistence, and paused-TTS repair (`tests/test_video_pipeline.py`)
  - Verify: focused regressions pass and the full `tests/test_video_pipeline.py -q` file is green.

## Phase 9 — Translation recovery UX + LM Studio translation profiles

- [x] **Task 9.1 [BACKEND]** — Add OpenAI-compatible translation profile persistence with `base_url` (`tool1_dashboard/database.py`, `tool1_dashboard/service.py`, `tool1_dashboard/app.py`, `tool1_dashboard/translation_profiles.py`)
  - Verify: profile CRUD persists `base_url`; hosted OpenAI still defaults to the official API base URL.
- [x] **Task 9.2 [BACKEND]** — Route model discovery and translation calls through provider-aware OpenAI-compatible endpoints (`tool1_dashboard/service.py`, `tool1_dashboard/translation/adapter.py`)
  - Verify: hosted OpenAI discovery still works; local provider discovery handles server-down errors cleanly and preserves manual model entry.
- [x] **Task 9.3 [BACKEND]** — Add reviewer defaults and provider/model plumbing (`tool1_dashboard/config.py`, `tool1_dashboard/service.py`, `tool1_dashboard/app.py`, `tool1_dashboard/translation/service.py`)
  - Verify: settings expose `translation_reviewer_provider` + `translation_reviewer_model`; default is `openai` + `gpt-4.1-mini`.
- [x] **Task 9.4 [BACKEND]** — Inject sensitive-term guidance and normalized quality categories (`tool1_dashboard/translation/prompts.py`, `tool1_dashboard/translation/quality.py`, `tool1_dashboard/translation/service.py`)
  - Verify: prompts include `SENSITIVE TERMS / DO NOT RENAME`; reports expose `error_summary`, `error_categories`, `review_scores`, and `review_passed`.
- [x] **Task 9.5 [FRONTEND-DESIGN]** — Rework translation preview into summary-first progressive disclosure (`tool1_dashboard/ui/app.js`, `tool1_dashboard/ui/app.css`)
  - Verify: preview modal shows short summary, issue bullets, score badges, collapsed raw reviewer findings, and chunk log below.
- [x] **Task 9.6 [FRONTEND-DESIGN]** — Replace the OpenAI-only translation profile editor with hosted OpenAI + LM Studio/OpenAI-compatible setup (`tool1_dashboard/ui/app.js`, `tool1_dashboard/ui/app.css`)
  - Verify: LM Studio tab shows base URL + optional key + manual model field; hosted OpenAI still requires live model discovery before save.
- [x] **Task 9.7 [FRONTEND]** — Show simplified translation failure summaries in episode language rows (`tool1_dashboard/service.py`, `tool1_dashboard/ui/app.js`)
  - Verify: episode overlay error column prefers `error_summary` instead of raw technical text.
- [x] **Task 9.8 [BACKEND]** — Focused regression coverage for provider-aware discovery, report enrichment, and prompt/quality changes (`tests/test_translation.py`, `tests/test_video_pipeline.py`)
  - Verify: `python -m pytest tests/test_translation.py tests/test_video_pipeline.py -q` passes.
- [ ] **Task 9.9 [MANUAL]** — Live LM Studio smoke
  - Verify: with LM Studio server running locally, create an OpenAI-compatible profile, discover models from `/v1/models`, save it, and confirm the UI handles both connected and server-down states clearly.

## Phase 1 — Backend safety net & helpers

- [x] **Task 1.1 [BACKEND]** — Guard `queue_episode` against assembly stages (`tool1_dashboard/service.py` ~line 3553)
  - Verify: `POST /api/episodes/{id}/queue` against an `asset_upload` episode returns HTTP 400; zero rows deleted from `stage_runs`.
- [x] **Task 1.2 [BACKEND]** — Add `assembly_progress` to episode payload (`tool1_dashboard/service.py` — episode hydration path)
  - Verify: `GET /api/episodes` includes `assembly_progress` for assembly-stage episodes only.
- [x] **Task 1.3 [BACKEND]** — Helper `next_assembly_stage(current)` (`tool1_dashboard/service.py` near `_assembly_stage_sequence`)
  - Verify: returns `"assembly_validation"` for `"asset_upload"`, `None` for `"final_review"`.

## Phase 2 — JS constants & predicates

- [x] **Task 2.1 [FRONTEND]** — Reuse `ASSEMBLY_STAGE_IDS` + add assembly label handling (`tool1_dashboard/ui/app.js` near `EPISODE_RUNNABLE_STAGE_IDS`)
  - Verify: `stageLabel("asset_upload") === "Asset Upload"` from console.
- [x] **Task 2.2 [FRONTEND]** — `isEpisodeInAssemblyStage(episode)` predicate (`tool1_dashboard/ui/app.js` near `isWorkflowActiveStatus`)
  - Verify: returns true for an episode mock with `current_stage="asset_upload"`.
- [x] **Task 2.3 [FRONTEND]** — `episodeQueueStartStage` early-return for assembly mode (`tool1_dashboard/ui/app.js:725-736`)
  - Verify: `pauseRequestedCopy` for a paused asset_upload episode reads "Asset Upload", not "Consistency Guide".
- [x] **Task 2.4 [FRONTEND]** — Extend `stageActivityLabel` with assembly entries (`tool1_dashboard/ui/app.js:758-774`)
  - Verify: card status badge for asset_upload reads "Upload scene assets".

## Phase 3 — Card and control-panel rewiring  ⚠️ activate UI/design skill

- [x] **Task 3.1 [FRONTEND-DESIGN]** — Branch `renderEpisodeWorkflowControlPanel` on assembly mode (`tool1_dashboard/ui/app.js:2930-2993`)
  - Verify: paused asset_upload card shows no "Run from step" dropdown and no `data-queue-episode` button.
- [x] **Task 3.2 [FRONTEND-DESIGN]** — New `renderEpisodeAssemblyControlPanel(episode, { surface })` (`tool1_dashboard/ui/app.js`)
  - Verify: 3/5 uploaded scene state shows correct progress, disabled Continue button, enabled "Open assembly workspace".
- [x] **Task 3.3 [FRONTEND-DESIGN]** — Card body queue-button branching (`tool1_dashboard/ui/app.js` around `renderEpisodeCard`)
  - Verify: board card for paused asset_upload shows tiny Continue button instead of "Resume from step".

## Phase 4 — Click delegation & fetch wiring

- [x] **Task 4.1 [FRONTEND]** — `data-advance-assembly` click handler (`tool1_dashboard/ui/app.js:6991-7072` block)
  - Verify: clicking Continue with all assets uploaded flips stage to `assembly_validation`; backend errors surface as toasts.
- [x] **Task 4.2 [FRONTEND]** — `data-open-assembly` click handler + `openAssemblyWorkspace(epId)` helper
  - Verify: "Open assembly workspace" opens the bulk-upload modal with no state change.
- [x] **Task 4.3 [FRONTEND]** — Auto-offer advance after successful validation (`tool1_dashboard/ui/app.js` validation handler + panel)
  - Verify: post-validation, "Move to Assembly Validation stage" / "Move to Video Render" button appears; one click advances.

## Phase 5 — Polish & regression guards

- [x] **Task 5.1 [FRONTEND-DESIGN]** — Terminal `final_review` rendering (`renderEpisodeAssemblyControlPanel`)
  - Verify: episode forced to `final_review` shows the disabled chip and no Continue button.
- [x] **Task 5.2 [FRONTEND-DESIGN]** — Reassurance notice on the asset_upload variant
  - Verify: "Uploaded assets are preserved across refreshes and workflow actions." renders as a low-tone helper.
- [x] **Task 5.3 [BACKEND]** — Smoke tests (`tool1_dashboard/tests/...`)
  - Verify: `pytest` is green; the new tests cover queue guard + advance preconditions.

## Phase 6 — Assembly workspace hierarchy follow-up  ⚠️ activate UI/design skill

- [x] **Task 6.1 [FRONTEND-DESIGN]** — Reorder modal/detail layout around the assembly workspace (`tool1_dashboard/ui/app.js`)
  - Verify: assembly stages render the workspace directly under the episode header/control panel, above generic pipeline progress and telemetry.
- [x] **Task 6.2 [FRONTEND-DESIGN]** — Add exact assembly stage naming + current-step strip (`tool1_dashboard/ui/app.js`, `tool1_dashboard/ui/app.css`)
  - Verify: the UI shows `Asset Upload`, `Assembly Validation`, `Video Render`, and `Final Review` consistently in the stepper/current-step panel.
- [x] **Task 6.3 [FRONTEND-DESIGN]** — Convert `loadAssemblyUI()` into a stable workspace composer (`tool1_dashboard/ui/app.js`)
  - Verify: advancing stages no longer removes prior assembly context; validation stays visible during render/review and scene/assets stay visible read-only after `asset_upload`.
- [x] **Task 6.4 [FRONTEND-DESIGN]** — Clarify render/review copy and render entrypoints (`tool1_dashboard/ui/app.js`)
  - Verify: `video_render` explains localized final video rendering clearly and shows per-language render actions even before any job exists.
- [x] **Task 6.5 [FRONTEND-DESIGN]** — Minimal styling pass for the new hierarchy (`tool1_dashboard/ui/app.css`)
  - Verify: the stepper/current-step panel/reference sections read as one ordered workspace without introducing a new design system.

## Phase 7 — Timeline overlap hardening and episode 205 recovery

- [x] **Task 7.1 [BACKEND]** — Add shared overlap normalization/validation in `tool1_dashboard/validators.py`
  - Verify: repairable overlaps (`<= 0.25s`) are snapped forward and reported via `overlap_adjustments`; larger overlaps remain invalid.
- [x] **Task 7.2 [BACKEND]** — Apply the shared validator to `scene_planning`, `timeline_mapping`, and review-data saves (`tool1_dashboard/service.py`)
  - Verify: invalid scene-planning drafts fail before persisting `timeline_draft.json`; broken per-language mappings mark that language failed; review saves reject larger overlaps without partial writes.
- [x] **Task 7.3 [FRONTEND-DESIGN]** — Surface `Timeline Validation` above the review timeline editor (`tool1_dashboard/ui/app.js`, `tool1_dashboard/ui/app.css`)
  - Verify: review shows pass/fail state, overlap repair count, blocking errors, and the note that per-language timing is generated later.
- [x] **Task 7.4 [BACKEND]** — Repair episode `205` through the production normalization path and rerun localized timelines
  - Verify: `timeline_draft.json` and `timeline_en.json` no longer overlap; `timeline_validation.json` reports `overlap_adjustments = 2`; assembly validation passes for all configured languages.
- [x] **Task 7.5 [BACKEND]** — Prune stale non-completed render jobs for `205` after confirming no live render process exists
  - Verify: queued/failed/stale-rendering rows are removed, stale assembly `temp/` folders are cleaned, and the episode remains in `video_render`.
- [ ] **Task 7.6 [MANUAL]** — Run a live English smoke render from the repaired `video_render` state
  - Verify: render `en` from the app after reopening the dashboard, confirm the job completes, then resume the remaining languages.

## End-to-end verification (run after every phase, mandatory after Phase 5)

- [ ] Episode runs through to export, then "Start Video Assembly" → `asset_upload`.
- [ ] Bulk upload assets; board card shows progress + Continue (no Resume dropdown).
- [ ] Continue → `assembly_validation`; validate → Continue → `video_render`; render → Continue → `final_review`.
- [ ] In `assembly_validation`, `video_render`, and `final_review`, the current step appears first and earlier assembly context remains visible read-only below it.
- [ ] In `video_render`, the UI explains that rendering one language means generating one localized final video and exposes a direct render action before any previous render job exists.
- [ ] Manual `curl POST /api/episodes/{id}/queue` against an assembly episode returns 400 with zero `stage_runs` deletions.
- [ ] `scene_assets` table and `workspace/episodes/{episode}/assembly/shared_assets/` unchanged before vs. after every advance.
- [ ] Page refresh at every stage preserves the new buttons (state comes from server payload).
