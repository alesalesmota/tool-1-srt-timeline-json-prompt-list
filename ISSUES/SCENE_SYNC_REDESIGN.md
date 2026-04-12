# Scene Sync Redesign Notes

## Purpose

This document records the scene desynchronization problem found in episode `206`, the root cause, and the proposed redesign discussed to solve it at the workflow level instead of relying on post-hoc repair.

It is written as a handoff note for another agent to review the proposed direction.

## Problem Observed

Episode `206` exposed a scene sync failure:

- visual asset for `scene_011` showed content equivalent to "a mother pulls her child closer"
- that visual appeared around `84.0s -> 97.3s`
- the English aligned SRT places that narration later, around `98.340s -> 111.450s`
- result: scene appeared about 3 scenes early

This strongly suggests the issue was not a render bug, but a planning bug: scene identity and scene timing became detached before the final assembly step.

## Root Cause

Root cause is in `scene_planning`.

Today the LLM does two jobs at once:

1. semantic job: decide how to split a subtitle block into scenes
2. deterministic job: assign `start`, `end`, and `duration`

The model is good enough at job `1`, but unreliable at job `2`.

What happened in practice:

- the LLM copied or summarized the correct scene text
- but hallucinated compressed or shifted timing values
- validation was strong enough to catch malformed payloads
- validation was not strong enough to guarantee that each scene text truly matched the subtitle cues at the same timestamps
- wrong timings survived merge and propagated downstream

So the real failure is not "video prompt mismatch" and not "render mismatch".

The real failure is:

`scene text correct` + `scene timing wrong` + `validator allowed it`

## Why Current Workflow Allows This

Current pipeline order from `tool1_dashboard/config.py` is:

1. `draft`
2. `consistency_guide`
3. `translation`
4. `tts`
5. `alignment`
6. `chunking`
7. `scene_planning`
8. `video_prompt_generation`
9. `image_prompt_generation`
10. `timeline_mapping`
11. `review`
12. `export`
13. `asset_upload`
14. `assembly_validation`
15. `video_render`
16. `final_review`

Current behavior relevant to this bug:

- translation happens before final scene structure is locked
- master language alignment creates a precise SRT
- chunking splits the master SRT into overlapping planning chunks
- scene planning asks the LLM to output full scene objects, including timing fields
- prompt generation uses those scene objects
- timeline mapping for other languages is still coarse: it scales master scene timing by total duration ratio and snaps to subtitle boundaries when close

This means the pipeline currently has two structural weaknesses:

1. scene timing is still partially entrusted to the LLM
2. multilingual scene timing is not derived from canonical scene-to-cue lineage

## Important Note About Current Code

There is already a temporary hardening patch in the working tree that realigns LLM scenes back to subtitle cues after scene planning. That helps as a guardrail, but it is not the preferred final architecture.

Preferred architecture should prevent the bad contract, not only repair it afterward.

## Proposed Core Principle

LLM should only decide semantic grouping.

Code should own all deterministic outputs.

That means:

- LLM decides where scene boundaries are
- code decides cue ranges
- code decides timings
- code decides scene ids
- code decides multilingual projection

## Proposed Scene Planning Contract

Instead of asking the model for:

- `start`
- `end`
- `duration`
- scene text block

ask for only cue boundaries.

Best version:

- model receives ordered master-language subtitle cues with stable cue ids
- model returns only scene boundaries, for example `break_after_cue_ids`

Example:

```json
{
  "break_after_cue_ids": [12, 18, 24, 31]
}
```

From that, code deterministically builds:

- `scene_001 = cues 1..12`
- `scene_002 = cues 13..18`
- `scene_003 = cues 19..24`
- `scene_004 = cues 25..31`

This removes timestamp hallucination from the contract entirely.

## Why Cue Boundaries Are Better Than Scene Text Matching

An intermediate improvement would be:

- model returns only scene text blocks
- code matches those blocks back to the SRT

This is better than the current design, but still fragile.

It still risks:

