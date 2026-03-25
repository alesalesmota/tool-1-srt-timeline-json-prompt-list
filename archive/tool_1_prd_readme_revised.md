# Tool 1 PRD / README — Planning & Pre-Generation System
## Goal: turn script + narration into the planning artifacts that enable human asset generation and automated final assembly

# 1. Overall Goal of the Whole Project

We are designing and building a software / automation system whose end goal is:

**take a script and a narration audio file and, by the end of the workflow, produce a finished YouTube video.**

The full project is divided into two major tools:

- **Tool 1 — Planning & Pre-Generation System**
- **Tool 2 — Final Video Assembly System**

Tool 1 creates the planning artifacts before image and video generation.
A human then uses the prompt list to generate the actual visual assets.
Tool 2 takes those assets and assembles the final synchronized video.

This document explains Tool 1 in depth.

# 2. What Tool 1 Is

Tool 1 is a **hybrid planning system**.

It is not a single algorithm.
It is not only a local app.
It is not only an AI prompt.

It is a coordinated workflow that combines:

- **actual local software / automation**
- **AI-agent reasoning steps**
- **human-in-the-loop execution at the image/video generation stage**

Tool 1 starts with:

- **script**
- **narration audio**

and ends with:

- **precise SRT**
- **final `timeline.json`**
- **prompt list for asset generation**

Those artifacts are then used by a human to generate images and videos, and later by Tool 2 to assemble the final video.

# 3. What Tool 1 Must Produce

Tool 1 must generate these main artifacts:

## 3.1 `final.srt`
A precisely timed subtitle file aligned to the narration audio.

## 3.2 `timeline.json`
The scene-level plan of the video.

This file defines:
- which scenes exist
- when each scene starts
- when each scene ends
- how long each scene lasts
- what text belongs to each scene
- whether the scene is better suited to image or video
- optional scene-level visual notes

## 3.3 `prompt_list.txt`
A generation-ready prompt list.

Critical format rule:
- **exactly 1 prompt per line**
- order must match scene order
- one line must correspond to one scene

## 3.4 Optional advanced artifacts
These are useful, but not mandatory for the first practical version:
- `words.json`
- `segments.json`
- diagnostics / validation reports

Important clarification:
for the current practical workflow, **SRT + `timeline.json` + prompt list are the core outputs**.

# 4. Why Tool 1 Exists

The old workflow was too rigid.

It typically worked like:
- narration exists
- subtitles are cut into fixed blocks
- visuals are generated on time windows like 10 or 20 seconds
- visual changes can happen in the middle of a sentence or explanation
- the visual flow may not follow the meaning of the narration

Tool 1 exists to replace that rigid method with a **semantic planning workflow**.

Instead of:
- one visual every arbitrary number of seconds

Tool 1 should create:
- accurate timing
- contextual scenes
- structured scene metadata
- prompts that reflect those scenes

So Tool 1 is the part of the system that makes the later visual stage actually meaningful.

# 5. The Core Philosophy Behind Tool 1

Tool 1 is built on these rules:

### 5.1 The script is already known
The narration audio comes from a script.
So subtitle timing should come from aligning the known text to the known audio.

### 5.2 Subtitle timing is infrastructure
The SRT is not just a subtitle file.
It is the timing foundation that later steps depend on.

### 5.3 Scene segmentation must be contextual
Scenes should follow the meaning of the narration.
They should not follow arbitrary fixed intervals.

### 5.4 One scene should map to one main asset
Current project rule:
- **1 contextual block = 1 scene**
- **1 scene = 1 main asset**
- that asset is either:
  - 1 image
  - or 1 video

### 5.5 The final planning artifact is `timeline.json`
Once scenes are defined, the `timeline.json` becomes the main structural plan used later by the final assembly system.

# 6. What Parts of Tool 1 Are Software, What Parts Are AI, and What Parts Are Human

This is one of the most important sections of this document.

Tool 1 is not “just an app”.
It is a **hybrid pipeline**.

## 6.1 Actual application / automation work

These are the parts that should be handled by real software:

### A. Input loading
The application reads:
- script file
- narration audio

### B. Subtitle alignment / timing generation
The application runs a subtitle alignment pipeline to generate:
- `final.srt`

