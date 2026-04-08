## Plan: MEDIUM Priority Fixes (F7 + B3 + B4)

> **Date:** 2026-04-08
> **Audit ref:** `PERFORMANCE_AUDIT.md` findings F7, B3, B4
> **Checklist ref:** `PERFORMANCE_CHECKLIST.md`
> **Status:** Complete (`F7` + `B3` + `B4` finished on 2026-04-08; plan moved to `plans/completed/`)

Three independent fixes. Each is small and self-contained. Implement in any order. None of them touch the same files, so they cannot collide.

> **Note on excluded findings:** F6 (SSE leaks) is grouped into `PERF_PLAN_HIGH_FINDINGS.md` because it's the same subsystem as F4/F5. B5 (MFA) and B6 (TTS) are intentionally not in any plan — both are inherent hardware/CPU constraints with no quick win. They are documented in the audit for awareness only.

---

## Fix F7: Assembly HTML Cache LRU Eviction

### Problem

`state.episodeAssemblyCache` (`app.js:147`) stores the rendered HTML of the assembly section per episode the user has visited. Each entry can be 50–200 KB. The cache is purely additive — entries are only removed when:

- The user navigates away from an episode entirely (`resetEpisodeSupplementalState` at `app.js:582`)
- The current episode is no longer in an assembly stage (`renderEpisodeAssemblySection` at `app.js:6245`)

If the user browses through 20 episodes during a session without leaving the episode view in a way that triggers the reset, the cache holds 1–4 MB of stale HTML strings forever. Combined with other memory pressure (F5 SSE logs, F8 JSON re-stringification), this contributes to slow gradual session degradation.

### Solution: Replace plain object with size-capped LRU Map

Use a `Map` instead of a plain object. `Map` preserves **insertion order**, which gives us LRU semantics for free if we re-insert the entry on every access. When the map exceeds the cap, delete the first key (the oldest).

**Why a Map and not a custom LRU class:** This is ~10 lines of logic. We don't need a generic LRU utility. Keep it inline and obvious.

**Cap value:** **5 episodes**. Rationale: a user typically works on 1–3 episodes at a time, and 5 gives headroom for tab-switching between recent episodes without re-fetching. At ~100 KB per entry, 5 × 100 KB = 500 KB peak — bounded and acceptable.

### Task F7.1 — Convert `episodeAssemblyCache` to a Map

**File:** `tool1_dashboard/ui/app.js`
**Where:** Line 147 (state declaration)

Change `episodeAssemblyCache: {}` to `episodeAssemblyCache: new Map()`.

This is a one-character mental shift but it changes how every reader interacts with the cache. The next two tasks update each access site.

### Task F7.2 — Update the cache writer to enforce LRU + cap

**File:** `tool1_dashboard/ui/app.js`
**Where:** `updateEpisodeAssemblyCache()` at lines 1091–1098

Current code uses bracket assignment (`state.episodeAssemblyCache[episodeId] = ...`). Change it to Map operations with LRU + eviction.

The logic should be:
1. If the key already exists, `delete` it first (so the re-insertion bumps it to the end of the insertion order — most-recently-used).
2. `set` the new value.
3. If `size > MAX_ASSEMBLY_CACHE_SIZE`, delete the first key returned by `keys().next().value` (least-recently-used).

Define `const MAX_ASSEMBLY_CACHE_SIZE = 5;` near the top of the file alongside other constants (around line 64–65 where `REFRESH_INTERVAL_MS` lives).

**Why delete-then-set:** Maps preserve insertion order. Re-setting an existing key does NOT re-order it — it keeps its original position. To bump to MRU, you must delete first.

### Task F7.3 — Update the cache reader to bump LRU on access

**File:** `tool1_dashboard/ui/app.js`
**Where:** `renderEpisodeAssemblySectionShell()` at line 1104

Current code uses `state.episodeAssemblyCache[episodeId]`. Change it to `state.episodeAssemblyCache.get(episodeId)`.

