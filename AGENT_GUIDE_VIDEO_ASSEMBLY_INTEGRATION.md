# Agent Guide: Video Assembly Integration into Tool 1

> **Purpose**: Technical reference for any AI agent tasked with merging Tool 2's video assembly
> pipeline into Tool 1's unified codebase. This document describes every output file produced
> by Tool 1's planning stages (1-10), its exact schema, how the video assembly pipeline
> consumes it, and the architectural contract for building the merged render stages.

---

## 1. Architecture Context

### What happened before (two separate tools)
```
Tool 1 (this repo)              Tool 2 (separate repo)
Planning + TTS + Alignment  -->  Video Assembly + Rendering
   Exports a ZIP bundle     -->  Reads ZIP, renders final videos
```

### What we are building (single unified tool)
```
Tool 1 (this repo) — ALL-IN-ONE
Stages 1-10: Planning + TTS + Alignment + Scene Planning + Prompt Generation + Timeline Mapping
Stages 11+:  Asset Intake + Validation + Scene Rendering + Concatenation + Muxing + Subtitling
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
             This is what gets merged from Tool 2
```

The frontend is Tool 1's existing FastAPI dashboard (`tool1_dashboard/app.py`).
The backend orchestration is Tool 1's `service.py` EpisodeService class.
Tool 2's logic becomes new stages in the same `service.py` pipeline, operating on the
same episode workspace directory — no ZIP export, no file copying, no separate project.

---

## 2. Episode Workspace Layout (Source of Truth)

Every episode lives in its own directory. After stages 1-10 complete, the workspace
contains all inputs the render stages need. Nothing needs to be moved or reorganized.

```
workspace/episodes/niche-{id}/ep-{id}/
│
│  *** SHARED OUTPUTS (same for all languages) ***
│
├── consistency_guide.json              # Visual style bible (colors, characters, moods)
├── consistency_guide_validation.json   # Validation result {status, errors, warnings, character_count}
├── master_scenes.json                  # Scene definitions from AI scene planning (source for all timelines)
├── timeline_draft.json                 # Master timeline = master_scenes.json (identical content)
├── timeline_validation.json            # {status, errors, warnings, scene_count, total_duration}
│
├── video_prompt_list_draft.txt         # One prompt per line, for video generation tools
├── video_prompt_blueprints.json        # Structured JSON array of video prompts
├── image_prompt_list_draft.txt         # One prompt per line, for image generation tools
├── image_prompt_blueprints.json        # Structured JSON array of image prompts
├── prompt_list_draft.txt               # Combined (video + image) prompts, one per line
├── prompt_blueprint.jsonl              # Combined JSONL, one JSON object per line
│
│  *** PER-LANGUAGE OUTPUTS ***
│
├── script_original.txt                 # Master language script
├── script_{lang}.txt                   # Translated script (one per language)
├── translation_log_{lang}.json         # Translation metadata
├── final_{lang}.srt                    # Aligned subtitle file (from TTS + alignment)
├── timeline_{lang}.json                # Language-specific timeline (ratio-mapped from master)
│
│  *** ALIGNMENT ARTIFACTS ***
│
├── alignment/{lang}/{run-id}/
│   ├── final.srt                       # The SRT that gets copied to final_{lang}.srt
│   ├── normalized_audio.wav            # The narration audio for this language
│   ├── alignment_report.json
│   ├── segments.json
│   └── words.json
│
│  *** PLANNING CHUNKS (intermediate, not needed for render) ***
│
├── planning_chunks/
│   ├── chunk-NNN.json
│   ├── chunk-NNN.srt
│   ├── chunk-NNN.txt
│   └── manifest.json
│
│  *** AI RUN ARTIFACTS (debug/audit, not needed for render) ***
│
└── runs/
    ├── consistency_guide/
    ├── scene_planning/
    ├── video_prompt_generation/
    └── image_prompt_generation/
```

### Where the render stages will write