This may use tools like:
- FFmpeg for normalization
- MFA or WhisperX-like alignment logic
- local Python orchestration

### C. Internal chunk preparation if needed
If the narration is long, the application can:
- split the SRT into overlapping chunks
- save those chunks into a folder
- prepare them as inputs for the scene-planning AI stage

### D. AI CLI orchestration
The application may call an AI CLI / agent tool to automate:
- contextual scene generation
- `timeline.json` creation
- prompt generation

### E. Merge and validation
The application may:
- collect chunk-level scene outputs
- merge them
- validate the final `timeline.json`
- optionally chunk the `timeline.json` again for prompt generation if it is too large
- merge prompt batches into a final prompt list

These are application / automation responsibilities.

## 6.2 AI-agent reasoning work

These are the parts where an AI agent does the reasoning-heavy work:

### A. Contextual scene planning
Given timed subtitle content, the AI decides:
- where a scene starts
- where it ends
- which lines belong together
- whether the scene is better as image or video
- what the scene represents visually

### B. Final `timeline.json` creation
The AI returns structured scene definitions.

### C. Prompt generation
Once a final `timeline.json` exists, another AI agent converts each scene into one generation prompt.

The AI is not expected to render media.
It is expected to reason over structure and description.

## 6.3 Human-made steps

This is the key human stage inside the wider workflow:

### Human image/video generation
After Tool 1 outputs the prompt list:
- a human uses those prompts to generate the actual images and videos

This generation step is **not** currently considered part of Tool 1 automation.

So the current intended reality is:

**Tool 1 automates planning up to prompt list generation.**
**The actual creation of image and video assets is done by a human.**

This must be stated clearly so the AI reading this document understands the boundary.

# 7. High-Level Flow of Tool 1

Tool 1 should behave like this:

**script + narration > subtitle alignment > SRT generation > optional chunking for scene planning > AI scene planning > partial scene merge > final `timeline.json` > optional `timeline.json` chunking for prompt generation > AI prompt generation > final prompt list**

This is the correct conceptual flow.

# 8. Why AI CLI / Agent Tools Matter in Tool 1

A major design decision discussed in this project is that browser chat is not the ideal environment for these planning steps.

Instead, Tool 1 should be designed so that an **AI CLI / agent tool** can be orchestrated by the application.

That means the local application can:
- save prepared input files into folders
- call the AI CLI on those files
- collect outputs
- validate outputs
- retry or continue automatically
- reduce the amount of manual chat-based operation

This is important because it allows Tool 1 to automate:
- scene planning
- `timeline.json` creation
- prompt generation

without the user having to manually copy/paste everything into browser chat.

So Tool 1 is not just “an app plus prompts”.
It is potentially:
- a **local controller application**
- plus an **AI CLI / agent execution layer**

# 9. Tech Stack Direction for Tool 1

The exact implementation may evolve, but the current best direction is:

## 9.1 Core local application
- **Python**
- file-based orchestration
- local folder management
- JSON handling
- subprocess execution
- validation logic

## 9.2 Audio / subtitle timing layer
- **FFmpeg** for audio normalization
- forced-alignment or alignment-based timing pipeline
- likely Python-based orchestration

## 9.3 AI execution layer
- **AI CLI / agent tool**
- used for:
  - scene planning
  - `timeline.json` generation
  - prompt generation

## 9.4 Validation layer
- JSON validation
- scene order validation
- duration validation
- merge rules

## 9.5 Output artifacts
- `final.srt`
- `timeline.json`
- `prompt_list.txt`

This is currently the strongest practical stack direction discussed.

# 10. Why `final.srt` Matters and How It Will Be Used

The SRT is one of the most important outputs of Tool 1.

It is used for:

### 10.1 Subtitle timing reference
It maps narration text to real time.

### 10.2 Input to scene planning
The scene-planning stage needs timed text in order to know:
- where narration ideas begin and end
- how long blocks of speech last
- what text belongs together

### 10.3 Review and debugging
The SRT gives a human-readable way to inspect subtitle alignment quality.

### 10.4 Optional later use in Tool 2
Tool 2 may optionally use the SRT for subtitle handling in the final video.

So the SRT is not just a side file.
It is a foundational artifact.

# 11. Why `timeline.json` Matters and How It Will Be Used

