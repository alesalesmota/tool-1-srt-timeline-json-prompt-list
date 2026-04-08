# Plan: Smart Refresh System (F1 + F2 + F3)

> **Date:** 2026-04-08  
> **Audit ref:** `PERFORMANCE_AUDIT.md` findings F1, F2, F3  
> **Checklist ref:** `PERFORMANCE_CHECKLIST.md`  
> **Status:** Implemented (2026-04-08)

---

## Problem Summary

The dashboard has a refresh loop that runs every 1–5 seconds. Each cycle:
1. Fires up to 10 API calls in parallel (F2)
2. Waits for all responses
3. Updates state
4. Calls `renderApp()` which rebuilds the ENTIRE page via `innerHTML` (F3)

When a workflow is active, this happens **every 1 second** (F1). The machine can't keep up — API responses queue, DOM rebuilds overlap, UI freezes.

These three findings are one interconnected problem. Fixing any one alone helps, but the real relief comes from fixing all three together.

---

## Solution Strategy

The fix has 3 layers, each independent. Implement them in this exact order because each layer reduces the impact of the next:

1. **State-change guard on `renderApp()`** — If the data didn't change, don't re-render. This is the highest-impact single fix because it makes frequent polling harmless.
2. **Route-aware fetching** — Only fetch API data relevant to the current view. Reduces network and server load.
3. **Increase polling intervals** — Slow down the timer. Simplest change but least impactful if layers 1-2 are in place.

---

## FILE: `tool1_dashboard/ui/app.js`

All changes are in this single file. No backend changes needed.

## Implementation Outcome

- Added `lastRenderFingerprint` + `computeRenderFingerprint()` and now update the fingerprint at the end of every `renderApp()` pass.
- Guarded only the polling-triggered `refreshData().then(renderApp)` path so passive refreshes skip DOM rebuilds when render-relevant state is unchanged.
- Made `/api/niche-projects` route-aware and confirmed `#/pipeline-board` is a legacy redirect (`parseRoute()` / `routeToHash()` both route through `legacyBoardRedirectRoute()`), so `/api/board/episodes` polling now stays disabled.
- Increased the refresh loop to `10s` idle / `3s` active.
- Evaluated `AbortController` and intentionally skipped it for now because `state.isRefreshingData` already blocks overlapping refresh cycles in the current flow.

---

## Task 1: Add a state fingerprint function

**Goal:** Create a way to detect whether `state` has meaningfully changed since the last render.

**Where to add:** Before `renderApp()` (before line 4803). Add a new function and a module-level variable.

**The idea:** After each `refreshData()` completes and writes to `state`, compute a lightweight fingerprint (hash) of the state fields that affect rendering. Store it. Before calling `renderApp()`, compare the new fingerprint with the stored one. If identical, skip the render.

**What fields matter for rendering:**  
Not ALL of `state` matters — only the fields that `renderApp()` reads to produce HTML. These are the ones that `refreshData()` updates (lines 4959-5011):

- `state.health`
- `state.nicheProjects`
- `state.boardEpisodes`
- `state.targetLanguages`
- `state.voiceProfiles`
- `state.workerHealth`
- `state.appRuntime`
- `state.translationProfiles`
- `state.episodeDetail`
- `state.nicheProjectDetail`
- `state.route`
- `state.episodeOverlayId`
- `state.notice`
- `state.modal`
- `state.isLoadingRoute`

**The fingerprint approach:** Use `JSON.stringify()` on a small object containing just these fields, then compare the string with the previous one. `JSON.stringify` is fast on small objects — this is NOT the same as stringifying the entire DOM.

**Implementation guidance:**

1. Add a module-level variable near line 160 (where `refreshTimer`, `noticeTimer` etc. are):
   ```
   let lastRenderFingerprint = "";
   ```

2. Create a function `computeRenderFingerprint()` that builds a plain object from the state fields listed above and returns `JSON.stringify(thatObject)`.

3. The function should be cheap — just read existing state properties into a flat object and stringify. Do NOT deep-clone or traverse nested structures. Use the top-level references (object identity changes when `refreshData` replaces them).

**Verification:** After this task, `computeRenderFingerprint()` exists and can be called. No behavior change yet.

---

## Task 2: Guard `renderApp()` with fingerprint check

**Goal:** Make the polling-triggered render path skip `renderApp()` when nothing changed.

**Where to change:** Line 5038, inside `resetAutoRefresh()`.

**Current code (line 5038):**
```javascript
refreshData().then(renderApp).catch(() => {});
```

