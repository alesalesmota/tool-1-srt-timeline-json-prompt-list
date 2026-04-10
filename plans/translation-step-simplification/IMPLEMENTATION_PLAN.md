# Translation Step Simplification Implementation Plan
> Last updated: 2026-04-10

## Summary

Tool 1 translation should behave much closer to `TRADUTOR`:

`translate -> deterministic safety checks -> clear diagnostics -> next stage`

The default runtime path must stop using AI quality review as a blocking gate. Translation should fail only on problems that can break automation or produce unusable narration inputs.

## Goals

- Keep translation as a visible pipeline step.
- Make failures explicit enough that the user immediately knows where to act.
- Preserve downstream contracts for TTS, alignment, timeline mapping, exports, and retry-from-translation flows.
- Persist planning docs for this effort under `plans/translation-step-simplification/`.

## Scope

### In scope

- Disable AI review in the default translation runtime path.
- Remove automatic AI repair from the default translation runtime path.
- Strengthen deterministic translation safety checks.
- Add structured diagnostics artifacts and richer preview payloads.
- Improve translation error visibility in the existing episode detail/overlay UI.
- Update documentation and project registry.

### Out of scope

- Manual inline translation editing.
- A new standalone translation page.
- A database migration for diagnostics storage.
- Reworking downstream TTS/alignment/timeline stages.

## Runtime Design

### Default runtime path

The default path becomes:

1. Build chunks.
2. Translate each chunk once.
3. Fail the language immediately on provider/runtime failure.
4. Assemble the full script.
5. Run deterministic pipeline-safety checks.
6. Write readable script + spoken script + diagnostics artifacts.
7. Mark translation as done or failed.

### Optional AI review path

- AI review remains in code for future use.
- Introduce a backend feature flag such as `translation_ai_review_enabled`.
- Default value is `false`.
- When disabled, the translation runtime must not call the reviewer model.

## Deterministic Translation Gate

### Required blockers

- empty translation
- suspicious duplicated source text
- untranslated source paragraphs
- English CTA leakage in non-English outputs
- source channel name leakage
- missing configured localized channel name when source script mentions the channel
- digits present in narration output
- gibberish / non-word token output
- provider returned structurally unusable text

### Error categories and next actions

- `quota_exceeded`
  - next action: switch provider/key/model or wait for quota reset
- `invalid_api_key`
  - next action: fix the API key / provider configuration
- `rate_limited`
  - next action: retry later or switch provider/model
- `network_timeout`
  - next action: retry or inspect provider/network health
- `empty_output`
  - next action: inspect provider/model behavior or prompt
- `source_text_leak`
  - next action: inspect prompt/profile or translation logic
- `english_cta_leak`
  - next action: inspect prompt/profile
- `source_channel_leak`
  - next action: inspect prompt/profile/config
- `missing_target_channel_name`
  - next action: inspect prompt/profile/config
- `digits_present`
  - next action: inspect prompt and normalization logic
- `gibberish_output`
  - next action: inspect provider/model/prompt
- `suspicious_length`
  - next action: inspect prompt/profile or translation duplication behavior

### Offending excerpts

- Diagnostics should include the exact offending excerpt when available.
- If exact excerpts are not safely derivable, include the first representative offending token or paragraph.

## Prompt Changes

- Update translation prompts so narration text must write numbers as words, not digits.
- Keep the output requirement focused on plain readable language suitable for TTS.
- Do not add more subjective fluency-review language.

## Diagnostics and API

### Workspace artifacts

- `translation_log_<lang>.json`
  - per-chunk operational log
  - include provider, model, category, offending_excerpt, next_action
- `translation_diagnostics_<lang>.json`
  - language-level summary
  - include status, blockers, warnings, provider, model, review_enabled, recommended_next_action

### API changes

- Extend `get_translation_preview()` and `/api/episodes/{episode_id}/translation-preview/{language_code}` to return:
  - provider
  - model
  - review_enabled
  - diagnostics
  - chunk log with categories and hints

## UI Changes

- Keep the current episode detail/overlay flow.
- Improve the per-language translation display with:
  - short category badge in the table
  - clearer translation failure summary
  - preview modal with blocker list
  - preview modal with offending excerpt
  - preview modal with next-action hint
  - preview modal with provider/model metadata

## Agent Execution

### Codex

- docs and registry updates
- translation runtime refactor
- deterministic diagnostics
- preview API changes
- regression tests

### Gemini

- translation-status presentation in existing UI
- preview modal clarity and visual hierarchy

### Jules

- smoke validation of representative failure modes
- copy/glue fixes if backend/frontend integration leaves ambiguous feedback

## Acceptance Criteria

- Translation passes when output is pipeline-safe even if minor wording is imperfect.
- Translation fails clearly on provider errors and content blockers.
- Translation preview returns actionable diagnostics, not only raw text.
- Episode detail/overlay shows categorized translation failures clearly per language.
- TTS/alignment/timeline continue to work unchanged after a passing translation.
- AI review still works only when explicitly enabled.