```
workspace/episodes/niche-{id}/ep-{id}/
│
│  *** NEW: GENERATED VISUAL ASSETS (user uploads or AI generates) ***
│
├── assets/                             # All visual assets go here
│   ├── scene_001.mp4                   # Video asset (matches scene_id)
│   ├── scene_002.jpg                   # Image asset
│   ├── scene_003.png
│   └── _blank_black.png                # Auto-generated fallback
│
│  *** NEW: RENDER ARTIFACTS (per-language) ***
│
├── render/{lang}/
│   ├── scenes/                         # Individual rendered scene clips
│   │   ├── scene_001.mp4
│   │   ├── scene_002.mp4
│   │   └── ...
│   ├── concat_list.txt                 # FFmpeg concat input file
│   ├── visual_master.mp4               # All scenes concatenated (no audio)
│   ├── final_video_{lang}.mp4          # Visual + narration audio
│   ├── final_video_subtitled_{lang}.mp4  # With burned subtitles (optional)
│   └── render_manifest_{lang}.json     # Audit log of the full render
```

**Key difference from old Tool 2**: Assets are SHARED across languages but rendered
separately per language because each language has different scene timings. The `assets/`
directory is at the episode level. The `render/` directory is per-language.

---

## 3. Output File Schemas (Exact Contracts)

### 3.1 `timeline_{lang}.json` — THE AUTHORITATIVE RENDER INPUT

This is the single most important file for video assembly. One exists per language.
It is a JSON array of scene objects.

```jsonc
[
  {
    "scene_id": "scene_001",          // String. Unique, sequential. Used to match asset files.
    "start": 0.0,                     // Float (seconds). When this scene begins in the narration.
    "end": 1.51,                      // Float (seconds). When this scene ends.
    "duration": 1.51,                 // Float (seconds). MUST equal (end - start). See note below.
    "text": "Four thousand years.",    // String. The narration text for this scene.
    "asset_type": "video" | "image",  // String. Determines which renderer to use.
    "visual_intent": "A vast desert landscape to symbolize the passage of time.",
                                      // String. Natural language description of what should be shown.
                                      // NOT a generation prompt — it's editorial intent.
    "notes": "Opening statement establishing the time context."
                                      // String. Production notes from scene planning AI.
  },
  // ... 468 scenes in the test episode
]
```

#### Critical properties

| Property | Contract |
|----------|----------|
| `scene_id` | Always `scene_NNN` format (zero-padded to 3 digits). Sequential, no gaps. |
| `start` / `end` | In seconds, float. Derived from SRT alignment timestamps. |
| `duration` | **ALWAYS use `end - start`**, not this field, for safety. The field is now correctly computed but older runs may have stale EN-master values in non-EN timelines. |
| `asset_type` | Either `"video"` or `"image"`. Determines the FFmpeg rendering strategy. |
| `text` | The narration text in the scene's language. Can be used for subtitle verification. |
| `visual_intent` | Describes what the scene should look like. Use for asset matching validation or AI-assisted generation — NOT passed to FFmpeg. |

#### Timing behavior across languages

The **same scenes** appear in every language timeline. Same `scene_id`, same `visual_intent`,
same `asset_type`, same `notes`. What differs is `start`, `end`, and `duration`:

```
timeline_en.json  scene_001: start=0.0,  end=1.51,  duration=1.51   (53.9 min total)
timeline_es.json  scene_001: start=0.0,  end=1.58,  duration=1.58   (59.7 min total)
timeline_de.json  scene_001: start=0.0,  end=1.54,  duration=1.54   (66.3 min total)
timeline_fr.json  scene_001: start=0.0,  end=1.58,  duration=1.58   (54.3 min total)
```

This means the render pipeline runs once per language, using the same shared assets
but adapting each asset's duration to match that language's narration timing.

#### Gaps between scenes