**Optional but recommended:** When a hit occurs, also delete-then-re-insert the entry to bump it to MRU. Strict LRU updates on read make the cache reflect actual usage instead of just write order. If the read site is hot and you're worried about cost, skip this — write-order LRU is usually good enough for a 5-entry cache.

### Task F7.4 — Update the two delete sites

**File:** `tool1_dashboard/ui/app.js`
**Where:** Lines 582 (full reset) and 584/6245 (per-episode delete)

- Line 582: `state.episodeAssemblyCache = {};` → `state.episodeAssemblyCache.clear();`
- Line 584: `delete state.episodeAssemblyCache[episodeId];` → `state.episodeAssemblyCache.delete(episodeId);`
- Line 6245: same as line 584

### Verification

- Open DevTools → Memory → take a heap snapshot
- Visit 10 different episodes that have assembly stages
- Take another heap snapshot
- Inspect `state.episodeAssemblyCache` — its `size` should be exactly 5, and the entries should be the 5 most recently visited episodes
- Browse a 6th new episode → the oldest cached one should be evicted automatically
- Verify no errors in console (cache reads/writes still work normally)

---

## Fix B3: Worker DB Polling Backoff When Idle

### Problem

The background worker loop at `service.py:210–220` runs forever:

```python
while not self._stop_event.is_set():
    episode = self.db.next_queued_episode()      # DB query #1
    if episode is not None:
        self._process_episode(episode)
        continue
    self._check_paused_tts_episodes()             # DB query #2
    self._check_stale_provider_stage_runs()       # DB query #3 (loads ALL running stage runs)
    with self._condition:
        self._condition.wait(timeout=1.0)         # Sleep 1 second, repeat
```

Even when the dashboard is **completely idle**, the worker makes 3 DB queries per second — 10,800 queries per hour, 24/7. SQLite uses file-level locking, so these queries contend with frontend API queries from `app.py`.

### Critical insight: the wait is interruptible

The worker uses `threading.Condition`. When a user starts new work via `start_episode_workflow()`, the code calls `self._condition.notify()` at `service.py:3551`. **This wakes the worker immediately** regardless of how long the timeout is. So we can safely raise the timeout from 1 s → much longer without impacting user-initiated work.

The 1-second timeout only matters for **time-based polling** of two situations:

1. **TTS resume detection** (`_check_paused_tts_episodes`): a TTS job naturally completes in the background and the worker needs to notice.
2. **Stale stage detection** (`_check_stale_provider_stage_runs`): a hung subprocess needs to be marked failed after `TOOL1_PROVIDER_STAGE_STALE_SECONDS` (default 900 s).

For both, **lag of 5–30 seconds is fine**:
- TTS jobs take minutes — a 30 s detection lag is invisible.
- Stale-stage detection is already on a 900 s timeout — checking every 30 s instead of every 1 s changes nothing.

### Solution: Exponential backoff on idle, instant wake on work

The principle: when the worker finds work, reset the wait to a short interval. When it finds nothing, double the wait up to a cap. This gives instant responsiveness when active and minimal DB pressure when idle.

### Task B3.1 — Add backoff state to the worker

**File:** `tool1_dashboard/service.py`
**Where:** `Tool1Service.__init__` around lines 165–180

Add two instance attributes:
- `self._idle_wait_seconds: float = 5.0` — current wait interval, mutated by the loop
- Constants at module level (or class-level): `IDLE_WAIT_MIN_SECONDS = 5.0`, `IDLE_WAIT_MAX_SECONDS = 30.0`

**Why these numbers:**
- 5 s minimum is 5× lighter than current and still well below TTS job duration
- 30 s maximum is well below the 900 s stale-stage timeout — stale detection still happens long before timeout matters
- Both are tunable via env var if you want, but hard-coded is fine for a first pass — only add env vars if a need appears

### Task B3.2 — Implement backoff in `_worker_loop`

**File:** `tool1_dashboard/service.py`
**Where:** `_worker_loop()` at lines 210–220

