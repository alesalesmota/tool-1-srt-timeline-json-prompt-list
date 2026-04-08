# Implementation Plan: Lightweight Assembly Upload & Preview

> **Date:** 2026-04-08  
> **Branch:** `codex/fix-assembly-scroll-glitch`  
> **Status:** Implemented on 2026-04-08  
> **Bridge Task #10:** "neither bulk upload, and scene upload is working, i choose the file and nothing uploads"

---

## Implementation Outcome

- Frontend now paginates assembly scenes `20` at a time, keeps per-episode visible counts, and exposes `Show 20 more`
- Single upload/delete/replace now refresh only the affected scene card via `GET /api/episodes/{episode_id}/scenes/{scene_id}`
- Bulk upload now runs in sequential 5-file batches with `batch X/N` progress notices and one final assembly rerender
- Verification completed with `node --check tool1_dashboard/ui/app.js`, `python -m pytest tests/test_video_pipeline.py -q` (`72` passing), and a live browser smoke on `http://127.0.0.1:8020/#/episodes/ep-20260402-201657-205` confirming `20 -> 40` visible cards after `Show 20 more`

---

## Why This Change Is Needed

The Assembly panel in the Tool 1 Dashboard renders ALL scene cards at once (100+ scenes). When an episode has many scenes, the browser creates a DOM that is 32,000+ pixels tall with hundreds of DOM nodes (images, inputs, buttons, dropzones). This overwhelms the user's machine — it freezes, uploads timeout, and nothing works.

The problems are:
1. ALL 100+ scene cards rendered at once (no pagination)
2. Every single upload/delete triggers a FULL re-render of the entire grid
3. Bulk upload sends ALL files in one massive HTTP request that blocks the server sequentially
4. No upload progress feedback — the user thinks nothing is happening

This plan has **3 phases** ordered by impact. Each phase is independent and can be tested on its own.

---

## THE CODEBASE — Key Files You Need to Know

| File | What It Does | Full Path |
|------|-------------|-----------|
| `app.js` | All frontend JavaScript. Assembly section is around lines 6235-6507 | `tool1_dashboard/ui/app.js` |
| `app.css` | All frontend CSS. Scene card styles around lines 3394-3470 | `tool1_dashboard/ui/app.css` |
| `app.py` | FastAPI backend routes. Scene/asset endpoints at lines 467-538 | `tool1_dashboard/app.py` |
| `service.py` | Backend business logic. Scene listing at line 2706, upload at line 2533, bulk upload at line 2762 | `tool1_dashboard/service.py` |
| `database.py` | SQLite database queries. Scene asset CRUD at lines 724-775 | `tool1_dashboard/database.py` |

### Important existing functions you'll interact with:

- **`renderAssemblySection(episodeId, options)`** — `app.js:6235` — Fetches ALL scenes from API and renders the entire grid. This is the main function we're optimizing.
- **`handleBulkUpload(episodeId, files)`** — `app.js:6388` — Sends ALL files in one HTTP POST. We're rewriting this.
- **`handleAssetUpload(episodeId, sceneId, file)`** — `app.js:6368` — Uploads a single file, then calls full re-render. We're changing it to incremental update.
- **`bindEpisodeAssemblyUploadInputs(root)`** — `app.js:6355` — Binds change listeners to file inputs. Called after every render.
- **`updateEpisodeAssemblyCache(episodeId, stage, html)`** — Caches the assembly HTML to avoid re-fetching when switching tabs. Must be kept up to date.
- **`list_episode_scenes(episode_id)`** — `service.py:2706` — Returns all scenes with their asset status.
- **`state`** — `app.js:108` — Global state object. We add new properties here.
- **`$()` function** — `app.js:168` — Shorthand for `document.getElementById()`.
- **`esc()` function** — `app.js:169` — HTML-escapes a string for safe insertion.
- **`api()` function** — Makes a fetch request and returns parsed JSON. Used throughout the frontend.
- **`setNotice(text, tone)`** — Shows a notification banner to the user.
- **`voiceTtsNumberValue(num, decimals)`** — Formats a number with fixed decimal places. Used in time display.
- **`shortText(text, maxLen)`** — Truncates text with ellipsis.