- repeated phrases
- paraphrase drift
- punctuation mismatch
- partial overlap between scenes
- ambiguous matches

Cue ids are cleaner because they are structural, not semantic.

## Proposed Multilingual Model

The correct multilingual model is not semantic matching across languages.

We should not try to "understand" that:

- `"A mother pulls her child closer"` in English
- matches some Portuguese sentence
- and then infer scene windows from meaning

That path is fragile.

Instead, we preserve lineage.

Scene identity should always be defined in the master language by source cue ids.

Example:

- canonical scene `scene_011` = master cues `143..147`

Other languages should reuse that exact source-cue span:

- Portuguese `scene_011` = translated/aligned output of source cues `143..147`
- Spanish `scene_011` = translated/aligned output of source cues `143..147`
- French `scene_011` = translated/aligned output of source cues `143..147`

Same scene id. Same asset. Different local timing.

## Translation Change Required

To make that possible, translation can no longer be "loose translated script only".

Translation must preserve source cue lineage.

Recommended translation contract:

- input is batched list of source cues
- output preserves same order
- safest variant: model returns translated text by array position, not by retyping ids
- code reattaches ids by index

Example input:

```json
[
  {"cue_id": 101, "text": "The dog jumped on the car"},
  {"cue_id": 102, "text": "and got tired"},
  {"cue_id": 103, "text": "after a while"},
  {"cue_id": 104, "text": "the dog arrived at home"}
]
```

Safest output shape:

```json
[
  "Um cachorro pulou no carro",
  "e ficou cansado",
  "depois de um tempo",
  "o cachorro chegou em casa"
]
```

Code then reconstructs:

```json
[
  {"source_cue_id": 101, "translated_text": "Um cachorro pulou no carro"},
  {"source_cue_id": 102, "translated_text": "e ficou cansado"},
  {"source_cue_id": 103, "translated_text": "depois de um tempo"},
  {"source_cue_id": 104, "translated_text": "o cachorro chegou em casa"}
]
```

This is better than asking the model to echo cue ids, because if the model must type ids, it can hallucinate them. By-position output reduces that risk.

## TTS Change Required

We should not generate one audio file per cue. That would hurt prosody and create too many seams.

Better approach:

- translated cue document remains source of truth
- code joins consecutive translated cues into longer TTS chunks
- TTS still runs on longer, natural text blocks
- chunk metadata preserves which cue ids are inside each TTS block

Recommended artifacts:

- `translated_cues_<lang>.json`
- `spoken_cues_<lang>.json`
- `tts_chunks_<lang>.json`

Example `tts_chunks_<lang>.json`:

```json
{
  "chunk_id": 7,
  "cue_ids": [101, 102, 103],
  "joined_spoken_text": "Um cachorro pulou no carro e ficou cansado depois de um tempo",
  "cue_word_spans": {
    "101": [0, 5],
    "102": [6, 9],
    "103": [10, 14]
  }
}
```

This keeps natural narration while preserving exact lineage back to source cues.

## How Other Languages Would Be Synchronized

This is the key point.

We do not synchronize by text meaning.

We synchronize by:

1. source cue ids
2. TTS chunk metadata
3. alignment word timings
4. reconstruction of localized cue timings

Flow:

1. translated cues keep `source_cue_id`
2. code builds TTS chunks from those cues
3. TTS generates audio for each chunk
4. alignment returns word timings for the chunk
5. code maps aligned words back to cue-local word spans
6. localized cue timing is rebuilt for each `source_cue_id`
7. localized SRT is generated from those cue timings
8. localized scene timeline is projected from canonical source cue ranges

So if `scene_011` is source cues `143..147`, then in Portuguese:

- take localized timings for source cues `143..147`
- scene start = start of localized cue `143`
- scene end = end of localized cue `147`

No semantic guessing required.

## How Workflow Works Today

Below is the workflow as it exists conceptually in code today.

### Current Workflow

