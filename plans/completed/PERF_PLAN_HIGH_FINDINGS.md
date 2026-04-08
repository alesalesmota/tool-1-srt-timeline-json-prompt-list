# Plan: HIGH Priority Fixes (F4 + F5 + F6 + B2)

> **Date:** 2026-04-08  
> **Audit ref:** `PERFORMANCE_AUDIT.md` findings F4, F5, F6, B2  
> **Checklist ref:** `PERFORMANCE_CHECKLIST.md`  
> **Status:** Complete (`F4` + `F5` + `F6` + `B2` shipped on 2026-04-08)

These are 4 independent fixes. Each is small and self-contained. Implement in any order.

---

## Fix F4: Elapsed Timer DOM Scan

### Problem

`resetElapsedTimer()` at `app.js:5042` runs a `setInterval` every 1 second that calls `document.querySelectorAll(".running-elapsed")` to find and update elapsed-time elements. This scans the **entire DOM** every second — expensive when the page has hundreds of elements.

Worse: `resetElapsedTimer()` is called from `renderApp()` (line 4826), which runs on every refresh cycle. So the timer is **destroyed and recreated** every 1-5 seconds. Each recreation does a fresh DOM scan immediately.

### Current Code (`app.js:5042-5054`)

```javascript
function resetElapsedTimer() {
  if (elapsedTimer) window.clearInterval(elapsedTimer);
  elapsedTimer = window.setInterval(() => {
    document.querySelectorAll(".running-elapsed").forEach((el) => {
      if (!el.dataset.startedAt) return;
      const startMs = new Date(el.dataset.startedAt).getTime();
      const diffSec = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
      const mins = Math.floor(diffSec / 60);
      const secs = diffSec % 60;
      el.textContent = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
    });
  }, 1000);
}
```

### Solution: Start once, never reset

The timer doesn't need to be destroyed and recreated on every render. The `querySelectorAll` inside the interval already adapts to whatever `.running-elapsed` elements exist in the DOM at that moment — if elements are removed by a re-render, the next tick just finds fewer (or zero) elements.

**Task F4.1 — Make the timer start-once, not reset-on-every-render**

**File:** `tool1_dashboard/ui/app.js`

Change `resetElapsedTimer()` so it only creates the interval if one doesn't already exist. Remove the `clearInterval` call.

The new logic:
```
function resetElapsedTimer() {
  if (elapsedTimer) return;  // already running, do nothing
  elapsedTimer = window.setInterval(() => { ... same tick logic ... }, 1000);
}
```

That's it. The interval is created once on first render and runs forever. The `querySelectorAll` inside the tick naturally finds whatever `.running-elapsed` elements exist at that moment. No need to restart the timer when the DOM changes.

**Why this is safe:** The tick function doesn't hold references to old elements. It queries the DOM fresh each second. If there are zero `.running-elapsed` elements (user is on Settings page), the `forEach` iterates over an empty list — negligible cost.

**Task F4.2 — (Optional) Replace querySelectorAll with targeted getElementById**

If the DOM is very large (100+ scene cards visible), even `querySelectorAll` every second adds up. An optimization:

Look at where `.running-elapsed` is rendered in the codebase. There are only 4 places:
- `app.js:3004` — episode card on kanban board (`episode-elapsed`)  
- `app.js:3629` — stage run detail metric
- `app.js:3636` — stage run preview metric
- `app.js:4134` — episode overlay

These are a small number of elements (typically 1-5 visible at a time). The `querySelectorAll` scan cost is dominated by the DOM size, not the result count.

If the smart refresh fix (F1-F3) is implemented, the DOM stays small and this scan is cheap. **Skip F4.2 unless profiling shows `querySelectorAll` is still a bottleneck after F1-F3.**

### Verification

- Start a workflow — elapsed timers on board cards should still tick every second
- Navigate between pages — timers should work without restart
- Open DevTools → Performance → Record for 10 seconds — `resetElapsedTimer` should NOT appear as a repeated call in the flame chart

---

## Fix F5: Unbounded SSE Log Growth

### Problem

