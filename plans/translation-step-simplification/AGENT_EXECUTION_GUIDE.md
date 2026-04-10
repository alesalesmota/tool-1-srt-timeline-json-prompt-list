# Translation Step Simplification Agent Execution Guide
> Use this guide to execute the 2026-04-10 translation simplification work without re-deciding scope.

## Product Decision

Translation should block automation only on pipeline-breaking problems, not on minor natural-language imperfections.

The desired runtime behavior is:

`translate -> deterministic safety checks -> diagnostics -> next stage`

AI review remains available in code, but is off by default and not part of the default translation success path.

## Why `TRADUTOR` Feels Better

The standalone `TRADUTOR` tool works because it is operationally simple:

- one translation prompt
- sequential chunk execution
- no backend orchestration complexity
- no blocking reviewer pass
- strong visible progress/log feedback

Tool 1 should keep its pipeline contracts, but the translation step should recover that same clarity.

## Responsibilities

### Codex

- Own all backend/runtime changes.
- Own docs, registry, tests, and diagnostics payloads.
- Keep downstream contracts stable.

### Gemini

- Own the translation-status presentation only inside the existing Tool 1 UI.
- Do not create a new page or inline editor.

### Jules

- Own smoke validation and ambiguity checks after integration.
- Only make minor glue/copy fixes if needed.

## Backend Execution Rules

- Default translation runtime must not require reviewer success.
- Default translation runtime must not perform automatic AI repair loops.
- Preserve:
  - `script_path`
  - `spoken_script_path`
  - current TTS/alignment/timeline inputs
  - retry-from-translation flows

## Required Error Categories

- `quota_exceeded`
- `invalid_api_key`
- `rate_limited`
- `network_timeout`
- `empty_output`
- `source_text_leak`
- `english_cta_leak`
- `source_channel_leak`
- `missing_target_channel_name`
- `digits_present`
- `gibberish_output`
- `suspicious_length`

Each category must include a concise `next_action`.

## Diagnostics Contract

### Chunk log

For each chunk include:

- `chunk_index`
- `scene_ids`
- `words_in`
- `words_out`
- `status`
- `provider`
- `model`
- `category`
- `error`
- `offending_excerpt`
- `next_action`

### Language summary

`translation_diagnostics_<lang>.json` must include:

- `language_code`
- `status`
- `provider`
- `model`
- `review_enabled`
- `blockers`
- `warnings`
- `recommended_next_action`

## UI Contract

The existing episode detail/overlay must show:

- short translation error badge in the language table
- short translation failure summary in the error column
- preview modal with:
  - provider/model
  - review enabled/disabled
  - blocker list
  - offending excerpts
  - next-action hints
  - chunk log

## Prompt Contract

Translation prompts must explicitly instruct:

- numbers in narration should be written as words, not digits
- output must remain plain readable language suitable for TTS

Do not reintroduce subjective reviewer-style wording into the main prompt.

## Validation Targets

Must verify:

- provider failures are clearly distinguishable from content failures
- digits cause translation failure
- gibberish causes translation failure
- safe imperfect translations still pass
- passing translation still flows into TTS and beyond unchanged