This is the polling path — every 1-5 seconds, it calls `refreshData()` then unconditionally calls `renderApp()`.

**What to change:** Replace `renderApp` with a guarded version that checks the fingerprint before rendering.

**The approach:**
```javascript
refreshData().then(() => {
  const fp = computeRenderFingerprint();
  if (fp !== lastRenderFingerprint) {
    lastRenderFingerprint = fp;
    renderApp();
  }
}).catch(() => {});
```

**CRITICAL — Do NOT guard these other `renderApp()` calls:**
- Line 5077: `renderApp()` after route loading state change — this MUST always render (user navigated)
- Line 5079: `renderApp()` after `refreshData` on route change — this MUST always render
- Any `renderApp()` triggered by user action (modal open, button click, etc.) — these MUST always render

**Only the polling path at line 5038** should be guarded. All user-initiated renders must go through unconditionally.

**Also update `renderApp()` itself** (line 4803): At the very end of `renderApp()`, update the fingerprint so that the next polling cycle has a fresh baseline:
```
lastRenderFingerprint = computeRenderFingerprint();
```

Add this as the last line inside `renderApp()`, after `restoreDashboardScroll()` (line 4827).

**Verification:**
- Open the dashboard on an idle view (e.g., Settings page)
- Open DevTools → Console, add a `console.count("renderApp")` at the top of `renderApp()`
- Watch for 30 seconds — the counter should tick only when data actually changes, NOT every 5 seconds
- Trigger a real change (start a workflow, upload something) — the counter should tick immediately

---

## Task 3: Make `nicheProjectsPromise` conditional

**Goal:** Stop fetching `/api/niche-projects` on every single refresh regardless of the current view.

**Where to change:** Line 4858 in `refreshData()`.

**Current code (line 4858):**
```javascript
const nicheProjectsPromise = api("/api/niche-projects");
```

This is ALWAYS fetched — even on Settings, Voice Profiles, Translation Profiles, and Templates pages where the data is never used.

**The fix:** Make it conditional based on the current route, same pattern used for the other promises.

**Which routes need niche projects?**
- `niche-projects` — yes (the project list page)
- `niche-project` — yes (project detail, but detail comes from separate call; the list is used for sidebar/navigation)
- `episode` — yes (overlay on project board needs project context)
- `pipeline-board` — yes (redirects to niche-projects)
- `voice-profiles` — NO
- `translation-profiles` — NO
- `settings` — NO
- `templates` — NO

**Approach:** Check `route.view` and only fetch if on a view that needs it. For other views, resolve with the cached state:
```javascript
const needsNicheProjects = ["niche-projects", "niche-project", "episode", "pipeline-board"].includes(route.view);
const nicheProjectsPromise = needsNicheProjects
  ? api("/api/niche-projects")
  : Promise.resolve({ projects: state.nicheProjects || [] });
```

**Also update the state assignment** at line 4971. Currently:
```javascript
state.nicheProjects = nicheProjects.projects || [];
```

Wrap it in the same condition:
```javascript
if (needsNicheProjects) {
  state.nicheProjects = nicheProjects.projects || [];
}
```

**Verification:**
- Navigate to Settings page
- Open DevTools → Network tab
- Watch for 30 seconds — you should NOT see `/api/niche-projects` requests
- Navigate to the Niche Projects page — requests should resume

---

## Task 4: Make `boardEpisodesPromise` smarter

**Goal:** The board episodes fetch currently skips on non-board views, which is good. But it still fetches on the `niche-project` view even though that view uses its own project-scoped episode list. Verify and tighten.

**Where to check:** Line 4837:
```javascript
const shouldFetchBoardEpisodes = force || route.view === "pipeline-board";
```

**Current state:** This is already conditional — only fetches on `pipeline-board` view. This is fine.

**However**, `pipeline-board` redirects to `niche-projects` in the frontend. Check if `pipeline-board` is still a real route that users land on. If it always redirects, this fetch never fires and can be removed.

**Action:** Verify whether `pipeline-board` is a real view or just a redirect. If redirect, change the condition to never fetch board episodes (the niche-project view fetches its own scoped episodes). If real, leave as-is.

**This is a verification task** — read the routing code, check if `pipeline-board` renders its own content or redirects. Minimal code change expected.

---

## Task 5: Add in-flight request cancellation

**Goal:** When a new refresh cycle starts, cancel any still-pending requests from the previous cycle so they don't pile up.

**Where to change:** `refreshData()` starting at line 4830.