1. user has source script
2. consistency guide generated from source script
3. translation generates per-language translated script files
4. TTS generates narration audio per language from those script files
5. alignment generates final SRT per language
6. master-language aligned SRT is chunked for scene planning
7. scene planning asks LLM to split chunk into scenes and also assign timings
8. timeline draft is merged for master language
9. video prompts generated from master timeline
10. image prompts generated from master timeline
11. non-master timelines are mapped by duration ratio plus boundary snapping
12. assets are uploaded and validated
13. video assembly consumes shared assets plus per-language timelines
14. render produces final output

### Main Weaknesses In Current Workflow

- scene planning contract too broad
- translation loses cue-level lineage
- timeline mapping across languages is approximate, not structural
- visual branch depends on master timeline objects that can already carry bad timing

## How We Imagine The Workflow After Redesign

The redesigned workflow should create one canonical scene plan first, then project it safely to each language.

### Proposed Workflow

1. source script
2. consistency guide
3. master spoken script
4. master TTS
5. master alignment
6. precise master SRT
7. parse precise master SRT into stable canonical cue ids
8. scene planning on master cue ids only
9. code builds canonical scenes from cue boundaries
10. visual branch starts from canonical scenes:
    - video prompt list
    - image prompt list
    - asset upload
    - asset validation
11. language branch starts from canonical source cues:
    - batched per-cue translation
    - per-cue spoken-form document
    - TTS chunk plan
    - localized TTS
    - localized alignment
    - localized cue timing reconstruction
    - localized SRT
    - localized scene projection from canonical cue ranges
12. assembly joins:
    - shared canonical assets
    - per-language projected timelines
    - per-language subtitles/audio
13. render
14. final review
15. export

## Visual Pipeline Impact

This redesign changes the visual branch in a good way.

Today the visual branch depends on a scene plan that may already contain hallucinated timing.

After redesign:

- video/image prompts are generated once from canonical scenes
- canonical scenes are structural and deterministic
- scene ids become stable across all languages
- asset identity becomes stable across all languages
- assembly only swaps local timing/audio/subtitles per language

This is exactly the desired product model:

- same scenes
- same assets
- same prompt blueprints
- different narration timing per language

In other words, the visual branch should become more independent from multilingual timing noise, not less.

## Branching Versus Linearity

There is an important architecture point here.

The system should be logically branching, but operationally it does not need to run in parallel.

Logical branching means:

- one canonical scene branch for visuals
- one per-language timing branch for audio/subtitles

Operationally, because of machine constraints, the pipeline can still execute mostly sequentially.

This distinction matters.

We do not need parallel execution to adopt branching dataflow.

## Local Machine Constraints

The user explicitly raised a valid concern: this machine should not be overloaded by aggressive parallel work.

That means the redesign should prefer:

- deterministic artifacts
- low concurrency
- one heavy stage at a time

Recommended runtime behavior:

- do not run TTS for multiple languages in parallel
- do not run alignment for multiple languages in parallel
- do not run render in parallel with alignment/TTS
- scene planning and prompt generation can remain provider-bound stages, but even those can stay sequential by default
- if concurrency exists at all, it should be tightly capped and configurable

In short:

- architecture can branch
- execution can remain mostly linear

This keeps the model clean without overloading CPU, GPU, RAM, or disk.

## Recommended UI/State Behavior

The workflow should not introduce a separate `needs attention` step.

Better behavior:

- card stays on the stage where failure happened
- stage is marked `failed` or `blocked`
- card turns red
- error summary is attached to that stage

So "attention required" is not a stage.
It is a state of the current stage.

## Repository Architecture Implications

Repository already has reusable service-style modules:

- `tool1_dashboard/service.py` - main orchestration layer
- `tool1_dashboard/tts/` - reusable TTS engine and worker management
- `tool1_dashboard/alignment_tool/` - reusable alignment orchestration
- `tool1_dashboard/translation/` - reusable translation logic and spoken-form rules
- `tool1_dashboard/video_assembly/` - reusable assembly/render logic

