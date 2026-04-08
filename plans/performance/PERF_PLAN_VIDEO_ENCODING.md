# Plan: Eliminate Redundant Video Re-encoding (B1)

> **Date:** 2026-04-08  
> **Audit ref:** `PERFORMANCE_AUDIT.md` finding B1  
> **Checklist ref:** `PERFORMANCE_CHECKLIST.md`  
> **Status:** Ready for implementation

---

## Problem Summary

The video assembly pipeline encodes video **up to 3 times** for a single render:

| Step | File | Codec | Preset | CRF | Re-encodes? |
|------|------|-------|--------|-----|-------------|
| 1. Per-scene render | `render_video_scene.py` / `render_image_scene.py` | libx264 | *(none — ffmpeg default "medium")* | *(none — ffmpeg default 23)* | YES — necessary (applies filters) |
| 2. Concatenation | `concat_scenes.py` | libx264 | *(none)* | *(none)* | **YES — unnecessary** |
| 3. Mux voiceover | `mux_voiceover.py` | copy | N/A | N/A | NO — already optimal |
| 4. Subtitle burn | `burn_subtitles.py` | libx264 | medium | 18 | YES — necessary (subtitle filter) |

**The waste:** Step 2 (concat) re-encodes the entire video even though every scene clip was already encoded with the exact same codec, resolution, framerate, and pixel format in Step 1. This is a full CPU-bound H.264 encode of the entire video duration — for zero quality or format benefit.

**On the user's machine:** A 30-minute video at 1920x1080 with libx264 `medium` preset takes roughly 30-60 minutes to encode. Doing it 3 times = 90-180 minutes. Eliminating one pass saves 30-60 minutes. Making the remaining passes faster saves more.

---

## Solution Strategy

Three independent optimizations, ordered by impact:

1. **Concat: switch to stream copy** — The concat step should use `-c:v copy` instead of `-c:v libx264`. This makes concatenation near-instant (seconds, not minutes) because it just copies the pre-encoded bitstream without re-encoding. This is safe because all scene clips share identical encoding parameters.

2. **All encoding passes: add `-preset fast`** — The per-scene renders and subtitle burn currently use ffmpeg's default preset (`medium`) or an explicit `medium`. Switching to `fast` cuts encoding time by ~40% with negligible quality difference at the same CRF. The `ultrafast` preset saves more time but produces noticeably larger files and slightly lower quality — `fast` is the right tradeoff.

3. **Add explicit CRF to per-scene renders** — Currently scene renders have no CRF, falling back to ffmpeg's default (23). Add `-crf 20` to give consistent, controlled quality. This matters especially on the no-subtitle path where scene renders ARE the final encode.

---

## Current Pipeline Flow (what changes and what doesn't)

```
CURRENT (3 full encodes):
  scenes → [libx264 encode] → scene clips
  scene clips → [libx264 RE-ENCODE] → visual_master.mp4      ← WASTE
  visual_master + audio → [copy video, encode AAC] → final.mp4
  final + subtitles → [libx264 RE-ENCODE] → final_subtitled.mp4

AFTER (1 encode for scenes + 1 encode only if subtitles):
  scenes → [libx264 -preset fast -crf 20] → scene clips
  scene clips → [STREAM COPY] → visual_master.mp4             ← INSTANT
  visual_master + audio → [copy video, encode AAC] → final.mp4
  final + subtitles → [libx264 -preset fast -crf 18] → final_subtitled.mp4
```

**No-subtitle path goes from 2 full encodes → 1.**  
**Subtitle path goes from 3 full encodes → 2** (scene render + subtitle burn). The concat step becomes near-instant in both cases.

---

## FILES TO MODIFY

| File | Purpose |
|------|---------|
| `tool1_dashboard/video_assembly/concat_scenes.py` | Switch from libx264 re-encode to stream copy |
| `tool1_dashboard/video_assembly/render_video_scene.py` | Add `-preset fast -crf 20` |
| `tool1_dashboard/video_assembly/render_image_scene.py` | Add `-preset fast -crf 20` |
| `tool1_dashboard/video_assembly/burn_subtitles.py` | Change preset from `medium` to `fast` |

