# Performance Audit: Creator Studio Tool 1

> **Date:** 2026-04-08  
> **Context:** The user's machine struggles under the dashboard's current workload. This audit identifies every operation that contributes to CPU, memory, network, or GPU pressure — beyond the assembly upload issues already addressed in `ASSEMBLY_LIGHTWEIGHT_PLAN.md`.  
> **Purpose:** Reference document for future optimization plans. Each finding includes impact level, evidence, and root cause so that fix plans can be designed later.

---

## Impact Scale

| Level | Meaning |
|-------|---------|
| **CRITICAL** | Actively causes freezes, crashes, or makes the app unusable on a weak machine. Must be fixed. |
| **HIGH** | Noticeably degrades performance during normal use. Strongly recommended to fix. |
| **MEDIUM** | Causes slowdown under specific conditions (long sessions, large episodes, many languages). Should fix when possible. |
| **LOW** | Minor inefficiency. Fix if convenient or as part of a related change. |

---

## FRONTEND FINDINGS

---

### F1. Aggressive Polling Refresh Loop

**Impact:** CRITICAL  
**Files:** `tool1_dashboard/ui/app.js`  
**Lines:** 5029-5039 (main refresh interval), 64-65 (interval constants)

**What happens:**  
The app runs a `setInterval` loop that calls `refreshData()` at a fixed interval:
- **Every 5 seconds** when idle (`REFRESH_INTERVAL_MS = 5000`)
- **Every 1 second** when any episode has an active workflow (`ACTIVE_REFRESH_INTERVAL_MS = 1000`)

The interval switches to 1-second mode as soon as any episode is `queued`, `running`, or `paused_for_tts`.

**Why it's a problem:**  
Each `refreshData()` call triggers up to **10 simultaneous API requests** (see F2). At 1-second intervals, the browser is making 10 HTTP requests per second while also re-rendering the entire UI (see F3). On a weak machine, the previous refresh cycle hasn't even finished before the next one starts, creating a cascading backlog that freezes the UI.

**Root cause:** The refresh interval was designed for responsiveness during active workflows, but 1 second is too aggressive for the amount of work each refresh does.

---

### F2. 10 Parallel API Calls Per Refresh Cycle

**Impact:** CRITICAL  
**Files:** `tool1_dashboard/ui/app.js`  
**Lines:** 4852-4884 (promise creation), 4897-4908 (Promise.all)

**What happens:**  
Every refresh cycle fires all of these API calls in parallel via `Promise.all()`:

1. `GET /api/health`
2. `GET /api/settings`
3. `GET /api/niche-projects` (always fetched, even when not on that page)
4. `GET /api/episodes` (board episodes)
5. `GET /api/target-languages`
6. `GET /api/voice-profiles`
7. `GET /api/worker/health`
8. `GET /api/runtime`
9. `GET /api/translation-profiles`
10. `GET /api/niche-projects/{id}` (if viewing a project)

**Why it's a problem:**  
- At 1-second polling (F1), this is **10 requests/second** sustained
- All responses arrive roughly simultaneously, each triggering JSON parsing and state updates
- No request deduplication — if a previous cycle's requests are still pending, new ones are fired anyway
- `niche-projects` is always fetched even when the user is on the Settings or Voice Profiles page
- On slow machines, the network queue grows faster than it drains, eventually causing timeouts and retries

**Root cause:** The refresh function fetches everything regardless of which view is active, and there's no cancellation of in-flight requests when a new cycle starts.

---

### F3. Full Page Re-render on Every Refresh

**Impact:** CRITICAL  
**Files:** `tool1_dashboard/ui/app.js`  
**Lines:** 4803-4828 (`renderApp()`), called from lines 5038, 5077, 5079, 5091, and many other places

**What happens:**  
`renderApp()` is called after every refresh cycle and after most user actions. It rebuilds the entire visible UI by assigning to `innerHTML` on:
- The sidebar (`$("sidebar").innerHTML = ...`)
- The topbar (`$("topbar").innerHTML = ...`)
- The main view (`$("view").innerHTML = ...`) — this is the entire page content