Restructure the loop so:

1. **When work is found** (`episode is not None`): process it, then **reset** `self._idle_wait_seconds = IDLE_WAIT_MIN_SECONDS` before the next iteration.
2. **When no work is found**: run the time-based checks, wait `self._idle_wait_seconds`, then **double** the interval (capped at `IDLE_WAIT_MAX_SECONDS`).

Pseudocode for the loop body:

```
while not stop:
    episode = next_queued_episode()
    if episode is not None:
        process_episode(episode)
        self._idle_wait_seconds = IDLE_WAIT_MIN_SECONDS  # reset
        continue
    check_paused_tts()
    check_stale_runs()
    with self._condition:
        self._condition.wait(timeout=self._idle_wait_seconds)
    self._idle_wait_seconds = min(self._idle_wait_seconds * 2, IDLE_WAIT_MAX_SECONDS)
```

**Important:** The doubling happens **after** the wait returns, not before. This way:
- A `notify()` from `start_episode_workflow` interrupts the wait early; the next loop iteration finds work and resets.
- A natural timeout completes the wait, then doubles for the next round.

### Task B3.3 — Reset backoff on `notify()` paths (no code change required)

The condition variable handles this automatically. When `start_episode_workflow` calls `self._condition.notify()` at line 3551, the worker wakes up, finds the queued episode in `next_queued_episode()`, processes it, and resets `self._idle_wait_seconds` to `IDLE_WAIT_MIN_SECONDS` per Task B3.2.

**No additional changes are needed at the `notify()` site.** This is here to confirm that the existing notify path doesn't need touching.

### Task B3.4 — Verify the TTS recovery flow still works

**File:** `tool1_dashboard/service.py`
**Where:** Read `_check_paused_tts_episodes()` at line 5338

This is a **read-only verification step**. The TTS recovery flow needs the worker to discover that paused-for-tts episodes have completed. With the new backoff (5–30 s), discovery lag goes from ~1 s to up to 30 s.

Ensure this is acceptable by reviewing how `_check_paused_tts_episodes()` is invoked elsewhere (search for the function name). If TTS completion has its own callback that calls `self._condition.notify()`, the lag is zero. If not, the lag is up to 30 s — which is fine because TTS jobs take minutes.

**Optional improvement (do NOT do as part of this fix):** If you find that TTS completion has a hook that could call `self._condition.notify()`, add the call there. But this is a separate optimization — the current plan does not require it. Ship F7/B3/B4 first; only revisit if 30 s TTS resume lag is reported as a problem.

### Verification

- Start the dashboard with no episodes queued
- Add a logging statement temporarily: `log.info(f"worker idle wait = {self._idle_wait_seconds}s")` right before the `wait()` call
- Watch the logs over 2 minutes — you should see the wait interval double from 5 → 10 → 20 → 30 → 30 → 30 …
- Queue an episode → the wait should be interrupted immediately, the episode should start processing within ~100 ms, and after processing the next idle cycle should reset to 5 s
- Remove the temporary log statement before committing
- Run `python -m pytest tests/test_video_pipeline.py -q` to confirm no regressions

---

## Fix B4: Parallel Translation Across Languages

### Problem

`_episode_run_translations()` at `service.py:4498–4626` translates each non-master language **sequentially**:

```python
for lang in non_master_langs:
    ...
    result = asyncio.run(translation_svc.translate_script(...))   # blocks until done
    ...
```

Each call to `asyncio.run()` creates a fresh event loop, runs one language to completion, then tears down the loop. For 10 target languages with ~5 minutes per language, that's 50 minutes of strictly serial work.

The audit also flags chunk-level serialization within `translate_script()`. **That part cannot be fixed:** each chunk's translation feeds context (`words[-context_tail_words:]`) into the next chunk for continuity (`translation/service.py:498`). Parallelizing chunks would lose continuity. **Leave the chunk loop alone.**

