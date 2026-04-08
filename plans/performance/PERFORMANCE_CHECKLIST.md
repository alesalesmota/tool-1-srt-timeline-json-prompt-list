# Performance Optimization Checklist

> **Date:** 2026-04-08  
> **Source:** `PERFORMANCE_AUDIT.md` — full details and root cause analysis for each finding  
> **Plans:** Each group will get its own implementation plan document when ready to execute

---

## CRITICAL

- [x] **Assembly upload lightweight pass (2026-04-08)**
  - [x] Paginate assembly scene cards 20 at a time with per-episode visible counts
  - [x] Reuse a shared scene-card renderer while preserving the lazy video placeholder flow
  - [x] Add `GET /api/episodes/{episode_id}/scenes/{scene_id}` with the same payload shape as the list endpoint
  - [x] Refresh only the changed scene card after single upload/delete/replace, with full-section fallback only when needed
  - [x] Chunk bulk upload into sequential 5-file batches with `batch X/N` notices and one final rerender
  - [x] Extend `tests/test_video_pipeline.py` for single-scene before/after upload/delete plus unknown-scene `404`
  - [x] Verify with `node --check tool1_dashboard/ui/app.js`, `python -m pytest tests/test_video_pipeline.py -q` (`72` passing), and live browser smoke on `ep-20260402-201657-205` showing `20 -> 40` cards after `Show 20 more`
  - Plan: `plans/completed/ASSEMBLY_LIGHTWEIGH_PLAN.md`

- [x] **F1 + F2 + F3 — Smart Refresh System** (2026-04-08; these three are one interconnected problem)
  - [x] F1: Increase polling intervals (idle 10s, active 3s)
  - [x] F2: Route-aware API fetching — skip calls irrelevant to current view
  - [x] F3: State-change detection — skip `renderApp()` when data hasn't changed
  - [x] Verify legacy `pipeline-board` routing is redirect-only, so `/api/board/episodes` polling stays disabled
  - [x] Evaluate in-flight cancellation and keep the existing `state.isRefreshingData` overlap guard for now (no `AbortController` added yet)
  - [x] Verify with `node --check tool1_dashboard/ui/app.js` and `python -m pytest tests/test_video_pipeline.py -q` (`72` passing)
  - Plan: `plans/completed/PERF_PLAN_SMART_REFRESH.md`

- [x] **B1 — Triple Video Re-encoding** (2026-04-08)
  - [x] Switch concat to stream copy (`-c:v copy`) in `concat_scenes.py`
  - [x] Add `-preset fast -crf 20` to `render_video_scene.py` and `render_image_scene.py`
  - [x] Change subtitle burn preset from `medium` to `fast` in `burn_subtitles.py`
  - [x] End-to-end verification (subtitle path + no-subtitle path + mixed assets)
  - [x] Verify with `python -m pytest tests/test_video_assembly/test_encoding_commands.py -q` (`4` passing), `python -m pytest tests/test_video_assembly_integration.py -q` (`16` passing), and a real local FFmpeg smoke covering mixed image/video scenes plus no-subtitle and subtitle outputs
  - Plan: `plans/completed/PERF_PLAN_VIDEO_ENCODING.md`

---

## HIGH

- [x] **F4 — Elapsed Timer DOM Scan** (2026-04-08)
  - [x] F4.1: Make `resetElapsedTimer()` start-once (remove `clearInterval` + recreate pattern)
  - [x] Verify with `node --check tool1_dashboard/ui/app.js` and inline Node smoke covering single-interval registration plus preserved elapsed text updates (`elapsed-timer-start-once-ok`)
  - Plan: `PERF_PLAN_HIGH_FINDINGS.md`

- [x] **F5 — Unbounded SSE Log Growth** (2026-04-08)
  - [x] F5.1: Cap log DOM to last 300 lines (remove oldest child nodes when exceeded)
  - [x] F5.2: Batch `appendChild` via `DocumentFragment` for single reflow
  - [x] Verify with `node --check tool1_dashboard/ui/app.js` and inline Node smoke covering placeholder clear, `DocumentFragment` append, and 300-line cap (`render-log-helper-ok`)
  - Plan: `PERF_PLAN_HIGH_FINDINGS.md`

- [ ] **B2 — Sequential ffprobe Re-probing All Assets**
  - [ ] B2.3: Investigate `SceneSpec.asset_id` key format in `timeline.py` (read-only)
  - [ ] B2.1: Write `cached_probes.json` during `_stage_assets_for_render()` from DB metadata
  - [ ] B2.2: Modify `probe_assets()` to read cache and skip ffprobe for complete entries
  - Plan: `PERF_PLAN_HIGH_FINDINGS.md`

---

## MEDIUM

- [ ] **F6 — SSE Connection Leaks**
  - [ ] F6.1: Remove duplicate handler (`addEventListener("update")` vs `onmessage`) — verify backend event format first
  - [ ] F6.2: Add `closeAllRenderSSE()` and call it on navigation away from episode view
  - [ ] F6.3: Add 5-minute inactivity timeout per SSE connection
  - Plan: `PERF_PLAN_HIGH_FINDINGS.md`

- [ ] **F7 — Assembly Cache Unbounded Growth**
  - [ ] Add LRU eviction (keep max 5 episodes cached)
  - Plan: *(to be designed)*

- [ ] **B3 — Worker DB Polling Every 1s When Idle**
  - [ ] Increase idle poll interval to 5-10 seconds
  - [ ] Add exponential backoff when no queued work found
  - Plan: *(to be designed)*

- [ ] **B4 — Sequential Translation (API-bound)**
  - [ ] Parallelize chunk translation within a single language (batch of 3-5 concurrent)
  - [ ] Evaluate parallel language translation (API-bound, doesn't stress local CPU)
  - Plan: *(to be designed)*

- [ ] **B5 — MFA Alignment CPU Pressure**
  - [ ] No quick fix — inherently CPU-intensive
  - [ ] Consider: lower retry beam width, timeout protection, progress reporting
  - Plan: *(to be designed)*

- [ ] **B6 — TTS GPU Memory Pressure**
  - [ ] No quick fix — hardware constraint, already sequential by design
  - [ ] Document: managed via existing one-at-a-time queue
  - Plan: *(not needed — already managed)*

---

## LOW

- [ ] **F8 — JSON Re-stringification Every Render**
  - [ ] Cache serialized JSON strings, only re-stringify when source data changes
  - Plan: *(to be designed)*

- [ ] **F9 — querySelectorAll Scans Per Render**
  - [ ] Cache element references after render instead of re-querying
  - Plan: *(to be designed)*

- [ ] **B7 — Large File I/O During Assembly**
  - [ ] No quick fix — inherent to video pipeline
  - Plan: *(not needed)*

- [ ] **B8 — Missing Database Indexes**
  - [ ] Add compound indexes on (episode_id, language_code) for stage_runs
  - [ ] Add index on pipeline_status for episodes
  - [ ] Add index on episode_id for render_jobs, scene_assets
  - Plan: *(to be designed)*