### Important existing patterns:

- **Event delegation:** Click handlers are NOT attached to individual buttons. Instead, there's ONE global `document.addEventListener("click", ...)` at line 6415 that checks `event.target.closest("[data-some-attribute]")` to figure out what was clicked. New click handlers should follow this same pattern.
- **File inputs:** File `<input>` elements are hidden (`style="display:none"`). When the user clicks a button, the code finds the corresponding hidden input and calls `.click()` on it.
- **Upload interaction tracking:** Before uploading, call `beginEpisodeAssemblyUploadInteraction()`. After upload completes (in `.finally()`), call `endEpisodeAssemblyUploadInteraction()`. This prevents the auto-refresh from overwriting the assembly while uploads are happening.

---

## PHASE 1: Paginated Rendering with "Load More"

### What This Solves

Right now, `renderAssemblySection()` at line 6235 fetches ALL scenes and renders ALL of them in one giant HTML string using `.map().join("")` at line 6262. With 100+ scenes, this creates a massive DOM — hundreds of images trying to load, hundreds of file inputs, hundreds of buttons. The browser chokes.

**Solution:** Only render 20 scene cards at a time. Show a "Load More" button to see more.

### Step 1.1 — Add a page size constant

**File:** `tool1_dashboard/ui/app.js`  
**Where:** Near the top of the file, around lines 15-20, where you see other constants like `const REFRESH_INTERVAL_MS = ...`

**What to add:**

```javascript
const ASSEMBLY_PAGE_SIZE = 20;
```

This constant controls how many scene cards we show at a time. 20 is a good balance — enough to see useful content, not enough to lag the browser.

### Step 1.2 — Add pagination state to the global `state` object

**File:** `tool1_dashboard/ui/app.js`  
**Where:** Inside the `const state = {` object that starts at line 108. Find the property `episodeAssemblyUploadsInFlight: 0,` at line 148.

**What to add:** After line 148, add this new property:

```javascript
episodeAssemblyVisibleCount: 20,
```

This tracks how many scene cards are currently visible. It starts at 20 and increases by 20 each time the user clicks "Load More".

### Step 1.3 — Reset pagination when episode changes

**File:** `tool1_dashboard/ui/app.js`  
**Where:** Inside `renderAssemblySection()`, which starts at line 6235. Right after the first two lines of the function.

**Current code (lines 6235-6240):**
```javascript
async function renderAssemblySection(episodeId, { readOnly = false, showLoading = true } = {}) {
  const container = $("episode-assembly-section");
  if (!container) return;
  if (showLoading) {
```

**Replace with:**
```javascript
async function renderAssemblySection(episodeId, { readOnly = false, showLoading = true } = {}) {
  const container = $("episode-assembly-section");
  if (!container) return;
  // Reset pagination when switching to a different episode
  if (container.dataset.currentEpisodeId !== episodeId) {
    state.episodeAssemblyVisibleCount = ASSEMBLY_PAGE_SIZE;
    container.dataset.currentEpisodeId = episodeId;
  }
  if (showLoading) {
```

**Why:** When the user navigates to a different episode, we want to start showing 20 cards again, not whatever number they scrolled to in the previous episode. We use `container.dataset.currentEpisodeId` to detect the switch.

### Step 1.4 — Slice the scenes array to only show visible cards

**File:** `tool1_dashboard/ui/app.js`  
**Where:** Inside `renderAssemblySection()`, at line 6262 where the grid is built.

**Current code (line 6262):**
```javascript
    const gridLayout = scenes.map(scene => {
```

**Replace with:** Add a line BEFORE line 6262, then change the `.map()` call:

```javascript
    const visibleScenes = scenes.slice(0, state.episodeAssemblyVisibleCount);
    const gridLayout = visibleScenes.map(scene => {
```

**What this does:** Instead of mapping over ALL scenes, we only map over the first `state.episodeAssemblyVisibleCount` scenes (starts at 20). The rest are simply not rendered — they don't exist in the DOM at all.

### Step 1.5 — Add "Load More" button after the grid

**File:** `tool1_dashboard/ui/app.js`  
**Where:** Inside `renderAssemblySection()`, where `container.innerHTML` is assigned. This is currently at lines 6309-6315.

**Current code (lines 6309-6315):**
```javascript
    container.innerHTML = `
      <div class="section-header" style="margin-bottom:12px;">
        <div class="eyebrow" style="margin:0;">Video Assembly Assets</div>
      </div>
      ${statsBar}
      <div class="scene-grid">${gridLayout}</div>
    `;
```

**Replace with:**
```javascript
    const hasMore = state.episodeAssemblyVisibleCount < scenes.length;
    const remaining = scenes.length - state.episodeAssemblyVisibleCount;
    const loadMoreBtn = hasMore
      ? `<div style="text-align:center; margin-top:16px;">
           <button class="button button-ghost" data-load-more-scenes="${esc(episodeId)}">
             Show ${Math.min(ASSEMBLY_PAGE_SIZE, remaining)} more (${Math.min(state.episodeAssemblyVisibleCount, scenes.length)} of ${scenes.length} shown)
           </button>
         </div>`
      : "";

    container.innerHTML = `
      <div class="section-header" style="margin-bottom:12px;">
        <div class="eyebrow" style="margin:0;">Video Assembly Assets</div>
      </div>
      ${statsBar}
      <div class="scene-grid">${gridLayout}</div>
      ${loadMoreBtn}
    `;
```

**What this does:** After the scene grid, if there are more scenes than what's currently shown, we add a button that says something like "Show 20 more (20 of 85 shown)". When all scenes are visible, the button doesn't appear.

### Step 1.6 — Add click handler for "Load More" button

**File:** `tool1_dashboard/ui/app.js`  
**Where:** Inside the global click event delegation block. This block starts at line 6415:

```javascript
document.addEventListener("click", async (event) => {
```

There's already a handler for video placeholders at line 6417, then one for bulk upload button at ~line 6431. We need to add our handler between them.

**What to add:** After the video placeholder handler (after the `return;` at line 6428), and BEFORE the bulk upload button handler, add:

```javascript
  // "Load More" scenes pagination
  const loadMoreBtn = event.target.closest("[data-load-more-scenes]");
  if (loadMoreBtn) {
    const epId = loadMoreBtn.dataset.loadMoreScenes;
    state.episodeAssemblyVisibleCount += ASSEMBLY_PAGE_SIZE;
    await renderAssemblySection(epId, { showLoading: false });
    return;
  }
```

**How this works:**
1. User clicks the "Load More" button
2. The click delegation finds `[data-load-more-scenes]` attribute
3. We increase `episodeAssemblyVisibleCount` by 20 (e.g., from 20 to 40)
4. We re-render the assembly with `showLoading: false` (so no loading spinner)
5. The re-render now shows 40 cards instead of 20

### How to Test Phase 1