**Files NOT modified:** `pipeline.py`, `mux_voiceover.py`, `ffmpeg_utils.py`, `models.py` — no orchestration or model changes needed.

---

## Task 1: Switch concat to stream copy

**File:** `tool1_dashboard/video_assembly/concat_scenes.py`

**What to change:** In the `concatenate_scenes()` function, the ffmpeg command at lines 18-34.

**Current ffmpeg args (lines 19-33):**
```python
"ffmpeg", "-y",
"-f", "concat",
"-safe", "0",
"-i", str(concat_list_path),
"-an",
"-c:v", "libx264",
"-pix_fmt", "yuv420p",
str(visual_master_path),
```

**The change:** Replace `-c:v libx264` with `-c:v copy` and remove `-pix_fmt yuv420p` (pixel format is irrelevant when stream-copying — the bitstream is passed through as-is).

**Why this is safe:** All input scene clips are produced by `render_video_scene.py` and `render_image_scene.py` which enforce identical parameters:
- Codec: libx264 (hardcoded in both renderers)
- Resolution: `config.width` × `config.height` (same config for all scenes)
- Framerate: `config.fps` (same config for all scenes)
- Pixel format: yuv420p (hardcoded in both renderers)

When all clips share these properties, the concat demuxer with stream copy produces a valid MP4. No re-encoding needed.

**Risk:** If a scene clip is somehow corrupted or has incompatible headers, concat will fail. This is extremely unlikely since all clips are produced by the same pipeline in the same run. The existing `run_command()` error handling (raises `CommandExecutionError` on non-zero exit) will catch this.

**Verification:** Render a test episode with both image and video scenes. The visual_master.mp4 should play correctly, and the concat step should complete in seconds (not minutes).

---

## Task 2: Add preset and CRF to video scene render

**File:** `tool1_dashboard/video_assembly/render_video_scene.py`

**What to change:** In the `render_video_scene()` function, the ffmpeg command at lines 57-71.

**Current ffmpeg args (lines 57-71):**
```python
run_command(
    [
        "ffmpeg", "-y",
        "-i", str(scene.asset_path(config)),
        "-vf", ",".join(filters),
        "-an",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(output_file),
    ],
    f"render video scene {scene.scene_id}",
)
```

**The change:** Add `-preset fast` and `-crf 20` after `-c:v libx264`. The args become:
```
..., "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", ...
```

**Why `-preset fast`:** Compared to `medium` (the implicit default), `fast` is ~40% faster with ~1-2% larger file size and virtually no visible quality difference. The `faster` and `ultrafast` presets save more time but produce noticeably larger intermediates — not worth it since these temp files accumulate on disk.

**Why `-crf 20`:** Without explicit CRF, ffmpeg defaults to 23 which is decent but visibly lossy on 1080p content. CRF 20 gives better quality headroom. This matters on the **no-subtitle path** where per-scene encode is the final quality. On the subtitle path, scenes are re-encoded again at CRF 18 during subtitle burn — the CRF 20 intermediate is high enough quality that the re-encode won't introduce visible degradation.

**Verification:** Render a video scene and compare file size / visual quality to the old output. Should look identical or slightly better, and render faster.

---

## Task 3: Add preset and CRF to image scene render

**File:** `tool1_dashboard/video_assembly/render_image_scene.py`

**What to change:** In the `render_image_scene()` function, the ffmpeg command at lines 40-62.

**Current ffmpeg args (lines 40-62):**
```python
run_command(
    [
        "ffmpeg", "-y",
        "-loop", "1",
        "-framerate", str(config.fps),
        "-i", str(scene.asset_path(config)),
        "-t", f"{scene.duration:.3f}",
        "-vf", filtergraph,
        "-an",
        "-r", str(config.fps),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(output_file),
    ],
    f"render image scene {scene.scene_id}",
)
```

**The change:** Same as Task 2 — add `-preset fast` and `-crf 20` after `-c:v libx264`.