But **languages are fully independent**. Different prompts, different outputs, different files, different DB rows. Parallelizing across languages is safe and gives near-linear speedup bounded only by the OpenAI API rate limits.

### Critical constraint: API rate limits, not local CPU

This is **API-bound work**, not local-compute-bound. The user's hardware constraints (TTS one-at-a-time, MFA sequential) don't apply here — translation does almost no local work, it's all `await` on HTTP requests to OpenAI. Per the user's notes in `hardware_constraints.md`, parallelizing API-bound work is explicitly OK.

The real ceiling is:
- OpenAI per-key TPM (tokens-per-minute) limits
- Risk of one stalled request blocking the rest

A `Semaphore` solves both: cap concurrent in-flight languages to 3–5, and one stall doesn't block others past the cap.

### Solution: Single event loop, gather across languages, semaphore-capped

Replace the per-language `asyncio.run()` calls with one outer `asyncio.run()` that calls `asyncio.gather()` over all languages. Wrap each language's coroutine in a semaphore so we don't fire all 10 simultaneously.

### Task B4.1 — Extract per-language work into an async helper

**File:** `tool1_dashboard/service.py`
**Where:** Inside `_episode_run_translations()` at line 4498

Refactor the body of the `for lang in non_master_langs:` loop into a new **async** helper inside the same function (or as a private method on the class). Call it `_run_one_language_translation(lang, ...)`. It should accept the per-language inputs and return either a success marker or raise (the existing try/except already handles failure as a status update).

**Important — what stays sequential inside this helper:**

- Profile lookup
- DB status updates (`update_episode_language_status`)
- File writes (`write_json`, `_write_language_script_assets`)
- The chunk loop inside `translate_script()` itself (already sequential — do not touch)

**What runs concurrently:** The `await translation_svc.translate_script(...)` calls across different languages.

**Why a helper and not inlining gather:** Keeps the per-language try/except around DB status updates clean and avoids tangling the failure-handling logic with the gather call.

### Task B4.2 — Replace the sequential loop with `asyncio.gather` under a semaphore

**File:** `tool1_dashboard/service.py`
**Where:** `_episode_run_translations()` after the helper from B4.1 is extracted

Build the new outer flow:

```
async def _run_all():
    sem = asyncio.Semaphore(TRANSLATION_MAX_CONCURRENT_LANGUAGES)
    async def _bounded(lang):
        async with sem:
            await self._run_one_language_translation(lang, ...)
    await asyncio.gather(*(_bounded(lang) for lang in non_master_langs), return_exceptions=True)

asyncio.run(_run_all())
```

Define `TRANSLATION_MAX_CONCURRENT_LANGUAGES = 4` as a module constant near the top of `service.py`.

**Why 4:**
- Most users run 5–10 target languages — 4 concurrent gives meaningful speedup without saturating typical OpenAI tier-1 rate limits
- Leaves room for other API traffic (review steps, alignment provider calls)
- Tunable later if profiling shows headroom

**`return_exceptions=True` is important:** The current loop catches per-language exceptions and converts them to a `failed` DB status (line 4615–4623). With `gather`, individual language failures should not abort the whole gather. By returning exceptions, the helper's own try/except still does the DB write, and the gather completes for all languages.

### Task B4.3 — Verify the post-gather "all failed" check still works

**File:** `tool1_dashboard/service.py`
**Where:** Lines 4625–4626

Current code:
```python
if failed_count == len(non_master_langs) and non_master_langs:
    raise RuntimeError("All translations failed.")
```

`failed_count` is incremented inside the synchronous loop. After moving to `gather`, this counter must be incremented from inside the helper — either via a shared `nonlocal` counter or by counting failed DB statuses after the gather completes.

**Recommended approach:** After `gather` returns, re-query `get_episode_language_statuses(episode_id)` and count entries with `translation_status == "failed"`. This is robust because it sources truth from the DB rather than from in-memory state shared across coroutines.

### Task B4.4 — Verify thread-safety of DB writes from concurrent coroutines

**File:** `tool1_dashboard/database.py` (read-only investigation)