So this redesign should not require rewriting TTS, alignment, or render from scratch.

Main refactor target is not low-level engine code.

Main refactor target is the contract between stages.

Current code is service-oriented, but orchestration is still too centralized in `Tool1Service`. The next architectural improvement should be stronger typed artifacts between stages.

Recommended new artifacts:

- `master_cues.json`
- `canonical_scene_plan.json`
- `translated_cues_<lang>.json`
- `spoken_cues_<lang>.json`
- `tts_chunks_<lang>.json`
- `aligned_cues_<lang>.json`
- `timeline_<lang>.json`

Those artifacts matter more than introducing more classes for their own sake.

## Recommended Final Direction

Preferred final direction is:

1. make master aligned SRT the canonical structural source
2. derive stable cue ids from that SRT
3. make scene planning output only cue boundaries
4. make translation preserve source cue lineage
5. make TTS use long chunks built from lineage-preserving cue documents
6. make alignment reconstruct localized cue timing
7. make timeline mapping project canonical scene cue ranges to each language
8. keep visual assets shared across languages
9. keep heavy runtime stages mostly sequential due machine limits

## Summary

Problem:

- scenes became desynchronized
- visible in episode `206`
- master cause was incorrect scene timing produced by the model during scene planning

Current patch:

- repair step can reduce damage
- useful as guardrail
- not ideal as final design

Proposed fix:

- remove timing responsibility from the model
- plan scenes by cue boundaries only
- preserve cue lineage through translation, TTS, alignment, and localized timeline projection

Expected result:

- one canonical scene structure shared by all languages
- same assets reused across languages
- per-language timing derived structurally, not heuristically
- less hallucination surface area
- more deterministic pipeline
- lower sync risk end to end

---

## Implementation Status — Pass 1 Landed (2026-04-12)

Pass 1 of the redesign was implemented as a surgical fix rather than a full rewrite, preserving downstream contracts (`merge_scene_chunks`, scene dict shape for timeline/prompt stages).

**What changed:**

- `scene_output_schema()` in `tool1_dashboard/validators.py` now accepts exactly one field: `break_after_cue_ids` (integer array). The LLM can no longer emit timestamps, text, ids, or asset types.
- New deterministic builder `validators.build_scenes_from_cue_breaks(chunk_cues, break_after_cue_ids, source_chunk_id)` partitions the chunk's cues into contiguous scene groups using the LLM's break points, then stamps each scene with `start`/`end`/`duration`/`text`/`asset_type="image"` directly from the aligned SRT cues.
- `service._episode_run_scene_planning` now passes each chunk's cues as an explicit `[{cue_id, start, end, text}, ...]` list to the LLM, consumes `break_after_cue_ids`, and invokes `build_scenes_from_cue_breaks` per chunk.
- `config/agents/scene_planning/{claude,codex,openai}.md` rewritten to describe the cue-boundary contract and forbid emitting anything other than `break_after_cue_ids`.
- Removed (no longer reachable): `normalize_scene_payload`, `realign_scenes_to_cues`, `_scene_from_cue_span`, `_normalize_alignment_text`, `_is_gap_placeholder`, `_remaining_scenes_form_malformed_tail`, and the `SCENE_TEXT_ALIGNMENT_*` constants.
- Tests: unit coverage for the new builder lives in `tests/test_chunking_and_validation.py`; integration coverage via `test_scene_planning_builds_scenes_from_cue_breaks` + existing `test_scene_planning_calls_llm_per_chunk`. Obsolete drift/overlap repair tests were deleted — those failure modes are now structurally impossible.

**Result:** drifted/hallucinated scene timings cannot survive the stage. Scene text and scene timing come from the same cue objects, so they cannot desync. Full suite: 316 passed.

**What Pass 1 does NOT do yet (future work):**

- cross-language cue lineage — scenes are still planned per-language from each language's aligned SRT, not projected from master cues
- canonical scene ids shared across languages
- ratio-based timeline mapping still exists for localized languages
