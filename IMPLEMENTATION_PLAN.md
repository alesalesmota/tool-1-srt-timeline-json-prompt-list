# Plan: Wire the post-upload assembly continuation flow

Status on 2026-04-08: implemented in code and regression tests. A follow-up UI hierarchy pass for the assembly modal/detail flow is also implemented in code. Timeline-overlap hardening is now also implemented in code, and episode `205` has repaired timeline artifacts plus stale non-completed render jobs cleared. Manual live browser/render verification is still pending.

## Supplemental implementation note — 2026-04-09 fail-stop multilingual pipeline

Implemented in code and regression tests on 2026-04-09:

- Translation is now fail-stop for multilingual runs: any required non-master language ending `failed`, `skipped`, or without translated script assets blocks the episode in `translation`.
- Non-master TTS no longer falls back to the master English script. Missing translated scripts now block TTS submission instead of generating invalid narration.
- Paused-TTS recovery now audits upstream translation readiness, cancels invalid queued/processing TTS jobs for broken languages, and fails the episode back to `translation` with preserved error feedback.
- Translation preview now returns `error_message` plus a structured `translation_report_{lang}.json` artifact while keeping `translation_log_{lang}.json` backward-compatible as the chunk-log array.
- Verified with the full `tests/test_video_pipeline.py` suite plus `node --check tool1_dashboard/ui/app.js`.

## Context

After exporting an episode and uploading the generated images/videos through the assembly modal, the user has no clear way to move the card forward into the next assembly stages (assembly_validation → video_render → final_review). The episode card still shows the regular **"Resume from step"** dropdown, which is built only from the upstream `EPISODE_RUNNABLE_STAGES` list. Because `asset_upload` is **not** in that list, clicking that button silently sends the card back to `translation` (the closest runnable fallback) and triggers `delete_stage_runs_for(...)`, which **looks** like the user lost all their uploaded assets.

The user's actual assets are safe — they live in the `scene_assets` DB table and on disk under `workspace/episodes/{episode}/assembly/shared_assets/` — but the UX is broken and dangerous: a wrong click can erase upstream stage runs and re-queue the entire pipeline. The user needs a clear, lossless **"Continue to next assembly stage"** action on the card itself, plus a way to re-enter the assembly workspace without confusion. The backend already supports this via `service.advance_assembly_stage()` and `POST /api/episodes/{id}/assembly/advance` — they just aren't surfaced anywhere in the card UI.

This plan is written for another agent (Codex / Gemini / Claude) to execute end-to-end. It says **what** to change, **why**, and **how to verify** each step, without dictating exact code.

## Root cause (file:line)

- `tool1_dashboard/config.py:58-68` — `EPISODE_RUNNABLE_STAGES` lists only `consistency_guide` … `timeline_mapping`. The four assembly stages (`asset_upload`, `assembly_validation`, `video_render`, `final_review`) are intentionally excluded because they are managed by a different code path.
- `tool1_dashboard/ui/app.js:725-736` — `episodeQueueStartStage(episode)` falls through to `"consistency_guide"` (or to a stale `queued_from_stage`) when `current_stage` is `asset_upload`, because the function only knows about `EPISODE_RUNNABLE_STAGE_IDS`. This is why clicking Resume lands on `translation`.
- `tool1_dashboard/ui/app.js:2911-2915` — `workflowStageOptions(selectedStage)` builds the dropdown strictly from `EPISODE_RUNNABLE_STAGE_IDS`, so assembly stages can never appear.
- `tool1_dashboard/ui/app.js:2930-2993` — `renderEpisodeWorkflowControlPanel()` unconditionally renders the "Run from step" dropdown + Resume button wired to `data-queue-episode`.
- `tool1_dashboard/ui/app.js:5826-5836` → `triggerEpisodeWorkflowStart()` → POST `/api/episodes/{id}/queue` → `service.queue_episode()` at `tool1_dashboard/service.py:3539-3603`. At line 3590, `delete_stage_runs_for(...)` wipes stage runs from the chosen start stage forward. **Nothing currently prevents this from running on an episode whose `current_stage` is an assembly stage.**
- `tool1_dashboard/ui/app.js:6698-6767` — `renderAssemblyValidationPanel()` shows a "Validate All Languages" button but its success path **never** calls `/api/episodes/{id}/assembly/advance`, so even passing validation leaves the episode stuck on `asset_upload`.