The existing code does DB writes (`update_episode_language_status`, `write_json`) sequentially. Under the new design, multiple coroutines may call DB methods at roughly the same time. SQLite supports concurrent writes via its internal lock, but the Python wrapper class needs to be safe.

Read `Tool1Database` to confirm:
- Connection is created per-call OR connection is protected by a thread lock
- `update_episode_language_status` does not hold any class-level mutable state

If the connection is shared and unprotected, B4 needs to wrap DB calls in a `threading.Lock` or use `asyncio.Lock`. Report what you find before implementing — this may turn into a small B4.5 task.

**Output of this task:** A 2-line note in the implementation PR confirming whether B4.5 is needed or not.

### Task B4.5 (conditional) — Add a DB write lock around per-language status updates

**Only needed if B4.4 finds that DB writes from multiple coroutines are unsafe.**

**File:** `tool1_dashboard/service.py`

Add `self._translation_db_lock = asyncio.Lock()` to `Tool1Service.__init__`. Wrap each `self.db.update_episode_language_status(...)` call inside the helper from B4.1 with `async with self._translation_db_lock:`.

**Why an asyncio.Lock and not a threading.Lock:** All the coroutines run in the same event loop on the same thread. A `threading.Lock` would work but adds unnecessary overhead. An `asyncio.Lock` is the natural fit.

### Verification

- Pick an episode with at least 4 configured target languages
- Trigger the translation stage
- Tail the dashboard logs — you should see translation starts for multiple languages within the first second, instead of one starting and the next waiting 5 minutes
- Confirm in DB that all language statuses end as `done` (or `failed` for any with profile issues)
- Translation total time should drop from `(N × per-language)` toward `(ceil(N / 4) × per-language)`
- Run `python -m pytest tests/test_video_pipeline.py -q` to confirm no regressions
- Test failure case: deliberately misconfigure one language's translation profile → that language should fail individually, others should still succeed

---

## Implementation Checklist

### F7 — Assembly Cache LRU
- [x] F7.1: Convert `episodeAssemblyCache` from `{}` to `new Map()` at `app.js:147`
- [x] F7.2: Update `updateEpisodeAssemblyCache()` to use `delete`+`set` and enforce `MAX_ASSEMBLY_CACHE_SIZE = 5`
- [x] F7.3: Update `renderEpisodeAssemblySectionShell()` to read via `Map.get`
- [x] F7.4: Update the 3 delete sites (`app.js:582`, `584`, `6245`) to use `Map` API

### B3 — Worker Idle Backoff
- [x] B3.1: Add `_idle_wait_seconds`, `IDLE_WAIT_MIN_SECONDS=5`, `IDLE_WAIT_MAX_SECONDS=30`
- [x] B3.2: Implement reset-on-work, double-on-idle in `_worker_loop()`
- [x] B3.3: (No change required — `notify()` path already wakes the loop)
- [x] B3.4: Read-only check that 30 s TTS resume lag is acceptable (`_check_paused_tts_episodes()` is still only polled from `_worker_loop()`, and that lag is acceptable because TTS jobs already take minutes)

### B4 — Parallel Translation Across Languages
- [x] B4.1: Extract per-language work into async helper `_run_one_language_translation`
- [x] B4.2: Replace sequential loop with `asyncio.gather` + `Semaphore(4)`
- [x] B4.3: Move "all failed" check to source from DB after gather completes
- [x] B4.4: Investigate `Tool1Database` thread-safety for concurrent coroutine writes (read-only)
- [x] B4.5 (conditional): Not needed — `Tool1Database` already uses `self._lock` plus a fresh SQLite connection per call, so no extra async lock was added
- [x] Verify with `python -m pytest tests/test_video_pipeline.py -k "translations_with_mock_service or translations_run_languages_concurrently_with_semaphore or translations_fail_when_service_returns_empty_script" -q` (`3` passing) and `python -m pytest tests/test_video_pipeline.py -q` (`76` passing)