**Why:** Same reasoning as Task 2. Image scenes (still image → video with optional zoom) are encoded with libx264 and benefit equally from the faster preset.

**Verification:** Render an image scene (both static and slow_zoom_in motion) and verify output plays correctly.

---

## Task 4: Change subtitle burn preset from `medium` to `fast`

**File:** `tool1_dashboard/video_assembly/burn_subtitles.py`

**What to change:** In the `burn_subtitles()` function, the ffmpeg command at lines 24-36.

**Current args (line 31):**
```python
"-preset", "medium",
```

**The change:** Replace `"medium"` with `"fast"`.

**Why:** The subtitle burn is the final encode (when subtitles exist). It already uses CRF 18 for good quality. Switching from `medium` to `fast` preset cuts encoding time by ~40% with negligible quality impact at CRF 18. The quality-size tradeoff of `fast` vs `medium` is measured in fractions of a dB PSNR — invisible to the human eye in a YouTube video.

**Do NOT change the CRF** — it's already at 18, which is the intended final quality. Only change the preset.

**Verification:** Render an episode with subtitles. Compare the subtitled output to a previous render — subtitles should be correctly positioned and readable, video quality should be visually identical.

---

## Task 5: Verify the complete pipeline end-to-end

**Goal:** Run a full render and confirm the optimized pipeline works correctly across both paths.

**Test 1 — Episode WITH subtitles:**
1. Pick an episode that has a subtitle file (SRT)
2. Trigger a render from the dashboard
3. Verify: concat step completes in seconds (check logs — "concatenating" stage should be fast)
4. Verify: final_video_subtitled.mp4 plays correctly with subtitles visible
5. Verify: audio is in sync with video

**Test 2 — Episode WITHOUT subtitles:**
1. Pick an episode without a subtitle file, or temporarily remove it
2. Trigger a render
3. Verify: final_video.mp4 plays correctly
4. Verify: no "subtitling" stage appears in the render log

**Test 3 — Mixed asset types:**
1. Use an episode with both image scenes and video scenes
2. Verify: both image-based and video-based scene clips are produced correctly
3. Verify: they concatenate without errors (stream copy handles both)

**Test 4 — Timing check:**
1. Compare total render time before and after the changes
2. The concat step alone should go from minutes to seconds
3. Overall render time should drop by roughly 30-40%

---

## Implementation Checklist

- [ ] Task 1: Switch `concat_scenes.py` from `-c:v libx264` to `-c:v copy`, remove `-pix_fmt`
- [ ] Task 2: Add `-preset fast -crf 20` to `render_video_scene.py`
- [ ] Task 3: Add `-preset fast -crf 20` to `render_image_scene.py`
- [ ] Task 4: Change `-preset medium` to `-preset fast` in `burn_subtitles.py`
- [ ] Task 5: End-to-end verification (subtitle path, no-subtitle path, mixed assets)

---

## Expected Impact

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Concat step time (30-min video) | 30-60 min | 2-5 sec | ~99% faster |
| Per-scene encode speed | baseline | ~40% faster | `-preset fast` |
| Subtitle burn speed | baseline | ~40% faster | `-preset fast` |
| Total render (with subtitles) | 3x video duration | ~1.2x video duration | ~60% faster |
| Total render (no subtitles) | 2x video duration | ~0.6x video duration | ~70% faster |
| Quality | inconsistent (no CRF on scenes) | consistent CRF 20 scenes, CRF 18 final | better controlled |

---

## What This Plan Does NOT Change

- **Pipeline orchestration** (`pipeline.py`) — no flow changes, just faster encoding per step
- **Mux voiceover** (`mux_voiceover.py`) — already uses stream copy, optimal
- **Probe assets** (`probe_assets.py`) — addressed separately in B2
- **ffmpeg_utils.py** — no changes needed
- **models.py** — no data model changes
- **Scene filter logic** — all filters in `render_video_scene.py` and `render_image_scene.py` remain unchanged
- **Subtitle style** — font size, colors, outline all unchanged