When a video render is in progress, the SSE handler at `app.js:6735-6739` appends log messages to a DOM element without limit:

```javascript
if (logOut && data.new_logs) {
  data.new_logs.forEach(msg => {
     logOut.appendChild(document.createTextNode(msg.message + "\\n"));
  });
  logOut.scrollTop = logOut.scrollHeight;
}
```

A 30-minute video render produces hundreds/thousands of log lines. Each line is a new `TextNode` appended to the DOM. The `scrollTop = scrollHeight` triggers a reflow after every batch. Over time the log container has thousands of child nodes, consuming memory and making layout calculations expensive.

The CSS limits the visible area (`max-height: 200px; overflow-y: auto`), but the DOM keeps growing behind the scroll.

### Solution: Cap log lines, batch the append

**Task F5.1 — Cap log DOM to 300 lines**

**File:** `tool1_dashboard/ui/app.js`  
**Where:** Inside the `handleUpdate` function within `startRenderSSE()`, lines 6735-6739.

**The approach:** After appending new log nodes, check how many child nodes `logOut` has. If it exceeds 300, remove the oldest nodes from the top until back to 300.

Guidance for the implementation:
```
// After appending new log messages:
const MAX_LOG_LINES = 300;
while (logOut.childNodes.length > MAX_LOG_LINES) {
  logOut.removeChild(logOut.firstChild);
}
```

**Why 300:** At ~40 characters per log line and a 200px-tall scrollable container showing ~10 lines at a time, 300 lines gives 30x scroll-back — more than enough context. Memory stays bounded at ~12KB of text nodes instead of growing to megabytes.

**Task F5.2 — Move `scrollTop` outside the forEach loop**

Currently `scrollTop = scrollHeight` is called once per batch (after the `forEach`), which is already correct. But verify that if `data.new_logs` has many entries (e.g., 50 logs arrive at once from a server catchup), the `forEach` + individual `appendChild` calls cause 50 reflows.

**The fix:** Build a `DocumentFragment`, append all text nodes to the fragment, then append the fragment to `logOut` in one operation. This triggers ONE reflow instead of N.

Guidance:
```
if (logOut && data.new_logs) {
  const frag = document.createDocumentFragment();
  data.new_logs.forEach(msg => {
    frag.appendChild(document.createTextNode(msg.message + "\n"));
  });
  logOut.appendChild(frag);
  // Cap log lines
  const MAX_LOG_LINES = 300;
  while (logOut.childNodes.length > MAX_LOG_LINES) {
    logOut.removeChild(logOut.firstChild);
  }
  logOut.scrollTop = logOut.scrollHeight;
}
```

### Verification

- Start a video render and watch the log output panel
- Logs should stream in normally and auto-scroll to bottom
- After a long render (or by inspecting with DevTools), `logOut.childNodes.length` should never exceed ~300
- No visible stutter or freezing during log streaming

---

## Fix F6: SSE Connection Leaks + Duplicate Handlers

### Problem

`startRenderSSE()` at `app.js:6716-6756` has three issues:

1. **Duplicate handlers:** Both `es.addEventListener("update", handleUpdate)` (line 6750) AND `es.onmessage = handleUpdate` (line 6751) are registered. `onmessage` fires for events without a named type OR with type "message". `addEventListener("update", ...)` fires for events with type "update". If the server sends events with type "update", only the addEventListener fires. If it sends events without a type, only `onmessage` fires. If it sends both types, both fire for their respective events. **Check the server-side SSE format to determine which handler is correct, then remove the other.**

2. **No navigation cleanup:** When the user navigates away from the episode page, SSE connections in `renderEventSources` stay open. They close on completion/failure (line 6742-6743) or on error (line 6753-6754), but if the user just navigates away, the connection hangs.

3. **No timeout:** If the server stops sending events (process crash, network issue), the connection stays open indefinitely. Browsers have built-in SSE reconnection, which makes it worse — a dead connection keeps retrying forever.

### Solution

**Task F6.1 — Remove the duplicate handler**

**File:** `tool1_dashboard/ui/app.js`  
**Where:** Lines 6750-6751 in `startRenderSSE()`