1. Start the dashboard and open an episode that has more than 20 scenes
2. You should see only 20 scene cards and a "Load More" button at the bottom
3. Click "Load More" → 20 more cards should appear, button text updates
4. When all scenes are shown, the button should disappear
5. Navigate to a different episode → should reset to 20 cards
6. Upload an asset → the grid should re-render but keep showing the current number of visible cards (NOT reset to 20, because the episode didn't change)

---

## PHASE 2: Chunked Bulk Upload with Progress

### What This Solves

Right now, `handleBulkUpload()` at line 6388 creates ONE `FormData` with ALL selected files and sends them in a single HTTP POST request. If the user selects 50 files (mix of images and generated videos), that's a massive multipart request — potentially hundreds of MB. The browser and server both struggle:

- The browser freezes while assembling the request
- The server processes each file sequentially (file copy + ffprobe metadata extraction per file)
- If anything fails or times out, the ENTIRE batch is lost
- The user sees no progress — just "Uploading 50 files..." and then nothing for a long time

**Solution:** Split the files into batches of 5 and upload each batch separately. Show progress after each batch.

### Step 2.1 — Replace `handleBulkUpload()` function entirely

**File:** `tool1_dashboard/ui/app.js`  
**Where:** The function at lines 6388-6412

**Current code (lines 6388-6412) — DELETE THIS ENTIRE FUNCTION:**
```javascript
async function handleBulkUpload(episodeId, files) {
  if (!files || files.length === 0) return;
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }
  setNotice(`Uploading ${files.length} file${files.length > 1 ? "s" : ""}…`, "neutral");
  try {
    const res = await fetch(`/api/episodes/${encodeURIComponent(episodeId)}/scenes/bulk-upload`, {
      method: "POST",
      body: formData
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Bulk upload failed");

    if (data.unmatched && data.unmatched.length > 0) {
      setNotice(`${data.matched.length} uploaded, ${data.unmatched.length} skipped (filename or type mismatch)`, "warn");
    } else {
      setNotice(`Bulk uploaded ${data.matched?.length || 0} assets`, "success");
    }
    await renderAssemblySection(episodeId);
  } catch (err) {
    setNotice(`Bulk upload failed: ${err.message}`, "error");
  }
}
```

**Replace with this NEW function:**
```javascript
async function handleBulkUpload(episodeId, files) {
  if (!files || files.length === 0) return;

  const CHUNK_SIZE = 5;
  const url = `/api/episodes/${encodeURIComponent(episodeId)}/scenes/bulk-upload`;

  // Split files into chunks of 5
  const chunks = [];
  for (let i = 0; i < files.length; i += CHUNK_SIZE) {
    chunks.push(files.slice(i, i + CHUNK_SIZE));
  }

  let totalMatched = 0;
  let allUnmatched = [];
  let failed = false;

  for (let i = 0; i < chunks.length; i++) {
    const chunk = chunks[i];
    setNotice(
      `Uploading batch ${i + 1}/${chunks.length} (${totalMatched} matched so far)…`,
      "neutral"
    );

    const formData = new FormData();
    for (const file of chunk) {
      formData.append("files", file);
    }

    try {
      const res = await fetch(url, { method: "POST", body: formData });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Batch upload failed");

      totalMatched += (data.matched?.length || 0);
      allUnmatched.push(...(data.unmatched || []));
    } catch (err) {
      setNotice(`Bulk upload failed on batch ${i + 1}: ${err.message}`, "error");
      failed = true;
      break;
    }
  }

  if (!failed) {
    if (allUnmatched.length > 0) {
      setNotice(
        `${totalMatched} uploaded, ${allUnmatched.length} skipped (filename or type mismatch)`,
        "warn"
      );
    } else {
      setNotice(`Bulk uploaded ${totalMatched} assets`, "success");
    }
  }

  await renderAssemblySection(episodeId);
}
```

**How this works step by step:**
1. Takes the array of files and splits it into groups of 5: `[file1..5], [file6..10], [file11..15]`, etc.
2. Loops through each group sequentially (one batch at a time)
3. For each batch: creates a small FormData with just 5 files, sends it to the existing backend endpoint
4. Updates the notice bar after each batch: `"Uploading batch 2/4 (5 matched so far)…"`
5. Accumulates the matched/unmatched results across all batches
6. If any batch fails, stops and shows error
7. After all batches complete, shows final summary and refreshes the grid ONCE

**Why this is safe:** The backend endpoint `/api/episodes/{episodeId}/scenes/bulk-upload` already handles any number of files — we're just calling it multiple times with smaller payloads. No backend changes needed.

**Why chunks of 5:** Each batch is small enough to complete quickly (typically under 30 seconds), prevents timeout, and gives the user visible progress. 5 files × ~10MB average = ~50MB per request, which is very manageable.

### How to Test Phase 2

1. Select 15+ files using the "Bulk Upload" button
2. Watch the notice bar at the top — it should show "Uploading batch 1/3...", "Uploading batch 2/3...", etc.
3. After all batches complete, you should see either:
   - `"Bulk uploaded 15 assets"` (all matched)
   - `"12 uploaded, 3 skipped (filename or type mismatch)"` (some unmatched)
4. The assembly grid should refresh ONCE at the end (not after each batch)
5. If you cancel or close the browser during upload, only the current batch is lost — previous batches are already saved

---

## PHASE 3: Incremental DOM Updates After Single Upload/Delete

### What This Solves

Right now, after uploading ONE file or deleting ONE asset, the code calls `renderAssemblySection(episodeId)` which:
1. Makes an API call to fetch ALL scenes
2. Rebuilds ALL HTML for ALL scene cards
3. Replaces the entire `innerHTML` of the container
4. Rebinds ALL event listeners

With pagination (Phase 1) this is less painful, but it's still unnecessary work and causes a visible flash/scroll-jump.

**Solution:** After a single upload or delete, update ONLY the one affected scene card in the DOM. Leave everything else untouched.

### Step 3.1 — Create a new backend endpoint to get a single scene

We need an API endpoint that returns data for just ONE scene instead of all scenes.

#### 3.1a — Add the service method

**File:** `tool1_dashboard/service.py`  
**Where:** After the `list_episode_scenes()` method, which ends at line 2741. Add the new method right after it (before `upload_scene_asset` which starts at line 2743).

**What to add:**
```python
    def get_single_scene(self, episode_id: str, scene_id: str) -> dict[str, Any]:
        """Return a single scene with its asset status (same shape as one element of list_episode_scenes)."""
        _, scenes = self._load_master_timeline_scenes(episode_id)
        scene = next((s for s in scenes if s["scene_id"] == scene_id), None)
        if scene is None:
            raise FileNotFoundError(f"Scene {scene_id} not found.")
        asset = self.db.get_scene_asset(episode_id, scene_id)
        asset_payload = None
        if asset is not None:
            asset_payload = {
                "filename": asset.get("original_filename") or asset.get("stored_filename"),
                "file_size": int(asset.get("file_size") or 0),
                "asset_type": asset.get("asset_type") or "image",
            }
        return {
            "scene_id": scene["scene_id"],
            "start": scene["start"],
            "end": scene["end"],
            "duration": scene["duration"],
            "text": scene["text"],
            "asset_type": scene["asset_type"],
            "asset": asset_payload,
        }
```

**Important:** This returns the EXACT same shape as one element of the `scenes` array from `list_episode_scenes()`. The frontend template expects this structure. Look at `list_episode_scenes()` at line 2706 to verify — the `payload_scenes.append(...)` at line 2725 builds the same keys.

#### 3.1b — Add the API endpoint

**File:** `tool1_dashboard/app.py`  
**Where:** After the existing `episode_scenes` endpoint at lines 467-474. Add this NEW endpoint between the `episode_scenes` endpoint (line 474) and the `upload_scene_asset` endpoint (line 477).

**CRITICAL PLACEMENT:** It MUST go BEFORE the POST route at line 477 (`/api/episodes/{episode_id}/scenes/{scene_id}/asset`). If you place it after, FastAPI's route matching could get confused because both routes start with `/api/episodes/{episode_id}/scenes/{scene_id}`.

**What to add after line 474:**
```python

@app.get("/api/episodes/{episode_id}/scenes/{scene_id}")
async def get_single_scene(episode_id: str, scene_id: str) -> dict[str, Any]:
    try:
        return service.get_single_scene(episode_id, scene_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

```

### Step 3.2 — Extract scene card HTML into a reusable function

Right now, the HTML template for each scene card is defined INLINE inside `renderAssemblySection()` at lines 6262-6306. We need to extract it into its own function so that both the full render AND the incremental update can use it. This avoids template duplication.

**File:** `tool1_dashboard/ui/app.js`  
**Where:** BEFORE `renderAssemblySection()` (before line 6235). Add this new function:

```javascript
function renderSceneCardHtml(scene, episodeId, readOnly) {
  const isUploaded = scene.asset !== null;
  const previewUrl = `/api/episodes/${encodeURIComponent(episodeId)}/scenes/${encodeURIComponent(scene.scene_id)}/asset/preview`;
  const assetMarkup = isUploaded
    ? `<div style="position:relative">
         ${scene.asset.asset_type === "video"
           ? `<div class="scene-card-video-placeholder" data-video-src="${esc(previewUrl)}">
                <div class="scene-card-video-play-overlay">
                  <svg width="48" height="48" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>
                </div>
                <div class="helper" style="font-size:0.75rem;">video &middot; ${esc(scene.asset.filename || "")}</div>
              </div>`
           : `<img src="${esc(previewUrl)}" class="scene-card-thumbnail" loading="lazy" />`
         }
         ${readOnly ? "" : `<div style="padding: 12px; display: flex; justify-content: flex-end; gap: 8px;">
           <button class="button button-ghost button-small" data-upload-asset="${esc(episodeId)}" data-scene="${esc(scene.scene_id)}">Replace</button>
           <button class="button button-ghost button-small button-danger" data-remove-asset="${esc(episodeId)}" data-scene="${esc(scene.scene_id)}">Remove</button>
          <input type="file" id="single-upload-input-${esc(scene.scene_id)}" accept="image/*,video/*" style="display:none;" data-single-upload-input="${esc(episodeId)}" data-scene="${esc(scene.scene_id)}" />
         </div>`}
       </div>`
    : readOnly
      ? `<div class="scene-card-dropzone"><div class="helper">No asset uploaded</div></div>`
      : `<div class="scene-card-dropzone" data-dropzone="${esc(episodeId)}" data-scene="${esc(scene.scene_id)}">
           <div class="helper">Drag & drop or</div>
           <button class="button button-ghost button-small" style="margin-top:8px;" data-upload-asset="${esc(episodeId)}" data-scene="${esc(scene.scene_id)}">Browse File</button>
           <input type="file" id="single-upload-input-${esc(scene.scene_id)}" accept="image/*,video/*" style="display:none;" data-single-upload-input="${esc(episodeId)}" data-scene="${esc(scene.scene_id)}" />
         </div>`;

  const timeRange = `${voiceTtsNumberValue(scene.start, 1)}s - ${voiceTtsNumberValue(scene.end, 1)}s`;

  return `
    <div class="scene-card" data-scene-id="${esc(scene.scene_id)}">
      <div class="scene-card-header">
        <div>
          <div class="scene-card-title">Scene ${esc(scene.scene_id)}</div>
          <div class="scene-card-time">${esc(timeRange)}</div>
        </div>
        <div class="asset-badge">${esc(scene.asset_type || "image")}</div>
      </div>
      <div class="scene-card-body">
        ${esc(shortText(scene.text, 60))}
      </div>
      ${assetMarkup}
    </div>
  `;
}
```

**CRITICAL NOTE:** Look at the root `<div class="scene-card" data-scene-id="...">` — the `data-scene-id` attribute is NEW. The original code at line 6293 does NOT have this attribute. We need it so we can find individual cards in the DOM for incremental updates.

### Step 3.3 — Modify `renderAssemblySection()` to use the extracted function

**File:** `tool1_dashboard/ui/app.js`  
**Where:** Inside `renderAssemblySection()`, the scene card mapping at lines 6262-6307

**Current code (lines 6262-6307) — This is the ENTIRE `.map()` block with all the template HTML inside:**

```javascript
    const gridLayout = visibleScenes.map(scene => {
      const isUploaded = scene.asset !== null;
      const previewUrl = `/api/episodes/${encodeURIComponent(episodeId)}/scenes/${encodeURIComponent(scene.scene_id)}/asset/preview`;
      // ... ~45 lines of template code ...
      return `
        <div class="scene-card">
          ...
        </div>
      `;
    }).join("");
```

**Replace the ENTIRE `.map()` block with this single line:**

```javascript
    const gridLayout = visibleScenes.map(scene => renderSceneCardHtml(scene, episodeId, readOnly)).join("");
```

**Why:** The template logic is now in `renderSceneCardHtml()` from Step 3.2. This line calls that function for each visible scene and joins the results into one HTML string — identical behavior, but the template is now reusable.

### Step 3.4 — Add `updateSingleSceneCard()` function

**File:** `tool1_dashboard/ui/app.js`  
**Where:** After `renderAssemblySection()` ends (after line 6328). Add this new function:

```javascript
async function updateSingleSceneCard(episodeId, sceneId) {
  try {
    // 1. Fetch only this one scene's data from the new endpoint
    const scene = await api(`/api/episodes/${encodeURIComponent(episodeId)}/scenes/${encodeURIComponent(sceneId)}`);

    // 2. Find the existing card in the DOM
    const container = $("episode-assembly-section");
    if (!container) return;
    const existingCard = container.querySelector(`[data-scene-id="${CSS.escape(sceneId)}"]`);

    if (!existingCard) {
      // Card is not visible (beyond the "Load More" cutoff) — fall back to full refresh
      await renderAssemblySection(episodeId, { showLoading: false });
      return;
    }

    // 3. Determine if we're in read-only mode by checking if bulk upload button exists
    const readOnly = !container.querySelector("[data-bulk-upload]");

    // 4. Build the new card HTML using the same template function
    const tempDiv = document.createElement("div");
    tempDiv.innerHTML = renderSceneCardHtml(scene, episodeId, readOnly);
    const newCard = tempDiv.firstElementChild;

    // 5. Replace just this one card in the DOM
    existingCard.replaceWith(newCard);

    // 6. Re-bind file input listeners on the new card only
    bindEpisodeAssemblyUploadInputs(newCard);

    // 7. Update the stats bar upload count (fetch fresh counts from API)
    const progressEl = container.querySelector(".asset-upload-progress");
    if (progressEl) {
      const data = await api(`/api/episodes/${encodeURIComponent(episodeId)}/scenes`);
      progressEl.textContent = `${data.uploaded_count}/${data.total_scenes} assets uploaded`;
    }

    // 8. Update the assembly cache so tab switching shows correct state
    const cachedStage = container.dataset.assemblyStage || state.episodeDetail?.episode?.current_stage || null;
    updateEpisodeAssemblyCache(episodeId, cachedStage, container.innerHTML);
  } catch (err) {
    // If anything goes wrong, fall back to a full re-render (safe fallback)
    await renderAssemblySection(episodeId, { showLoading: false });
  }
}
```

**How this works step by step:**
1. Calls the NEW API endpoint `GET /api/episodes/{episodeId}/scenes/{sceneId}` to get just this one scene's data
2. Finds the existing card in the DOM using `[data-scene-id="..."]` (the attribute we added in Step 3.2)
3. If the card isn't in the DOM (user hasn't scrolled to it yet because of pagination), falls back to full re-render
4. Builds new HTML for just that one card using `renderSceneCardHtml()`
5. Replaces the old card with the new one using `replaceWith()` — the rest of the DOM is untouched
6. Binds event listeners on just the new card
7. Updates the upload progress counter in the stats bar
8. Updates the assembly cache
9. If anything fails, safely falls back to full re-render