Scenes do NOT tile perfectly. There are intentional gaps (narration pauses, breathing room)
and occasionally large gaps (sections of narration where the AI scene planner assigned no
visual). The render pipeline must handle gaps by either:
- Holding the previous scene's last frame (freeze/extend)
- Inserting a black frame
- The current Tool 2 approach: gaps pass through as-is since scenes are concatenated
  back-to-back (the visual track is shorter than the audio and gets padded during mux)

**Actual gap profile from test episode (English):**
- 39 gaps > 0.5s
- Largest: 235s (scene_029 → scene_030), 109s (scene_147 → scene_148)
- 1 overlap: 0.22s (scene_401 → scene_402) — edge case from AI scene planning

### 3.2 `final_{lang}.srt` — SUBTITLE FILE

Standard SRT format. Aligned to actual TTS audio via forced alignment (MFA/Whisper).

```
1
00:00:00,000 --> 00:00:01,510
Four thousand years.

2
00:00:01,860 --> 00:00:06,450
That is the distance between a decision
made inside a desert tent and the war that

3
00:00:06,450 --> 00:00:08,060
fills your news feed right now.
```

**Critical property**: The last SRT entry's end time matches `timeline_{lang}.json`'s
last scene end time **exactly** (verified 0.00s difference for all 4 languages in test episode).
This guarantees audio, subtitles, and visual timeline are perfectly synchronized.

**Encoding**: UTF-8 (important for non-Latin scripts like German umlauts, French accents).

### 3.3 Narration Audio

Located at: `alignment/{lang}/{latest-run-id}/normalized_audio.wav`

The audio file is the TTS-generated narration for that language. It has been:
- Generated by XTTS-v2 from the translated script
- Normalized during alignment
- Its timing is the source of truth for SRT timestamps and therefore for all timeline entries

**The render pipeline needs to locate the correct audio file per language.** The path is
stored in the database: `episode_language_status.tts_audio_path` or can be found via
the alignment directory structure.

### 3.4 `consistency_guide.json` — VISUAL STYLE BIBLE

```jsonc
{
  "world_style": {
    "setting": "...",        // Cinematic universe description
    "look": "...",           // Visual treatment (e.g., "prestige feature-film realism")
    "palette": "...",        // Color palette specification
    "lighting": "...",       // Lighting approach
    "camera_language": "...",// Camera/composition rules
    "negative_rules": "..."  // What to NEVER show (text, logos, split-screen, etc.)
  },
  "characters": [
    {
      "character_id": "abram_abraham",
      "label": "Abram / Abraham",
      "visual_description": "...",
      "wardrobe": "...",
      "demeanor": "...",
      "usage_notes": "..."
    }
    // ... more characters
  ]
}
```

**Use in render stages**: Not directly consumed by FFmpeg. Used for:
- AI-assisted asset generation (if integrated later)
- Validation that generated assets match the style guide
- UI display for the user during asset review

### 3.5 `image_prompt_blueprints.json` — STRUCTURED IMAGE PROMPTS

```jsonc
[
  {
    "scene_id": "scene_021",       // Maps 1:1 to timeline scene_id
    "asset_type": "image",
    "prompt": "A dusty path in Ur, The path stretches into the distance...",
    "subject": "A dusty path",
    "setting": "Ur",
    "look": "Ancient, textured earth tones...",
    "lighting": "Late afternoon sun casts long shadows...",
    "rules": "Foreshadows an important journey.",
    "character_refs": [],           // Array of character labels referenced
    "composition": "The path stretches into the distance, framed by mudbrick buildings..."
  }
]
```

**scene_id alignment**: Every `scene_id` in this file exists in the timeline.
Every timeline scene with `asset_type: "image"` has a corresponding entry here.
Verified: 448 image scenes = 448 blueprint entries, IDs match perfectly.

### 3.6 `video_prompt_blueprints.json` — STRUCTURED VIDEO PROMPTS

