import sys

filepath = r'tool1_dashboard\ui\app.js'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

lines = content.split('\n')

# Lines 4410-4585 (0-indexed: 4409-4584) are the renderSettings function + blank line
# Replace with our new implementation

new_func = r"""function renderSettings() {
  const settings = state.settings || {};
  const stageProviderOpenAi = activeStageProviderOpenAi();
  const openaiHealth = state.health?.providers?.openai || {};
  const appRuntime = state.appRuntime || {};
  const workerHealth = state.workerHealth || {};
  const workerInfo = describeWorkerHealth(workerHealth);
  const appStartedAt = appRuntime.started_at ? formatDate(appRuntime.started_at) : null;

  const runtimeItems = [
    {
      label: "App shell",
      value: appRuntimeModeLabel(appRuntime),
      copy: appRuntime.close_copy || null,
    },
    {
      label: "App process",
      value: appRuntime.pid ? `PID ${appRuntime.pid}` : "Unknown",
      copy: appRuntimeProcessCopy(appRuntime) || null,
    },
    {
      label: "Voice engine",
      value: workerLifecycleLabel(workerHealth),
      copy: workerInfo.copy || workerInfo.meta || null,
    },
    {
      label: "OpenAI API",
      value: openaiHealth.has_api_key ? "Ready" : "Key needed",
      copy: openaiHealth.has_api_key && openaiHealth.model_count
        ? `${openaiHealth.model_count} model${openaiHealth.model_count === 1 ? "" : "s"} cached`
        : null,
    },
    {
      label: "Claude CLI",
      value: state.health?.providers?.claude?.logged_in
        ? "Ready"
        : state.health?.providers?.claude?.available
          ? "Login needed"
          : "Not installed",
      copy: null,
    },
  ];

  const runtimeCards = runtimeItems.map((item) => `
    <div class="settings-runtime-item">
      <div class="settings-runtime-label">${esc(item.label)}</div>
      <div class="settings-runtime-value">${esc(item.value)}</div>
      ${item.copy ? `<div class="settings-runtime-copy">${esc(item.copy)}</div>` : ""}
    </div>
  `).join("");

  const runtimeBadges = [
    appRuntime.single_instance ? healthBadge("Single instance", true) : "",
    appStartedAt ? statusBadge(`Started ${appStartedAt.toLocaleString()}`, "neutral") : "",
    healthBadge(`ffmpeg ${state.health?.alignment?.ffmpeg ? "ready" : "missing"}`, state.health?.alignment?.ffmpeg),
    healthBadge(`MFA ${state.health?.alignment?.mfa ? "ready" : "check"}`, state.health?.alignment?.mfa ? true : "warn"),
    healthBadge(`WhisperX ${state.health?.alignment?.whisperx ? "ready" : "check"}`, state.health?.alignment?.whisperx ? true : "warn"),
  ].filter(Boolean).join("");

  $("view").innerHTML = `
    <div class="settings-page">

      <div class="settings-page-header">
        <div>
          <div class="eyebrow">Global defaults</div>
          <h2 class="section-title">Pipeline settings</h2>
        </div>
        <div class="button-row">
          <button type="submit" form="settings-form" class="button button-primary has-icon">${iconContent("save", "Save settings")}</button>
          <button type="button" class="button has-icon" data-nav="templates">${iconContent("templates", "Templates")}</button>
        </div>
      </div>

      <form id="settings-form">

        <div class="settings-section">
          <div class="settings-section-label">AI models</div>
          <div class="settings-model-grid">
            ${renderStageSetupCard({
              icon: "scene",
              title: "Scene planning",
              providerId: "settings-scene",
              providerValue: settings.default_scene_planning_provider || "claude",
              modelId: "settings-scene-model",
              modelValue: settings.default_scene_planning_model || "haiku",
            })}
            ${renderStageSetupCard({
              icon: "bible",
              title: "Consistency guide",
              providerId: "settings-bible",
              providerValue: settings.default_visual_bible_provider || "claude",
              modelId: "settings-bible-model",
              modelValue: settings.default_visual_bible_model || "haiku",
            })}
            ${renderStageSetupCard({
              icon: "play",
              title: "Video prompts",
              providerId: "settings-video",
              providerValue: settings.default_video_prompt_provider || "codex",
              modelId: "settings-video-model",
              modelValue: settings.default_video_prompt_model || "gpt-5.4",
            })}
            ${renderStageSetupCard({
              icon: "prompts",
              title: "Image prompts",
              providerId: "settings-image",
              providerValue: settings.default_image_prompt_provider || "codex",
              modelId: "settings-image-model",
              modelValue: settings.default_image_prompt_model || "gpt-5.4",
            })}
          </div>
        </div>

        <div class="settings-section">
          <div class="settings-section-label">Pipeline configuration</div>
          <div class="settings-config-grid">
            ${renderSetupCard({
              icon: "timeline",
              title: "Output strategy",
              copy: "",
              tone: "neutral",
              fields: `
                <label class="field">
                  <span class="field-label">Video-first scenes</span>
                  <input id="settings-leading-video" type="number" min="0" value="${esc(settings.leading_video_scene_count || 20)}" />
                </label>
              `,
            })}
            ${renderSetupCard({
              icon: "prompts",
              title: "Prompt batches",
              copy: "",
              fields: `
                <label class="field">
                  <span class="field-label">Batch size</span>
                  <input id="settings-batch-size" type="number" min="1" value="${esc(settings.prompt_batch_size || 24)}" />
                </label>
              `,
            })}
            ${renderSetupCard({
              icon: "timeline",
              title: "Planning chunk size",
              copy: "",
              fields: `
                <label class="field">
                  <span class="field-label">Chunk seconds</span>
                  <input id="settings-chunk-seconds" type="number" min="1" value="${esc(settings.planning_chunk_seconds || 360)}" />
                </label>
              `,
            })}
            ${renderSetupCard({
              icon: "rerun",
              title: "Planning overlap",
              copy: "",
              fields: `
                <label class="field">
                  <span class="field-label">Overlap seconds</span>
                  <input id="settings-overlap-seconds" type="number" min="0" value="${esc(settings.planning_overlap_seconds || 30)}" />
                </label>
              `,
            })}
            ${renderSetupCard({
              icon: "refresh",
              title: "OpenAI API access",
              copy: "",
              tone: stageProviderOpenAi.hasSavedApiKey ? "active" : "warn",
              fields: `
                <label class="field">
                  <span class="field-label">API key</span>
                  <input
                    id="stage-provider-openai-api-key"
                    type="password"
                    value="${esc(stageProviderOpenAi.apiKeyDraft)}"
                    placeholder="${stageProviderOpenAi.hasSavedApiKey ? "Leave blank to keep saved key" : "Paste an OpenAI key"}"
                    autocomplete="off"
                    spellcheck="false"
                  />
                </label>
                ${renderStageProviderOpenAiMeta(stageProviderOpenAi)}
                <button type="button" class="button has-icon button-small" id="stage-provider-openai-discover-button" data-stage-provider-openai-discover="true">${iconContent("refresh", stageProviderOpenAi.modelCount ? "Refresh models" : "Check key")}</button>
                <div id="stage-provider-openai-status">${renderStageProviderOpenAiStatus(stageProviderOpenAi)}</div>
              `,
            })}
          </div>
        </div>

      </form>

      <div class="settings-runtime">
        <div class="settings-runtime-header">
          <span class="eyebrow">Health</span>
          <span class="settings-runtime-title">Runtime</span>
        </div>
        <div class="settings-runtime-grid">${runtimeCards}</div>
        ${runtimeBadges ? `<div class="badge-row settings-runtime-badges">${runtimeBadges}</div>` : ""}
      </div>

    </div>
  `;
}
"""

# Replace lines 4409 to 4584 (0-indexed)
new_lines = lines[:4409] + [new_func] + lines[4585:]
new_content = '\n'.join(new_lines)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Done! Lines 4410-4585 replaced.")

# Verify
with open(filepath, 'r', encoding='utf-8') as f:
    verify_lines = f.read().split('\n')
print(f"New total lines: {len(verify_lines)}")
print(f"New line 4410: {verify_lines[4409][:60]}")
