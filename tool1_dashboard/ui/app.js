// ── Episode pipeline (TTS-first) constants ────────────────────────
const EPISODE_PIPELINE_COLUMNS = [
  { id: "draft", label: "Script", short: "Script", copy: "Script submitted, pipeline not started." },
  { id: "consistency_guide", label: "Consistency Guide", short: "Guide", copy: "Locking characters, style, and continuity rules." },
  { id: "translation", label: "Translation", short: "Translate", copy: "Translating script for each target language." },
  { id: "tts", label: "TTS / Voice", short: "TTS", copy: "Generating narration audio per language." },
  { id: "alignment", label: "Precise SRT", short: "SRT", copy: "Aligning audio to script for exact subtitles." },
  { id: "chunking", label: "Chunking", short: "Chunks", copy: "Splitting master SRT into planning chunks." },
  { id: "scene_planning", label: "Scene Planning", short: "Scenes", copy: "Building scene structure from chunks." },
  { id: "video_prompt_generation", label: "Video Prompts", short: "Video Pr.", copy: "Writing prompts for leading video scenes." },
  { id: "image_prompt_generation", label: "Image Prompts", short: "Image Pr.", copy: "Writing prompts for remaining image scenes." },
  { id: "timeline_mapping", label: "Timelines", short: "Timelines", copy: "Mapping master scenes to per-language durations." },
  { id: "review", label: "Review", short: "Review", copy: "Human review before final export." },
  { id: "export", label: "Exported", short: "Export", copy: "Final files ready to use." },
  { id: "needs_attention", label: "Needs Attention", short: "Attention", copy: "Failed or blocked episodes." },
];
const EPISODE_STAGE_LABELS = Object.fromEntries(EPISODE_PIPELINE_COLUMNS.map((c) => [c.id, c.label]));
const EPISODE_PER_LANG_STAGES = ["translation", "tts", "alignment", "timeline_mapping"];

const PROVIDERS = ["claude", "codex"];
const DEFAULT_MODEL_CATALOG = {
  claude: [
    { value: "haiku", label: "Haiku" },
    { value: "sonnet", label: "Sonnet" },
    { value: "opus", label: "Opus" },
  ],
  codex: [
    { value: "gpt-5.4", label: "GPT-5.4" },
    { value: "gpt-5.4-mini", label: "GPT-5.4 Mini" },
    { value: "gpt-5.2-codex", label: "GPT-5.2 Codex" },
    { value: "gpt-5.1-codex-mini", label: "GPT-5.1 Codex Mini" },
  ],
};
const REFRESH_INTERVAL_MS = 5000;

const state = {
  health: null,
  settings: null,
  modelCatalog: DEFAULT_MODEL_CATALOG,
  templates: [],
  route: { view: "pipeline-board" },
  theme: "dark",
  notice: { text: "", tone: "neutral" },
  modal: { kind: null },
  boardScrollLeft: 0,
  voiceProfiles: [],
  translationProfiles: [],
  targetLanguages: [],
  workerHealth: null,
  nicheProjects: [],
  nicheProjectDetail: null,
  boardEpisodes: [],
  episodeDetail: null,
  translationPreview: null,
};

let refreshTimer = null;
let noticeTimer = null;
let elapsedTimer = null;

const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