The `timeline.json` is the most important structural artifact produced by Tool 1.

It is used for:

### 11.1 Defining the final scene structure
It tells the system:
- what the scenes are
- where they begin and end
- what text belongs to them
- whether they lean image or video

### 11.2 Driving prompt generation
The prompt-generation agent reads this file to create the prompt list.

### 11.3 Driving final assembly
Tool 2 later uses the same file as the authoritative timeline when building the final video.

This means `timeline.json` is the bridge between:
- subtitle timing
- asset generation
- final assembly

It is the core machine-readable scene plan of the project.

# 12. Why `prompt_list.txt` Matters and How It Will Be Used

The prompt list is the final output of Tool 1 before the human generation stage.

It is used for:

### 12.1 Human asset generation
The user will take the prompts and generate:
- images
- videos

### 12.2 Order preservation
Because there is 1 prompt per line in scene order, the later generated assets can be enumerated in that same order.

### 12.3 Tool 2 compatibility
When assets are generated in scene order, Tool 2 can match them back to the timeline more cleanly.

So the prompt list is not just “creative output”.
It is a production artifact.

# 13. Optional Artifacts and Why They Are Not Central Right Now

Files like:
- `words.json`
- `segments.json`

can be useful for future upgrades, debugging, or more advanced automation.

But in the current simplified architecture, they are not the main required outputs.

The core outputs that actually drive the workflow are:

- `final.srt`
- `timeline.json`
- `prompt_list.txt`

This must be made explicit so the workflow stays understandable.

# 14. Internal Stage Breakdown of Tool 1

## Stage A — Subtitle alignment
Input:
- script
- narration audio

Output:
- `final.srt`

This is mostly application/automation work.

## Stage B — Scene-planning preparation
Input:
- `final.srt`

Output:
- AI-friendly chunk files if needed

This is application/automation work.

## Stage C — Contextual scene generation
Input:
- timed SRT chunks or full SRT

Output:
- scene JSON

This is AI-agent reasoning work.

## Stage D — Scene merge and timeline validation
Input:
- scene JSON outputs

Output:
- final `timeline.json`

This is application/automation work, possibly with AI assistance in edge cases.

## Stage E — Prompt generation
Input:
- final `timeline.json`

Output:
- ordered prompts

This is AI-agent reasoning work.

## Stage F — Prompt list consolidation
Input:
- prompt batches

Output:
- final `prompt_list.txt`

This is application/automation work.

# 15. Current Chunking Logic Inside Tool 1

Chunking may be needed in two different places.

## 15.1 SRT chunking
Used before scene planning if the subtitle content is too large.

Current discussed default:
- 6 minutes / 360 seconds
- 30 seconds overlap

Reason:
- reduce AI drift
- preserve context at boundaries

## 15.2 `timeline.json` chunking
May be used before prompt generation if the timeline is too large.

This chunking is different:
- usually by scene range
- usually no overlap needed
- used mainly for output control and consistency

This is important and should be explicit in the architecture.

# 16. Current Modeling Rules Tool 1 Must Respect

Tool 1 must follow these rules unless the project intentionally changes them later:

- 1 contextual block = 1 scene
- 1 scene = 1 main asset
- a scene’s main asset is either image or video
- scenes should follow meaning, not arbitrary fixed timing
- final prompts must be exactly 1 prompt per line
- prompt order must match scene order
- generated assets will later be created by a human
- Tool 2 will later use `timeline.json` as the authoritative plan

# 17. Recommended Structure of `timeline.json`

Each scene should include at least:

- `scene_id`
- `start`
- `end`
- `duration`
- `text`
- `asset_type`
- optional `visual_intent`
- optional `notes`

Conceptual example:

```json
{
  "scene_id": "scene_001",
  "start": 0.0,
  "end": 8.4,
  "duration": 8.4,
  "text": "Nobody really knows what Jesus looked like.",
  "asset_type": "video",
  "visual_intent": "mystery, reflective, ancient atmosphere"
}
```

# 18. System Prompt — Agent Responsible for `timeline.json` Creation

## Purpose of this agent

This agent takes timed subtitle content and creates scene-level structured timeline output.

## System Prompt

You are a scene-planning agent for a long-form narrative YouTube video automation system.