Backend support that already exists and must be reused:
- `tool1_dashboard/service.py:3462-3482` — `start_assembly()` (already moves `export/done` → `asset_upload/paused`).
- `tool1_dashboard/service.py:3484-3537` — `advance_assembly_stage(episode_id, target_stage)` (already enforces all preconditions: assets uploaded, validation passed, render completed).
- `tool1_dashboard/service.py:3226-3287` — `validate_assembly()`.
- `tool1_dashboard/service.py:3499` — `_assembly_shared_validation()` (cheap check that returns counts + missing scenes).
- `tool1_dashboard/app.py:551-597` — REST endpoints `/assembly/validate`, `/assembly/start`, `/assembly/advance`, `/assembly/render`.
- `tool1_dashboard/config.py:70` — `VIDEO_ASSEMBLY_STAGES = ("asset_upload", "assembly_validation", "video_render")`.

## Design overview

Teach the episode-card workflow panel to recognize assembly mode as a first-class state. When `episode.current_stage` is in `VIDEO_ASSEMBLY_STAGES` or equals `"final_review"`, render an **Assembly Continuation** panel instead of the runnable-stages dropdown. That panel surfaces upload progress, an "Open assembly workspace" shortcut, and a single primary **"Continue to {next stage}"** button wired to a brand-new `data-advance-assembly` click handler that POSTs `/api/episodes/{id}/assembly/advance`. Extend the existing validation panel so a successful validation also offers a one-click advance. Add a defensive backend guard so `queue_episode` refuses any episode currently sitting in an assembly stage. **No schema changes. No asset deletion. `EPISODE_RUNNABLE_STAGES` stays untouched.**

## Backend vs Frontend split (read this before starting)

Each task below is tagged with one of:

- **[BACKEND]** — Python only (`tool1_dashboard/service.py`, `tool1_dashboard/app.py`, tests). Pure logic, DB writes, REST contract. No UI work.
- **[FRONTEND]** — JavaScript / DOM only (`tool1_dashboard/ui/app.js`, `tool1_dashboard/ui/app.css`). Touches user-facing UI elements (panels, buttons, copy, layout, enable/disable states).
- **[FRONTEND-DESIGN]** — Frontend tasks that introduce **new visible UI components** (new panels, new buttons, new layouts). The implementing agent should activate any available UI/design skill (e.g. design system review, component styling, accessibility checks) **before** writing the markup, and match the existing visual language documented in `tool1_dashboard/ui/app.css`.

**Phase-level summary:**

| Phase | Tag | Files | Notes |
|-------|-----|-------|-------|
| Phase 1 — Backend safety net & helpers | [BACKEND] | `service.py` | Add a guard, augment payload, add a helper. No UI. |
| Phase 2 — JS constants & predicates | [FRONTEND] | `app.js` | Pure additive constants and helper functions. No new visible components. |
| Phase 3 — Card and control-panel rewiring | [FRONTEND-DESIGN] | `app.js`, possibly `app.css` | New `renderEpisodeAssemblyControlPanel` component + card button variant. **Activate the design skill here.** |
| Phase 4 — Click delegation & fetch wiring | [FRONTEND] | `app.js` | Wires existing/new buttons to fetch calls. No new components. |
| Phase 5 — Polish & regression guards | Mixed | `app.js` (5.1, 5.2 — [FRONTEND-DESIGN]), `tests/` (5.3 — [BACKEND]) | Terminal state UI and reassurance copy are visual; tests are Python. |