First, check what event type the backend sends. Search in `service.py` or the render event endpoint for how SSE events are formatted. Look for `event:` field in the SSE data or for the endpoint that streams events.

- If events are sent with `event: update`, keep `addEventListener("update", ...)` and remove `onmessage`.
- If events are sent without a type (bare `data:` lines), keep `onmessage` and remove the `addEventListener`.
- If both, keep `addEventListener("update", ...)` (more explicit) and remove `onmessage`.

The goal: each server event triggers the handler exactly **once**, not twice.

**Task F6.2 — Add a cleanup function for navigation**

**File:** `tool1_dashboard/ui/app.js`

Add a new function `closeAllRenderSSE()` that iterates `renderEventSources`, closes each `EventSource`, and empties the object:

```
function closeAllRenderSSE() {
  for (const [jobId, es] of Object.entries(renderEventSources)) {
    es.close();
  }
  renderEventSources = {};
}
```

Then call `closeAllRenderSSE()` in the navigation cleanup path. Search for where `resetEpisodeSupplementalState` is called (this function clears episode-specific data on navigation). Add `closeAllRenderSSE()` alongside it.

The relevant locations to search for:
- `syncRouteAndRender()` at `app.js:5056` — when the route changes
- Look for where `state.episodeOverlayId` is set to `null`
- Look for `resetEpisodeSupplementalState()` calls

The principle: when the user leaves the episode view, close all render SSE connections. If they re-open the episode, `startRenderSSE` will re-establish connections for active jobs.

**Task F6.3 — Add an inactivity timeout**

**File:** `tool1_dashboard/ui/app.js`  
**Where:** Inside `startRenderSSE()`, after creating the EventSource.

Add a 5-minute inactivity timeout. Reset the timer every time an event arrives. If 5 minutes pass with no event, close the connection.

Guidance:
```
let inactivityTimeout = null;
const resetInactivity = () => {
  if (inactivityTimeout) clearTimeout(inactivityTimeout);
  inactivityTimeout = setTimeout(() => {
    es.close();
    delete renderEventSources[jobId];
  }, 5 * 60 * 1000);
};
resetInactivity(); // start the timer
```

Call `resetInactivity()` at the start of the `handleUpdate` function (each event received resets the timer).

Also clear the timeout in `es.onerror` and in the completion/failure handler where `es.close()` is called.

### Verification

- Start a render — SSE should connect and stream logs/progress normally
- Navigate away from the episode — DevTools Network tab should show the SSE connection closed
- Return to the episode — SSE should reconnect for active render jobs
- Check that log messages are NOT duplicated (each event produces one log line, not two)

---

## Fix B2: Sequential ffprobe Re-probing All Assets

### Problem

The render pipeline calls `probe_assets()` (`video_assembly/probe_assets.py:48`) which runs `ffprobe` on EVERY scene asset file before rendering. This is a blocking subprocess per file — for 100 scenes, it's 100 sequential ffprobe calls taking 2-8 minutes total.

But the same metadata (width, height, duration, codec) was **already collected during upload** by `_probe_scene_asset_metadata()` in `service.py:2505` and stored in the `scene_assets` database table.

The render pipeline probes from scratch because it operates on staged files in the render project directory and doesn't know about the dashboard's database.

### Current Flow

```
Upload: file → _probe_scene_asset_metadata() → stores width/height/duration in scene_assets DB
  ↓ (later, at render time)
Render: _stage_assets_for_render() copies files to project_dir/input/assets/
  ↓
RenderPipeline.run() → probe_assets() → ffprobe EVERY file again
```

### Solution: Write a pre-built probes file during asset staging

The key insight: `probe_assets()` writes its results to `temp/probed_assets.json` (line 72). And the `RenderPipeline.run()` calls `probe_assets()` at line 79. We can **skip the ffprobe calls** by pre-writing `probed_assets.json` with the cached metadata from the database during the staging step.

But there's a subtlety: the render pipeline needs `probe_assets()` to also **validate** assets (check for video streams, detect type mismatches, verify duration exists). We don't want to skip that validation — it catches corrupt or mismatched files.