function titleCase(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function routeToHash(route) {
  if (route.view === "niche-project" && route.nicheProjectId) {
    return `#/niche-projects/${encodeURIComponent(route.nicheProjectId)}`;
  }
  if (route.view === "episode" && route.episodeId) {
    return `#/episodes/${encodeURIComponent(route.episodeId)}`;
  }
  return `#/${route.view || "pipeline-board"}`;
}

function parseRoute() {
  const hash = window.location.hash.replace(/^#/, "").replace(/^\/+/, "");
  if (!hash) return { view: "pipeline-board" };
  const parts = hash.split("/").filter(Boolean);
  if (parts[0] === "niche-projects" && parts[1]) {
    return { view: "niche-project", nicheProjectId: decodeURIComponent(parts[1]) };
  }
  if (parts[0] === "niche-projects") {
    return { view: "niche-projects" };
  }
  if (parts[0] === "episodes" && parts[1]) {
    return { view: "episode", episodeId: decodeURIComponent(parts[1]) };
  }
  if (parts[0] === "pipeline-board") {
    return { view: "pipeline-board" };
  }
  if (parts[0] === "voice-profiles") {
    return { view: "voice-profiles" };
  }
  if (parts[0] === "translation-profiles") {
    return { view: "translation-profiles" };
  }
  if (["settings", "templates"].includes(parts[0])) {
    return { view: parts[0] };
  }
  return { view: "pipeline-board" };
}

function applyTheme(theme) {
  state.theme = theme === "light" ? "light" : "dark";
  document.body.dataset.theme = state.theme;
  window.localStorage.setItem("tool1-theme", state.theme);
}

function bootTheme() {
  const stored = window.localStorage.getItem("tool1-theme");
  applyTheme(stored || "dark");
}

function setNotice(text, tone = "neutral") {
  state.notice = { text: text || "", tone };
  renderTopbar();
  if (noticeTimer) window.clearTimeout(noticeTimer);
  if (text) {
    noticeTimer = window.setTimeout(() => {
      state.notice = { text: "", tone: "neutral" };
      renderTopbar();
    }, 5000);
  }
}

function toneFromBoardStatus(status) {
  if (status === "Done") return "success";
  if (status === "Review" || status === "Queued") return "active";
  if (status === "Needs Attention") return "error";
  if (status === "Running") return "warn";
  return "neutral";
}

function toneFromRunStatus(status) {
  if (status === "completed") return "success";
  if (status === "failed") return "error";
  if (status === "running") return "warn";
  return "neutral";
}

function providerLabel(provider) {
  return provider === "claude" ? "Claude CLI" : provider === "codex" ? "Codex CLI" : provider || "Unknown";
}

function formatDate(value) {
  if (!value) return "unknown";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(parsed);
}

function shortText(value, max = 140) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, max).trimEnd()}...`;
}

function summarizeCardIssue(value, max = 120) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  const jsonStart = text.search(/\s[\[{]/);
  const cleaned = jsonStart > 0 ? text.slice(0, jsonStart).trim() : text;
  return shortText(cleaned || text, max);
}

function statusBadge(label, tone = "neutral") {
  return `<span class="badge" data-tone="${tone}">${esc(label)}</span>`;
}

function healthBadge(label, okState) {
  const tone = okState === "warn" ? "warn" : okState ? "success" : "error";
  return statusBadge(label, tone);
}

function iconSvg(name) {
  const icons = {
    add: '<svg viewBox="0 0 20 20"><path d="M10 4v12"/><path d="M4 10h12"/></svg>',
    back: '<svg viewBox="0 0 20 20"><path d="M15 10H5"/><path d="m9 6-4 4 4 4"/></svg>',
    bible: '<svg viewBox="0 0 20 20"><path d="M6 4.5h8.5a1.5 1.5 0 0 1 1.5 1.5v10H8a2 2 0 0 0-2 2"/><path d="M6 4.5A2.5 2.5 0 0 0 3.5 7V16a2 2 0 0 1 2-2h10.5"/></svg>',
    board: '<svg viewBox="0 0 20 20"><rect x="3" y="4" width="4" height="12" rx="1.2"/><rect x="8.5" y="4" width="4" height="8" rx="1.2"/><rect x="14" y="4" width="3" height="10" rx="1.2"/></svg>',
    close: '<svg viewBox="0 0 20 20"><path d="m5 5 10 10"/><path d="M15 5 5 15"/></svg>',
    delete: '<svg viewBox="0 0 20 20"><path d="M4.5 6h11"/><path d="M8 3.5h4"/><path d="M6.5 6v9.5a1 1 0 0 0 1 1h5a1 1 0 0 0 1-1V6"/><path d="M8.5 8.5v5"/><path d="M11.5 8.5v5"/></svg>',
    download: '<svg viewBox="0 0 20 20"><path d="M10 4v8"/><path d="m6.5 9.5 3.5 3.5 3.5-3.5"/><path d="M4 15.5h12"/></svg>',
    eye: '<svg viewBox="0 0 20 20"><path d="M1.8 10s3-5 8.2-5 8.2 5 8.2 5-3 5-8.2 5-8.2-5-8.2-5Z"/><circle cx="10" cy="10" r="2.2"/></svg>',
    finalize: '<svg viewBox="0 0 20 20"><path d="M10 3.5v8"/><path d="m6.5 8.5 3.5 3.5 3.5-3.5"/><path d="M4 14.5h12"/></svg>',
    language: '<svg viewBox="0 0 20 20"><path d="M10 3.5A6.5 6.5 0 1 0 16.5 10"/><path d="M3.9 7.5h12.2"/><path d="M3.9 12.5h8.1"/><path d="M10 3.5c1.8 1.8 2.8 4.1 2.8 6.5 0 2.4-1 4.7-2.8 6.5"/><path d="M10 3.5C8.2 5.3 7.2 7.6 7.2 10c0 2.4 1 4.7 2.8 6.5"/></svg>',
    moon: '<svg viewBox="0 0 20 20"><path d="M14.8 12.9A6 6 0 0 1 7.1 5.2 6.5 6.5 0 1 0 14.8 12.9Z"/></svg>',
    move: '<svg viewBox="0 0 20 20"><path d="M3 10h14"/><path d="m6.5 6-3.5 4 3.5 4"/><path d="m13.5 6 3.5 4-3.5 4"/></svg>',
    open: '<svg viewBox="0 0 20 20"><path d="M8 4H5.5A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16h9a1.5 1.5 0 0 0 1.5-1.5V12"/><path d="M11 4h5v5"/><path d="m9 11 7-7"/></svg>',
    overview: '<svg viewBox="0 0 20 20"><rect x="3" y="4" width="5.5" height="5.5" rx="1.2"/><rect x="11.5" y="4" width="5.5" height="5.5" rx="1.2"/><rect x="3" y="11" width="5.5" height="5.5" rx="1.2"/><rect x="11.5" y="11" width="5.5" height="5.5" rx="1.2"/></svg>',
    play: '<svg viewBox="0 0 20 20"><path d="m7 5 8 5-8 5Z"/></svg>',
    prompts: '<svg viewBox="0 0 20 20"><path d="M4 5.5h12"/><path d="M4 9.5h12"/><path d="M4 13.5h7"/></svg>',
    refresh: '<svg viewBox="0 0 20 20"><path d="M16 10a6 6 0 1 1-1.6-4.1"/><path d="M16 4.5v4h-4"/></svg>',
    rerun: '<svg viewBox="0 0 20 20"><path d="M15.5 7A6 6 0 1 0 16 10"/><path d="M15.5 3.8v3.7h-3.7"/></svg>',
    runs: '<svg viewBox="0 0 20 20"><path d="M3.5 4.5h13v11h-13z"/><path d="m6.5 8 2 2-2 2"/><path d="M10.5 12h3"/></svg>',
    save: '<svg viewBox="0 0 20 20"><path d="M4.5 4.5h9l2 2v9h-11z"/><path d="M7 4.5v4h6v-4"/><path d="M7.2 13h5.6"/></svg>',
    scene: '<svg viewBox="0 0 20 20"><path d="M3.5 6.5h13v9h-13z"/><path d="M6 3.5 4.2 6.5"/><path d="M10 3.5 8.2 6.5"/><path d="M14 3.5 12.2 6.5"/></svg>',
    settings: '<svg viewBox="0 0 20 20"><path d="M10 3.5v2"/><path d="M10 14.5v2"/><path d="M3.5 10h2"/><path d="M14.5 10h2"/><path d="m5.4 5.4 1.4 1.4"/><path d="m13.2 13.2 1.4 1.4"/><path d="m14.6 5.4-1.4 1.4"/><path d="m6.8 13.2-1.4 1.4"/><circle cx="10" cy="10" r="2.6"/></svg>',
    sun: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="3.2"/><path d="M10 2.8v2"/><path d="M10 15.2v2"/><path d="M2.8 10h2"/><path d="M15.2 10h2"/><path d="m4.9 4.9 1.4 1.4"/><path d="m13.7 13.7 1.4 1.4"/><path d="m15.1 4.9-1.4 1.4"/><path d="m6.3 13.7-1.4 1.4"/></svg>',
    templates: '<svg viewBox="0 0 20 20"><path d="M5 4.5h10v11H5z"/><path d="M7.5 8h5"/><path d="M7.5 11h5"/><path d="M7.5 14h3"/></svg>',
    timeline: '<svg viewBox="0 0 20 20"><path d="M4 5.5h4"/><path d="M12 5.5h4"/><path d="M4 10h12"/><path d="M4 14.5h4"/><path d="M12 14.5h4"/><circle cx="10" cy="10" r="1.8"/></svg>',
  };
  return icons[name] || icons.overview;
}

function iconMarkup(name) {
  return `<span class="button-icon" aria-hidden="true">${iconSvg(name)}</span>`;
}

function iconContent(name, label, { iconOnly = false } = {}) {
  return `${iconMarkup(name)}${iconOnly ? `<span class="sr-only">${esc(label)}</span>` : `<span class="button-label">${esc(label)}</span>`}`;
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Request failed.");
  return data;
}

function latestRunMap(runs) {
  const latest = new Map();
  for (const run of runs || []) {
    if (!latest.has(run.stage)) latest.set(run.stage, run);
  }
  return latest;
}

function routeTitle(route) {
  if (route.view === "pipeline-board") {
    return {
      title: "Pipeline Board",
      copy: "Episode pipeline board. Each card is one episode moving through planning stages.",
    };
  }
  if (route.view === "niche-projects" || route.view === "niche-project") {
    return {
      title: "Niche Projects",
      copy: "Manage niche projects. Each project groups episodes with shared language and provider settings.",
    };
  }
  if (route.view === "episode") {
    return {
      title: "Episode Detail",
      copy: "Per-language status, stage runs, and pipeline controls for this episode.",
    };
  }
  if (route.view === "voice-profiles") {
    return {
      title: "Voice Profiles",
      copy: "Manage voice profiles for TTS narration. Upload reference audio and test voice cloning.",
    };
  }
  if (route.view === "translation-profiles") {
    return {
      title: "Translation Profiles",
      copy: "Manage translation provider configurations for localization builds.",
    };
  }
  if (route.view === "settings") {
    return {
      title: "Settings",
      copy: "Global provider defaults, chunking limits, and runtime controls.",
    };
  }
  if (route.view === "templates") {
    return {
      title: "Agent Templates",
      copy: "Edit stage instructions separately so prompt logic does not compete with job review space.",
    };
  }
  return {
    title: "Pipeline Board",
    copy: "Episode pipeline board.",
  };
}

function autoRefreshAllowed(route) {
  if (route.view === "pipeline-board") return true;
  if (route.view === "niche-projects") return true;
  if (route.view === "niche-project") return true;
  if (route.view === "episode") return true;
  if (route.view === "voice-profiles") return true;
  return false;
}

function navIsActive(navView) {
  const v = state.route.view;
  if (navView === "pipeline-board" && (v === "pipeline-board" || v === "episode")) return true;
  if (navView === "niche-projects" && (v === "niche-projects" || v === "niche-project")) return true;
  return v === navView;
}

function renderSidebar() {
  const providers = state.health?.providers || {};
  const alignment = state.health?.alignment || {};
  const navItems = [
    { view: "pipeline-board", label: "Pipeline Board", icon: "board", count: state.boardEpisodes.length },
    { view: "niche-projects", label: "Niche Projects", icon: "settings", count: state.nicheProjects.length },
    { view: "voice-profiles", label: "Voice Profiles", icon: "settings", count: state.voiceProfiles.length },
    { view: "translation-profiles", label: "Translation Profiles", icon: "settings", count: state.translationProfiles.length },
    { view: "settings", label: "Settings", icon: "settings", count: "" },
    { view: "templates", label: "Templates", icon: "templates", count: state.templates.length },
  ];

  $("sidebar").innerHTML = `
    <section class="sidebar-brand">
      <div class="eyebrow">CLI-first workspace</div>
      <div class="brand-title">Tool 1</div>
      <div class="brand-copy">Board-first workflow. Create the card in the Draft column, then use the board to scan the pipeline left to right.</div>
    </section>

    <section class="sidebar-section">
      <div class="eyebrow">Navigation</div>
      <div class="sidebar-nav">
        ${navItems
          .map(
            (item) => `
            <button type="button" class="nav-link" data-nav="${item.view}" aria-current="${navIsActive(item.view) ? "page" : "false"}">
              <span class="nav-label">${iconMarkup(item.icon)}<span>${esc(item.label)}</span></span>
              ${item.count !== "" ? `<span class="nav-count">${esc(item.count)}</span>` : ""}
            </button>`
          )
          .join("")}
      </div>
    </section>

    <section class="sidebar-section">
      <div class="eyebrow">System health</div>
      <div class="badge-row" style="margin-top:10px;">
        ${healthBadge(`Codex ${providers.codex?.available ? (providers.codex?.logged_in ? "ready" : "login") : "missing"}`, providers.codex?.available ? (providers.codex?.logged_in ? true : "warn") : false)}
        ${healthBadge(`Claude ${providers.claude?.available ? (providers.claude?.logged_in ? "ready" : "login") : "missing"}`, providers.claude?.available ? (providers.claude?.logged_in ? true : "warn") : false)}
        ${healthBadge(`ffmpeg ${alignment.ffmpeg ? "ready" : "missing"}`, alignment.ffmpeg)}
        ${healthBadge(`MFA ${alignment.mfa ? "ready" : "check"}`, alignment.mfa ? true : "warn")}
      </div>
    </section>
  `;
}

function renderTopbar() {
  const current = routeTitle(state.route);
  $("topbar").innerHTML = `
    <div class="topbar-head">
      <div class="eyebrow">${esc(state.route.view === "job" ? `Job workspace / ${state.route.tab || "overview"}` : state.route.view)}</div>
      <h1 class="topbar-title">${esc(current.title)}</h1>
      <div class="helper section-copy">${esc(current.copy)}</div>
      ${state.notice.text ? `<div class="notice" data-tone="${esc(state.notice.tone)}">${esc(state.notice.text)}</div>` : ""}
    </div>
    <div class="topbar-actions">
      <button type="button" class="button button-ghost icon-only" data-refresh="true" aria-label="Refresh" title="Refresh">
        ${iconContent("refresh", "Refresh", { iconOnly: true })}
      </button>
      <button type="button" class="button ${state.theme === "dark" ? "button-primary" : "button-ghost"} icon-only" data-theme-toggle="true" aria-label="${esc(state.theme === "dark" ? "Light mode" : "Dark mode")}" title="${esc(state.theme === "dark" ? "Light mode" : "Dark mode")}">
        ${iconContent(state.theme === "dark" ? "sun" : "moon", state.theme === "dark" ? "Light mode" : "Dark mode", { iconOnly: true })}
      </button>
    </div>
  `;
}

function languageOptions(selected) {
  const languages = state.health?.languages || [];
  return languages
    .map(
      (language) =>
        `<option value="${esc(language.code)}" ${selected === language.code ? "selected" : ""}>${esc(language.label)}</option>`
    )
    .join("");
}

function providerOptions(selected) {
  return PROVIDERS.map(
    (provider) =>
      `<option value="${provider}" ${provider === selected ? "selected" : ""}>${esc(providerLabel(provider))}</option>`
  ).join("");
}

function modelCatalogFor(provider) {
  return state.modelCatalog?.[provider] || DEFAULT_MODEL_CATALOG[provider] || [];
}

function defaultModelForProvider(provider) {
  return modelCatalogFor(provider)[0]?.value || "";
}

function modelOptions(provider, selected) {
  const options = modelCatalogFor(provider);
  const preferred = selected || defaultModelForProvider(provider);
  return options
    .map(
      (option) =>
        `<option value="${esc(option.value)}" ${option.value === preferred ? "selected" : ""}>${esc(option.label)}</option>`
    )
    .join("");
}

function syncProviderModelSelect(providerSelectId, modelSelectId, preferredModel = "") {
  const providerSelect = $(providerSelectId);
  const modelSelect = $(modelSelectId);
  if (!providerSelect || !modelSelect) return;
  const provider = providerSelect.value || "claude";
  const options = modelCatalogFor(provider);
  const current = preferredModel || modelSelect.value || modelSelect.dataset.currentValue || "";
  modelSelect.innerHTML = options
    .map(
      (option) =>
        `<option value="${esc(option.value)}" ${option.value === current ? "selected" : ""}>${esc(option.label)}</option>`
    )
    .join("");
  const valid = options.some((option) => option.value === current);
  modelSelect.value = valid ? current : defaultModelForProvider(provider);
  modelSelect.dataset.currentValue = modelSelect.value;
}

function syncAllProviderModelSelects() {
  syncProviderModelSelect("settings-scene", "settings-scene-model");
  syncProviderModelSelect("settings-bible", "settings-bible-model");
  syncProviderModelSelect("settings-video", "settings-video-model");
  syncProviderModelSelect("settings-image", "settings-image-model");
  syncProviderModelSelect("niche-scene_planning-provider", "niche-scene_planning-model");
  syncProviderModelSelect("niche-visual_bible-provider", "niche-visual_bible-model");
  syncProviderModelSelect("niche-video_prompt-provider", "niche-video_prompt-model");
  syncProviderModelSelect("niche-image_prompt-provider", "niche-image_prompt-model");
}

function renderSetupCard({ icon, title, copy, fields, tone = "neutral", compact = false }) {
  return `
    <article class="setup-card${compact ? " setup-card-compact" : ""}" data-tone="${esc(tone)}">
      <div class="setup-card-head">
        <div class="setup-card-title-row">
          <span class="setup-card-glyph" aria-hidden="true">${iconMarkup(icon)}</span>
          <div class="setup-card-title-wrap">
            <div class="setup-card-title">${esc(title)}</div>
            <div class="setup-card-copy">${esc(copy)}</div>
          </div>
        </div>
      </div>
      <div class="setup-card-fields">${fields}</div>
    </article>
  `;
}

function renderStageSetupCard({
  icon,
  title,
  copy,
  providerId,
  providerValue,
  modelId,
  modelValue,
}) {
  return renderSetupCard({
    icon,
    title,
    copy,
    tone: "active",
    fields: `
      <div class="setup-card-field-grid">
        <label class="field">
          <span class="field-label">Provider</span>
          <select id="${esc(providerId)}">${providerOptions(providerValue)}</select>
        </label>
        <label class="field">
          <span class="field-label">Model</span>
          <select id="${esc(modelId)}">${modelOptions(providerValue, modelValue)}</select>
        </label>
      </div>
    `,
  });
}

function pipelineTone(status) {
  if (status === "running") return "info";
  if (status === "completed" || status === "done") return "success";
  if (status === "failed" || status === "error") return "error";
  if (status === "queued") return "warn";
  if (status === "paused_for_tts") return "warn";
  return "neutral";
}

function relativeTime(iso) {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function renderVoiceProfiles() {
  const profiles = state.voiceProfiles || [];
  const wh = state.workerHealth || {};
  const workerStatus = wh.running ? "running" : wh.is_stale ? "stale" : "stopped";

  const cards = profiles
    .map(
      (p) => `
      <div class="profile-card">
        <div class="profile-card-head">
          <h3 class="profile-card-title">${esc(p.name)}</h3>
          <div style="display:flex;gap:4px;">
            <button type="button" class="button button-ghost button-small" data-test-voice="${esc(p.id)}" title="Test voice">Test</button>
            <button type="button" class="button button-danger button-small icon-only" data-delete-voice-profile="${esc(p.id)}" aria-label="Delete" title="Delete">${iconContent("delete", "Delete", { iconOnly: true })}</button>
          </div>
        </div>
        <div class="badge-row" style="margin-top:8px;">
          <span class="badge">${esc(p.language_code || "?")}</span>
          ${p.has_latents ? `<span class="badge badge-success">Latents ready</span>` : `<span class="badge">No latents</span>`}
        </div>
        <p class="helper" style="margin-top:6px;font-size:0.78rem;">${esc(p.audio_file || "")}</p>
      </div>
    `
    )
    .join("");

  $("view").innerHTML = `
    <div class="detail-section">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div class="eyebrow">TTS Worker</div>
        <div style="display:flex;gap:8px;align-items:center;">
          <span class="worker-badge" data-status="${workerStatus}">${esc(titleCase(workerStatus))}</span>
          ${wh.running
            ? `<button type="button" class="button button-danger button-small" data-worker-action="stop">Stop</button>`
            : `<button type="button" class="button button-primary button-small" data-worker-action="start">Start</button>`}
        </div>
      </div>
      ${wh.status ? `<p class="helper" style="margin-top:6px;">Status: ${esc(wh.status)} ${wh.current_job_id ? `· Job: ${esc(wh.current_job_id)}` : ""}</p>` : ""}
    </div>

    <div class="detail-section">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div class="eyebrow">Voice profiles (${profiles.length})</div>
        <button type="button" class="button button-primary button-small has-icon" data-create-voice-profile="true">${iconContent("add", "Create profile")}</button>
      </div>
      ${profiles.length
        ? `<div class="profiles-grid" style="margin-top:12px;">${cards}</div>`
        : `<p class="helper" style="margin-top:8px;">No voice profiles yet.</p>`}
    </div>

    ${state.modal.kind === "create-voice-profile" ? renderCreateVoiceProfileModal() : ""}
    ${state.modal.kind === "test-voice" ? renderTestVoiceModal() : ""}
  `;
}

function renderCreateVoiceProfileModal() {
  const langs = state.targetLanguages || [];
  const langOpts = langs.map((l) => `<option value="${esc(l.code)}">${esc(l.label || l.code)}</option>`).join("");
  return `
    <div class="modal-backdrop" data-modal-backdrop="true">
      <div class="modal-panel">
        <div class="modal-header">
          <h2>Create voice profile</h2>
          <button type="button" class="button button-ghost icon-only" data-close-modal="true">${iconContent("close", "Close", { iconOnly: true })}</button>
        </div>
        <form id="create-voice-profile-form" class="stack">
          <div class="form-grid">
            <label class="field">
              <span class="field-label">Name</span>
              <input id="vp-name" required />
            </label>
            <label class="field">
              <span class="field-label">Language</span>
              <select id="vp-language">${langOpts}</select>
            </label>
            <label class="field">
              <span class="field-label">Reference audio</span>
              <input id="vp-audio" type="file" accept=".wav,.mp3,audio/*" required />
            </label>
          </div>
          <div class="button-row" style="margin-top:18px;">
            <button type="submit" class="button button-primary has-icon">${iconContent("add", "Create")}</button>
            <button type="button" class="button button-ghost" data-close-modal="true">Cancel</button>
          </div>
        </form>
      </div>
    </div>
  `;
}

function renderTestVoiceModal() {
  return `
    <div class="modal-backdrop" data-modal-backdrop="true">
      <div class="modal-panel">
        <div class="modal-header">
          <h2>Test voice</h2>
          <button type="button" class="button button-ghost icon-only" data-close-modal="true">${iconContent("close", "Close", { iconOnly: true })}</button>
        </div>
        <form id="test-voice-form" class="stack">
          <label class="field">
            <span class="field-label">Text to speak</span>
            <textarea id="tv-text" rows="3" required placeholder="Enter text to test the voice…"></textarea>
          </label>
          <div class="button-row" style="margin-top:18px;">
            <button type="submit" class="button button-primary">Test</button>
            <button type="button" class="button button-ghost" data-close-modal="true">Cancel</button>
          </div>
        </form>
      </div>
    </div>
  `;
}

function renderTranslationProfiles() {
  const profiles = state.translationProfiles || [];
  const cards = profiles
    .map(
      (p) => `
      <div class="profile-card">
        <div class="profile-card-head">
          <h3 class="profile-card-title">${esc(p.name)}</h3>
          <button type="button" class="button button-danger button-small icon-only" data-delete-translation-profile="${esc(p.id)}" aria-label="Delete" title="Delete">${iconContent("delete", "Delete", { iconOnly: true })}</button>
        </div>
        <div class="badge-row" style="margin-top:8px;">
          <span class="badge">${esc(p.provider || "?")}</span>
          ${p.model ? `<span class="badge">${esc(p.model)}</span>` : ""}
        </div>
      </div>
    `
    )
    .join("");

  $("view").innerHTML = `
    <div class="detail-section">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div class="eyebrow">Translation profiles (${profiles.length})</div>
        <button type="button" class="button button-primary button-small has-icon" data-create-translation-profile="true">${iconContent("add", "Create profile")}</button>
      </div>
      ${profiles.length
        ? `<div class="profiles-grid" style="margin-top:12px;">${cards}</div>`
        : `<p class="helper" style="margin-top:8px;">No translation profiles yet.</p>`}
    </div>

    ${state.modal.kind === "create-translation-profile" ? renderCreateTranslationProfileModal() : ""}
  `;
}

function renderCreateTranslationProfileModal() {
  return `
    <div class="modal-backdrop" data-modal-backdrop="true">
      <div class="modal-panel">
        <div class="modal-header">
          <h2>Create translation profile</h2>
          <button type="button" class="button button-ghost icon-only" data-close-modal="true">${iconContent("close", "Close", { iconOnly: true })}</button>
        </div>
        <form id="create-translation-profile-form" class="stack">
          <div class="form-grid">
            <label class="field">
              <span class="field-label">Name</span>
              <input id="tp-name" required />
            </label>
            <label class="field">
              <span class="field-label">Provider</span>
              <select id="tp-provider">
                <option value="claude">Claude</option>
                <option value="codex">Codex</option>
              </select>
            </label>
            <label class="field">
              <span class="field-label">API key (optional)</span>
              <input id="tp-api-key" type="password" placeholder="Leave blank to use default" />
            </label>
            <label class="field">
              <span class="field-label">Model (optional)</span>
              <input id="tp-model" placeholder="e.g. haiku, gpt-5.4" />
            </label>
          </div>
          <div class="button-row" style="margin-top:18px;">
            <button type="submit" class="button button-primary has-icon">${iconContent("add", "Create")}</button>
            <button type="button" class="button button-ghost" data-close-modal="true">Cancel</button>
          </div>
        </form>
      </div>
    </div>
  `;
}

// ── Episode pipeline board ────────────────────────────────────────

function episodeColumnForCard(ep) {
  if (!ep) return "draft";
  if (ep.board_status === "Needs Attention" || ep.pipeline_status === "failed") return "needs_attention";
  if (ep.board_status === "Done" || ep.current_stage === "export" || ep.pipeline_status === "done") return "export";
  if (ep.board_status === "Review" || ep.current_stage === "review" || ep.pipeline_status === "review") return "review";
  if (EPISODE_PIPELINE_COLUMNS.some((c) => c.id === ep.current_stage)) return ep.current_stage;
  return "draft";
}

function episodeColumnTone(colId, episodes) {
  const count = episodes.filter((ep) => episodeColumnForCard(ep) === colId).length;
  if (colId === "needs_attention") return count ? "error" : "neutral";
  if (colId === "export") return count ? "success" : "neutral";
  if (colId === "review") return count ? "active" : "neutral";
  return count ? "info" : "neutral";
}

function langProgressHtml(langStatuses, stage) {
  if (!langStatuses || !langStatuses.length) return "";
  const total = langStatuses.length;
  const statusKey = stage + "_status";
  const done = langStatuses.filter((ls) => ls[statusKey] === "done").length;
  const running = langStatuses.filter((ls) => ls[statusKey] === "running").length;
  const failed = langStatuses.filter((ls) => ls[statusKey] === "failed").length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  let label = `${done}/${total}`;
  if (running) label += ` (${running} running)`;
  if (failed) label += ` (${failed} failed)`;
  return `
    <div class="lang-progress">
      <div class="lang-progress-bar"><div class="lang-progress-fill" style="width:${pct}%"></div></div>
      <span class="lang-progress-label">${esc(label)}</span>
    </div>`;
}

function renderEpisodeCard(ep) {
  const currentStage = ep.current_stage || "draft";
  const isPerLang = EPISODE_PER_LANG_STAGES.includes(currentStage);
  const progress = isPerLang ? langProgressHtml(ep.language_statuses, currentStage) : "";
  const tone = pipelineTone(ep.pipeline_status);
  const error = ep.last_error ? `<div class="episode-card-error">${esc(summarizeCardIssue(ep.last_error, 80))}</div>` : "";
  const nicheLabel = ep.niche_project_title ? `<span class="episode-card-niche">${esc(ep.niche_project_title)}</span>` : "";
  const langCount = (ep.configured_languages || []).length;
  const isRunning = ep.pipeline_status === "running";
  const canQueue = ["idle", "failed", "review", "done"].includes(ep.pipeline_status || "idle");
  const elapsedHtml = isRunning && ep.updated_at
    ? `<span class="running-elapsed episode-elapsed" data-started-at="${esc(ep.updated_at)}">${esc(relativeTime(ep.updated_at))}</span>`
    : "";

  return `
    <div class="episode-card surface" data-open-episode="${esc(ep.id)}">
      <div class="episode-card-head">
        <strong class="episode-card-title">${esc(ep.title || ep.id)}</strong>
        ${nicheLabel}
      </div>
      <div class="badge-row" style="margin-top:6px;">
        <span class="badge badge-${tone}">${esc(EPISODE_STAGE_LABELS[currentStage] || titleCase(currentStage))}</span>
        <span class="badge">${esc(titleCase(ep.pipeline_status || "idle"))}</span>
        ${langCount ? `<span class="badge badge-small">${langCount} lang${langCount > 1 ? "s" : ""}</span>` : ""}
        ${elapsedHtml}
      </div>
      ${progress}
      ${error}
      <div class="episode-card-footer">
        <span class="helper" style="font-size:0.7rem;opacity:0.5;">${esc(relativeTime(ep.updated_at))}</span>
        <div class="episode-quick-actions" onclick="event.stopPropagation()">
          ${canQueue ? `<button type="button" class="button button-primary button-tiny" data-queue-episode="${esc(ep.id)}" title="Queue">${iconContent("play", "Queue")}</button>` : ""}
          <button type="button" class="button button-danger button-tiny" data-delete-episode="${esc(ep.id)}" title="Delete">${iconContent("delete", "Delete", { iconOnly: true })}</button>
        </div>
      </div>
    </div>`;
}

function renderPipelineBoard() {
  const episodes = state.boardEpisodes || [];

  $("view").innerHTML = `
    <section class="surface board-surface">
      <div class="section-head">
        <div>
          <div class="eyebrow">Pipeline</div>
          <h2 class="section-title">Episode pipeline board</h2>
        </div>
        <div class="button-row">
          <button type="button" class="button button-primary has-icon" data-nav="niche-projects">${iconContent("settings", "Niche Projects")}</button>
        </div>
      </div>
      <div class="helper section-copy">Each card is one episode (script submission). Per-language progress is shown inside the card. Columns represent pipeline stages from left to right.</div>
      <div class="badge-row" style="margin-top:12px;">
        ${statusBadge('Total episodes: ' + episodes.length, "active")}
      </div>
      <div id="pipeline-board" class="kanban-board pipeline-board" style="margin-top:18px;">
        ${EPISODE_PIPELINE_COLUMNS.map((col) => {
          const colEpisodes = episodes.filter((ep) => episodeColumnForCard(ep) === col.id);
          const cards = colEpisodes.map(renderEpisodeCard).join("");
          const empty = !cards ? '<div class="kanban-empty">No episodes at this step.</div>' : "";
          return '<section class="kanban-column">' +
            '<div class="kanban-column-head">' +
              '<div>' +
                '<div class="kanban-column-title">' + esc(col.label) + '</div>' +
                '<div class="tiny workflow-column-copy">' + esc(col.copy) + '</div>' +
                '<div class="tiny">' + colEpisodes.length + ' card(s)</div>' +
              '</div>' +
              statusBadge(String(colEpisodes.length), episodeColumnTone(col.id, episodes)) +
            '</div>' +
            '<div class="kanban-card-list">' + cards + empty + '</div>' +
          '</section>';
        }).join("")}
      </div>
    </section>
  `;
}

// ── Niche projects view ──────────────────────────────────────────

function renderNicheProjects() {
  const projects = state.nicheProjects || [];
  const grid = projects.length
    ? projects.map((p) => {
        const langs = (p.configured_languages || []).join(", ");
        return '<div class="surface project-card" data-open-niche-project="' + esc(p.id) + '">' +
          '<div class="project-card-head">' +
            '<h3 class="project-card-title">' + esc(p.title || p.id) + '</h3>' +
            '<button type="button" class="button button-danger button-small icon-only" data-delete-niche-project="' + esc(p.id) + '" data-project-title="' + esc(p.title) + '" aria-label="Delete">' + iconContent("delete", "Delete", { iconOnly: true }) + '</button>' +
          '</div>' +
          '<div class="badge-row" style="margin-top:6px;">' +
            '<span class="badge">' + esc(p.master_language || "en") + '</span>' +
            '<span class="badge">' + (p.episode_count || 0) + ' episode(s)</span>' +
          '</div>' +
          '<div class="helper" style="margin-top:4px;font-size:0.8rem;">' + esc(langs || "No languages configured") + '</div>' +
          '<div class="helper" style="margin-top:4px;font-size:0.75rem;opacity:0.6;">' + esc(relativeTime(p.updated_at)) + '</div>' +
        '</div>';
      }).join("")
    : '<div class="surface" style="padding:2rem;text-align:center;"><p class="helper">No niche projects yet. Create one to get started.</p></div>';

  $("view").innerHTML = `
    <div class="projects-header">
      <button type="button" class="button button-primary has-icon" data-open-create-niche="true">${iconContent("add", "Create niche project")}</button>
    </div>
    <div class="projects-grid">${grid}</div>
    ${state.modal.kind === "create-niche" ? renderCreateNicheModal() : ""}
    ${state.modal.kind === "submit-episode" ? renderSubmitEpisodeModal() : ""}
  `;
}

function renderCreateNicheModal() {
  const langs = state.targetLanguages || [];
  const langCheckboxes = langs.map((l) =>
    '<label style="display:flex;align-items:center;gap:6px;"><input type="checkbox" class="niche-lang-cb" value="' + esc(l.code) + '" /> ' + esc(l.label) + '</label>'
  ).join("");

  return `
    <div class="modal-backdrop" data-modal-backdrop="true">
      <div class="modal-panel">
        <div class="modal-header">
          <h2>Create niche project</h2>
          <button type="button" class="button button-ghost icon-only" data-close-modal="true">${iconContent("close", "Close", { iconOnly: true })}</button>
        </div>
        <form id="create-niche-form" class="stack">
          <div class="form-grid">
            <label class="field">
              <span class="field-label">Project name</span>
              <input id="niche-name" required placeholder="e.g., Religion Channel" />
            </label>
            <label class="field">
              <span class="field-label">Master language</span>
              <select id="niche-master-lang">${languageOptions("en")}</select>
            </label>
          </div>
          <div class="field" style="margin-top:12px;">
            <span class="field-label">Target languages</span>
            <div class="lang-checkbox-grid">${langCheckboxes}</div>
          </div>
          <div class="button-row" style="margin-top:18px;">
            <button type="submit" class="button button-primary has-icon">${iconContent("add", "Create")}</button>
            <button type="button" class="button button-ghost" data-close-modal="true">Cancel</button>
          </div>
        </form>
      </div>
    </div>
  `;
}

function renderSubmitEpisodeModal() {
  const projectId = state.modal.nicheProjectId;
  return `
    <div class="modal-backdrop" data-modal-backdrop="true">
      <div class="modal-panel">
        <div class="modal-header">
          <h2>Submit episode script</h2>
          <button type="button" class="button button-ghost icon-only" data-close-modal="true">${iconContent("close", "Close", { iconOnly: true })}</button>
        </div>
        <form id="submit-episode-form" class="stack">
          <input type="hidden" id="se-project-id" value="${esc(projectId)}" />
          <div class="form-grid">
            <label class="field">
              <span class="field-label">Episode title</span>
              <input id="se-title" required placeholder="e.g., The Story of Moses" />
            </label>
          </div>
          <label class="field" style="margin-top:12px;">
            <span class="field-label">Script text</span>
            <textarea id="se-script" rows="10" required placeholder="Paste the full script here..."></textarea>
          </label>
          <div class="button-row" style="margin-top:18px;">
            <button type="submit" class="button button-primary has-icon">${iconContent("add", "Submit & auto-start")}</button>
            <button type="button" class="button button-ghost" data-close-modal="true">Cancel</button>
          </div>
        </form>
      </div>
    </div>
  `;
}

// ── Niche project detail ──────────────────────────────────────────

function overallLangTone(ls) {
  const statuses = [ls.translation_status, ls.tts_status, ls.srt_status, ls.timeline_status];
  if (statuses.some((s) => s === "failed")) return "error";
  if (statuses.some((s) => s === "running")) return "info";
  if (statuses.every((s) => s === "done")) return "success";
  if (statuses.some((s) => s === "done")) return "warn";
  return "neutral";
}

function langMiniDots(episode, configuredLangs) {
  const statuses = episode.language_statuses || [];
  const statusMap = Object.fromEntries(statuses.map((s) => [s.language_code, s]));
  return configuredLangs.map((lang) => {
    const ls = statusMap[lang];
    const tone = ls ? overallLangTone(ls) : "neutral";
    return '<span class="lang-dot" data-tone="' + tone + '" title="' + esc(lang) + '"></span>';
  }).join("");
}

function renderProjectStats(detail) {
  const stats = detail.statistics || {};
  const byStatus = stats.by_status || {};
  const items = [
    { label: "Total", value: stats.total_episodes || 0, tone: "" },
    { label: "Draft", value: byStatus.idle || 0, tone: "" },
    { label: "Queued", value: byStatus.queued || 0, tone: "warn" },
    { label: "Running", value: byStatus.running || 0, tone: "info" },
    { label: "Done", value: byStatus.done || 0, tone: "success" },
    { label: "Failed", value: byStatus.failed || 0, tone: "error" },
    { label: "Languages", value: stats.languages_configured || 0, tone: "" },
    { label: "Complete", value: (stats.completion_rate || 0) + "%", tone: "success" },
  ];
  return '<div class="stats-bar">' + items.map((it) =>
    '<div class="stat-card"' + (it.tone ? ' data-tone="' + it.tone + '"' : '') + '>' +
      '<div class="stat-card-value">' + esc(it.value) + '</div>' +
      '<div class="stat-card-label">' + esc(it.label) + '</div>' +
    '</div>'
  ).join("") + '</div>';
}

function renderLanguageConfigSection(project, voiceProfiles, translationProfiles) {
  const langs = project.configured_languages || [];
  const vps = project.language_voice_profiles || {};
  const tps = project.language_translation_profiles || {};
  const allLangs = state.targetLanguages || [];
  const usedSet = new Set(langs);

  const rows = langs.map((langCode) => {
    const langObj = allLangs.find((l) => l.code === langCode);
    const langLabel = langObj ? langObj.label : langCode;

    const vpOptions = (voiceProfiles || [])
      .filter((vp) => !vp.language_code || vp.language_code === langCode || vp.language_code === langCode.split("-")[0])
      .map((vp) =>
        '<option value="' + esc(vp.id) + '"' + (vps[langCode] === vp.id ? " selected" : "") + '>' + esc(vp.name) + '</option>'
      ).join("");

    const tpOptions = (translationProfiles || [])
      .map((tp) =>
        '<option value="' + esc(tp.id) + '"' + (tps[langCode] === tp.id ? " selected" : "") + '>' + esc(tp.name) + '</option>'
      ).join("");

    return '<tr>' +
      '<td><strong>' + esc(langLabel) + '</strong> <span class="helper">(' + esc(langCode) + ')</span></td>' +
      '<td><select class="lang-voice-select" data-lang="' + esc(langCode) + '"><option value="">— none —</option>' + vpOptions + '</select></td>' +
      '<td><select class="lang-trans-select" data-lang="' + esc(langCode) + '"><option value="">— none —</option>' + tpOptions + '</select></td>' +
      '<td><button type="button" class="button button-danger button-small icon-only" data-remove-language="' + esc(langCode) + '" aria-label="Remove">' + iconContent("delete", "Remove", { iconOnly: true }) + '</button></td>' +
    '</tr>';
  }).join("");

  const unusedLangs = allLangs.filter((l) => !usedSet.has(l.code));
  const addOptions = unusedLangs.map((l) =>
    '<option value="' + esc(l.code) + '">' + esc(l.label) + '</option>'
  ).join("");

  return `
    <div class="detail-section">
      <div class="section-header">
        <div class="eyebrow">Language Configuration</div>
        <button type="button" class="button button-primary button-small has-icon" data-save-lang-config="${esc(project.id)}">${iconContent("save", "Save")}</button>
      </div>
      <table class="lang-config-table">
        <thead><tr><th>Language</th><th>Voice Profile</th><th>Translation Profile</th><th></th></tr></thead>
        <tbody>${rows || '<tr><td colspan="4" class="helper" style="text-align:center;">No languages configured.</td></tr>'}</tbody>
      </table>
      ${unusedLangs.length ? '<div class="add-lang-row"><select id="add-lang-select"><option value="">Add language...</option>' + addOptions + '</select><button type="button" class="button button-ghost button-small has-icon" data-add-language="true">' + iconContent("add", "Add") + '</button></div>' : ''}
    </div>
  `;
}

function renderProviderConfigSection(project) {
  const stages = [
    { key: "scene_planning", icon: "scene", title: "Scene Planning", copy: "LLM plans scene structure" },
    { key: "visual_bible", icon: "bible", title: "Visual Bible", copy: "Consistency guide generation" },
    { key: "video_prompt", icon: "prompts", title: "Video Prompts", copy: "Leading scene video prompts" },
    { key: "image_prompt", icon: "prompts", title: "Image Prompts", copy: "Remaining scene image prompts" },
  ];

  const cards = stages.map((s) =>
    renderStageSetupCard({
      icon: s.icon,
      title: s.title,
      copy: s.copy,
      providerId: "niche-" + s.key + "-provider",
      providerValue: project[s.key + "_provider"] || "claude",
      modelId: "niche-" + s.key + "-model",
      modelValue: project[s.key + "_model"] || "",
    })
  ).join("");

  return `
    <div class="detail-section">
      <div class="section-header">
        <div class="eyebrow">AI Provider Configuration</div>
        <button type="button" class="button button-primary button-small has-icon" data-save-provider-config="${esc(project.id)}">${iconContent("save", "Save")}</button>
      </div>
      <div class="provider-config-grid">${cards}</div>
      <div style="margin-top:10px;">
        <label class="field" style="max-width:200px;">
          <span class="field-label">Leading video scenes</span>
          <input id="niche-leading-video" type="number" min="1" value="${project.leading_video_scene_count || 20}" />
        </label>
      </div>
    </div>
  `;
}

function renderNicheProjectDetail() {
  const detail = state.nicheProjectDetail;
  if (!detail) {
    $("view").innerHTML = '<div class="surface" style="padding:2rem;"><p class="helper">Niche project not found.</p></div>';
    return;
  }
  const project = detail.project;
  const episodes = detail.episodes || [];
  const stats = detail.statistics || {};
  const byStatus = stats.by_status || {};
  const langs = project.configured_languages || [];

  const draftCount = byStatus.idle || 0;
  const failedCount = byStatus.failed || 0;

  const episodeCards = episodes.length
    ? episodes.map((ep) => {
        const stage = ep.current_stage || "draft";
        const tone = pipelineTone(ep.pipeline_status);
        const canQueue = ["idle", "failed", "done"].includes(ep.pipeline_status || "idle");
        return '<div class="episode-card-enhanced" data-open-episode="' + esc(ep.id) + '">' +
          '<div class="episode-card-top">' +
            '<strong>' + esc(ep.title || ep.id) + '</strong>' +
            '<div style="display:flex;gap:4px;">' +
              (canQueue ? '<button type="button" class="button button-ghost button-small icon-only" data-queue-episode="' + esc(ep.id) + '" title="Queue">' + iconContent("play", "Queue", { iconOnly: true }) + '</button>' : '') +
              '<button type="button" class="button button-danger button-small icon-only" data-delete-episode="' + esc(ep.id) + '" aria-label="Delete">' + iconContent("delete", "Delete", { iconOnly: true }) + '</button>' +
            '</div>' +
          '</div>' +
          '<div class="badge-row" style="margin-top:6px;">' +
            '<span class="badge" data-tone="' + tone + '">' + esc(EPISODE_STAGE_LABELS[stage] || titleCase(stage)) + '</span>' +
            '<span class="badge">' + esc(titleCase(ep.pipeline_status || "idle")) + '</span>' +
            '<span class="lang-dots">' + langMiniDots(ep, langs) + '</span>' +
          '</div>' +
        '</div>';
      }).join("")
    : '<p class="helper">No episodes yet. Submit a script to start.</p>';

  $("view").innerHTML = `
    <div class="detail-section">
      <div class="eyebrow">Niche Project</div>
      <h2 style="margin:4px 0 0;">${esc(project.title || project.id)}</h2>
      <div class="badge-row" style="margin-top:8px;">
        <span class="badge">Master: ${esc(project.master_language || "en")}</span>
      </div>
    </div>

    ${renderProjectStats(detail)}

    ${renderLanguageConfigSection(project, detail.voice_profiles || [], detail.translation_profiles || [])}

    ${renderProviderConfigSection(project)}

    <div class="detail-section">
      <div class="section-header">
        <div class="eyebrow">Episodes (${episodes.length})</div>
        <div class="button-row">
          ${draftCount > 0 ? '<button type="button" class="button button-ghost button-small has-icon" data-batch-queue-drafts="' + esc(project.id) + '">' + iconContent("play", "Queue all drafts (" + draftCount + ")") + '</button>' : ''}
          ${failedCount > 0 ? '<button type="button" class="button button-ghost button-small has-icon" data-batch-queue-failed="' + esc(project.id) + '">' + iconContent("rerun", "Re-run failed (" + failedCount + ")") + '</button>' : ''}
          <button type="button" class="button button-primary button-small has-icon" data-open-submit-episode="${esc(project.id)}">${iconContent("add", "Submit script")}</button>
        </div>
      </div>
      <div class="loc-list" style="margin-top:12px;">${episodeCards}</div>
    </div>

    <div class="detail-section">
      <button type="button" class="button button-ghost has-icon" data-nav="niche-projects">${iconContent("back", "Back to niche projects")}</button>
    </div>

    ${state.modal.kind === "submit-episode" ? renderSubmitEpisodeModal() : ""}
  `;

  // Sync provider/model selects after render
  syncProviderModelSelect("niche-scene_planning-provider", "niche-scene_planning-model");
  syncProviderModelSelect("niche-visual_bible-provider", "niche-visual_bible-model");
  syncProviderModelSelect("niche-video_prompt-provider", "niche-video_prompt-model");
  syncProviderModelSelect("niche-image_prompt-provider", "niche-image_prompt-model");
}

// ── Episode detail view ───────────────────────────────────────────

function renderEpisodeDetail() {
  const detail = state.episodeDetail;
  if (!detail) {
    $("view").innerHTML = '<div class="surface" style="padding:2rem;"><p class="helper">Episode not found.</p></div>';
    return;
  }
  const episode = detail.episode;
  const langStatuses = detail.language_statuses || [];
  const stageRuns = detail.stage_runs || [];
  const currentStage = episode.current_stage || "draft";

  // Pipeline progress bar
  const allStages = EPISODE_PIPELINE_COLUMNS.filter((c) => c.id !== "needs_attention");
  const currentIdx = allStages.findIndex((c) => c.id === currentStage);
  const doneCount = allStages.filter((s, i) => {
    if (episode.pipeline_status === "done") return true;
    if (i < currentIdx) return true;
    if (i === currentIdx && (episode.pipeline_status === "review" || episode.pipeline_status === "done")) return true;
    return false;
  }).length;
  const progressPct = allStages.length ? Math.round((doneCount / allStages.length) * 100) : 0;

  const stageStrip = allStages.map((s, i) => {
    let st = "pending";
    if (episode.pipeline_status === "done") st = "done";
    else if (i < currentIdx) st = "done";
    else if (i === currentIdx && episode.pipeline_status === "running") st = "active";
    else if (i === currentIdx && (episode.pipeline_status === "review" || episode.pipeline_status === "done")) st = "done";
    else if (i === currentIdx && episode.pipeline_status === "failed") st = "failed";
    return '<div class="stage-strip-item" data-state="' + st + '" title="' + esc(s.label) + '">' + esc(s.short) + '</div>';
  }).join("");

  // Worker health
  const wh = detail.worker_health || {};
  const workerRunning = wh.running;
  const workerTone = workerRunning ? (wh.is_stale ? "warn" : "success") : "error";
  const workerLabel = workerRunning ? (wh.is_stale ? "TTS Worker stale" : "TTS Worker active") : "TTS Worker offline";

  // Per-language table
  const isPipelineIdle = !["running", "queued"].includes(episode.pipeline_status || "idle");
  const langRows = langStatuses.map((ls) => {
    const canRetryTranslation = isPipelineIdle && (ls.translation_status === "failed" || ls.translation_status === "skipped");
    const canRetryTts = isPipelineIdle && (ls.tts_status === "failed" || ls.tts_status === "skipped");
    const hasTranslation = ls.translation_status === "done" && ls.language_code !== (episode.master_language || "en");
    const ttsProgress = ls.tts_job_progress ? ' <span class="helper" style="font-size:0.7rem;">(' + esc(ls.tts_job_progress) + ')</span>' : '';

    return '<tr>' +
      '<td><strong>' + esc(ls.language_code) + '</strong></td>' +
      '<td>' + langStatusBadge(ls.translation_status) +
        (hasTranslation ? ' <button type="button" class="button button-ghost button-small" style="font-size:0.7rem;padding:2px 6px;" data-preview-translation="' + esc(episode.id) + '" data-preview-lang="' + esc(ls.language_code) + '">Preview</button>' : '') +
        (canRetryTranslation ? ' <button type="button" class="button button-ghost button-small" style="font-size:0.7rem;padding:2px 6px;" data-retry-language="' + esc(episode.id) + '" data-retry-lang="' + esc(ls.language_code) + '" data-retry-stage="translation">Retry</button>' : '') +
      '</td>' +
      '<td>' + langStatusBadge(ls.tts_status) + ttsProgress +
        (canRetryTts ? ' <button type="button" class="button button-ghost button-small" style="font-size:0.7rem;padding:2px 6px;" data-retry-language="' + esc(episode.id) + '" data-retry-lang="' + esc(ls.language_code) + '" data-retry-stage="tts">Retry</button>' : '') +
      '</td>' +
      '<td>' + langStatusBadge(ls.srt_status) + '</td>' +
      '<td>' + langStatusBadge(ls.timeline_status) + '</td>' +
      '<td class="helper" style="font-size:0.75rem;">' + esc(ls.error_message || "") + '</td>' +
    '</tr>';
  }).join("");

  const langTable = langStatuses.length ? `
    <table class="lang-table">
      <thead><tr><th>Lang</th><th>Translation</th><th>TTS</th><th>SRT</th><th>Timeline</th><th>Error</th></tr></thead>
      <tbody>${langRows}</tbody>
    </table>` : '<p class="helper">No language data.</p>';

  // Stage runs — expandable detail cards
  const runsHtml = stageRuns.length ? stageRuns.slice(0, 30).map((r) => {
    const duration = r.started_at && r.finished_at
      ? Math.round((new Date(r.finished_at) - new Date(r.started_at)) / 1000) + "s"
      : r.started_at && !r.finished_at ? "running…" : "";
    return `<details class="run-detail-card">
      <summary>
        <div class="run-detail-head">
          <span class="badge badge-${toneFromRunStatus(r.status)}">${esc(r.stage || "?")}</span>
          <span class="badge">${esc(r.status || "?")}</span>
          ${r.language_code ? '<span class="badge badge-small">' + esc(r.language_code) + '</span>' : ''}
          ${duration ? '<span class="helper" style="font-size:0.75rem;">' + esc(duration) + '</span>' : ''}
          <span class="helper" style="font-size:0.75rem;margin-left:auto;">${esc(relativeTime(r.started_at))}</span>
        </div>
      </summary>
      <div class="run-detail-body">
        ${r.provider ? '<div class="run-meta">Provider: <strong>' + esc(r.provider) + '</strong></div>' : ''}
        ${r.error_message ? '<div class="notice" data-tone="error" style="margin-top:6px;font-size:0.8rem;">' + esc(r.error_message) + '</div>' : ''}
        ${r.stdout_preview ? '<pre class="run-output">' + esc(r.stdout_preview) + '</pre>' : ''}
      </div>
    </details>`;
  }).join("") : '<p class="helper">No stage runs recorded yet.</p>';

  const queueDisabled = episode.pipeline_status === "running" ? "disabled" : "";
  const isRunning = episode.pipeline_status === "running";

  // Output files section (lazy-loaded)
  const filesSection = `
    <div class="detail-section">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div class="eyebrow">Output files</div>
        <button type="button" class="button button-ghost button-small" onclick="loadEpisodeFiles('${esc(episode.id)}')">Refresh files</button>
      </div>
      <div id="episode-files-list" class="output-files-grid" style="margin-top:10px;">
        <p class="helper">Click "Refresh files" to load output files.</p>
      </div>
    </div>`;

  $("view").innerHTML = `
    <div class="detail-section">
      <div class="eyebrow">Episode</div>
      <h2 style="margin:4px 0 0;">${esc(episode.title || episode.id)}</h2>
      <div class="badge-row" style="margin-top:8px;">
        <span class="badge badge-${pipelineTone(episode.pipeline_status)}">${esc(titleCase(episode.pipeline_status || "idle"))}</span>
        <span class="badge">${esc(EPISODE_STAGE_LABELS[currentStage] || titleCase(currentStage))}</span>
        <span class="badge">Master: ${esc(episode.master_language || "en")}</span>
        ${langStatuses.length ? '<span class="badge badge-small">' + langStatuses.length + ' lang' + (langStatuses.length > 1 ? 's' : '') + '</span>' : ''}
        ${isRunning ? '<span class="running-elapsed" data-started-at="' + esc(episode.updated_at) + '">…</span>' : ''}
      </div>
      ${episode.last_error ? '<div class="notice" data-tone="error" style="margin-top:8px;">' + esc(episode.last_error) + '</div>' : ""}
    </div>

    <div class="detail-section">
      <div class="eyebrow">Pipeline progress — ${progressPct}%</div>
      <div class="pipeline-progress-bar" style="margin-top:8px;">
        <div class="pipeline-progress-fill" style="width:${progressPct}%"></div>
      </div>
      <div class="stage-strip" style="margin-top:10px;">${stageStrip}</div>
      <div class="button-row" style="margin-top:12px;">
        <button type="button" class="button button-primary has-icon" data-queue-episode="${esc(episode.id)}" ${queueDisabled}>${iconContent("play", "Queue / Rerun")}</button>
        <button type="button" class="button button-danger has-icon" data-delete-episode="${esc(episode.id)}">${iconContent("delete", "Delete episode")}</button>
      </div>
    </div>

    <div class="detail-section">
      <div class="section-header">
        <div class="eyebrow">Per-language status</div>
        <div class="badge-row">
          <span class="badge" data-tone="${workerTone}">${esc(workerLabel)}</span>
          ${!workerRunning ? '<button type="button" class="button button-ghost button-small" data-worker-action="start">Start Worker</button>' : ''}
        </div>
      </div>
      ${langTable}
    </div>

    ${filesSection}

    <div class="detail-section">
      <div class="eyebrow">Stage runs (${stageRuns.length})</div>
      ${runsHtml}
    </div>

    <div class="detail-section">
      <button type="button" class="button button-ghost has-icon" data-nav="pipeline-board">${iconContent("back", "Back to board")}</button>
    </div>

    ${state.modal.kind === "translation-preview" ? renderTranslationPreviewModal() : ""}
  `;

  // Auto-load files
  loadEpisodeFiles(episode.id);
}

async function loadEpisodeFiles(episodeId) {
  const container = $("episode-files-list");
  if (!container) return;
  container.innerHTML = '<p class="helper">Loading…</p>';
  try {
    const data = await api("/api/episodes/" + encodeURIComponent(episodeId) + "/files");
    const files = data.files || [];
    if (!files.length) {
      container.innerHTML = '<p class="helper">No output files yet.</p>';
      return;
    }
    const fileIcons = { ".srt": "timeline", ".json": "settings", ".txt": "prompts", ".wav": "play", ".mp3": "play" };
    container.innerHTML = files.map((f) => {
      const sizeKb = (f.size / 1024).toFixed(1);
      const icon = fileIcons[f.ext] || "open";
      return '<div class="output-file-card">' +
        '<div class="output-file-info">' +
          iconMarkup(icon) +
          '<div>' +
            '<div class="output-file-name">' + esc(f.name) + '</div>' +
            '<div class="helper" style="font-size:0.7rem;">' + sizeKb + ' KB</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    }).join("");
  } catch (err) {
    container.innerHTML = '<p class="helper" style="color:var(--clr-error);">' + esc(err.message) + '</p>';
  }
}

function renderTranslationPreviewModal() {
  const preview = state.translationPreview;
  if (!preview) return '';
  const statusBadgeHtml = langStatusBadge(preview.translation_status);
  const logSummary = (preview.translation_log || []).map((c) =>
    '<div class="helper" style="font-size:0.75rem;">' +
      'Chunk ' + c.chunk_index + ': ' + c.words_in + ' → ' + c.words_out + ' words ' +
      '<span class="badge badge-' + (c.status === "ok" ? "success" : "error") + '" style="font-size:0.65rem;">' + esc(c.status) + '</span>' +
      (c.error ? ' <span class="helper" style="color:var(--error);">' + esc(c.error) + '</span>' : '') +
    '</div>'
  ).join("");

  return `
    <div class="modal-backdrop" data-modal-backdrop="true">
      <div class="modal-panel" style="max-width:900px;width:95vw;">
        <div class="modal-header">
          <h2>Translation Preview — ${esc(preview.language_code)} ${statusBadgeHtml}</h2>
          <button type="button" class="button button-ghost icon-only" data-close-modal="true">${iconContent("close", "Close", { iconOnly: true })}</button>
        </div>
        <div class="translation-preview-grid">
          <div class="translation-preview-col">
            <div class="eyebrow">Original</div>
            <pre class="translation-preview-text">${esc(preview.original || "")}</pre>
          </div>
          <div class="translation-preview-col">
            <div class="eyebrow">Translated (${esc(preview.language_code)})</div>
            <pre class="translation-preview-text">${esc(preview.translated || "(no translation yet)")}</pre>
          </div>
        </div>
        ${logSummary ? '<div style="margin-top:12px;"><div class="eyebrow">Chunk log</div>' + logSummary + '</div>' : ''}
      </div>
    </div>
  `;
}

function langStatusBadge(status) {
  const tone = status === "done" ? "success" : status === "running" ? "info" : status === "failed" ? "error" : "neutral";
  return '<span class="badge badge-' + tone + '">' + esc(titleCase(status || "pending")) + '</span>';
}

function captureDashboardScroll() {
  const board = $("pipeline-board");
  if (!board) return;
  state.boardScrollLeft = board.scrollLeft || 0;
}

function restoreDashboardScroll() {
  if (state.route.view !== "pipeline-board") return;
  const board = $("pipeline-board");
  if (!board) return;
  board.scrollLeft = state.boardScrollLeft || 0;
  board.addEventListener(
    "scroll",
    () => {
      state.boardScrollLeft = board.scrollLeft || 0;
    },
    { passive: true }
  );
}

function renderSettings() {
  const settings = state.settings || {};
  $("view").innerHTML = `
    <section class="split-grid">
      <section class="surface">
        <div class="section-head">
          <div>
            <div class="eyebrow">Global defaults</div>
            <h2 class="section-title">Pipeline settings</h2>
          </div>
        </div>
        <form id="settings-form" class="stack">
          <div class="helper">These are the defaults every new card starts with. You can still override them on a single card later.</div>
          <div class="workflow-setup-grid">
            ${renderStageSetupCard({
              icon: "scene",
              title: "Scene planning",
              copy: "Default writer for scene structure.",
              providerId: "settings-scene",
              providerValue: settings.default_scene_planning_provider || "claude",
              modelId: "settings-scene-model",
              modelValue: settings.default_scene_planning_model || "haiku",
            })}
            ${renderStageSetupCard({
              icon: "bible",
              title: "Consistency guide",
              copy: "Default writer for character, place, and object consistency.",
              providerId: "settings-bible",
              providerValue: settings.default_visual_bible_provider || "claude",
              modelId: "settings-bible-model",
              modelValue: settings.default_visual_bible_model || "haiku",
            })}
            ${renderStageSetupCard({
              icon: "play",
              title: "Video prompts",
              copy: "Default writer for moving scenes.",
              providerId: "settings-video",
              providerValue: settings.default_video_prompt_provider || "codex",
              modelId: "settings-video-model",
              modelValue: settings.default_video_prompt_model || "gpt-5.4",
            })}
            ${renderStageSetupCard({
              icon: "prompts",
              title: "Image prompts",
              copy: "Default writer for still scenes.",
              providerId: "settings-image",
              providerValue: settings.default_image_prompt_provider || "codex",
              modelId: "settings-image-model",
              modelValue: settings.default_image_prompt_model || "gpt-5.4",
            })}
            ${renderSetupCard({
              icon: "timeline",
              title: "Output strategy",
              copy: "How many opening scenes should start as video before the rest become image prompts.",
              tone: "warn",
              fields: `
                <label class="field">
                  <span class="field-label">Video-first scenes</span>
                  <input id="settings-leading-video" type="number" min="0" value="${esc(settings.leading_video_scene_count || 20)}" />
                </label>
              `,
            })}
          </div>
          <div class="workflow-setup-grid workflow-setup-grid-compact">
            ${renderSetupCard({
              icon: "prompts",
              title: "Prompt batches",
              copy: "How many scenes each prompt-generation run should handle at once.",
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
              copy: "How many subtitle seconds to feed into each planning chunk.",
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
              copy: "How many seconds neighboring chunks should overlap for safer merges.",
              fields: `
                <label class="field">
                  <span class="field-label">Overlap seconds</span>
                  <input id="settings-overlap-seconds" type="number" min="0" value="${esc(settings.planning_overlap_seconds || 30)}" />
                </label>
              `,
            })}
          </div>
          <div class="button-row">
            <button type="submit" class="button button-primary has-icon">${iconContent("save", "Save settings")}</button>
            <button type="button" class="button has-icon" data-nav="templates">${iconContent("templates", "Open templates")}</button>
          </div>
        </form>
      </section>

      <section class="surface">
        <div class="section-head">
          <div>
            <div class="eyebrow">Health</div>
            <h2 class="section-title">Runtime overview</h2>
          </div>
        </div>
        <div class="provider-grid">
          <article class="summary-card">
            <div class="metric-label">Codex CLI</div>
            <div class="metric-value">${esc(state.health?.providers?.codex?.logged_in ? "Ready" : state.health?.providers?.codex?.available ? "Login" : "Missing")}</div>
            <div class="metric-copy">Install and login state from the local machine.</div>
          </article>
          <article class="summary-card">
            <div class="metric-label">Claude CLI</div>
            <div class="metric-value">${esc(state.health?.providers?.claude?.logged_in ? "Ready" : state.health?.providers?.claude?.available ? "Login" : "Missing")}</div>
            <div class="metric-copy">Used by default for planning and world-building steps.</div>
          </article>
        </div>
        <div class="badge-row" style="margin-top:16px;">
          ${healthBadge(`ffmpeg ${state.health?.alignment?.ffmpeg ? "ready" : "missing"}`, state.health?.alignment?.ffmpeg)}
          ${healthBadge(`MFA ${state.health?.alignment?.mfa ? "ready" : "check"}`, state.health?.alignment?.mfa ? true : "warn")}
          ${healthBadge(`WhisperX ${state.health?.alignment?.whisperx ? "ready" : "check"}`, state.health?.alignment?.whisperx ? true : "warn")}
        </div>
        <div class="notice" style="margin-top:18px;">Templates now live on their own page so settings stays focused on actual runtime defaults.</div>
      </section>
    </section>
  `;
}

function renderTemplates() {
  const selectedStage = state.route.templateStage || "scene_planning";
  const selectedProvider = state.route.templateProvider || "claude";
  const template = state.templates.find((item) => item.stage === selectedStage && item.provider === selectedProvider);
  $("view").innerHTML = `
    <section class="surface">
      <div class="section-head">
        <div>
          <div class="eyebrow">Templates</div>
          <h2 class="section-title">Agent instructions</h2>
        </div>
        <div class="button-row">
          <button type="button" class="button button-ghost has-icon" data-nav="settings">${iconContent("back", "Back to settings")}</button>
        </div>
      </div>
      <div class="helper section-copy">These instructions are now separated from the dashboard so you only touch them when you really mean to change stage behavior.</div>
      <form id="templates-form" class="stack" style="margin-top:18px;">
        <div class="form-grid">
          <label class="field">
            <span class="field-label">Stage</span>
            <select id="template-stage">
              <option value="scene_planning" ${selectedStage === "scene_planning" ? "selected" : ""}>Scene planning</option>
    <option value="visual_bible" ${selectedStage === "visual_bible" ? "selected" : ""}>Consistency guide</option>
              <option value="video_prompt_generation" ${selectedStage === "video_prompt_generation" ? "selected" : ""}>Video prompts</option>
              <option value="image_prompt_generation" ${selectedStage === "image_prompt_generation" ? "selected" : ""}>Image prompts</option>
            </select>
          </label>
          <label class="field">
            <span class="field-label">Provider</span>
            <select id="template-provider">${providerOptions(selectedProvider)}</select>
          </label>
        </div>
        <label class="field">
          <span class="field-label">Template body</span>
          <textarea id="template-body" class="is-large">${esc(template?.body || "")}</textarea>
        </label>
        <div class="button-row">
          <button type="submit" class="button button-primary has-icon">${iconContent("save", "Save template")}</button>
        </div>
      </form>
    </section>
  `;
}

function renderApp() {
  captureDashboardScroll();
  renderSidebar();
  renderTopbar();
  const view = state.route.view;
  if (view === "pipeline-board") renderPipelineBoard();
  else if (view === "niche-projects") renderNicheProjects();
  else if (view === "niche-project") renderNicheProjectDetail();
  else if (view === "episode") renderEpisodeDetail();

  else if (view === "voice-profiles") renderVoiceProfiles();
  else if (view === "translation-profiles") renderTranslationProfiles();

  else if (view === "settings") renderSettings();
  else if (view === "templates") renderTemplates();

  else renderPipelineBoard();
  document.body.classList.toggle("modal-open", Boolean(state.modal.kind));
  syncAllProviderModelSelects();
  resetElapsedTimer();
  restoreDashboardScroll();
}

async function refreshData({ preserveNotice = true } = {}) {
  const route = state.route;
  const [health, settings] = await Promise.all([api("/api/health"), api("/api/settings")]);
  state.health = health;
  state.settings = settings.settings || {};
  state.modelCatalog = settings.model_catalog || DEFAULT_MODEL_CATALOG;
  state.templates = settings.templates || [];

  // Always fetch niche projects + board episodes for sidebar/board
  try {
    const [npRes, beRes] = await Promise.all([
      api("/api/niche-projects"),
      api("/api/board/episodes"),
    ]);
    state.nicheProjects = npRes.projects || [];
    state.boardEpisodes = beRes.episodes || [];
  } catch { state.nicheProjects = []; state.boardEpisodes = []; }

  // Niche project detail
  if (route.view === "niche-project" && route.nicheProjectId) {
    try {
      state.nicheProjectDetail = await api(`/api/niche-projects/${encodeURIComponent(route.nicheProjectId)}`);
    } catch (error) {
      state.nicheProjectDetail = null;
      if (!preserveNotice) throw error;
      setNotice(error.message, "error");
      window.location.hash = routeToHash({ view: "niche-projects" });
      return;
    }
  } else {
    state.nicheProjectDetail = null;
  }

  // Episode detail
  if (route.view === "episode" && route.episodeId) {
    try {
      state.episodeDetail = await api(`/api/episodes/${encodeURIComponent(route.episodeId)}`);
    } catch (error) {
      state.episodeDetail = null;
      if (!preserveNotice) throw error;
      setNotice(error.message, "error");
      window.location.hash = routeToHash({ view: "pipeline-board" });
      return;
    }
  } else {
    state.episodeDetail = null;
  }

  // Fetch target languages + profiles for niche project modals
  if (route.view === "niche-projects" || route.view === "niche-project") {
    try {
      const [tlRes, vpRes, tpRes] = await Promise.all([
        api("/api/target-languages"),
        api("/api/voice-profiles"),
        api("/api/translation-profiles"),
      ]);
      state.targetLanguages = tlRes.languages || [];
      state.voiceProfiles = vpRes.profiles || [];
      state.translationProfiles = tpRes.profiles || [];
    } catch { /* keep existing */ }
  }

  if (route.view === "voice-profiles") {
    try {
      const [vpRes, whRes] = await Promise.all([
        api("/api/voice-profiles"),
        api("/api/worker-health"),
      ]);
      state.voiceProfiles = vpRes.profiles || [];
      state.workerHealth = whRes;
    } catch { state.voiceProfiles = []; state.workerHealth = null; }
  }

  if (route.view === "translation-profiles") {
    try {
      const tpRes = await api("/api/translation-profiles");
      state.translationProfiles = tpRes.profiles || [];
    } catch { state.translationProfiles = []; }
  }

  // Fetch target languages for modals that need them
  if (route.view === "voice-profiles") {
    try {
      const tlRes = await api("/api/target-languages");
      state.targetLanguages = tlRes.languages || [];
    } catch { state.targetLanguages = []; }
  }

}

function resetAutoRefresh() {
  if (refreshTimer) window.clearInterval(refreshTimer);
  if (!autoRefreshAllowed(state.route)) return;
  refreshTimer = window.setInterval(() => {
    if (state.modal.kind) return;
    refreshData().then(renderApp).catch(() => {});
  }, REFRESH_INTERVAL_MS);
}

function resetElapsedTimer() {
  if (elapsedTimer) window.clearInterval(elapsedTimer);
  elapsedTimer = window.setInterval(() => {
    document.querySelectorAll(".running-elapsed").forEach((el) => {
      if (!el.dataset.startedAt) return;
      const startMs = new Date(el.dataset.startedAt).getTime();
      const diffSec = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
      const mins = Math.floor(diffSec / 60);
      const secs = diffSec % 60;
      el.textContent = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
    });
  }, 1000);
}

async function syncRouteAndRender() {
  state.route = parseRoute();
  await refreshData();
  renderApp();
  resetAutoRefresh();
}

async function createVoiceProfile(event) {
  event.preventDefault();
  const audioFile = $("vp-audio")?.files?.[0];
  if (!audioFile) throw new Error("Choose a reference audio file.");
  const fd = new FormData();
  fd.append("name", $("vp-name").value.trim());
  fd.append("language_code", $("vp-language").value);
  fd.append("audio_file", audioFile);
  await api("/api/voice-profiles", { method: "POST", body: fd });
  state.modal = { kind: null };
  setNotice("Voice profile created.", "success");
  await refreshData();
  renderApp();
}

async function testVoiceProfile(event) {
  event.preventDefault();
  const profileId = state.modal.profileId;
  if (!profileId) throw new Error("No profile selected.");
  const text = $("tv-text")?.value?.trim();
  if (!text) throw new Error("Enter some text to test.");
  await api(`/api/voice-profiles/${encodeURIComponent(profileId)}/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  state.modal = { kind: null };
  setNotice("Voice test job submitted.", "success");
  await refreshData();
  renderApp();
}

