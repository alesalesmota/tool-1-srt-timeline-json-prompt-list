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
  - Plan: `ASSEMBLY_LIGHTWEIGH_PLAN.md`

- [ ] **F1 + F2 + F3 — Smart Refresh System** (these three are one interconnected problem)
  - [ ] F1: Increase polling intervals (idle 10s, active 3s)
  - [ ] F2: Route-aware API fetching — skip calls irrelevant to current view
  - [ ] F3: State-change detection — skip `renderApp()` when data hasn't changed
  - Plan: `PERF_PLAN_SMART_REFRESH.md`

- [ ] **B1 — Triple Video Re-encoding**
  - [ ] Audit ffmpeg flags for concat pass (can use stream-copy instead of re-encode?)
  - [ ] Add `-preset ultrafast` to CPU-bound encoding passes
  - [ ] Explore combining concat + subtitle burn into a single pass
  - Plan: *(to be designed)*

---

## HIGH

- [ ] **F4 — Elapsed Timer DOM Scan**
  - [ ] Replace `querySelectorAll` scan with a tracked element set
  - [ ] Register/unregister elapsed elements when they mount/unmount
  - Plan: *(to be designed)*

- [ ] **F5 — Unbounded SSE Log Growth**
  - [ ] Cap log DOM to last 200 lines (remove oldest nodes when exceeded)
  - [ ] Batch `appendChild` calls to reduce reflows
  - Plan: *(to be designed)*

- [ ] **B2 — Sequential ffprobe Re-probing All Assets**
  - [ ] Reuse metadata already stored in `scene_assets` DB table from upload time
  - [ ] Only call ffprobe on assets missing metadata
  - Plan: *(to be designed)*

---

## MEDIUM

- [ ] **F6 — SSE Connection Leaks**
  - [ ] Add 5-minute timeout on idle SSE connections
  - [ ] Close all SSE connections when navigating away from assembly section
  - [ ] Fix duplicate event handlers (`addEventListener` + `onmessage`)
  - Plan: *(to be designed)*

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