```jsonc
[
  {
    "scene_id": "scene_001",       // Maps 1:1 to timeline scene_id
    "asset_type": "video",
    "prompt": "Vast desert landscape in open desert, symbolizing the passage of time...",
    "subject": "vast desert landscape",
    "setting": "open desert",
    "look": "mythic and timeless",
    "lighting": "hard desert sun",
    "rules": "avoid empty backgrounds",
    "character_refs": [],
    "action": "symbolizing the passage of time",   // Video-specific: describes motion
    "camera": "wide shot capturing the expanse"     // Video-specific: camera movement
  }
]
```

**Video prompt differences from image**: Has `action` and `camera` fields instead of `composition`.
Verified: 20 video scenes = 20 blueprint entries, IDs match perfectly.

### 3.7 `prompt_blueprint.jsonl` — COMBINED PROMPTS (JSONL)

One JSON object per line, 468 lines total. Contains all video + image prompts in scene order.
Same schema as the individual blueprint files. Each line is independently parseable JSON.

```
{"scene_id": "scene_001", "asset_type": "video", "prompt": "...", ...}
{"scene_id": "scene_002", "asset_type": "video", "prompt": "...", ...}
...
{"scene_id": "scene_021", "asset_type": "image", "prompt": "...", ...}
```

**Use case**: Batch processing tools that want a single file with all prompts.
Can be streamed line-by-line without loading the full array into memory.

### 3.8 `prompt_list_draft.txt` / `image_prompt_list_draft.txt` / `video_prompt_list_draft.txt`

Plain text files. One prompt per line. No JSON structure. Just the raw prompt string
ready to be copy-pasted into image/video generation tools (Midjourney, Runway, Sora, etc.).

```
Vast desert landscape in open desert, symbolizing the passage of time. Wide shot capturing the expanse. Mythic and timeless. Hard desert sun. Avoid empty backgrounds, no text, no subtitles, ...
```

**Line count correspondence**:
- `video_prompt_list_draft.txt`: 20 lines (= video scenes in timeline)
- `image_prompt_list_draft.txt`: 448 lines (= image scenes in timeline)
- `prompt_list_draft.txt`: 468 lines (= total scenes)

Line N in `video_prompt_list_draft.txt` corresponds to the Nth video scene in timeline order.
Line N in `image_prompt_list_draft.txt` corresponds to the Nth image scene in timeline order.
Line N in `prompt_list_draft.txt` corresponds to `scene_N` in the timeline (1-indexed).

---

## 4. How Tool 2's Render Pipeline Consumes These Files

This section documents the exact consumption pattern of Tool 2's existing code,
so the merging agent knows what to preserve.

### 4.1 Timeline Loading (`timeline.py`)

Tool 2 already has a `_convert_tool1_timeline()` function that converts Tool 1's flat array
into its nested `{project: {...}, scenes: [...]}` format. When merging, this conversion
becomes unnecessary because the render stages will operate directly on the episode data.

**Fields consumed from each scene entry:**

| Field | Required | How Used |
|-------|----------|----------|
| `scene_id` | Yes | Logging, output filenames (`scene_001.mp4`), asset matching |
| `start` | Yes | Timing validation (ensures start < end, no overlaps) |
| `end` | Yes | Timing validation |
| `duration` | Yes | **Primary duration source for FFmpeg `-t` parameter**. Validated as `end - start`. |
| `asset_type` | Yes | Routes to `render_image_scene()` or `render_video_scene()` |
| `asset_file` | Yes* | Path to visual asset file. *In merged version, resolved from `assets/` dir by scene_id. |
| `text` | No | Logging only |
| `visual_intent` | No | Not consumed by Tool 2 (Tool 1-specific field) |
| `notes` | No | Not consumed by Tool 2 (Tool 1-specific field) |

