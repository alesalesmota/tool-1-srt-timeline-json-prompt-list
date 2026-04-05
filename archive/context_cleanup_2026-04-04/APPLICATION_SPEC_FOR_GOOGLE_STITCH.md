# Tool 1: Multilingual Planning & Pre-Generation System
## Technical Specification & Design Guide (for Google Stitch)

> [!IMPORTANT]
> This document provides an intrinsic, deep-dive explanation of **Tool 1**, specifically tailored for UI/UX design and architectural understanding.

---

## 1. The Core Concept (The "Big Idea")
**Tool 1** is a specialized "pre-production" engine for automated video content creation. It solves the hardest problem in multilingual video: **Timing Consistency.**

In traditional video localization, a 1-minute English video might become 1:20 in Spanish due to narration length. **Tool 1** reverses this. It generates the narration (TTS) for *every* language first, then calculates a "Master Timeline" where visual scenes match the specific pacing of every language.

### Primary Goal
To take a single script and a set of target languages, and produce a ZIP archive containing:
- **Localized Audio**: High-quality TTS narrations.
- **Subtitles**: Perfectly aligned SRT files.
- **Visual Mapping**: A JSON timeline where every scene duration is tailored to the audio.
- **Creative Assets**: Prompt lists for generating the actual visual assets (images/videos).

---

## 2. Application Hierarchy & Navigation
Google Stitch must design a navigation experience that reflects the following strictly nested hierarchy:

### Layer 1: Niche Projects (Global Level)
The top-level entry point (e.g., "Religion Channel", "World History").
- **Navigation**: Users select a Niche from their project library.
- **Role**: Defines the "Environment." All voice profiles, translation models, and target languages are configured here and inherited by child episodes.

### Layer 2: The Kanban Dashboard (Niche Level)
Once inside a Niche, the user arrives at its dedicated **Kanban Dashboard**.
- **Role**: This is the "Command Center." It shows all episodes currently in production for this specific niche.
- **Interaction**: Users drag/drop cards or monitor real-time progress across the 7 board columns (Draft, Queued, Running, etc.).

### Layer 3: Episodes (Content Level)
An **Episode** is the core execution unit. It is created directly on the Kanban board.
- **Input**: Every episode begins with a single **Source Script**.
- **The Engine**: The episode is where the 10-stage "Asset Factory" runs.

### Layer 4: The Multi-Channel Workflow (Execution Level)
Inside the **Episode Detail (Overlay)**, the workflow takes that single script and branches out:
- **One Script → Multiple Channels**: It generates translated scripts, narrations, and timelines for every target language simultaneously.
- **Structural Assets**: It also generates the visual "prompts" used to create the final videos in external tools.

---

## 3. The Workflow (10-Stage Pipeline)
Every "Episode" in the system follows a rigid, sequential state machine. Users move episodes through these columns on a Kanban board.

| # | Stage | Description | Tech/Service |
|---|---|---|---|
| 1 | **Consistency Guide** | Defines the "Visual Bible" (colors, characters, moods) to keep AI generation consistent across all scenes. | OpenAI (Structured JSON) |
| 2 | **Scene Planning** | Breaks the script into logical visual blocks (e.g., "Scene 1: Close up of a man thinking"). | OpenAI (Structured JSON) |
| 3 | **Translation** | Translates the master script into all target languages. | OpenAI API |
| 4 | **TTS (Narration)**| Generates audio chunks for every line in every language. | XTTS-v2 (GPU Accelerated) |
| 5 | **Alignment** | Syncs phonetic audio patterns with the text to get millisecond-accurate timestamps. | MFA (Montreal Forced Aligner) |
| 6 | **SRT Chunking** | Converts raw alignments into readable subtitle blocks. | Custom Python Logic |
| 7 | **Timeline Gen** | Calculates the master timeline. If Spanish Scene 1 is 5s but English is 4s, the timeline handles the stretch/hold. | Custom Python Logic |
| 8 | **Video Prompts** | Expands scenes into detailed prompts for AI video generators (Sora, Runway, etc.). | OpenAI (Structured JSON) |
| 9 | **Image Prompts** | Generates prompts for static assets (Midjourney, DALL-E) to fill gaps. | OpenAI (Structured JSON) |
| 10 | **Export** | Bundles all JSONs, audio files, and SRTs into a final package for **Tool 2**. | ZIP / Workspace Files |

---

## 4. Technology Stack
- **Backend**: Python 3.10+ with **FastAPI**.
- **Frontend**: Vanilla JavaScript (ES6+), Modern CSS (Vanilla, zero frameworks), Responsive Design.
- **Database**: SQLite with WAL (Write-Ahead Logging) for high-frequency state updates.
- **AI/LLM**: OpenAI API (Responses API with JSON Schema) for all structural logic.
- **Audio Service**: Dedicated **XTTS-v2** worker process (Python/PyTorch) with CUDA support for real-time throughput.
- **Automation**: Moat/Drawbridge for browser-integrated UI annotations and task tracking.