**REST contract between backend and frontend** (do not change unless both sides agree):
- `GET /api/episodes` and `GET /api/episodes/{id}` may now return a new optional field `assembly_progress` on episodes whose `current_stage` is in `VIDEO_ASSEMBLY_STAGES + ("final_review",)`. Shape defined in Task 1.2.
- `POST /api/episodes/{id}/assembly/advance` already exists at `app.py:574-584`. Request body: `{ "target_stage": "<stage>" }` (matches `AssemblyAdvanceRequest`). The frontend must POST exactly this shape; the backend already enforces all preconditions.
- `POST /api/episodes/{id}/queue` will newly return HTTP 400 with detail `"Assembly stages must be advanced via /assembly/advance, not queued."` for assembly-stage episodes (Task 1.1). The frontend should never need to handle this in practice because the new UI will not surface the queue button on assembly cards — but a graceful error toast is still expected.

## Phase 1 — Backend safety net & helpers

### Task 1.1 [BACKEND] — Guard `queue_episode` against assembly stages
- **File:** `tool1_dashboard/service.py` near the existing `if stage not in EPISODE_RUNNABLE_STAGES` check around `service.py:3553`.
- **What:** Before that check (and before `delete_stage_runs_for` at `service.py:3590` can possibly run), raise `ValueError("Assembly stages must be advanced via /assembly/advance, not queued.")` if either `start_stage` is in `VIDEO_ASSEMBLY_STAGES + ("final_review",)` **or** if the loaded `episode["current_stage"]` is in that set.
- **Why:** Defense in depth. Even if the UI regresses, this guarantees uploaded assets and upstream stage runs can never be deleted by a mis-routed Resume click.
- **Verify:** Manually flip an episode row to `current_stage="asset_upload"`, hit `POST /api/episodes/{id}/queue` with curl — expect HTTP 400 and zero rows deleted from `stage_runs`.

### Task 1.2 [BACKEND] — Add `assembly_progress` to the episode payload
- **File:** `tool1_dashboard/service.py`. Find the method that hydrates the dict returned to the board (search for `_hydrate_episode_record` and the place where `queue_readiness` is attached). Apply the change in the same shared serializer used by `GET /api/episodes` and `GET /api/episodes/{id}`.
- **What:** When `current_stage in VIDEO_ASSEMBLY_STAGES + ("final_review",)`, attach a new dict key `assembly_progress` with:
  - `stage` — the current assembly stage
  - `next_stage` — next stage in the assembly sequence or `null` if none
  - `assets_uploaded` and `assets_total` — counts derived from `_assembly_shared_validation()` (count scenes vs. count of scenes whose `scene_id` is **not** in `missing_scenes`)
  - `all_assets_uploaded` — boolean from the same helper
  - `validation_ok` — `null` unless cheap to compute; leave as `null` to avoid running the heavy `validate_assembly()` on every list call. The UI will treat `null` as "click Validate first".
- **Why:** The card needs to render counts and decide button enable/disable without an extra roundtrip.
- **Verify:** `GET /api/episodes` for an `asset_upload` episode contains `assembly_progress`; for a `translation` episode it does not (or it is `null`).

### Task 1.3 [BACKEND] — Helper `next_assembly_stage(current)`
- **File:** `tool1_dashboard/service.py` near `_assembly_stage_sequence()` at `service.py:3164-3166`.
- **What:** Tiny pure helper returning the next stage name in the assembly sequence, or `None` for `final_review`. Use it inside Task 1.2 and inside `advance_assembly_stage()` (replace the inline `sequence.index(...)` math).
- **Why:** Single source of truth for the order; avoids string-magic in the UI.
- **Verify:** `next_assembly_stage("asset_upload") == "assembly_validation"`; `next_assembly_stage("final_review") is None`.

## Phase 2 — JS constants & predicates

### Task 2.1 [FRONTEND] — Introduce assembly stage constants in `app.js`
- **File:** `tool1_dashboard/ui/app.js` near the existing `EPISODE_RUNNABLE_STAGE_IDS` constant (search `EPISODE_RUNNABLE_STAGE_IDS` to locate it).
- **What:**
  - Add `EPISODE_ASSEMBLY_STAGE_IDS = ["asset_upload", "assembly_validation", "video_render", "final_review"]`.
  - Add a label map `{ asset_upload: "Asset Upload", assembly_validation: "Assembly Validation", video_render: "Video Render", final_review: "Final Review" }` and merge it into the existing `EPISODE_STAGE_LABELS` (or extend `stageLabel()` at `app.js:738-740` to fall back through it).