**Why it's a problem:**  
- `innerHTML` assignment destroys ALL existing DOM nodes and creates new ones from scratch
- Every CSS style is recalculated, every layout reflows, every image re-evaluates loading
- Scroll positions are lost (the code tries to restore them, but it's fighting the symptom)
- Focus states are lost (if user is typing in a field, it gets destroyed and recreated)
- Combined with 1-second polling (F1), this means the entire page is torn down and rebuilt every second during active workflows
- Event listeners bound after render are re-bound every cycle

**Root cause:** No diffing or selective update mechanism. The entire rendering model is "rebuild everything from scratch" — fine for initial render, catastrophic when repeated every second.

---

### F4. Elapsed Timer DOM Scan Every Second

**Impact:** HIGH  
**Files:** `tool1_dashboard/ui/app.js`  
**Lines:** 5044-5054

**What happens:**  
A separate `setInterval` runs every 1 second. It calls `document.querySelectorAll(".running-elapsed")` and updates the text content of each matching element with a recalculated elapsed time.

**Why it's a problem:**  
- `querySelectorAll` scans the entire DOM tree every second
- On a page with hundreds of scene cards and episode elements, this scan is expensive
- Each text update triggers a micro-reflow
- This timer runs INDEPENDENTLY of the main refresh timer, so the DOM is being hammered from two directions simultaneously

**Root cause:** Elapsed time display uses a brute-force "scan and update" approach instead of tracking known elements.

---

### F5. Unbounded SSE Log DOM Growth

**Impact:** HIGH  
**Files:** `tool1_dashboard/ui/app.js`  
**Lines:** 6735-6739

**What happens:**  
When a video render is in progress, the SSE (Server-Sent Events) handler receives log messages and appends each one as a new `TextNode` to a `<div>` in the DOM:

```javascript
data.new_logs.forEach(msg => {
  logOut.appendChild(document.createTextNode(msg.message + "\n"));
});
logOut.scrollTop = logOut.scrollHeight;
```

**Why it's a problem:**  
- No maximum number of log lines — the DOM grows indefinitely
- A 30-minute video render can produce thousands of log messages
- Each `appendChild` triggers a reflow
- `scrollTop = scrollHeight` after every message triggers another reflow
- After enough messages, the DOM itself becomes a memory hog (hundreds of thousands of text nodes)
- The CSS limits the visual area (`max-height: 200px; overflow-y: auto`), but the DOM keeps growing behind the scroll

**Root cause:** No log rotation or cap. Messages are accumulated forever during a render session.

---

### F6. SSE Connection Leaks

**Impact:** MEDIUM  
**Files:** `tool1_dashboard/ui/app.js`  
**Lines:** 6716-6756

**What happens:**  
When a video render starts, an `EventSource` connection is opened to `/api/episodes/{id}/assembly/render/{jobId}/events` and stored in `renderEventSources[jobId]`. The connection listens for `"update"` events.

**Why it's a problem:**  
- If a render job fails silently on the backend, the SSE connection stays open indefinitely
- No timeout or keep-alive check — if the server stops sending events, the connection hangs
- Both `addEventListener("update", ...)` and `onmessage = ...` are registered (lines 6750-6751), meaning each message is handled TWICE
- Navigating away from the episode doesn't close the connection — it stays in `renderEventSources`
- Each open SSE holds a browser network socket and server thread

**Root cause:** No cleanup lifecycle for SSE connections — no timeout, no navigation cleanup, duplicate handlers.

---

### F7. Assembly HTML Cache Grows Unbounded

**Impact:** MEDIUM  
**Files:** `tool1_dashboard/ui/app.js`  
**Lines:** 147 (state declaration), 1090-1097 (cache write)

**What happens:**  
`state.episodeAssemblyCache` stores the full `innerHTML` string of the assembly section for each episode the user visits. This cache is never evicted.

**Why it's a problem:**  
- Each cached entry can be 50-200KB of HTML (depending on scene count and asset previews)
- Browsing through 20 episodes = 1-4MB of cached HTML strings sitting in memory
- The cache is purely additive — nothing is ever removed during a session
- Combined with other memory pressure (F5, F8), this contributes to gradual session degradation

**Root cause:** No eviction policy or maximum cache size.

---

### F8. Large JSON Stringification on Review Render

**Impact:** LOW-MEDIUM  
**Files:** `tool1_dashboard/ui/app.js`  
**Lines:** 1112-1115, 4189-4222

**What happens:**  
When the review section renders, it serializes large data structures with `JSON.stringify(data, null, 2)`:
- Consistency guides (can be 50-100KB of JSON)
- Timeline drafts (100+ scenes = 100KB+ of JSON)
- Prompt lists

These serialized strings are placed into `<textarea>` elements for display/editing.

**Why it's a problem:**  
- `JSON.stringify` with pretty-printing on large objects is CPU-expensive
- This happens on every render cycle (F3) — the same data is re-serialized every 1-5 seconds
- Large textareas with 100KB+ of content are expensive to render and scroll

**Root cause:** No caching of the serialized result between identical renders.

---

### F9. Multiple querySelectorAll Scans Per Render

**Impact:** LOW-MEDIUM  
**Files:** `tool1_dashboard/ui/app.js`  
**Lines:** 5810-5816, 406-413, 6456-6461

**What happens:**  
Several `querySelectorAll()` calls scan the DOM on every render cycle or frequently:
- `".lang-voice-select"` — syncing voice dropdowns (line 5810)
- `"[data-bulk-upload-input]"` / `"[data-single-upload-input]"` — rebinding file inputs (line 6456)
- Episode files list scroll restoration (lines 406-413)

**Why it's a problem:**  
- Each `querySelectorAll` walks the DOM tree
- When the DOM is large (100+ scene cards, multiple sections), each scan takes measurable time
- Combined with F3 (full re-render every cycle), these scans happen every 1-5 seconds
- Individually small, but they add up in the hot path

**Root cause:** No element caching; selectors re-query the DOM instead of holding references.

---

## BACKEND FINDINGS

---

### B1. Triple Video Re-encoding (Render → Concat → Subtitles)

**Impact:** CRITICAL  
**Files:**
- `tool1_dashboard/video_assembly/render_video_scene.py` (lines 32-81) — per-scene render
- `tool1_dashboard/video_assembly/concat_scenes.py` (lines 8-36) — concatenation
- `tool1_dashboard/video_assembly/burn_subtitles.py` — subtitle burning

**What happens:**  
The video assembly pipeline encodes the video THREE separate times:

1. **Per-scene render** — Each scene is individually encoded with libx264 (H.264). For 100 scenes, this is 100 separate ffmpeg invocations with CPU-intensive encoding.
2. **Concatenation** — All rendered scenes are re-encoded into a single video file using libx264 again.
3. **Subtitle burning** — The concatenated video is re-encoded a THIRD time to overlay subtitles.

**Why it's a problem:**  
- H.264 encoding is the single most CPU-intensive operation in the entire pipeline
- Doing it 3 times means the total encoding time is roughly 3x what it needs to be
- For a 30-minute video with 100 scenes on a weak machine: easily **2-4 hours** of sustained 100% CPU
- During this time, the machine is barely usable for anything else
- No `-preset ultrafast` flag — the default preset (`medium`) prioritizes quality over speed

**Root cause:** The pipeline treats each step as isolated (render scenes → concat → burn subtitles) instead of combining passes or using stream-copy where possible.

---

### B2. Sequential ffprobe on All Scene Assets

**Impact:** HIGH  
**Files:**
- `tool1_dashboard/video_assembly/ffmpeg_utils.py` (lines 47-72) — `probe_assets()`
- `tool1_dashboard/service.py` — called during validation

**What happens:**  
Before rendering, the pipeline validates all scene assets by calling `ffprobe` on EACH file individually in a loop:

```python
def probe_assets(scenes):
    for scene in scenes:
        _probe_asset(scene)  # calls ffprobe_json() -> subprocess.run()
```

**Why it's a problem:**  
- Each `ffprobe` call is a synchronous subprocess that takes 1-5 seconds
- For 100 scenes: 100-500 seconds (2-8 minutes) of sequential blocking just to VALIDATE before rendering even starts
- This metadata (width, height, duration) is ALREADY stored in the database from upload time (see `_probe_scene_asset_metadata` in service.py:2505)
- The validation re-probes from scratch instead of reading the cached DB values

**Root cause:** The render pipeline doesn't reuse the metadata already collected during upload. It re-probes every file from disk.

---

### B3. Worker Loop Polls Database Every 1 Second

**Impact:** MEDIUM-HIGH  
**Files:** `tool1_dashboard/service.py` (lines 210-220)

**What happens:**  
The background worker runs an infinite loop:

```python
while not self._stop_event.is_set():
    episode = self.db.next_queued_episode()
    if episode is not None:
        self._process_episode(episode)
        continue
    self._check_paused_tts_episodes()       # DB query
    self._check_stale_provider_stage_runs()  # DB query: loads ALL running stage runs
    with self._condition:
        self._condition.wait(timeout=1.0)    # Sleep 1 second, then repeat
```

**Why it's a problem:**  
- Even when completely idle (no episodes queued), the worker makes 3+ DB queries every second
- `_check_stale_provider_stage_runs()` (line 234-264) calls `list_running_stage_runs()` which loads ALL running stage runs without limit
- With many episodes, these queries scan the full `stage_runs` and `episodes` tables
- SQLite uses file-level locking — frequent worker queries contend with frontend API queries
- 3,600 query cycles per hour, 24/7, even when nothing is happening

**Root cause:** Polling interval is too short for idle state. No exponential backoff when there's nothing to do.

---

### B4. Sequential Translation Across Languages

**Impact:** MEDIUM-HIGH  
**Files:**
- `tool1_dashboard/service.py` (lines 3744-3759) — translation stage entry point
- `tool1_dashboard/translation/service.py` (lines 424-549) — `translate_script()`

**What happens:**  
For each target language, the translation service translates the script chunk-by-chunk:
- Script is split into chunks
- Each chunk is sent to OpenAI API individually, awaited, then the next chunk is sent
- If quality review fails, chunks are re-translated (1-2 repair rounds)
- Languages are translated one at a time in the pipeline

For 10 languages × 100 chunks: ~1,000 sequential API calls minimum.

**Why it's a problem:**  
- `asyncio.run()` (line 3744) blocks the entire worker thread during translation
- Each API call has a 120-second timeout (adapter.py line 16)
- If one API call stalls, the entire pipeline stalls
- No parallelization across chunks or across languages
- On a weak connection, this can take 30-60 minutes for a single episode

**Root cause:** The pipeline is strictly sequential by design (per the user's hardware constraints for TTS/GPU), but translation is API-bound (not local-compute-bound) and could safely parallelize chunks or languages without impacting local resources.

---

### B5. MFA Alignment Subprocess (CPU-Intensive)

**Impact:** MEDIUM  
**Files:**
- `tool1_dashboard/alignment_tool/align_with_mfa.py` (lines 93-157)
- `tool1_dashboard/service.py` — alignment stage

**What happens:**  
Montreal Forced Aligner (MFA) runs as a subprocess to phoneme-align audio with text. It's called per-language:
- First attempt with default beam width
- If it fails, retry with wider beam (`--beam 100 --retry_beam 400`)
- For long-form audio (30+ minutes), this is extremely CPU-intensive

**Why it's a problem:**  
- MFA is CPU-bound and can take 5-30 minutes per language for a 30-minute video
- Retry with wider beam is exponentially slower than the first attempt
- Stale check timeout is 4800 seconds (80 minutes) — the system will wait that long before considering it stuck
- Multiple languages means multiple sequential MFA runs
- During MFA execution, the machine is heavily loaded

**Root cause:** MFA is inherently CPU-intensive. The retry with wider beam makes failures even more expensive. No way to offload this to a faster machine.

---

### B6. TTS GPU Memory Pressure

**Impact:** MEDIUM  
**Files:** `tool1_dashboard/tts/worker.py` (lines 238-305, 580-646)

**What happens:**  
XTTS v2 is a 3GB+ neural TTS model loaded to GPU:
- Model loads once when the TTS worker starts
- Each generation job computes voice conditioning latents (neural inference)
- Audio chunks are generated sequentially and merged in memory
- `torch.cuda.empty_cache()` is called after each job

**Why it's a problem:**  
- RTX 3050 Laptop GPU has limited VRAM — XTTS v2 fills most of it
- While TTS is running, GPU memory is not available for other tasks
- If CUDA is unavailable, silent CPU fallback makes TTS 10x slower
- `merge_wav_chunks_streaming()` reads all audio chunks into memory before writing
- Already sequential by design, but the GPU memory pressure limits what else can run

**Root cause:** This is a known hardware constraint. The user already handles it by running TTS one-at-a-time. Documented here for completeness.

---

### B7. Large File I/O During Assembly Export

**Impact:** LOW-MEDIUM  
**Files:** `tool1_dashboard/service.py` — export and assembly-related methods

**What happens:**  
During assembly validation and export:
- Full timeline JSON files are read and parsed (can be 100KB+ for large episodes)
- Scene asset files are copied/moved
- Rendered videos are written to disk (potentially GB-sized)
- All I/O is synchronous

**Why it's a problem:**  
- On machines with slow HDD (not SSD), large file operations block the worker for extended periods
- Reading/writing GB-sized video files competes with SQLite database I/O
- No buffered or streamed writing for large exports

**Root cause:** Synchronous file I/O without consideration for disk speed.

---

### B8. No Database Indexes for Common Query Patterns

**Impact:** LOW-MEDIUM  
**Files:** `tool1_dashboard/database.py` (table creation around lines 100-170)

**What happens:**  
The SQLite database is queried frequently by both the frontend API handlers and the background worker. Common query patterns include:
- Filter by `episode_id` + `language_code` (stage runs, TTS jobs)
- Filter by `pipeline_status` (queued, running, paused)
- Filter by `episode_id` (scene assets, render jobs)

**Why it's a problem:**  
- Without compound indexes on frequently-filtered columns, SQLite does full table scans
- With 100+ episodes and 500+ stage runs, full scans on every worker loop iteration (B3) add up
- SQLite's file-level locking means slow queries block everything else

**Root cause:** Indexes were likely not added during initial development when tables were small.

---

## IMPACT SUMMARY

### Critical (Fix First)
| ID | Finding | Layer | Quick Win? |
|----|---------|-------|-----------|
| F1 | Polling every 1s when active | Frontend | Yes — change 2 constants |
| F2 | 10 parallel API calls per refresh | Frontend | Medium — add route-aware fetching |
| F3 | Full page re-render every refresh | Frontend | Medium — add data-change detection |
| B1 | Triple video re-encoding | Backend | Medium — add `-preset ultrafast`, combine passes |

### High (Fix Soon)
| ID | Finding | Layer | Quick Win? |
|----|---------|-------|-----------|
| F4 | Elapsed timer DOM scan every 1s | Frontend | Yes — track elements instead of scanning |
| F5 | Unbounded SSE log DOM growth | Frontend | Yes — cap at 200 lines |
| B2 | Sequential ffprobe re-probing all assets | Backend | Yes — reuse DB metadata from upload |

### Medium (Fix When Possible)
| ID | Finding | Layer | Quick Win? |
|----|---------|-------|-----------|
| F6 | SSE connection leaks | Frontend | Small — add timeout + navigation cleanup |
| F7 | Assembly cache grows unbounded | Frontend | Small — add LRU eviction |
| B3 | Worker polls DB every 1s when idle | Backend | Yes — increase idle interval to 5-10s |
| B4 | Sequential translation (API-bound, not CPU) | Backend | Medium — parallelize chunks |
| B5 | MFA alignment CPU pressure | Backend | No quick win — inherently heavy |
| B6 | TTS GPU memory pressure | Backend | No quick win — hardware constraint |

### Low (Fix If Convenient)
| ID | Finding | Layer | Quick Win? |
|----|---------|-------|-----------|
| F8 | Large JSON re-stringification every render | Frontend | Small — cache serialized result |
| F9 | Multiple querySelectorAll scans per render | Frontend | Small — cache element refs |
| B7 | Large file I/O during assembly | Backend | No quick win |
| B8 | Missing DB indexes | Backend | Yes — add compound indexes |