**The problem:** If the previous `refreshData()` call hasn't finished when the next polling tick fires, the guard at line 5033 (`state.isRefreshingData`) prevents a new call. But if the API is slow, this means the UI goes stale. And if the guard somehow doesn't catch it (race condition), two sets of 10 requests overlap.

**Current protection (line 5031-5037):**
```javascript
if (
  state.modal.kind ||
  state.isLoadingRoute ||
  state.isRefreshingData ||    // <-- this prevents overlapping refreshes
  voiceProfileAudioIsPlaying() ||
  projectConfigInteractionIsActive() ||
  episodeAssemblyInteractionIsActive()
) return;
```

**Assessment:** The `isRefreshingData` guard already prevents overlapping refresh cycles. This is adequate for now.

**Optional improvement:** Use an `AbortController` per refresh cycle. At the start of `refreshData()`, abort the previous controller and create a new one. Pass `{ signal: controller.signal }` to each `api()` call. This cleanly cancels stale requests when a new cycle starts.

**Guidance for the agent:**
- This is a nice-to-have, not a must-have
- The `isRefreshingData` guard is already working
- Only implement AbortController if you observe overlapping requests in DevTools after Tasks 1-3 are done
- If implementing: store the controller in a module-level `let refreshAbortController = null;`, abort the old one at the top of `refreshData()`, create a new one, and pass its signal to the `api()` calls via `fetch` options

---

## Task 6: Increase polling intervals

**Goal:** Slow down the refresh timer to reduce baseline load.

**Where to change:** Lines 64-65:
```javascript
const REFRESH_INTERVAL_MS = 5000;
const ACTIVE_REFRESH_INTERVAL_MS = 1000;
```

**Change to:**
```javascript
const REFRESH_INTERVAL_MS = 10000;
const ACTIVE_REFRESH_INTERVAL_MS = 3000;
```

**Why these values:**
- **10 seconds idle** — Settings, Voice Profiles, Templates pages don't need frequent updates. With the fingerprint guard (Task 2), even if data hasn't changed, we only pay the cost of API calls, not re-render. 10s is a good balance.
- **3 seconds active** — When a workflow is running, the user wants to see progress. 3 seconds feels responsive without hammering the machine. Combined with the fingerprint guard, most of these cycles will be no-op renders anyway.

**Why this is Task 6 (last):** With Tasks 1-3 in place, the polling interval matters much less. The fingerprint guard prevents unnecessary renders, and route-aware fetching reduces API calls. Slowing the timer is extra insurance.

**Verification:**
- Start a workflow on an episode
- The board should still feel responsive — cards move between columns within ~3 seconds
- On idle pages (Settings), network requests should be sparse (every 10s)

---

## Implementation Checklist

- [x] Task 1: Add `computeRenderFingerprint()` function + `lastRenderFingerprint` variable
- [x] Task 2: Guard polling `renderApp()` call with fingerprint check (line 5038 only)
- [x] Task 3: Make `nicheProjectsPromise` conditional on route
- [x] Task 4: Verify `boardEpisodesPromise` / `pipeline-board` route status
- [x] Task 5: Evaluate AbortController need and skip it because the existing overlap guard is sufficient
- [x] Task 6: Increase polling intervals to 10s idle / 3s active

---

## Testing the Full Fix

After all tasks are implemented:

1. **Idle page test (Settings):**
   - Open Settings page, open DevTools Network tab
   - Over 30 seconds: should see ~3 API requests (every 10s), NOT 30 (every 1s)
   - DOM should NOT flicker or lose scroll position

2. **Active workflow test:**
   - Start a workflow on an episode from the project board
   - Board cards should update within ~3 seconds of stage changes
   - CPU usage should stay low between updates (no constant re-rendering)
   - The elapsed timer on running cards should still tick every second (independent of refresh)

3. **Route switch test:**
   - Navigate from Settings → Niche Projects → Voice Profiles → back to Niche Projects
   - Each navigation should render immediately (user-initiated renders are NOT guarded)
   - API calls in Network tab should match only the active view's needs

4. **Modal test:**
   - Open a modal (create project, edit voice profile, etc.)
   - While modal is open, no refreshes should fire (existing guard at line 5031)
   - Close the modal — next refresh should render if data changed

5. **No regressions:**
   - Upload an asset in assembly → should update immediately (user action, not polling)
   - Start/stop a workflow → board should reflect new status within 3 seconds
   - Edit and save settings → should reflect immediately (user action triggers render)