- **Why:** Required by every subsequent render function. Keep `EPISODE_RUNNABLE_STAGE_IDS` untouched.
- **Verify:** `stageLabel("asset_upload")` returns `"Asset Upload"` from the browser console.

### Task 2.2 [FRONTEND] — Predicate `isEpisodeInAssemblyStage(episode)`
- **File:** `tool1_dashboard/ui/app.js` near `isWorkflowActiveStatus` at `app.js:742-744`.
- **What:** Returns `true` if `episode?.current_stage` is in `EPISODE_ASSEMBLY_STAGE_IDS`. Used by the card render, the control panel, and the click delegation.
- **Why:** Single source of truth.
- **Verify:** Call against a mocked episode with `current_stage="asset_upload"` — true.

### Task 2.3 [FRONTEND] — `episodeQueueStartStage` fallback for assembly episodes
- **File:** `tool1_dashboard/ui/app.js:725-736`.
- **What:** At the very top of the function, if `isEpisodeInAssemblyStage(episode)` return `episode.current_stage` as-is. Do **not** funnel assembly stages into the dropdown; this only fixes downstream callers (`pauseRequestedCopy`, `stageActivityLabel`) that read this helper for display copy.
- **Why:** Prevents misleading copy like "The workflow will stop before Consistency Guide" on a paused asset_upload episode.
- **Verify:** `pauseRequestedCopy(episode)` for a paused asset_upload episode reads "Asset Upload", not "Consistency Guide".

### Task 2.4 [FRONTEND] — Extend `stageActivityLabel` with assembly entries
- **File:** `tool1_dashboard/ui/app.js:758-774`.
- **What:** Add entries: `asset_upload: "Upload scene assets"`, `assembly_validation: "Validating assembly"`, `video_render: "Rendering video"`, `final_review: "Awaiting final review"`.
- **Why:** Card status badges currently fall through to `Running Asset_upload` style strings.
- **Verify:** Card badge for an asset_upload episode reads "Upload scene assets".

## Phase 3 — Card and control-panel rewiring

### Task 3.1 [FRONTEND-DESIGN] — Branch `renderEpisodeWorkflowControlPanel` on assembly mode
- **File:** `tool1_dashboard/ui/app.js:2930-2993`.
- **What:** At the very top of the function, if `isEpisodeInAssemblyStage(episode)`, delegate to a new `renderEpisodeAssemblyControlPanel(episode, { surface })` and return early. The existing path for upstream episodes stays exactly as it is.
- **Why:** Keeps the change surgical — the runnable-stages dropdown is never shown for assembly cards, eliminating the misleading "Resume to translation" trap.
- **Verify:** Load the board with a paused `asset_upload` episode. Confirm there is **no** "Run from step" dropdown on the card and **no** `data-queue-episode` button.