async function createTranslationProfile(event) {
  event.preventDefault();
  const payload = {
    name: $("tp-name").value.trim(),
    provider: $("tp-provider").value,
    api_key: $("tp-api-key")?.value?.trim() || "",
    model: $("tp-model")?.value?.trim() || "",
  };
  await api("/api/translation-profiles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  state.modal = { kind: null };
  setNotice("Translation profile created.", "success");
  await refreshData();
  renderApp();
}

async function createNicheProject(event) {
  event.preventDefault();
  const name = $("niche-name").value.trim();
  const masterLang = $("niche-master-lang").value;
  const langCbs = document.querySelectorAll(".niche-lang-cb:checked");
  const langs = Array.from(langCbs).map((cb) => cb.value);
  if (!langs.includes(masterLang)) langs.unshift(masterLang);

  const result = await api("/api/niche-projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      master_language: masterLang,
      configured_languages: langs,
    }),
  });
  state.modal = { kind: null };
  setNotice("Niche project created.", "success");
  window.location.hash = routeToHash({ view: "niche-project", nicheProjectId: result.project.id });
}

async function submitEpisode(event) {
  event.preventDefault();
  const projectId = $("se-project-id").value;
  const title = $("se-title").value.trim();
  const scriptText = $("se-script").value;
  if (!scriptText.trim()) throw new Error("Script text cannot be empty.");

  const result = await api('/api/niche-projects/' + encodeURIComponent(projectId) + '/episodes', {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, script_text: scriptText }),
  });
  // Auto-queue the episode
  await api('/api/episodes/' + encodeURIComponent(result.episode.id) + '/queue', {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  state.modal = { kind: null };
  setNotice("Episode submitted and queued.", "success");
  window.location.hash = routeToHash({ view: "episode", episodeId: result.episode.id });
}

async function saveSettings(event) {
  event.preventDefault();
  await api("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      default_scene_planning_provider: $("settings-scene").value,
      default_visual_bible_provider: $("settings-bible").value,
      default_video_prompt_provider: $("settings-video").value,
      default_image_prompt_provider: $("settings-image").value,
      default_scene_planning_model: $("settings-scene-model").value,
      default_visual_bible_model: $("settings-bible-model").value,
      default_video_prompt_model: $("settings-video-model").value,
      default_image_prompt_model: $("settings-image-model").value,
      leading_video_scene_count: Number($("settings-leading-video").value),
      planning_chunk_seconds: Number($("settings-chunk-seconds").value),
      planning_overlap_seconds: Number($("settings-overlap-seconds").value),
      prompt_batch_size: Number($("settings-batch-size").value),
    }),
  });
  await refreshData();
  renderApp();
  setNotice("Settings saved.", "success");
}