### Step 3.5 — Modify `handleAssetUpload()` to use incremental update

**File:** `tool1_dashboard/ui/app.js`  
**Where:** Inside `handleAssetUpload()` at lines 6368-6386

**Find line 6382:**
```javascript
    await renderAssemblySection(episodeId);
```

**Replace with:**
```javascript
    await updateSingleSceneCard(episodeId, sceneId);
```

**Why:** After uploading a single file, we now update only the one card that changed, instead of rebuilding the entire grid.

### Step 3.6 — Modify the delete handler to use incremental update

**File:** `tool1_dashboard/ui/app.js`  
**Where:** Inside the global click delegation block, the remove/delete button handler. Find line 6456 (inside the `removeBtn` handler).

**Find line 6456:**
```javascript
      await renderAssemblySection(epId);
```

**Replace with:**
```javascript
      await updateSingleSceneCard(epId, sceneId);
```

**Why:** Same reason — deleting one asset should only update that one card.

### Step 3.7 — Keep bulk upload using full re-render (DO NOT CHANGE)

The `handleBulkUpload()` function (from Phase 2) should continue calling `renderAssemblySection(episodeId)` after all chunks complete. This is correct because bulk upload affects many cards at once — incremental updates would be more complex than a full refresh here.

### How to Test Phase 3