### Task 3.2 [FRONTEND-DESIGN] — New `renderEpisodeAssemblyControlPanel(episode, { surface })`
- **File:** `tool1_dashboard/ui/app.js` (co-locate next to `renderEpisodeWorkflowControlPanel`).
- **What:** Render a `<section>` matching the existing `.project-readiness-panel.workflow-control-panel` styling but with this content:
  - **Header badge:** `Stage: {label of current_stage}` using the new label map.
  - **Progress line** sourced from `episode.assembly_progress`:
    - For `asset_upload` show `"{assets_uploaded}/{assets_total} scenes uploaded"`. Tone success when complete, neutral otherwise.
    - For `assembly_validation` show `"Run validation to verify timeline + voiceover for each language"`.
    - For `video_render` show `"Render the configured languages from the assembly workspace"`.
    - For `final_review` show `"Review the rendered videos and mark the episode done"`.
  - **Primary button** `data-advance-assembly="{episodeId}" data-target-stage="{assembly_progress.next_stage}"` labelled `"Continue to {label of next stage}"`. Button enable rules:
    - `asset_upload → assembly_validation`: enabled only when `assembly_progress.all_assets_uploaded === true`.
    - `assembly_validation → video_render`: enabled when `assembly_progress.validation_ok === true`. If `validation_ok` is `null`, render the button **disabled** with helper "Run validation first".
    - `video_render → final_review`: enabled when at least one render job is `completed` (UI can read this from existing `episode.render_jobs` / assembly section state if available; otherwise always enable and let the backend reject — `advance_assembly_stage()` already enforces this at `service.py:3517-3523`).
    - `final_review`: hide the Continue button entirely (terminal — see Task 5.1).
  - **Secondary ghost button** `data-open-assembly="{episodeId}"` labelled `"Open assembly workspace"`.
  - **Helper line:** `"Your uploaded assets are saved. Use Continue when you're ready to move to the next stage."` (only on the asset_upload variant; vary copy slightly per stage).
  - **Pause button:** preserve the existing `data-pause-episode` button only if `activeWorkflow` is true (it usually won't be on a paused assembly card).
- **Why:** Gives the user a clear, lossless forward action and an obvious way back into the upload UI.
- **Verify:** A paused `asset_upload` episode with 3/5 scenes uploaded shows "3/5 scenes uploaded", a **disabled** "Continue to Assembly Validation" button, and an enabled "Open assembly workspace" button. Uploading the remaining 2 scenes (and refreshing) enables Continue.

### Task 3.3 [FRONTEND-DESIGN] — Card body adjustments
- **File:** `tool1_dashboard/ui/app.js` around `renderEpisodeCard` at `app.js:3007-3051`.
- **What:** The existing compact `queueButton` rendered on the board card should also branch on `isEpisodeInAssemblyStage`. For assembly episodes, render a small `data-advance-assembly` button (button-tiny variant) instead of the compact queue button. Reuse the same enable rules from Task 3.2 (extract them into a helper like `assemblyContinueButtonState(episode)` so the panel and the card share the logic).
- **Why:** Without this, the board card still shows the misleading queue button even though the detail panel is fixed.
- **Verify:** Board card for a paused `asset_upload` episode shows a tiny "Continue →" or similar, not "Resume from step".

## Phase 4 — Click delegation & fetch wiring

### Task 4.1 [FRONTEND] — `data-advance-assembly` click handler
- **File:** `tool1_dashboard/ui/app.js` — extend the existing delegated click handler block that already handles `data-validate-assembly`, `data-render-lang`, `data-render-all` at `app.js:6991-7072`.
- **What:** Add a new branch matching `event.target.closest("[data-advance-assembly]")`:
  1. Read `episodeId = btn.dataset.advanceAssembly` and `targetStage = btn.dataset.targetStage`.
  2. If `!targetStage` bail out (terminal stage).
  3. Disable the button + `setNotice("Advancing to {label}…", "neutral")`.
  4. POST to `/api/episodes/${encodeURIComponent(episodeId)}/assembly/advance` with `Content-Type: application/json` body `{"target_stage": targetStage}` (matches `AssemblyAdvanceRequest` at `app.py:574-584`).
  5. On 2xx: `setNotice("Advanced to {label}", "success")` then refresh the relevant view. Use the same refresh helper invoked by the existing `data-validate-assembly` branch (likely `loadAssemblyUI(...)` and/or the board rerender). Trace what `data-start-video-assembly` does and follow the same pattern.
  6. On non-2xx: read `data.detail`, surface via `setNotice(detail, "error")`, re-enable the button.
- **Why:** Single source of truth for moving the episode through the assembly sequence.
- **Verify:** With all assets uploaded, click Continue — the card flips to `assembly_validation`, the button refreshes, no console errors.

### Task 4.2 [FRONTEND] — `data-open-assembly` click handler
- **File:** `tool1_dashboard/ui/app.js` (same delegated block).
- **What:** Locate the existing entry point that opens the assembly modal — search for `data-start-video-assembly` and trace where it ends up calling `loadAssemblyUI(...)` (around `app.js:6986`). If the modal-open logic is inline, factor it into a small helper `openAssemblyWorkspace(episodeId)` and call it from both `data-start-video-assembly` and the new `data-open-assembly` branch. **Important:** do **not** call `/assembly/start` again — the episode is already in `asset_upload` and `start_assembly()` requires `current_stage="export"`/`pipeline_status="done"` so it would 400 anyway.
- **Why:** Re-entry into the upload UI without confusion or stage churn.
- **Verify:** Click "Open assembly workspace" on a paused `asset_upload` card — the bulk-upload modal opens, no fetch errors, episode state unchanged.

### Task 4.3 [FRONTEND] — Auto-offer advance after successful validation
- **File:** `tool1_dashboard/ui/app.js` inside the existing `data-validate-assembly` handler at `app.js:6991-7009` and inside `renderAssemblyValidationPanel()` at `app.js:6698-6767`.
- **What:**
  - After validation returns successfully, inspect `normalized.shared.ok` and whether at least one language has `ok: true`.
  - If both true and `episode.current_stage === "asset_upload"`, render a prominent "Move to Assembly Validation stage" button inside the validation panel using the same `data-advance-assembly` attribute with `data-target-stage="assembly_validation"`.
  - If `episode.current_stage === "assembly_validation"` and validation passes, render "Move to Video Render" with `data-target-stage="video_render"`.
  - **Do NOT auto-fire the advance.** Require an explicit click — surprises here are dangerous.
  - Update the in-memory episode object so the card panel's `assembly_progress.validation_ok` flips to `true` and the Continue button on the card enables on the next render. Easiest path: trigger the same board refresh helper used by `data-validate-assembly` today, then `loadEpisodes()` (or whatever the existing reload path is).
- **Why:** Closes the loop: the user can validate and advance with two clicks instead of being stuck.
- **Verify:** Validate an episode with all assets present — a "Move to Assembly Validation stage" button appears in the panel; one click flips the stage; the card now offers "Continue to Video Render" (still disabled until validation re-runs at the next stage).

## Phase 5 — Polish & regression guards

### Task 5.1 [FRONTEND-DESIGN] — Terminal `final_review` rendering
- **File:** `tool1_dashboard/ui/app.js` in `renderEpisodeAssemblyControlPanel`.
- **What:** When `episode.current_stage === "final_review"` (or `assembly_progress.next_stage === null`), replace the Continue button with a disabled "Awaiting final review" chip and a helper line "Mark this episode done from the assembly workspace once you've reviewed the rendered videos." Also keep the "Open assembly workspace" button so the user can reach the videos.
- **Verify:** Force an episode to `final_review` (DB update) — no Continue button, no JS errors.

### Task 5.2 [FRONTEND-DESIGN] — Reassurance notice
- **File:** `tool1_dashboard/ui/app.js` inside `renderEpisodeAssemblyControlPanel`.
- **What:** Render a persistent helper line on the asset_upload variant: `"Uploaded assets are preserved across refreshes and workflow actions."` Style as a low-tone notice, not an error.
- **Why:** Emotional requirement — the user explicitly worried about losing progress; this should be visible right next to the action.

### Task 5.3 [BACKEND] — Smoke tests
- **Files:** existing pytest suite for `service.py` (search for `test_queue_episode` or `tests/test_service.py`).
- **What:**
  1. Test that with `current_stage="asset_upload"`, `service.queue_episode(...)` raises `ValueError` and that no rows in `stage_runs` for that episode are deleted.
  2. Test that `advance_assembly_stage("asset_upload" → "assembly_validation")` still raises when assets are missing and succeeds when they are present (regression guard for Task 1.3 helper).
  3. (Optional) Test that the new `assembly_progress` block appears on episodes whose `current_stage` is in `VIDEO_ASSEMBLY_STAGES`.
- **Verify:** `pytest tool1_dashboard/tests/...` is green.

## Critical files to modify

- `tool1_dashboard/ui/app.js`
- `tool1_dashboard/service.py`
- `tool1_dashboard/app.py` *(no changes expected — endpoints already exist; only verify request payload contract for `/assembly/advance`)*
- `tool1_dashboard/config.py` *(read-only — DO NOT add assembly stages to `EPISODE_RUNNABLE_STAGES`)*

## Reused functions / utilities (do not reinvent)

- `tool1_dashboard/service.py:3462-3482` — `start_assembly()`
- `tool1_dashboard/service.py:3484-3537` — `advance_assembly_stage()` (already enforces every precondition)
- `tool1_dashboard/service.py:3226-3287` — `validate_assembly()`
- `tool1_dashboard/service.py:3187-3208` — `_assembly_shared_validation()` (counts source for `assembly_progress`)
- `tool1_dashboard/service.py:3164-3166` — `_assembly_stage_sequence()`
- `tool1_dashboard/config.py:70` — `VIDEO_ASSEMBLY_STAGES`
- `tool1_dashboard/app.py:551-597` — existing `/assembly/validate`, `/assembly/start`, `/assembly/advance`, `/assembly/render` endpoints
- `tool1_dashboard/ui/app.js:738-740` — `stageLabel()`
- `tool1_dashboard/ui/app.js:758-774` — `stageActivityLabel()`
- `tool1_dashboard/ui/app.js:2930-2993` — `renderEpisodeWorkflowControlPanel()`
- `tool1_dashboard/ui/app.js:6698-6767` — `renderAssemblyValidationPanel()`
- `tool1_dashboard/ui/app.js:6991-7072` — delegated click handler block (pattern to follow)
- `tool1_dashboard/ui/app.js` — existing `loadAssemblyUI()` helper used by `data-start-video-assembly` (search for it; reuse for the new `data-open-assembly` branch)

## 2026-04-08 UX follow-up execution note

The original continuation fix solved the destructive rerun path, but the user still got lost inside the assembly modal/detail flow because the UI hierarchy was backwards. The implemented follow-up keeps the same backend/API contract and restructures the assembly UI around the active step:

- The assembly workspace now renders immediately after the episode header/control panel for assembly stages, above generic pipeline telemetry.
- The current-step panel now includes a visible four-step strip with exact labels: `Asset Upload`, `Assembly Validation`, `Video Render`, `Final Review`.
- Stage copy was standardized so the UI no longer mixes `Assembly Check` / `Render Progress` wording with the backend stage ids.
- `loadAssemblyUI(...)` now composes a stable workspace stack instead of replacing the whole surface per stage.
- Later stages preserve earlier context as read-only reference: validation stays visible during render/review, and the scene/assets preview stays visible beyond `asset_upload`.
- `video_render` now exposes render entrypoints even before any render job exists, so “render one language” is concretely presented as rendering one localized final video.
- `final_review` now shows finished-video playback/download first while preserving render/validation/scene context below.

## 2026-04-08 Timeline overlap hardening execution note

The render-only overlap failure on episode `205` exposed an inconsistent rule: early timeline validation treated overlapping scenes as warnings, but final render treated the same condition as fatal. The implementation now hardens that path before render:

- `tool1_dashboard/validators.py` now exposes `normalize_and_validate_timeline(...)`, which auto-repairs only small positive overlaps (`<= 0.25s`) by snapping the later scene start to the previous scene end, then re-validates with a stricter overlap error tolerance.
- `tool1_dashboard/service.py` now applies that shared validator in `scene_planning`, `timeline_mapping`, `get_review_data`, and `update_review_data`, so invalid master/per-language timelines fail earlier instead of surfacing only in `video_render`.
- `scene_planning` now persists `timeline_validation.json` even when a draft is invalid, and large overlaps block `timeline_draft.json` persistence entirely.
- The review surface now renders a compact `Timeline Validation` summary above the timeline editor with pass/fail state, blocking errors, and overlap repair count.
- Targeted regression coverage now lives in `tests/test_chunking_and_validation.py` and `tests/test_video_pipeline.py` for auto-repair, hard-fail overlaps, scene-planning persistence, timeline-mapping rejection, and review-save behavior.

Episode `205` was repaired through the production code path instead of a one-off edit:

- Re-saved the master `timeline_draft.json` through `update_review_data(...)`, which rewrote `timeline_validation.json` with `overlap_adjustments = 2`.
- Re-ran `timeline_mapping` for `de`, `en`, `es`, `fr`, and `it`, so all localized timelines reflect the repaired master timeline.
- Re-ran assembly validation and confirmed all configured languages pass again with all shared assets present.
- Cleared the stale non-completed render jobs left from the pre-repair batch (`en` failed, `de` stale rendering, `fr/it` queued) after confirming there was no live `python/pythonw/ffmpeg` render process, then cleaned stale assembly `temp/` folders.

Verification completed:

1. `python -m py_compile tool1_dashboard\\validators.py tool1_dashboard\\service.py`
2. `node --check tool1_dashboard\\ui\\app.js`
3. `python -m pytest tests\\test_chunking_and_validation.py -k "timeline_validation or merge_scene_chunks_repairs_small_overlap_before_report" -q`
4. `python -m pytest tests\\test_video_pipeline.py -k "scene_planning_repairs_small_overlap_and_persists_validation or scene_planning_rejects_large_overlap_and_persists_invalid_report or retry_single_timeline_mapping_marks_language_failed_when_master_timeline_is_invalid or update_review_data_repairs_small_overlap_and_persists_validation or update_review_data_rejects_large_overlap_without_partial_persist" -q`

Still pending:

- A live English smoke render from the repaired `video_render` state. The stale batch is gone, but no dashboard render process was running in this session, so the next safe step is to open the app and render `en` first from the UI.

## End-to-end verification

1. Seed an episode and run through to export. Click "Start Video Assembly" — episode transitions to `asset_upload`.
2. Bulk-upload all scene assets via the existing modal. Close the modal.
3. **Board card:** confirm the card shows `"Asset Upload — N/N uploaded"`, no "Run from step" dropdown, and a tiny "Continue" button (enabled because all assets are uploaded).
4. **Detail panel:** confirm the new assembly continuation panel shows: stage badge, progress line, enabled Continue button, ghost "Open assembly workspace" button, and the reassurance notice.
5. Click "Open assembly workspace" — modal opens; close it. Episode state unchanged.
6. Click "Continue to Assembly Validation". Card flips to `assembly_validation`. The Continue button on the new panel now reads "Continue to Video Render" but is disabled until validation runs.
7. Open the assembly workspace, click "Validate All Languages". On success, a "Move to Video Render" button appears in the validation panel; clicking it advances the stage. The card panel's Continue button enables in parallel.
8. Continue through to `video_render`, render at least one language, then continue to `final_review`. Confirm the terminal state from Task 5.1.
9. **Regression:** with an episode in `asset_upload`, manually `curl -X POST /api/episodes/{id}/queue` — expect HTTP 400 and zero rows deleted from `stage_runs` for that episode.
10. Inspect `workspace/episodes/{episode}/assembly/shared_assets/` and the `scene_assets` table before and after every click above — file count and row count must be unchanged.
11. Refresh the page at every stage — buttons survive reloads because state comes from the server payload (`assembly_progress`).

## Risks / things to NOT touch

- **DO NOT add assembly stages to `EPISODE_RUNNABLE_STAGES`** at `tool1_dashboard/config.py:58-68`. Doing so would let `queue_episode` accept them, and `delete_stage_runs_for(...)` at `service.py:3590` would happily wipe upstream stage runs.
- **DO NOT let `queue_episode()` reach `delete_stage_runs_for()` for an assembly-stage episode.** Task 1.1's guard must run *before* that call.
- **DO NOT delete, move, or re-materialize files** under `workspace/episodes/{episode}/assembly/shared_assets/` or rows in `scene_assets`. The advance flow is metadata-only.
- **DO NOT overload `data-queue-episode`** for assembly actions. Use a brand-new `data-advance-assembly` attribute so the existing handler (and its `delete_stage_runs_for` path) is never reachable from an assembly card.
- **DO NOT auto-fire stage advance on validation success.** Always require an explicit click.
- **DO NOT modify `start_assembly()` preconditions** at `service.py:3468-3469` — it must still require `export/done`. The fix is strictly about what happens *after* the episode is already in an assembly stage.
- **Be careful with `episodeQueueStartStage`** at `app.js:725-736` — it is consumed by status copy helpers like `pauseRequestedCopy` and `stageActivityLabel`. Only change the early-return for assembly mode; do not feed assembly stages into the upstream stage dropdown.
- **`assembly_progress.validation_ok` should default to `null`**, not `false`. The UI must distinguish "not yet validated" (button disabled with helper text) from "validation failed" (button disabled with error text).