**Fields Tool 2 adds during conversion (not in Tool 1's output):**

| Field | Default | Purpose |
|-------|---------|---------|
| `asset_id` | `asset_NNN` | Cache key for probed assets |
| `asset_file` | Resolved from `assets/` | Relative path to the asset file |
| `motion` | `{enabled: false, mode: "static"}` | Image animation config |
| `retime` | `{enabled: false, mode: "auto"}` | Video speed adjustment config |

### 4.2 Asset Resolution (`asset_resolver.py`)

Scans the `assets/` directory and matches files to scenes by number:

1. Extract number from filename: `scene_001.mp4` → 1, `prompt42.jpg` → 42, `3_desert.png` → 3
2. Match to scene by 1-based index (scene_001 = index 1)
3. Prefer type-matched files (image scene → .jpg/.png, video scene → .mp4/.mov)
4. Unmatched scenes get remaining files in order, or a generated blank black frame

**Naming convention for asset files:**
```
scene_001.mp4    ← Best: matches scene_id exactly
scene_001.jpg    ← Also good
prompt1.png      ← Works: extracts number 1
asset_001.webp   ← Works: extracts number 1
1_landscape.jpg  ← Works: leading number
```

### 4.3 Scene Rendering

**Image scenes** (`render_image_scene.py`):
```
Input:  scene_NNN.jpg/png/webp (any resolution)
Output: scene_NNN.mp4 (H.264, yuv420p, 1920x1080, 30fps, no audio)

FFmpeg pipeline:
1. -loop 1 -framerate 30 -i {asset}
2. -t {scene.duration}
3. -vf "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
4. Optional: zoompan filter for slow_zoom_in motion
5. -c:v libx264 -pix_fmt yuv420p
```

**Video scenes** (`render_video_scene.py`):
```
Input:  scene_NNN.mp4/mov/mkv/webm (any resolution/duration)
Output: scene_NNN.mp4 (H.264, yuv420p, 1920x1080, 30fps, no audio)

Duration adaptation logic:
- If source ≈ target (±0.05s): direct copy with scale+crop
- If source < target (need to slow down):
  - Factor ≤ 1.35x: setpts slowdown
  - Factor > 1.35x: slowdown 1.35x + freeze last frame for remainder
- If source > target (need to speed up):
  - Factor ≤ 1.15x: setpts speedup
  - Factor > 1.15x: trim to target duration
```

### 4.4 Concatenation + Muxing

```
1. Write concat_list.txt (all rendered scene_NNN.mp4 files in order)
2. FFmpeg concat demuxer → visual_master.mp4 (all scenes, no audio, no gaps*)
3. Mux: visual_master.mp4 + narration.wav → final_video.mp4 (video=copy, audio=AAC 192k)
4. Optional: burn subtitles from SRT file → final_video_subtitled.mp4

* Note: Scenes are concatenated back-to-back. The visual track duration equals
  the SUM of all scene durations, which may be SHORTER than the narration audio
  duration due to timeline gaps. FFmpeg muxing handles this — the audio continues
  playing while the video shows the last frame of the last scene.
```

---

## 5. Integration Contract for the Merged Pipeline

### 5.1 New stages to add to EpisodeService

| Stage | Name | Type | Description |
|-------|------|------|-------------|
| 11 | `asset_intake` | Shared | User uploads/assigns visual assets to `assets/` dir |
| 12 | `render_validation` | Per-language | Validate timeline + assets + audio for one language |
| 13 | `scene_rendering` | Per-language | Render each scene to individual .mp4 clip |
| 14 | `concatenation` | Per-language | Concatenate all scene clips into visual master |
| 15 | `audio_muxing` | Per-language | Mux visual master + narration audio |
| 16 | `subtitle_burn` | Per-language | Burn SRT subtitles into final video |

### 5.2 Data flow for render stages

```python
# For each language in episode.configured_languages:

# INPUT FILES (already exist from stages 1-10):
timeline_path  = workspace / f"timeline_{lang}.json"      # Scene timing
srt_path       = workspace / f"final_{lang}.srt"           # Subtitles
audio_path     = db.get_episode_language_status(ep_id, lang)["tts_audio_path"]  # Narration
assets_dir     = workspace / "assets"                       # Shared visual assets

# OUTPUT FILES (render stages create these):
render_dir     = workspace / "render" / lang
scenes_dir     = render_dir / "scenes"
visual_master  = render_dir / "visual_master.mp4"
final_video    = render_dir / f"final_video_{lang}.mp4"
final_subtitled = render_dir / f"final_video_subtitled_{lang}.mp4"
manifest       = render_dir / f"render_manifest_{lang}.json"
```

### 5.3 The per-language render loop

```python
def render_for_language(episode_id: str, lang: str):
    """Pseudocode for the merged render pipeline per language."""

    # 1. Load timeline
    timeline = json.loads((workspace / f"timeline_{lang}.json").read_text("utf-8"))

    # 2. Resolve assets (shared across languages)
    #    Match assets/ files to scene_ids by number extraction
    asset_map = resolve_assets(timeline, workspace / "assets")

    # 3. Enrich scenes with asset info + defaults
    for scene in timeline:
        scene["asset_file"] = asset_map[scene["scene_id"]]["asset_file"]
        scene["duration"] = scene["end"] - scene["start"]  # ALWAYS recompute
        scene.setdefault("motion", {"enabled": True, "mode": "slow_zoom_in"})
        scene.setdefault("retime", {"enabled": True, "mode": "auto"})

    # 4. Validate
    #    - All asset files exist
    #    - No overlaps (start[i] >= end[i-1])
    #    - Duration = end - start
    #    - Audio file exists

    # 5. Render each scene
    for scene in timeline:
        output = scenes_dir / f"{scene['scene_id']}.mp4"
        if scene["asset_type"] == "image":
            render_image_scene(scene, output, fps=30, width=1920, height=1080)
        else:
            render_video_scene(scene, output, fps=30, width=1920, height=1080)

    # 6. Concatenate
    concat_list = [scenes_dir / f"{s['scene_id']}.mp4" for s in timeline]
    ffmpeg_concat(concat_list, visual_master)

    # 7. Mux audio
    audio_path = get_narration_audio_path(episode_id, lang)
    ffmpeg_mux(visual_master, audio_path, final_video)

    # 8. Burn subtitles
    srt_path = workspace / f"final_{lang}.srt"
    ffmpeg_burn_srt(final_video, srt_path, final_subtitled)
```

### 5.4 What to port from Tool 2 (file-by-file)

| Tool 2 File | Merge Into | Notes |
|-------------|-----------|-------|
| `render_image_scene.py` | `tool1_dashboard/render_image_scene.py` | Copy as-is. Pure FFmpeg logic, no dependencies. |
| `render_video_scene.py` | `tool1_dashboard/render_video_scene.py` | Copy as-is. Pure FFmpeg logic, no dependencies. |
| `concat_scenes.py` | `tool1_dashboard/concat_scenes.py` | Copy as-is. |
| `mux_voiceover.py` | `tool1_dashboard/mux_voiceover.py` | Copy as-is. |
| `burn_subtitles.py` | `tool1_dashboard/burn_subtitles.py` | Copy as-is. |
| `ffmpeg_utils.py` | `tool1_dashboard/ffmpeg_utils.py` | Copy. Tool 1 may already have FFmpeg utils — merge if so. |
| `probe_assets.py` | `tool1_dashboard/probe_assets.py` | Copy as-is. |
| `asset_resolver.py` | `tool1_dashboard/asset_resolver.py` | Copy, but adapt path resolution to use episode workspace instead of `input/` dir. |
| `models.py` | Merge into existing `tool1_dashboard/` models | Port `SceneSpec`, `AssetProbe`, `SceneRenderResult`, `RenderSummary`, `MotionSpec`, `RetimeSpec`. Drop `ProjectConfig` (use episode config instead). |
| `timeline.py` | **NOT needed** | Tool 1 already produces the timeline. No conversion needed. |
| `validation.py` | `tool1_dashboard/render_validation.py` | Port validation logic but adapt to read from episode workspace. |
| `pipeline.py` | Integrate into `tool1_dashboard/service.py` | The `RenderPipeline.run()` orchestration becomes a new method in `EpisodeService`. |
| `jobs.py` | **NOT needed** | Tool 1 already has its own job/pipeline management. |
| `main.py`, `cli.py` | **NOT needed** | Tool 1 has its own FastAPI app and doesn't need a separate CLI. |
| `ui/` | Merge into `tool1_dashboard/ui/` | Port render progress UI into Tool 1's existing dashboard. |

### 5.5 Database extensions needed

Add to `episode_language_status`:
```sql
render_status       TEXT DEFAULT 'idle'    -- idle/running/done/failed
render_video_path   TEXT                   -- path to final_video_{lang}.mp4
render_manifest_path TEXT                  -- path to render_manifest_{lang}.json
```

Add to `episodes`:
```sql
asset_intake_status TEXT DEFAULT 'idle'    -- idle/partial/ready
```

---

## 6. Known Edge Cases and How to Handle Them

### 6.1 Timeline gaps (scenes don't tile)

**Problem**: There are 39+ gaps > 0.5s in the English timeline. The largest is 235 seconds
(scene_029 → scene_030). During these gaps, the narration plays but no scene is assigned.

**Current Tool 2 behavior**: Gaps are ignored. Scenes are concatenated back-to-back,
making the visual track shorter than the audio. FFmpeg muxing handles this by freezing
the last video frame while audio continues.

**Recommended approach for merged tool**: Same as current. The concat-and-mux strategy
naturally handles gaps because:
1. Visual master duration = sum of scene durations (no gaps)
2. Audio duration = narration duration (includes gap periods)
3. Muxing with `-c:v copy` stops video at visual end, audio plays to completion
4. Most players show the last frame while audio finishes

**Alternative** (more polished): Insert explicit black/hold frames for gaps > N seconds.
This requires synthesizing filler clips during concatenation.

### 6.2 One overlap in master timeline

**Problem**: scene_401 (end=2422.0) overlaps scene_402 (start=2421.78) by 0.22s in the
English master timeline. Non-English timelines are clean (mapping code prevents overlaps).

**Solution**: The render validation should clamp: if `scene[i].start < scene[i-1].end`,
set `scene[i].start = scene[i-1].end`. Tool 2's validation already flags this as an error,
but the merged pipeline should auto-fix rather than fail.

### 6.3 Duration field accuracy

**Problem**: Prior to commit `ca70f7d`, non-English timelines had the wrong `duration` value
(copied from English master instead of computed from the language's `end - start`). The fix
is now in place, but episodes processed before this fix have stale `duration` fields.

**Solution**: **Always compute duration as `end - start`**. Never trust the `duration` field
alone. The render code should do:
```python
scene_duration = scene["end"] - scene["start"]
```

### 6.4 Asset type ratio (96% images)

**Observation**: The test episode has 20 video scenes and 448 image scenes. This is normal
for long-form content where most visuals are AI-generated images (Midjourney, DALL-E) with
only key scenes getting AI video (Sora, Runway).

**Implication for render**: The image renderer (with slow_zoom_in motion) will be called ~22x
more than the video renderer. Optimize image rendering path for throughput. Consider:
- Parallel rendering of image scenes (they're independent)
- GPU-accelerated encoding if available

### 6.5 Italian script with no SRT/timeline

**Observation**: `script_it.txt` exists but Italian has no SRT or timeline. This means
Italian was translated but TTS/alignment was never run (possibly not in configured languages).

**Solution**: The render pipeline should only attempt languages that have both
`final_{lang}.srt` and `timeline_{lang}.json`. Check `episode_language_status` for
`timeline_status == "done"`.

### 6.6 Asset files may not exist yet

**Context**: Tool 1 produces prompts. The user then generates images/videos externally
(using Midjourney, Runway, Sora, etc.) and uploads them to the `assets/` directory.
The render pipeline cannot run until assets are available.

**Solution**: The `asset_intake` stage should:
1. Show the user which scenes need assets (with prompts as reference)
2. Allow upload/drag-drop of files
3. Auto-match uploaded files to scenes by filename number
4. Show a coverage report (N/468 scenes have assets)
5. Allow partial renders (only render scenes that have assets)
6. Generate blank frames for missing assets

---

## 7. Multilingual Render Strategy

### Current state (one language at a time)
Tool 2 processes one language per invocation. For the merged tool, this becomes:

### Target state (all languages from single trigger)

```
User clicks "Render All Languages"
    │
    ├─► Render English
    │   ├─ Load timeline_en.json
    │   ├─ Locate narration audio (English TTS)
    │   ├─ Render 468 scenes (shared assets, EN timing)
    │   ├─ Concat → visual_master_en.mp4
    │   ├─ Mux with EN audio → final_video_en.mp4
    │   └─ Burn final_en.srt → final_video_subtitled_en.mp4
    │
    ├─► Render Spanish
    │   ├─ Load timeline_es.json
    │   ├─ Locate narration audio (Spanish TTS)
    │   ├─ Render 468 scenes (SAME assets, ES timing — different durations!)
    │   ├─ Concat → visual_master_es.mp4
    │   ├─ Mux with ES audio → final_video_es.mp4
    │   └─ Burn final_es.srt → final_video_subtitled_es.mp4
    │
    ├─► Render German  (same pattern)
    └─► Render French  (same pattern)
```

### Optimization: Shared scene rendering cache

Since the same asset file is used across languages, and many scenes have similar (though not
identical) durations, a smart cache could avoid redundant FFmpeg calls:

```python
# Cache key: (asset_file, asset_type, rounded_duration, motion_mode)
# If English scene_042 is 3.45s and Spanish scene_042 is 3.62s,
# they need separate renders. But if French is also 3.45s, reuse English's clip.
```

This is an optimization, not a requirement. The naive approach (render everything per language)
is correct and simpler.

### Sequential vs parallel rendering

**Hardware constraint**: The user's machine has limited resources (see memory file
`hardware_constraints.md`). Render languages sequentially, not in parallel. Within a single
language's render, scene rendering CAN be parallelized (each scene is independent).

---

## 8. Quick Reference: File → Purpose → Consumer

| File | Producer Stage | Consumer Stage | Format |
|------|---------------|----------------|--------|
| `timeline_{lang}.json` | timeline_mapping (9) | render_validation (12), scene_rendering (13) | JSON array |
| `final_{lang}.srt` | alignment (4) | subtitle_burn (16) | SRT text |
| `normalized_audio.wav` | tts (3) + alignment (4) | audio_muxing (15) | WAV audio |
| `image_prompt_blueprints.json` | image_prompt_generation (8) | asset_intake UI (11) | JSON array |
| `video_prompt_blueprints.json` | video_prompt_generation (7) | asset_intake UI (11) | JSON array |
| `consistency_guide.json` | consistency_guide (1) | asset_intake UI (11) | JSON object |
| `master_scenes.json` | scene_planning (6) | (reference only, not used in render) | JSON array |
| `prompt_list_draft.txt` | export (10) | external tools (Midjourney, etc.) | Plain text |
| `assets/scene_NNN.*` | user upload | scene_rendering (13) | Image/Video files |

---

## 9. Validation Checklist Before Render

Before starting the render for any language, verify:

- [ ] `timeline_{lang}.json` exists and is valid JSON array
- [ ] `final_{lang}.srt` exists and is valid SRT
- [ ] Narration audio file exists (check `episode_language_status.tts_audio_path`)
- [ ] `assets/` directory exists with at least some files
- [ ] For each scene in timeline:
  - [ ] `asset_type` is `"image"` or `"video"`
  - [ ] `start < end`
  - [ ] Computed duration (`end - start`) > 0
  - [ ] Matching asset file exists (or blank frame will be generated)
- [ ] No scene overlaps previous scene by more than 0.05s
- [ ] Timeline last scene `end` time approximately matches narration audio duration
- [ ] FFmpeg is available on PATH