**Better approach:** Make `probe_assets()` accept an optional pre-built probes dict. For each asset, if a cached probe exists with complete metadata (width, height, duration for videos), use it. Only call ffprobe for assets where cached data is incomplete or missing. This gives us both: skipping ffprobe for known-good assets AND still validating unknown assets.

**Task B2.1 — Modify `_stage_assets_for_render` to write a cached probes JSON**

**File:** `tool1_dashboard/service.py`  
**Where:** `_stage_assets_for_render()` at line 3011

This function already iterates through all scenes and their DB assets (line 3019-3035). It has access to the full `scene_assets` records including `width`, `height`, `duration_seconds`.

**The change:** After staging the files, build a probes dict from the DB metadata and write it to the render project directory as `input/cached_probes.json`. 

The probes dict should map `asset_id → { width, height, duration, type }`. The `asset_id` for the render pipeline is typically the scene_id or the staged filename — check how `SceneSpec.asset_id` is set in `timeline.py` to match the key format.

The key format used by `probe_assets()` is `scene.asset_id` from `SceneSpec`. Check `video_assembly/timeline.py` → `load_timeline()` to see how `asset_id` is assigned to each `SceneSpec`. The cached probes JSON must use the same key format.

Write the file to `project_dir / "input" / "cached_probes.json"`.

**Task B2.2 — Modify `probe_assets()` to read cached probes and skip ffprobe**

**File:** `tool1_dashboard/video_assembly/probe_assets.py`

**The approach:** At the top of `probe_assets()`, check if `config.project_dir / "input" / "cached_probes.json"` exists. If it does, read it into a dict.

Then, in the per-scene loop, before calling `_probe_asset()` (ffprobe), check if the cached dict has a complete entry for this asset_id:
- For **video** assets: cached entry must have `width`, `height`, and `duration` (all non-null)
- For **image** assets: cached entry must have `width` and `height` (duration not required)

If the cached entry is complete, build an `AssetProbe` from it without calling ffprobe. If the entry is missing or incomplete, fall back to the normal ffprobe path.

This means:
- Fresh uploads with complete metadata → no ffprobe (instant)
- Old uploads with missing metadata → falls back to ffprobe (safe)
- Corrupt or replaced files → ffprobe catches them (validation preserved)

**Task B2.3 — Verify the asset_id mapping**

**File:** `tool1_dashboard/video_assembly/timeline.py`

Read this file to understand how `SceneSpec.asset_id` is constructed. The cached probes JSON (from Task B2.1) must use the same key that `probe_assets()` uses to look up probes.

This is a **read-only investigation task** — don't change code, just verify the key format and ensure Tasks B2.1 and B2.2 use matching keys. Report the key format so the implementer can ensure consistency.

### Verification

- Upload assets for an episode (images and videos)
- Trigger a render
- Check the render logs — the "probing" stage should complete in seconds (not minutes)
- Check that `input/cached_probes.json` exists in the render project directory
- Check that the rendered video is correct (no duration mismatches, no corrupt scenes)
- Test edge case: delete an asset and re-upload a different file for the same scene, then render — the new probe data should be used

---

## Implementation Checklist

### F4 — Elapsed Timer
- [x] F4.1: Make `resetElapsedTimer()` start-once (remove clearInterval + re-create pattern)

### F5 — SSE Log Growth
- [x] F5.1: Cap log DOM to 300 lines (remove oldest child nodes when exceeded)
- [x] F5.2: Batch log appends using DocumentFragment for single reflow

### F6 — SSE Connection Leaks
- [x] F6.1: Remove duplicate handler (backend confirmed `event: update`, so keep `addEventListener("update")`)
- [x] F6.2: Add `closeAllRenderSSE()` and call it on episode close / route + episode switches
- [x] F6.3: Add 5-minute inactivity timeout per SSE connection

### B2 — ffprobe Re-probing
- [x] B2.3: Investigate `asset_id` key format in `timeline.py` (do this first — read-only)
- [x] B2.1: Write `cached_probes.json` during `_stage_assets_for_render()`
- [x] B2.2: Modify `probe_assets()` to read cache and skip ffprobe for complete entries