---

## 5. Architecture & State Machine
The system uses a **Serialized Background Worker** model:
1. The **Service Layer** (`Tool1Service`) manages the state in SQLite.
2. A **Background Thread** polls for "Queued" episodes and executes the 10 stages sequentially.
3. The **TTS Worker** is a separate specialized process that handles the heavy GPU load of audio generation.

### Folder Structure (Intrinsic Detail)
- `tool1_dashboard/`: Main application code.
  - `ui/`: Frontend assets (app.js, app.css).
  - `tts/`: Narration engine logic.
  - `alignment_tool/`: MFA/Whisper integration.
- `workspace/`: The "Live" data store.
  - `episodes/{id}/`: Every episode has its own folder containing `script.txt`, `scenes.json`, `audio/*.wav`, etc.
- `config/agents/`: Prompt templates for the AI agents that design scenes and prompts.

---

## 6. Information & Data Entities
To design the UI, you must understand these four pillars of data:

### A. Niche Projects (The Buckets)
Episodes are grouped into "Niches" (e.g., "Religion", "Tech News").
- **Properties**: Title, Master Language (Default 'en'), Target Languages (List), Default Voice Profiles, Default Translation Models.
- **UI Role**: Act as a workspace separator.

### B. Episodes (The Unit of Work)
The primary interaction object.
- **Properties**: Title, Script, Current Stage, Pipeline Status (idle/running/failed), Error Logs, Workspace Path.
- **UI Role**: Kanban cards and the "Detail Overlay."

### C. Voice Profiles (The Personas)
Reusable XTTS voices.
- **Properties**: Name, Reference Audio (Sample), Latent Embeddings, TTS Pacing Config (Speed, Stability, Similarity).
- **Interactions**: Users can "Play Test" a voice sample instantly.

### D. Stage Runs (The Audit Trail)
Every time the AI runs (e.g., Scene Planning), a record is created.
- **Properties**: CLI Command, Stdout/Stderr logs, started/finished timestamps, exit codes.
- **UI Role**: Technical visibility for debugging "failed" stages.

---

## 7. Important Interactions (Design Touchpoints)
1. **The Kanban Board**:
   - Visualizing the throughput of episodes.
   - Live progress bars showing "TTS 42/100 lines complete."
   - Status indicators for individual languages (e.g., "PT: Done, ES: Running, FR: Queued").

2. **The Unified Overlay**:
   - Instead of navigating away, episodes open in a slide-out overlay.
   - Contains a "Live Activity" panel showing real-time terminal output from AI agents.
   - A "File Explorer" to preview generated audio/JSON artifacts immediately.

3. **Voice Tuning**:
   - A dedicated modal to adjust the "humanity" of the voice (Expressiveness vs. Stability) with real-time audio feedback.

4. **Queue Readiness**:
   - The app actively blocks the "Start" button if setup is incomplete (e.g., "Missing API Key" or "Spanish has no voice assigned").

---

## 8. Telemetry & Tracking
What the system tracks and the UI must display:
- **TTS Throughput**: CPU vs GPU status, queue depth, and chunk-by-chunk generation speed.
- **Provider Health**: Are OpenAI and the local TTS worker "Online"?
- **Artifact Sync**: The UI automatically detects new files in the workspace and updates the list without page refreshes.
- **Error Extraction**: Deep nested errors from AI providers (e.g., "Rate limit reached") are extracted and shown in a readable "Needs Attention" card.

---

## 9. UX & Design Aesthetics (Target for Google Stitch)
The application must transition from its current functional state to a **Premium Industrial Brutalist** experience.

### A. The Marathon Aesthetic
Drawing deep inspiration from the **Marathon (Bungie)** visual language:
- **Atmosphere**: Moody, high-contrast, and oppressive but incredibly sharp.
- **Geometry**: Heavy blocks, raw edges, and visible structural "pipes" or "wiring" in the UI.
- **Micro-Detail**: Tiny technical labels, coordinate readings, and status codes that make the app feel like a real industrial terminal.

### B. Color Palette (High-Contrast Neon)
- **Primary Theme**: Deep **Dark Mode** (Blacks/Off-blacks, zero pure-white backgrounds).
- **Accents**: 
  - **Yellow Neon**: Primary branding and action-related highlights.
  - **Green Neon**: Positive state, system health, and "Go" actions.
  - **Red**: Critically reserved for Errors, Stops, and items that need immediate attention.

### C. Industrial Brutalism
- **Typography**: Heavily functional, monospaced or brutalist sans-serifs (e.g., *Inter, Roboto Mono, JetBrains Mono*).
- **Layout**: Clear, rigid grids with heavy borders or high-contrast separation. No soft shadows; use hard edges and high-contrast outlines.
- **Interactions**: Tactile "switch-like" buttons, binary transitions, and fast, glitch-free animations that feel mechanical.

---
**End of Specification.**
*This document represents the system as of March 29, 2026.*