async function saveTemplate(event) {
  event.preventDefault();
  const stage = $("template-stage").value;
  const provider = $("template-provider").value;
  await api(`/api/templates/${stage}/${provider}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body: $("template-body").value }),
  });
  await refreshData();
  renderApp();
  setNotice("Template saved.", "success");
}

async function prepareLanguage() {
  const languageCode = $("create-language")?.value || "en";
  const payload = await api(`/api/languages/${encodeURIComponent(languageCode)}/prepare`, { method: "POST" });
  setNotice(`Language preparation: ${payload.status}`, "success");
  await refreshData();
  renderApp();
}

document.addEventListener("click", async (event) => {
  if (event.target.matches("[data-modal-backdrop]")) {
    state.modal = { kind: null };
    renderApp();
    resetAutoRefresh();
    return;
  }
  const target = event.target.closest("[data-nav], [data-refresh], [data-theme-toggle], [data-prepare-language], [data-close-modal], [data-worker-action], [data-delete-voice-profile], [data-create-voice-profile], [data-test-voice], [data-create-translation-profile], [data-delete-translation-profile], [data-open-niche-project], [data-open-create-niche], [data-delete-niche-project], [data-open-episode], [data-open-submit-episode], [data-queue-episode], [data-delete-episode], [data-save-lang-config], [data-save-provider-config], [data-add-language], [data-remove-language], [data-batch-queue-drafts], [data-batch-queue-failed], [data-retry-language], [data-preview-translation]");
  if (!target) return;
  event.preventDefault();
  try {
    if (target.dataset.closeModal) {
      state.modal = { kind: null };
      renderApp();
      resetAutoRefresh();
      return;
    }
    if (target.dataset.nav) {
      window.location.hash = routeToHash({ view: target.dataset.nav });
      return;
    }
    if (target.dataset.refresh) {
      await refreshData({ preserveNotice: false });
      renderApp();
      setNotice("Refreshed.", "success");
      return;
    }
    if (target.dataset.themeToggle) {
      applyTheme(state.theme === "dark" ? "light" : "dark");
      renderApp();
      return;
    }
    if (target.dataset.prepareLanguage) {
      await prepareLanguage();
      return;
    }
    if (target.dataset.workerAction) {
      await api(`/api/worker/${target.dataset.workerAction}`, { method: "POST" });
      await refreshData();
      renderApp();
      setNotice(`Worker ${target.dataset.workerAction}.`, "success");
      return;
    }
    if (target.dataset.deleteVoiceProfile) {
      if (!confirm("Delete this voice profile?")) return;
      await api(`/api/voice-profiles/${encodeURIComponent(target.dataset.deleteVoiceProfile)}`, { method: "DELETE" });
      await refreshData();
      renderApp();
      setNotice("Voice profile deleted.", "success");
      return;
    }
    if (target.dataset.createVoiceProfile) {
      state.modal = { kind: "create-voice-profile" };
      renderApp();
      resetAutoRefresh();
      return;
    }
    if (target.dataset.testVoice) {
      state.modal = { kind: "test-voice", profileId: target.dataset.testVoice };
      renderApp();
      resetAutoRefresh();
      return;
    }
    if (target.dataset.createTranslationProfile) {
      state.modal = { kind: "create-translation-profile" };
      renderApp();
      resetAutoRefresh();
      return;
    }
    if (target.dataset.deleteTranslationProfile) {
      if (!confirm("Delete this translation profile?")) return;
      await api(`/api/translation-profiles/${encodeURIComponent(target.dataset.deleteTranslationProfile)}`, { method: "DELETE" });
      await refreshData();
      renderApp();
      setNotice("Translation profile deleted.", "success");
      return;
    }
    if (target.dataset.openNicheProject) {
      window.location.hash = routeToHash({ view: "niche-project", nicheProjectId: target.dataset.openNicheProject });
      return;
    }
    if (target.dataset.openCreateNiche) {
      state.modal = { kind: "create-niche" };
      renderApp();
      resetAutoRefresh();
      return;
    }
    if (target.dataset.deleteNicheProject) {
      if (!confirm('Delete niche project "' + (target.dataset.projectTitle || target.dataset.deleteNicheProject) + '"? This cannot be undone.')) return;
      await api('/api/niche-projects/' + encodeURIComponent(target.dataset.deleteNicheProject), { method: "DELETE" });
      await refreshData();
      renderApp();
      setNotice("Niche project deleted.", "success");
      return;
    }
    if (target.dataset.openEpisode) {
      window.location.hash = routeToHash({ view: "episode", episodeId: target.dataset.openEpisode });
      return;
    }
    if (target.dataset.openSubmitEpisode) {
      state.modal = { kind: "submit-episode", nicheProjectId: target.dataset.openSubmitEpisode };
      renderApp();
      resetAutoRefresh();
      return;
    }
    if (target.dataset.queueEpisode) {
      await api('/api/episodes/' + encodeURIComponent(target.dataset.queueEpisode) + '/queue', {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_stage: target.dataset.stage || null }),
      });
      await refreshData();
      renderApp();
      setNotice("Episode queued.", "success");
      return;
    }
    if (target.dataset.deleteEpisode) {
      if (!confirm("Delete this episode? This cannot be undone.")) return;
      await api('/api/episodes/' + encodeURIComponent(target.dataset.deleteEpisode), { method: "DELETE" });
      if (state.route.view === "episode") {
        window.location.hash = routeToHash({ view: "pipeline-board" });
      } else {
        await refreshData();
        renderApp();
      }
      setNotice("Episode deleted.", "success");
      return;
    }
    if (target.dataset.saveLangConfig) {
      const projectId = target.dataset.saveLangConfig;
      const langVoice = {};
      const langTrans = {};
      document.querySelectorAll(".lang-voice-select").forEach((sel) => {
        if (sel.value) langVoice[sel.dataset.lang] = sel.value;
      });
      document.querySelectorAll(".lang-trans-select").forEach((sel) => {
        if (sel.value) langTrans[sel.dataset.lang] = sel.value;
      });
      await api('/api/niche-projects/' + encodeURIComponent(projectId), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language_voice_profiles: langVoice, language_translation_profiles: langTrans }),
      });
      await refreshData();
      renderApp();
      setNotice("Language configuration saved.", "success");
      return;
    }
    if (target.dataset.saveProviderConfig) {
      const projectId = target.dataset.saveProviderConfig;
      const payload = {
        scene_planning_provider: $("niche-scene_planning-provider")?.value,
        visual_bible_provider: $("niche-visual_bible-provider")?.value,
        video_prompt_provider: $("niche-video_prompt-provider")?.value,
        image_prompt_provider: $("niche-image_prompt-provider")?.value,
        scene_planning_model: $("niche-scene_planning-model")?.value,
        visual_bible_model: $("niche-visual_bible-model")?.value,
        video_prompt_model: $("niche-video_prompt-model")?.value,
        image_prompt_model: $("niche-image_prompt-model")?.value,
        leading_video_scene_count: Number($("niche-leading-video")?.value || 20),
      };
      await api('/api/niche-projects/' + encodeURIComponent(projectId), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      await refreshData();
      renderApp();
      setNotice("Provider configuration saved.", "success");
      return;
    }
    if (target.dataset.addLanguage) {
      const sel = $("add-lang-select");
      if (!sel || !sel.value) return;
      const langCode = sel.value;
      const detail = state.nicheProjectDetail;
      if (!detail) return;
      const current = detail.project.configured_languages || [];
      if (!current.includes(langCode)) {
        current.push(langCode);
        await api('/api/niche-projects/' + encodeURIComponent(detail.project.id), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ configured_languages: current }),
        });
        await refreshData();
        renderApp();
        setNotice("Language added.", "success");
      }
      return;
    }
    if (target.dataset.removeLanguage) {
      const langCode = target.dataset.removeLanguage;
      const detail = state.nicheProjectDetail;
      if (!detail) return;
      if (!confirm('Remove language "' + langCode + '" from this project?')) return;
      const current = (detail.project.configured_languages || []).filter((l) => l !== langCode);
      await api('/api/niche-projects/' + encodeURIComponent(detail.project.id), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ configured_languages: current }),
      });
      await refreshData();
      renderApp();
      setNotice("Language removed.", "success");
      return;
    }
    if (target.dataset.batchQueueDrafts) {
      const projectId = target.dataset.batchQueueDrafts;
      const result = await api('/api/niche-projects/' + encodeURIComponent(projectId) + '/batch-queue', {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filter_status: "draft" }),
      });
      await refreshData();
      renderApp();
      setNotice("Queued " + (result.queued_count || 0) + " draft episode(s).", "success");
      return;
    }
    if (target.dataset.batchQueueFailed) {
      const projectId = target.dataset.batchQueueFailed;
      const result = await api('/api/niche-projects/' + encodeURIComponent(projectId) + '/batch-queue', {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filter_status: "failed" }),
      });
      await refreshData();
      renderApp();
      setNotice("Re-queued " + (result.queued_count || 0) + " failed episode(s).", "success");
      return;
    }
    if (target.dataset.retryLanguage) {
      const episodeId = target.dataset.retryLanguage;
      const langCode = target.dataset.retryLang;
      const stage = target.dataset.retryStage;
      await api('/api/episodes/' + encodeURIComponent(episodeId) + '/retry-language', {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language_code: langCode, stage }),
      });
      await refreshData();
      renderApp();
      setNotice("Retrying " + stage + " for " + langCode + ".", "success");
      return;
    }
    if (target.dataset.previewTranslation) {
      const episodeId = target.dataset.previewTranslation;
      const langCode = target.dataset.previewLang;
      const preview = await api('/api/episodes/' + encodeURIComponent(episodeId) + '/translation-preview/' + encodeURIComponent(langCode));
      state.translationPreview = preview;
      state.modal = { kind: "translation-preview" };
      renderApp();
      return;
    }
  } catch (error) {
    setNotice(error.message, "error");
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target;
  try {
    if (form.id === "create-voice-profile-form") await createVoiceProfile(event);
    else if (form.id === "test-voice-form") await testVoiceProfile(event);
    else if (form.id === "create-translation-profile-form") await createTranslationProfile(event);
    else if (form.id === "create-niche-form") await createNicheProject(event);
    else if (form.id === "submit-episode-form") await submitEpisode(event);
    else if (form.id === "settings-form") await saveSettings(event);
    else if (form.id === "templates-form") await saveTemplate(event);
  } catch (error) {
    event.preventDefault();
    setNotice(error.message, "error");
  }
});

document.addEventListener("change", (event) => {
  if (event.target.id === "template-stage" || event.target.id === "template-provider") {
    state.route = {
      view: "templates",
      templateStage: $("template-stage").value,
      templateProvider: $("template-provider").value,
    };
    renderTemplates();
    return;
  }
  const providerModelPairs = {
    "settings-scene": "settings-scene-model",
    "settings-bible": "settings-bible-model",
    "settings-video": "settings-video-model",
    "settings-image": "settings-image-model",
    "niche-scene_planning-provider": "niche-scene_planning-model",
    "niche-visual_bible-provider": "niche-visual_bible-model",
    "niche-video_prompt-provider": "niche-video_prompt-model",
    "niche-image_prompt-provider": "niche-image_prompt-model",
  };
  const modelSelectId = providerModelPairs[event.target.id];
  if (modelSelectId) {
    syncProviderModelSelect(event.target.id, modelSelectId);
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.modal.kind) {
    state.modal = { kind: null };
    renderApp();
    resetAutoRefresh();
  }
});

window.addEventListener("hashchange", () => {
  syncRouteAndRender().catch((error) => setNotice(error.message, "error"));
});

window.addEventListener("DOMContentLoaded", () => {
  bootTheme();
  if (!window.location.hash) {
    window.location.hash = routeToHash({ view: "pipeline-board" });
    return;
  }
  syncRouteAndRender().catch((error) => setNotice(error.message, "error"));
});