1. Upload a single asset to a scene using "Browse File" or drag-and-drop
   - Only that one card should update (open DevTools → Elements panel to verify other cards aren't recreated)
   - The stats bar counter should update (e.g., "5/85 assets uploaded" → "6/85 assets uploaded")
2. Click "Remove" on an asset
   - Only that card should revert to the empty dropzone state
   - Stats bar counter should decrease
3. Click "Replace" on an asset
   - Only that card should update with the new preview
4. Bulk upload should still do a full grid refresh (this is correct — Phase 2 behavior)
5. If the scene card being updated is NOT currently visible (past the "Load More" cutoff), verify it falls back to full refresh gracefully without errors

---

## VERIFICATION CHECKLIST

After implementing all 3 phases, verify each item:

- [ ] Episode with 50+ scenes only shows ~20 cards initially
- [ ] "Load More" button appears and shows accurate count text
- [ ] Clicking "Load More" shows more cards without a loading spinner
- [ ] Button disappears when all scenes are shown
- [ ] Navigating to a different episode resets to 20 cards
- [ ] Bulk upload of 15+ files shows batch progress messages
- [ ] No single bulk upload request sends more than 5 files
- [ ] After bulk upload completes, grid refreshes once showing all matched assets
- [ ] Single upload via "Browse File" updates only the affected card
- [ ] Single upload via drag-and-drop updates only the affected card
- [ ] Delete asset updates only the affected card
- [ ] Replace asset updates only the affected card
- [ ] Stats bar counter ("X/Y assets uploaded") stays accurate after all operations
- [ ] Video placeholder click-to-play still works
- [ ] Assembly cache is correct (switching tabs and coming back shows right state)
- [ ] No JavaScript console errors during any of these operations
- [ ] The `GET /api/episodes/{episode_id}/scenes/{scene_id}` endpoint returns correct data

---

## FILES MODIFIED — SUMMARY

| File | What Changes |
|------|-------------|
| `tool1_dashboard/ui/app.js` | Add `ASSEMBLY_PAGE_SIZE` constant, add `episodeAssemblyVisibleCount` to state, modify `renderAssemblySection()` for pagination + Load More button, rewrite `handleBulkUpload()` for chunked uploads, extract `renderSceneCardHtml()` helper, add `updateSingleSceneCard()`, add "Load More" click handler, change single upload + delete to use incremental updates |
| `tool1_dashboard/app.py` | Add `GET /api/episodes/{episode_id}/scenes/{scene_id}` endpoint (between lines 474 and 477) |
| `tool1_dashboard/service.py` | Add `get_single_scene()` method (after line 2741) |
| `tool1_dashboard/ui/app.css` | No changes needed |
| `tool1_dashboard/database.py` | No changes needed |