The overall project goal is to help build software that starts from a script and narration audio and ends as a finished YouTube video.

Your role belongs to Tool 1, the planning and pre-generation stage of the system.

Tool 1 does not generate the final video.
Tool 1 does not generate the actual image/video assets.
Those assets will later be generated by a human using prompts.
Your role is to transform timed subtitle content into a structured scene timeline that can later guide both prompt generation and final video assembly.

The broader workflow is:
- a script exists
- narration audio was generated from that script
- a subtitle alignment stage created a precise SRT
- your job is to convert timed text into contextual scenes
- later, another agent will generate one prompt per scene
- later, a human will use those prompts to generate images/videos
- later, another tool will assemble narration + timeline + assets into the final video

The old workflow used rigid timing windows for visuals, which caused scene changes in the middle of explanations.
Your job is to avoid that by creating contextual scene blocks.

Core rules you must follow:
- 1 contextual block = 1 scene
- 1 scene = 1 main asset
- a scene’s main asset can be image or video
- scene boundaries must follow meaning, not arbitrary timing windows
- use the timing data you are given
- do not invent narration timing
- do not create absurdly short or unnecessarily long scenes unless strongly justified
- favor scene units that can realistically be illustrated as one main asset

If the input is chunked:
- understand that the chunk may overlap with neighboring chunks
- do the best local scene planning possible from the provided chunk
- do not try to solve global merge problems unless explicitly asked

You are not writing prompts in this stage.
You are not summarizing the whole narration.
You are defining scenes.

When structured output is requested, output structured JSON only.

Each scene should include:
- `scene_id` or provisional id
- `start`
- `end`
- `duration`
- `text`
- `asset_type`
- optional `visual_intent`
- optional `notes`

Your priority order is:
1. semantic coherence
2. faithful timing usage
3. usable scene size
4. downstream asset realism
5. clean machine-readable output

# 19. System Prompt — Agent Responsible for Prompt List Creation

## Purpose of this agent

This agent takes the final scene timeline and generates one image/video prompt per scene.

## System Prompt

You are a prompt-generation agent for a long-form narrative YouTube video automation system.

The overall project goal is to help build software that starts from a script and narration audio and ends as a finished YouTube video.

Your role belongs to Tool 1, the planning and pre-generation stage of the system.

By the time you run:
- a script already existed
- narration audio already existed
- a subtitle alignment stage already produced precise timing
- a scene-planning stage already produced the final `timeline.json`

Your role is to transform those already-defined scenes into generation-ready prompts.

Important boundary:
- you are not deciding scene boundaries
- you are not editing timing
- you are not assembling the final video
- you are not generating the actual assets
- after your work is done, a human will use your prompt list to generate the images and videos

You must preserve scene order exactly.
Each scene corresponds to one main asset.
Each scene must produce exactly one prompt.

Final output rules:
- output exactly 1 prompt per line
- line order must match scene order
- do not add headings
- do not add bullet points
- do not add numbering unless explicitly asked
- do not add explanations before or after the prompt list
- do not skip scenes
- do not merge scenes
- do not split scenes

Each prompt should:
- stay faithful to the meaning of the scene
- reflect the intended asset type if provided
- be visually clear
- be generation-ready
- be concise enough to be practical
- be descriptive enough to be strong
- maintain style consistency when style guidance is provided

When a scene is marked as video:
- write the prompt so it works as a video-generation prompt

When a scene is marked as image:
- write the prompt so it works as an image-generation prompt

Do not output JSON unless explicitly requested.
Do not output prose summaries.
When final prompt output is requested, output only the prompt lines.

Your priority order is:
1. exact scene coverage
2. scene order fidelity
3. visual clarity
4. generation usability
5. style consistency

# 20. Short Tool 1 Workflow

**script + narration > subtitle alignment > precise SRT > scene planning > final `timeline.json` > prompt generation > prompt list > human image/video generation**

# 21. Final Summary

Tool 1 is the hybrid planning and pre-generation system of the project.

It combines:
- local software / automation
- AI-agent reasoning
- a human downstream generation step

Its purpose is to take script + narration and produce the planning artifacts that make asset generation and final video assembly possible.

The three core outputs that matter most right now are:
- `final.srt`
- `timeline.json`
- `prompt_list.txt`
