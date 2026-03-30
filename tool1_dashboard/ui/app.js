// ── Episode pipeline (TTS-first) constants ────────────────────────
const EPISODE_PIPELINE_COLUMNS = [
  { id: "draft", label: "Draft", short: "Draft", copy: "Script saved to the project board and waiting for an explicit workflow start." },
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
const EPISODE_RUNNABLE_STAGE_IDS = EPISODE_PIPELINE_COLUMNS
  .map((column) => column.id)
  .filter((columnId) => !["draft", "review", "export", "needs_attention"].includes(columnId));

const PROVIDERS = ["claude", "codex", "openai"];
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
  openai: [
    { value: "gpt-5.4-mini", label: "GPT-5.4 Mini" },
    { value: "gpt-5.4", label: "GPT-5.4" },
    { value: "gpt-4o-mini", label: "GPT-4o Mini" },
    { value: "gpt-4.1-mini", label: "GPT-4.1 Mini" },
  ],
};
const TRANSLATION_PROFILE_PROVIDER_CATALOG = [
  {
    id: "openai",
    label: "OpenAI API",
    mode: "api",
    placeholder: false,
    description: "Runnable now. Paste a key, load available models, then save the profile.",
  },
  {
    id: "claude_cli",
    label: "Claude Code CLI",
    mode: "cli",
    placeholder: true,
    description: "UI preview only for now. This tab shows the planned CLI setup shape.",
  },
];
const REFRESH_INTERVAL_MS = 5000;
const ACTIVE_REFRESH_INTERVAL_MS = 1000;
const EPISODE_FILES_CACHE_TTL_MS = 3000;
const HEALTH_CACHE_TTL_MS = 15000;
const SETTINGS_CACHE_TTL_MS = 30000;
const TARGET_LANGUAGES_CACHE_TTL_MS = 60000;
const FALLBACK_VOICE_TTS_PRESETS = {
  natural_stable: {
    preset: "natural_stable",
    temperature: 0.55,
    top_p: 0.75,
    top_k: 30,
    speed: 1.0,
    chunk_max_chars: 180,
    silence_gap_seconds: 0.12,
  },
  balanced: {
    preset: "balanced",
    temperature: 0.65,
    top_p: 0.82,
    top_k: 40,
    speed: 1.0,
    chunk_max_chars: 200,
    silence_gap_seconds: 0.15,
  },
  expressive: {
    preset: "expressive",
    temperature: 0.75,
    top_p: 0.88,
    top_k: 50,
    speed: 1.0,
    chunk_max_chars: 220,
    silence_gap_seconds: 0.15,
  },
};
const FALLBACK_VOICE_TTS_LIMITS = {
  temperature: { min: 0.35, max: 0.85 },
  top_p: { min: 0.60, max: 0.95 },
  top_k: { min: 10, max: 80 },
  speed: { min: 0.96, max: 1.05 },
  chunk_max_chars: { min: 120, max: 260 },
  silence_gap_seconds: { min: 0.00, max: 0.25 },
};

const state = {
  health: null,
  healthFetchedAt: 0,
  settings: null,
  settingsFetchedAt: 0,
  modelCatalog: DEFAULT_MODEL_CATALOG,
  templates: [],
  route: { view: "niche-projects" },
  theme: "dark",
  notice: { text: "", tone: "neutral" },
  modal: { kind: null },
  translationProfileEditor: null,
  stageProviderOpenAi: null,
  submittingVoiceTestProfileId: null,
  pendingVoiceTestJobs: {},
  autoPlayedVoiceTestJobs: {},
  episodeOverlayId: null,
  boardScrollLeft: 0,
  modalMainScrollTop: 0,
  voiceProfiles: [],
  translationProfiles: [],
  targetLanguages: [],
  targetLanguagesFetchedAt: 0,
  workerHealth: null,
  nicheProjects: [],
  nicheProjectDetail: null,
  boardEpisodes: [],
  episodeDetail: null,
  episodeFiles: {},
  episodeWorkflowActions: {},
  episodeWorkflowStageSelections: {},
  translationPreview: null,
  isLoadingRoute: false,
  isRefreshingData: false,
  lastEpisodeFilesLoadedFor: null,
  lastEpisodeReviewLoadedFor: null,
  projectConfigDisclosures: {
    projectId: null,
    panels: {
      language: false,
      provider: false,
    },
  },
};

let refreshTimer = null;
let noticeTimer = null;
let elapsedTimer = null;
let refreshGeneration = 0;
let activeRefreshes = 0;
let blockingRefreshes = 0;
let currentRefreshIntervalMs = REFRESH_INTERVAL_MS;

const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

function domSafeId(...parts) {
  return parts
    .map((part) =>
      String(part ?? "")
        .trim()
        .replace(/[^a-zA-Z0-9_-]+/g, "-")
        .replace(/^-+|-+$/g, "") || "item"
    )
    .join("-");
}

function parseDateValue(value) {
  if (value === null || value === undefined || value === "") return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === "number" && Number.isFinite(value)) {
    const ms = value < 1e12 ? value * 1000 : value;
    const parsed = new Date(ms);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  const text = String(value).trim();
  if (!text) return null;
  if (/^\d+(\.\d+)?$/.test(text)) {
    const numeric = Number(text);
    if (Number.isFinite(numeric)) {
      const ms = numeric < 1e12 ? numeric * 1000 : numeric;
      const parsed = new Date(ms);
      return Number.isNaN(parsed.getTime()) ? null : parsed;
    }
  }
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function titleCase(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function lastOpenProjectId() {
  return window.localStorage.getItem("tool1-last-open-project");
}

function rememberLastOpenProject(projectId) {
  if (!projectId) return;
  window.localStorage.setItem("tool1-last-open-project", projectId);
}

function legacyBoardRedirectRoute() {
  const nicheProjectId = lastOpenProjectId();
  return nicheProjectId
    ? { view: "niche-project", nicheProjectId }
    : { view: "niche-projects" };
}

function routeToHash(route) {
  if (route.view === "niche-project" && route.nicheProjectId) {
    return `#/niche-projects/${encodeURIComponent(route.nicheProjectId)}`;
  }
  if (route.view === "episode" && route.episodeId) {
    return `#/episodes/${encodeURIComponent(route.episodeId)}`;
  }
  if (route.view === "pipeline-board") {
    return routeToHash(legacyBoardRedirectRoute());
  }
  return `#/${route.view || "niche-projects"}`;
}

function parseRoute() {
  const hash = window.location.hash.replace(/^#/, "").replace(/^\/+/, "");
  if (!hash) return { view: "niche-projects" };
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
    return legacyBoardRedirectRoute();
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
  return { view: "niche-projects" };
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
  return provider === "claude"
    ? "Claude CLI"
    : provider === "codex"
      ? "Codex (API)"
      : provider === "openai"
        ? "OpenAI API"
        : provider || "Unknown";
}

function formatDate(value) {
  if (!value) return "unknown";
  const parsed = parseDateValue(value);
  if (!parsed) return String(value);
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

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  if (bytes < 1024) return `${bytes.toFixed(0)} B`;
  const units = ["KB", "MB", "GB"];
  let size = bytes / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const digits = size >= 100 ? 0 : size >= 10 ? 1 : 2;
  return `${size.toFixed(digits)} ${units[unitIndex]}`;
}

function baseEpisodeFilesState() {
  return {
    items: [],
    loading: false,
    error: "",
    syncedAt: 0,
    selectedPath: "",
    listScrollTop: 0,
    previewByPath: {},
  };
}

function episodeFilesState(episodeId) {
  const current = state.episodeFiles?.[episodeId];
  if (!current) return baseEpisodeFilesState();
  return {
    ...baseEpisodeFilesState(),
    ...current,
    previewByPath: { ...(current.previewByPath || {}) },
  };
}

function setEpisodeFilesState(episodeId, nextState) {
  if (!episodeId) return;
  state.episodeFiles = {
    ...(state.episodeFiles || {}),
    [episodeId]: nextState,
  };
}

function updateEpisodeFilesState(episodeId, updater) {
  if (!episodeId || typeof updater !== "function") return baseEpisodeFilesState();
  const nextState = updater(episodeFilesState(episodeId)) || episodeFilesState(episodeId);
  setEpisodeFilesState(episodeId, nextState);
  return nextState;
}

function setEpisodeFilesListScrollTop(episodeId, scrollTop) {
  if (!episodeId) return;
  const nextScrollTop = Math.max(0, Number(scrollTop) || 0);
  updateEpisodeFilesState(episodeId, (current) => {
    if ((current.listScrollTop || 0) === nextScrollTop) return current;
    return {
      ...current,
      listScrollTop: nextScrollTop,
    };
  });
}

function captureEpisodeFilesListScroll(root = document) {
  if (!root?.querySelectorAll) return;
  root.querySelectorAll(".episode-files-list[data-episode-id]").forEach((listEl) => {
    setEpisodeFilesListScrollTop(listEl.dataset.episodeId || "", listEl.scrollTop || 0);
  });
}

function restoreEpisodeFilesListScroll(root = document) {
  if (!root?.querySelectorAll) return;
  root.querySelectorAll(".episode-files-list[data-episode-id]").forEach((listEl) => {
    const episodeId = listEl.dataset.episodeId || "";
    if (!episodeId) return;
    listEl.scrollTop = episodeFilesState(episodeId).listScrollTop || 0;
    listEl.dataset.scrollReady = "true";
    if (listEl.dataset.scrollBound === "true") return;
    listEl.dataset.scrollBound = "true";
    listEl.addEventListener("scroll", () => {
      setEpisodeFilesListScrollTop(episodeId, listEl.scrollTop || 0);
    }, { passive: true });
  });
}

function episodeFileSignature(file) {
  return `${Number(file?.size || 0)}:${String(file?.modified_at || "")}`;
}

function episodeFileDownloadUrl(episodeId, relativePath) {
  return `/api/episodes/${encodeURIComponent(episodeId)}/files/download?path=${encodeURIComponent(relativePath)}`;
}

function episodeFilePreviewUrl(episodeId, relativePath) {
  return `/api/episodes/${encodeURIComponent(episodeId)}/files/content?path=${encodeURIComponent(relativePath)}`;
}

function episodeFileBadgeTone(file) {
  if (file?.is_empty) return "warn";
  if (file?.preview_type === "audio") return "active";
  if (file?.preview_type === "binary") return "neutral";
  return "success";
}

function episodeFileTypeLabel(file) {
  if (file?.is_empty) return "Empty";
  if (file?.preview_type === "audio") return "Audio";
  if (file?.preview_type === "json") return "JSON";
  if (file?.preview_type === "binary") return file?.ext ? file.ext.replace(".", "").toUpperCase() : "Binary";
  return "Text";
}

function episodeFileIcon(file) {
  const ext = String(file?.ext || "").toLowerCase();
  if (file?.preview_type === "audio") return "play";
  if (ext === ".srt") return "timeline";
  if (ext === ".json" || ext === ".jsonl") return "settings";
  if (ext === ".txt" || ext === ".md" || ext === ".log" || ext === ".csv") return "prompts";
  if (ext === ".zip") return "download";
  return "open";
}

function pickEpisodeFileSelection(files = []) {
  if (!files.length) return "";
  const preferred = [...files].sort((left, right) => {
    const leftRank = left.is_empty ? 1 : 0;
    const rightRank = right.is_empty ? 1 : 0;
    if (leftRank !== rightRank) return leftRank - rightRank;
    const priority = {
      ".srt": 0,
      ".json": 1,
      ".jsonl": 2,
      ".txt": 3,
      ".log": 4,
      ".md": 5,
      ".csv": 6,
      ".wav": 7,
      ".mp3": 8,
      ".m4a": 9,
      ".ogg": 10,
      ".zip": 11,
    };
    return (priority[left.ext] ?? 99) - (priority[right.ext] ?? 99);
  });
  return preferred[0]?.relative_path || "";
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
    edit: '<svg viewBox="0 0 20 20"><path d="m4 13.5 8.8-8.8 2.5 2.5-8.8 8.8L4 16z"/><path d="M11.7 5.8 14.2 8.3"/><path d="M4 16h3.2"/></svg>',
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

function iconMarkup(name, { className = "" } = {}) {
  const classes = ["button-icon"];
  if (className) classes.push(className);
  return `<span class="${classes.join(" ")}" aria-hidden="true">${iconSvg(name)}</span>`;
}

function iconContent(name, label, { iconOnly = false, iconClass = "" } = {}) {
  return `${iconMarkup(name, { className: iconClass })}${iconOnly ? `<span class="sr-only">${esc(label)}</span>` : `<span class="button-label">${esc(label)}</span>`}`;
}

function workflowTerminology(text) {
  return String(text || "")
    .replace(/\bbefore re-?queueing\b/gi, "before restarting the workflow")
    .replace(/\bbefore queueing\b/gi, "before starting the workflow")
    .replace(/\bre-?queueing\b/gi, "restarting the workflow")
    .replace(/\bqueueing\b/gi, "starting the workflow");
}

function apiErrorMessage(detail) {
  if (!detail) return "Request failed.";
  if (typeof detail === "string") return workflowTerminology(detail);
  if (typeof detail.message === "string" && detail.message.trim()) return workflowTerminology(detail.message);
  if (detail.queue_readiness?.blockers?.length) {
    return detail.queue_readiness.blockers.map((item) => workflowTerminology(item.message)).join(" ");
  }
  return "Request failed.";
}

function cacheIsFresh(timestamp, ttlMs) {
  return Boolean(timestamp) && (Date.now() - timestamp) < ttlMs;
}

function resetEpisodeSupplementalState(episodeId = null) {
  if (!episodeId || state.lastEpisodeFilesLoadedFor === episodeId) {
    state.lastEpisodeFilesLoadedFor = null;
  }
  if (!episodeId || state.lastEpisodeReviewLoadedFor === episodeId) {
    state.lastEpisodeReviewLoadedFor = null;
  }
  clearEpisodeWorkflowActionState(episodeId);
}

function episodeWorkflowActionState(episodeId) {
  return episodeId ? state.episodeWorkflowActions?.[episodeId] || null : null;
}

function setEpisodeWorkflowActionState(episodeId, nextState) {
  if (!episodeId) return;
  state.episodeWorkflowActions = {
    ...state.episodeWorkflowActions,
    [episodeId]: nextState,
  };
}

function clearEpisodeWorkflowActionState(episodeId = null) {
  if (!episodeId) {
    state.episodeWorkflowActions = {};
    return;
  }
  if (!state.episodeWorkflowActions?.[episodeId]) return;
  const nextStates = { ...state.episodeWorkflowActions };
  delete nextStates[episodeId];
  state.episodeWorkflowActions = nextStates;
}

function workflowActionEpisodes() {
  const episodes = [];
  const seen = new Set();
  const register = (episode) => {
    if (!episode?.id || seen.has(episode.id)) return;
    seen.add(episode.id);
    episodes.push(episode);
  };
  (state.boardEpisodes || []).forEach(register);
  (state.nicheProjectDetail?.episodes || []).forEach(register);
  register(state.episodeDetail?.episode);
  return episodes;
}

function findEpisodeReference(episodeId) {
  if (!episodeId) return null;
  if (state.episodeDetail?.episode?.id === episodeId) {
    return state.episodeDetail.episode;
  }
  const projectEpisode = (state.nicheProjectDetail?.episodes || []).find((episode) => episode.id === episodeId);
  if (projectEpisode) {
    return projectEpisode;
  }
  return (state.boardEpisodes || []).find((episode) => episode.id === episodeId) || null;
}

function updateEpisodeReferences(episodeId, updater) {
  if (!episodeId || typeof updater !== "function") return;
  if (Array.isArray(state.boardEpisodes)) {
    state.boardEpisodes = state.boardEpisodes.map((episode) => (episode.id === episodeId ? updater(episode) : episode));
  }
  if (Array.isArray(state.nicheProjectDetail?.episodes)) {
    state.nicheProjectDetail = {
      ...state.nicheProjectDetail,
      episodes: state.nicheProjectDetail.episodes.map((episode) => (episode.id === episodeId ? updater(episode) : episode)),
    };
  }
  if (state.episodeDetail?.episode?.id === episodeId) {
    state.episodeDetail = {
      ...state.episodeDetail,
      episode: updater(state.episodeDetail.episode),
    };
  }
}

function episodeQueueStartStage(episode, explicitStage = null) {
  if (explicitStage) return explicitStage;
  const pipelineStatus = String(episode?.pipeline_status || "idle").toLowerCase();
  const currentStage = String(episode?.current_stage || "").trim();
  if (["failed", "paused"].includes(pipelineStatus) && EPISODE_RUNNABLE_STAGE_IDS.includes(currentStage)) {
    return currentStage;
  }
  const queuedStage = String(episode?.queued_from_stage || "").trim();
  if (EPISODE_RUNNABLE_STAGE_IDS.includes(queuedStage)) return queuedStage;
  if (EPISODE_RUNNABLE_STAGE_IDS.includes(currentStage)) return currentStage;
  return "consistency_guide";
}

function stageLabel(stage) {
  return EPISODE_STAGE_LABELS[stage] || titleCase(stage || "workflow");
}

function isWorkflowActiveStatus(status) {
  return ["queued", "running", "paused_for_tts"].includes(String(status || "idle").toLowerCase());
}

function pauseRequestedCopy(episode) {
  const pipelineStatus = String(episode?.pipeline_status || "idle").toLowerCase();
  if (pipelineStatus === "paused_for_tts") {
    return "Pause requested. The workflow will stop after narration finishes.";
  }
  const currentStage = stageLabel(episode?.current_stage || episodeDisplayStage(episode) || episodeQueueStartStage(episode));
  if (pipelineStatus === "queued") {
    return `Pause requested. The workflow will stop before ${currentStage}.`;
  }
  return `Pause requested. The workflow will stop after ${currentStage}.`;
}

function stageActivityLabel(stage) {
  const activity = {
    draft: "Ready to start workflow",
    consistency_guide: "Running Consistency Guide",
    translation: "Running Translation",
    tts: "Generating TTS",
    alignment: "Aligning audio",
    chunking: "Running Chunking",
    scene_planning: "Running Scene Planning",
    video_prompt_generation: "Generating video prompts",
    image_prompt_generation: "Generating image prompts",
    timeline_mapping: "Mapping timeline",
    review: "Ready for review",
    export: "Export completed",
  };
  return activity[stage] || `Running ${stageLabel(stage)}`;
}

function episodeDisplayStage(episode) {
  if (!episode) return "draft";
  const currentStage = episode.current_stage || "";
  const pipelineStatus = episode.pipeline_status || "idle";
  if (pipelineStatus === "queued" || (pipelineStatus === "running" && currentStage === "draft")) {
    const queuedStage = episodeQueueStartStage(episode);
    if (EPISODE_PIPELINE_COLUMNS.some((column) => column.id === queuedStage)) return queuedStage;
  }
  if (EPISODE_PIPELINE_COLUMNS.some((column) => column.id === currentStage)) return currentStage;
  if (["queued", "running", "paused", "paused_for_tts"].includes(pipelineStatus)) {
    const queuedStage = episodeQueueStartStage(episode);
    if (EPISODE_PIPELINE_COLUMNS.some((column) => column.id === queuedStage)) return queuedStage;
  }
  return "draft";
}

function languageStageStatusKey(stage) {
  const statusKeys = {
    translation: "translation_status",
    tts: "tts_status",
    alignment: "srt_status",
    timeline_mapping: "timeline_status",
  };
  return statusKeys[stage] || `${stage}_status`;
}

function perLanguageStageCounts(languageStatuses, stage) {
  if (!Array.isArray(languageStatuses) || !languageStatuses.length || !EPISODE_PER_LANG_STAGES.includes(stage)) {
    return null;
  }
  const statusKey = languageStageStatusKey(stage);
  return languageStatuses.reduce((counts, langStatus) => {
    const status = String(langStatus?.[statusKey] || "").toLowerCase();
    if (status === "done" || status === "completed") {
      counts.done += 1;
    } else if (status === "queued") {
      counts.queued += 1;
    } else if (status === "running" || status === "processing") {
      counts.running += 1;
    } else if (status === "failed" || status === "error") {
      counts.failed += 1;
    } else {
      counts.pending += 1;
    }
    return counts;
  }, {
    total: languageStatuses.length,
    done: 0,
    queued: 0,
    running: 0,
    pending: 0,
    failed: 0,
  });
}

function perLanguageStageSummary(stage, counts) {
  const activity = stageActivityLabel(stage);
  if (!counts?.total) return `${activity}.`;
  const parts = [`${activity} ${counts.done}/${counts.total} done`];
  if (counts.running) parts.push(`${counts.running} running`);
  if (counts.queued) parts.push(`${counts.queued} queued`);
  if (counts.failed) parts.push(`${counts.failed} failed`);
  return `${parts.join(", ")}.`;
}

function joinStatusCopy(base, extra) {
  const left = String(base || "").trim().replace(/[.]+$/, "");
  const right = String(extra || "").trim().replace(/[.]+$/, "");
  if (!left) return right ? `${right}.` : "";
  if (!right) return `${left}.`;
  return `${left}. ${right}.`;
}

function activeTtsJob(episode) {
  const job = episode?.active_tts_job;
  if (!job) return null;
  return ["queued", "processing"].includes(String(job.status || "").toLowerCase()) ? job : null;
}

function activeTtsJobCopy(job) {
  if (!job) return "";
  const language = String(job.language_code || "").trim().toUpperCase();
  const prefix = language ? `${language} ` : "";
  const currentChunk = Number(job.current_chunk);
  const totalChunks = Number(job.total_chunks);
  const percent = Number(job.percent);
  if (Number.isFinite(currentChunk) && currentChunk > 0 && Number.isFinite(totalChunks) && totalChunks > 0) {
    const pctSuffix = Number.isFinite(percent) ? ` (${Math.max(0, Math.min(100, Math.round(percent)))}%)` : "";
    return `${prefix}chunk ${currentChunk}/${totalChunks}${pctSuffix}`;
  }
  if (job.progress) return `${prefix}${job.progress}`;
  return language ? `${language} narration in progress` : "Narration in progress";
}

function episodeCardStatusTone(episode) {
  const pipelineStatus = episode?.pipeline_status || "idle";
  if (pipelineStatus === "failed") return "error";
  if (pipelineStatus === "review" || pipelineStatus === "done") return "success";
  if (pipelineStatus === "queued") return "warn";
  if (pipelineStatus === "paused") return "warn";
  if (pipelineStatus === "paused_for_tts") {
    return activeTtsJob(episode)?.worker_active ? "active" : "warn";
  }
  if (pipelineStatus === "running") return "active";
  if (episode?.pause_requested) return "warn";
  if (episode?.queue_readiness?.ok === false) return "warn";
  return "neutral";
}

function episodeWorkflowStatusCopy(episode) {
  if (!episode) return "";
  const pipelineStatus = episode.pipeline_status || "idle";
  const displayStage = episodeDisplayStage(episode);
  const counts = perLanguageStageCounts(episode.language_statuses, displayStage);
  const liveTtsCopy = activeTtsJobCopy(activeTtsJob(episode));
  if (episode.pause_requested && isWorkflowActiveStatus(pipelineStatus)) return pauseRequestedCopy(episode);
  if (pipelineStatus === "failed") return `Stopped in ${stageLabel(displayStage)}.`;
  if (pipelineStatus === "done") return "Export completed.";
  if (pipelineStatus === "review") return "Ready for review.";
  if (pipelineStatus === "paused") return `Paused before ${stageLabel(displayStage)}.`;
  if (pipelineStatus === "paused_for_tts") {
    const baseCopy = counts?.total ? perLanguageStageSummary("tts", counts) : "Waiting for TTS jobs to finish.";
    return joinStatusCopy(baseCopy, liveTtsCopy);
  }
  if (pipelineStatus === "running") {
    const baseCopy = EPISODE_PER_LANG_STAGES.includes(displayStage)
      ? perLanguageStageSummary(displayStage, counts)
      : `${stageActivityLabel(displayStage)}.`;
    return displayStage === "tts" ? joinStatusCopy(baseCopy, liveTtsCopy) : baseCopy;
  }
  if (pipelineStatus === "queued") return `Waiting for worker to start ${stageLabel(displayStage)}.`;
  if (episode.queue_readiness?.ok === false) return "Workflow blocked until setup is fixed.";
  if (displayStage === "draft") return "Ready to start workflow.";
  return `${stageActivityLabel(displayStage)}.`;
}

function renderEpisodeWorkflowStatus(episode) {
  const statusCopy = episodeWorkflowStatusCopy(episode);
  if (!statusCopy) return "";
  return `
    <div class="episode-card-status" data-tone="${esc(episodeCardStatusTone(episode))}" role="status" aria-live="polite">
      <span class="episode-card-status-dot" aria-hidden="true"></span>
      <span class="episode-card-status-copy">${esc(statusCopy)}</span>
    </div>`;
}

function renderActiveTtsProgress(episode) {
  if (episodeDisplayStage(episode) !== "tts") return "";
  const job = activeTtsJob(episode);
  if (!job) return "";
  const label = activeTtsJobCopy(job);
  const percent = Number(job.percent);
  const showBar = Number.isFinite(percent) && percent > 0;
  return `
    <div class="lang-progress tts-live-progress">
      ${showBar ? `<div class="lang-progress-bar"><div class="lang-progress-fill" style="width:${Math.max(2, Math.min(100, Math.round(percent)))}%"></div></div>` : ""}
      <span class="lang-progress-label">${esc(label)}</span>
    </div>`;
}

function languageTtsJobCopy(langStatus) {
  if (!langStatus) return "";
  const currentChunk = Number(langStatus.tts_job_current_chunk);
  const totalChunks = Number(langStatus.tts_job_total_chunks);
  const percent = Number(langStatus.tts_job_percent);
  if (Number.isFinite(currentChunk) && currentChunk > 0 && Number.isFinite(totalChunks) && totalChunks > 0) {
    const pctSuffix = Number.isFinite(percent) ? ` (${Math.max(0, Math.min(100, Math.round(percent)))}%)` : "";
    return `chunk ${currentChunk}/${totalChunks}${pctSuffix}`;
  }
  return String(langStatus.tts_job_progress || "").trim();
}

function optimisticQueuedEpisodeRecord(episode, startStage) {
  return {
    ...episode,
    board_status: "Queued",
    pipeline_status: "queued",
    current_stage: startStage,
    queued_from_stage: startStage,
    pause_requested: false,
    last_error: null,
    updated_at: new Date().toISOString(),
  };
}

function applyOptimisticEpisodeWorkflowStart(episodeId, startStage) {
  updateEpisodeReferences(episodeId, (episode) => optimisticQueuedEpisodeRecord(episode, startStage));
}

function defaultProjectConfigDisclosurePanels() {
  return {
    language: false,
    provider: false,
  };
}

function resetProjectConfigDisclosures() {
  state.projectConfigDisclosures = {
    projectId: null,
    panels: defaultProjectConfigDisclosurePanels(),
  };
}

function ensureProjectConfigDisclosures(projectId) {
  const normalizedProjectId = projectId ? String(projectId) : null;
  if (!normalizedProjectId) {
    resetProjectConfigDisclosures();
    return state.projectConfigDisclosures.panels;
  }
  if (state.projectConfigDisclosures.projectId !== normalizedProjectId) {
    state.projectConfigDisclosures = {
      projectId: normalizedProjectId,
      panels: defaultProjectConfigDisclosurePanels(),
    };
  }
  return state.projectConfigDisclosures.panels;
}

function setProjectConfigDisclosure(projectId, panelName, isOpen) {
  const panels = ensureProjectConfigDisclosures(projectId);
  if (!(panelName in panels)) return false;
  panels[panelName] = Boolean(isOpen);
  return panels[panelName];
}

function toggleProjectConfigDisclosure(projectId, panelName) {
  const panels = ensureProjectConfigDisclosures(projectId);
  if (!(panelName in panels)) return false;
  return setProjectConfigDisclosure(projectId, panelName, !panels[panelName]);
}

function syncProjectConfigDisclosureDom(disclosure, isOpen) {
  if (!disclosure) return;
  disclosure.dataset.open = isOpen ? "true" : "false";
  const toggle = disclosure.querySelector("[data-project-config-toggle]");
  const body = disclosure.querySelector(".project-config-disclosure-body");
  if (toggle) toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
  if (body) body.hidden = !isOpen;
}

function renderProjectConfigDisclosure({ projectId, panelName, label, body, isOpen }) {
  const bodyId = domSafeId("project-config", projectId, panelName, "panel");
  return `
    <section
      class="surface project-config-disclosure"
      data-project-config-disclosure="${esc(panelName)}"
      data-project-id="${esc(projectId)}"
      data-open="${isOpen ? "true" : "false"}"
    >
      <div class="project-config-disclosure-head">
        <button
          type="button"
          class="project-config-toggle"
          data-project-config-toggle="${esc(panelName)}"
          data-project-id="${esc(projectId)}"
          aria-expanded="${isOpen ? "true" : "false"}"
          aria-controls="${esc(bodyId)}"
        >
          <span class="project-config-toggle-label">${esc(label)}</span>
          <span class="project-config-toggle-caret" aria-hidden="true"></span>
        </button>
      </div>
      <div id="${esc(bodyId)}" class="project-config-disclosure-body"${isOpen ? "" : " hidden"}>
        ${body}
      </div>
    </section>
  `;
}

function ensureEpisodeSupplementalDataLoaded(episodeId, { force = false } = {}) {
  if (!episodeId) return;
  const fileState = episodeFilesState(episodeId);
  const fileCacheIsFresh = fileState.syncedAt && (Date.now() - fileState.syncedAt) < EPISODE_FILES_CACHE_TTL_MS;
  if (force || state.lastEpisodeFilesLoadedFor !== episodeId || (!fileState.loading && !fileCacheIsFresh)) {
    state.lastEpisodeFilesLoadedFor = episodeId;
    loadEpisodeFiles(episodeId);
  } else {
    syncEpisodeFilesSection(episodeId);
  }
  if (force || state.lastEpisodeReviewLoadedFor !== episodeId) {
    state.lastEpisodeReviewLoadedFor = episodeId;
    loadReviewData(episodeId);
  }
}

function renderLoadingSurface(title, copy) {
  return `
    <section class="surface loading-surface">
      <div class="loading-surface-copy">
        <div class="loading-inline">
          <span class="loading-dot" aria-hidden="true"></span>
          <span class="eyebrow">Syncing</span>
        </div>
        <h2 class="loading-title">${esc(title)}</h2>
        <p class="helper">${esc(copy)}</p>
      </div>
    </section>
  `;
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(apiErrorMessage(data.detail));
    error.detail = data.detail;
    throw error;
  }
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
      title: "Niche Projects",
      copy: "Legacy board links now redirect to niche projects.",
    };
  }
  if (route.view === "niche-projects" || route.view === "niche-project") {
    return {
      title: "Niche Projects",
      copy: "Project boards own the workflow: draft first, then an explicit workflow start.",
    };
  }
  if (route.view === "episode") {
    return {
      title: "Episode Details",
      copy: "Episode detail is shown as an overlay on the project board.",
    };
  }
  if (route.view === "voice-profiles") {
    return {
      title: "Voice Profiles",
      copy: "Create a voice once, then generate a fresh test sample whenever you need to hear it.",
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
    title: "Niche Projects",
    copy: "Project-scoped workflow.",
  };
}

function autoRefreshAllowed(route) {
  if (route.view === "niche-projects") return true;
  if (route.view === "niche-project") return true;
  if (route.view === "episode") return true;
  if (route.view === "voice-profiles") return true;
  return false;
}

function hasActiveEpisodeWorkflows() {
  return workflowActionEpisodes().some((episode) => ["queued", "running", "paused_for_tts"].includes(episode?.pipeline_status || "idle"));
}

function desiredRefreshIntervalMs() {
  return hasActiveEpisodeWorkflows() ? ACTIVE_REFRESH_INTERVAL_MS : REFRESH_INTERVAL_MS;
}

function syncAutoRefreshInterval() {
  const nextIntervalMs = desiredRefreshIntervalMs();
  if (nextIntervalMs === currentRefreshIntervalMs) return;
  currentRefreshIntervalMs = nextIntervalMs;
  resetAutoRefresh();
}

function navIsActive(navView) {
  const v = state.route.view;
  if (navView === "niche-projects" && (v === "niche-projects" || v === "niche-project" || v === "episode")) return true;
  return v === navView;
}

function renderSidebar() {
  const providers = state.health?.providers || {};
  const alignment = state.health?.alignment || {};
  const themeToggleLabel = state.theme === "dark" ? "Light mode" : "Dark mode";
  const themeToggleIcon = state.theme === "dark" ? "sun" : "moon";
  const navItems = [
    { view: "niche-projects", label: "Niche Projects", icon: "settings", count: state.nicheProjects.length },
    { view: "voice-profiles", label: "Voice Profiles", icon: "settings", count: state.voiceProfiles.length },
    { view: "translation-profiles", label: "Translation Profiles", icon: "settings", count: state.translationProfiles.length },
    { view: "settings", label: "Settings", icon: "settings", count: "" },
    { view: "templates", label: "Templates", icon: "templates", count: state.templates.length },
  ];

  $("sidebar").innerHTML = `
      <section class="sidebar-brand">
      <div class="sidebar-brand-row">
        <button type="button" class="sidebar-toggle" data-sidebar-toggle="true" aria-label="Toggle sidebar" title="Toggle sidebar">
          ${iconSvg("board")}
        </button>
        <div class="brand-title">Tool 1</div>
      </div>
        <div class="brand-copy">Project-scoped workflow. Create the card in Draft, then start it explicitly when the project is ready.</div>
      </section>

    <section class="sidebar-section">
      <div class="eyebrow">Navigation</div>
      <div class="sidebar-nav">
        ${navItems
          .map(
            (item) => `
            <button type="button" class="nav-link" data-nav="${item.view}" aria-current="${navIsActive(item.view) ? "page" : "false"}" title="${esc(item.label)}">
              <span class="nav-label">${iconMarkup(item.icon)}<span>${esc(item.label)}</span></span>
              ${item.count !== "" ? `<span class="nav-count">${esc(item.count)}</span>` : ""}
            </button>`
          )
          .join("")}
      </div>
    </section>

    <section class="sidebar-section sidebar-utility-section">
      <div class="eyebrow">Quick actions</div>
      <div class="sidebar-nav sidebar-utility-nav">
        <button type="button" class="nav-link sidebar-utility-button" data-refresh="true" aria-label="Refresh data" title="Refresh data">
          <span class="nav-label">${iconMarkup("refresh")}<span>Refresh data</span></span>
        </button>
        <button type="button" class="nav-link sidebar-utility-button" data-theme-toggle="true" aria-label="${esc(themeToggleLabel)}" title="${esc(themeToggleLabel)}">
          <span class="nav-label">${iconMarkup(themeToggleIcon)}<span>${esc(themeToggleLabel)}</span></span>
        </button>
      </div>
    </section>

    <section class="sidebar-section system-health-section">
      <div class="eyebrow">System health</div>
      <div class="badge-row" style="margin-top:10px;">
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
      <h1 class="topbar-title">${esc(current.title)}</h1>
      <div class="topbar-meta">
        ${state.isRefreshingData ? `
          <div class="topbar-sync-indicator">
            <span class="loading-dot" aria-hidden="true"></span>
            <span>Syncing</span>
          </div>
        ` : ""}
        ${state.notice.text ? `<div class="notice" data-tone="${esc(state.notice.tone)}">${esc(state.notice.text)}</div>` : ""}
      </div>
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

function languageCatalog() {
  return state.targetLanguages?.length ? state.targetLanguages : state.health?.languages || [];
}

function languageLabel(code) {
  return languageCatalog().find((language) => language.code === code)?.label || code;
}

function renderSelectedNicheLanguages(selectedCodes = []) {
  const container = $("niche-target-language-list");
  if (!container) return;
  container.innerHTML = selectedCodes.length
    ? selectedCodes.map((code) => {
        const label = languageLabel(code);
        return `
          <div class="niche-language-pill">
            <span class="niche-language-pill-label">${esc(label)}</span>
            <button
              type="button"
              class="niche-language-pill-remove"
              data-remove-niche-language="${esc(code)}"
              aria-label="${esc(`Remove ${label}`)}"
              title="${esc(`Remove ${label}`)}"
            >
              ${iconContent("close", `Remove ${label}`, { iconOnly: true })}
            </button>
            <input type="hidden" name="niche-target-language" value="${esc(code)}" />
          </div>
        `;
      }).join("")
    : '<div class="helper niche-language-empty">No target languages added yet.</div>';
}

function readSelectedNicheLanguages() {
  return Array.from(document.querySelectorAll('input[name="niche-target-language"]'))
    .map((input) => input.value)
    .filter(Boolean);
}

function syncCreateNicheLanguagePicker() {
  const options = $("niche-lang-options");
  if (!options) return;

  const masterLang = $("niche-master-lang")?.value || "en";
  const selectedCodes = [...new Set(readSelectedNicheLanguages().filter((code) => code !== masterLang))];
  const available = languageCatalog().filter(
    (language) => language.code !== masterLang && !selectedCodes.includes(language.code)
  );

  renderSelectedNicheLanguages(selectedCodes);
  options.innerHTML = available
    .map((language) => `<option value="${esc(language.label)}" label="${esc(language.code)}"></option>`)
    .join("");

  const searchInput = $("niche-lang-search");
  if (searchInput) {
    searchInput.disabled = available.length === 0;
    searchInput.placeholder = available.length ? "Type a language or code" : "All languages already added";
  }

  const addButton = document.querySelector("[data-add-niche-language]");
  if (addButton) addButton.disabled = available.length === 0;
}

function resolveNicheLanguageQuery(query, { masterLang = "", selectedCodes = [] } = {}) {
  const normalized = String(query || "").trim().toLowerCase();
  if (!normalized) return null;
  return languageCatalog().find(
    (language) =>
      language.code !== masterLang &&
      !selectedCodes.includes(language.code) &&
      (language.code.toLowerCase() === normalized || language.label.toLowerCase() === normalized)
  ) || null;
}

function addCreateNicheLanguage(query = "", { silent = false } = {}) {
  const searchInput = $("niche-lang-search");
  const masterLang = $("niche-master-lang")?.value || "en";
  const selectedCodes = readSelectedNicheLanguages();
  const rawQuery = query || searchInput?.value || "";
  const match = resolveNicheLanguageQuery(rawQuery, { masterLang, selectedCodes });

  if (!match) {
    if (!silent && rawQuery.trim()) {
      setNotice("Choose a language from the suggestions or type its code exactly.", "warn");
    }
    return false;
  }

  renderSelectedNicheLanguages([...new Set([...selectedCodes, match.code])]);
  if (searchInput) {
    searchInput.value = "";
    searchInput.focus();
  }
  syncCreateNicheLanguagePicker();
  return true;
}

function removeCreateNicheLanguage(code) {
  renderSelectedNicheLanguages(readSelectedNicheLanguages().filter((value) => value !== code));
  syncCreateNicheLanguagePicker();
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

function modelDatalistId(modelInputId) {
  return `${modelInputId}-options`;
}

function modelOptions(provider) {
  return modelCatalogFor(provider)
    .map((option) => `<option value="${esc(option.value)}" label="${esc(option.label)}"></option>`)
    .join("");
}

function syncProviderModelSelect(providerSelectId, modelSelectId, preferredModel = "") {
  const providerSelect = $(providerSelectId);
  const modelSelect = $(modelSelectId);
  const modelDatalist = $(modelDatalistId(modelSelectId));
  if (!providerSelect || !modelSelect || !modelDatalist) return;
  const provider = providerSelect.value || "claude";
  const options = modelCatalogFor(provider);
  const previousProvider = modelSelect.dataset.provider || provider;
  const current = preferredModel || modelSelect.value || modelSelect.dataset.currentValue || "";
  const knownForProvider = new Set(options.map((option) => option.value));
  const knownForPreviousProvider = new Set(modelCatalogFor(previousProvider).map((option) => option.value));
  modelDatalist.innerHTML = modelOptions(provider);
  let nextValue = current;
  if (!nextValue) {
    nextValue = defaultModelForProvider(provider);
  } else if (!preferredModel && !knownForProvider.has(nextValue) && knownForPreviousProvider.has(nextValue)) {
    nextValue = defaultModelForProvider(provider);
  }
  modelSelect.placeholder = defaultModelForProvider(provider) || "Type a model id";
  modelSelect.value = nextValue;
  modelSelect.dataset.currentValue = modelSelect.value;
  modelSelect.dataset.provider = provider;
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
          <span class="field-label">Model id</span>
          <input
            id="${esc(modelId)}"
            list="${esc(modelDatalistId(modelId))}"
            value="${esc(modelValue || defaultModelForProvider(providerValue))}"
            data-current-value="${esc(modelValue || defaultModelForProvider(providerValue))}"
            data-provider="${esc(providerValue || "claude")}"
            placeholder="${esc(defaultModelForProvider(providerValue) || "Type a model id")}"
            spellcheck="false"
            autocomplete="off"
          />
          <datalist id="${esc(modelDatalistId(modelId))}">${modelOptions(providerValue)}</datalist>
          <span class="helper">Pick a suggestion or type a newer model id manually.</span>
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
  if (status === "paused") return "warn";
  if (status === "paused_for_tts") return "warn";
  return "neutral";
}

function relativeTime(iso) {
  const parsed = parseDateValue(iso);
  if (!parsed) return "";
  const diff = Date.now() - parsed.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function ttsJobTone(status) {
  if (status === "completed") return "success";
  if (status === "failed") return "error";
  if (status === "processing") return "active";
  if (status === "queued") return "warn";
  return "neutral";
}

function describeWorkerHealth(wh = {}) {
  const activeGenerateJobs = Number(wh.active_generate_jobs || 0);
  const queuedGenerateJobs = Number(wh.queued_generate_jobs || 0);
  const lastHeartbeat = wh.last_heartbeat ? `Last heartbeat ${relativeTime(wh.last_heartbeat)}.` : "";
  const torchBuild = wh.torch_version
    ? `torch ${wh.torch_version}${wh.torch_build ? `+${wh.torch_build}` : ""}`
    : "";
  const queueMeta = `Generate queue: ${activeGenerateJobs} active, ${queuedGenerateJobs} queued.`;
  const runtimeMeta = wh.device === "cuda"
    ? `${wh.gpu_name ? `CUDA on ${wh.gpu_name}` : "CUDA enabled"}${torchBuild ? ` via ${torchBuild}` : ""}.`
    : wh.device === "cpu"
      ? `CPU-only runtime${torchBuild ? ` via ${torchBuild}` : ""}.`
      : torchBuild ? `${torchBuild}.` : "";
  if (wh.startup_error) {
    return {
      visible: true,
      status: "stopped",
      label: "Voice engine unavailable",
      copy: wh.startup_error,
      meta: [runtimeMeta, queueMeta, lastHeartbeat || "Install the XTTS runtime before cloning or testing voices."]
        .filter(Boolean)
        .join(" "),
      };
  }
  if (wh.device === "cpu") {
    return {
      visible: true,
      status: "stale",
      label: "Voice engine on CPU",
      copy: "Long-form narration will be much slower until CUDA-enabled PyTorch is installed for this dashboard environment.",
      meta: [runtimeMeta, queueMeta, lastHeartbeat].filter(Boolean).join(" "),
    };
  }
  if (wh.device === "cuda") {
    return {
      visible: false,
      status: "running",
      label: "Voice engine on GPU",
      copy: wh.gpu_name
        ? `Long-form narration is using CUDA on ${wh.gpu_name}.`
        : "Long-form narration is using CUDA.",
      meta: [runtimeMeta, queueMeta, lastHeartbeat].filter(Boolean).join(" "),
    };
  }
  return {
    visible: false,
    status: wh.lifecycle_state || "sleeping",
    label: "",
    copy: "",
    meta: [queueMeta, lastHeartbeat].filter(Boolean).join(" "),
  };
}

function renderWorkerHealthBanner(wh = {}, { forceVisible = false } = {}) {
  const workerInfo = describeWorkerHealth(wh);
  if (!workerInfo.visible && !forceVisible) return "";
  if (!workerInfo.label && !workerInfo.copy && !workerInfo.meta) return "";
  return `
    <div class="voice-worker-banner" data-status="${workerInfo.status}">
      <div class="voice-worker-banner-copy">
        ${workerInfo.label ? `<span class="worker-badge" data-status="${workerInfo.status}">${esc(workerInfo.label)}</span>` : ""}
        ${workerInfo.copy ? `<p class="helper">${esc(workerInfo.copy)}</p>` : ""}
        ${workerInfo.meta ? `<p class="helper voice-worker-meta">${esc(workerInfo.meta)}</p>` : ""}
      </div>
    </div>
  `;
}

function voiceProfileLatestReadyJob(profile) {
  const job = profile.latest_test_job;
  if (job?.status === "completed" && job.result_available) {
    return job;
  }
  return null;
}

function voiceTtsPresets() {
  return state.settings?.voice_tts_presets || FALLBACK_VOICE_TTS_PRESETS;
}

function voiceTtsLimits() {
  return state.settings?.voice_tts_limits || FALLBACK_VOICE_TTS_LIMITS;
}

function resolveVoiceProfileTtsConfig(profile) {
  const presets = voiceTtsPresets();
  const presetName = profile?.tts_config?.preset || "natural_stable";
  const preset = presets[presetName] || presets.natural_stable || FALLBACK_VOICE_TTS_PRESETS.natural_stable;
  return { ...preset, ...(profile?.tts_config || {}), preset: presetName in presets ? presetName : "natural_stable" };
}

function voiceTtsPresetLabel(preset) {
  if (preset === "natural_stable") return "Natural stable";
  return titleCase(preset);
}

function voiceTtsPresetOptions(selectedPreset) {
  const presets = voiceTtsPresets();
  return Object.keys(presets)
    .map((preset) => `<option value="${esc(preset)}"${preset === selectedPreset ? " selected" : ""}>${esc(voiceTtsPresetLabel(preset))}</option>`)
    .join("");
}

function voiceTtsNumberValue(value, decimals = 2) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(decimals) : "";
}

function applyVoiceTtsPresetToForm(presetName) {
  const presets = voiceTtsPresets();
  const preset = presets[presetName] || presets.natural_stable || FALLBACK_VOICE_TTS_PRESETS.natural_stable;
  if (!preset) return;
  if ($("voice-tuning-temperature")) $("voice-tuning-temperature").value = voiceTtsNumberValue(preset.temperature, 2);
  if ($("voice-tuning-top-p")) $("voice-tuning-top-p").value = voiceTtsNumberValue(preset.top_p, 2);
  if ($("voice-tuning-top-k")) $("voice-tuning-top-k").value = String(preset.top_k);
  if ($("voice-tuning-speed")) $("voice-tuning-speed").value = voiceTtsNumberValue(preset.speed, 2);
  if ($("voice-tuning-chunk-max-chars")) $("voice-tuning-chunk-max-chars").value = String(preset.chunk_max_chars);
  if ($("voice-tuning-silence-gap")) $("voice-tuning-silence-gap").value = voiceTtsNumberValue(preset.silence_gap_seconds, 2);
}

function voiceProfileIsStarting(profile) {
  const job = profile.latest_test_job;
  return (
    state.submittingVoiceTestProfileId === profile.id &&
    job?.status !== "processing" &&
    job?.status !== "queued"
  );
}

function voiceProfileIsGenerating(profile) {
  const job = profile.latest_test_job;
  return (
    job?.status === "processing" ||
    job?.status === "queued"
  );
}

function voiceProfileCardState(profile, workerHealth = {}) {
  const latentJob = profile.latest_latent_job;
  const latestTestJob = profile.latest_test_job;
  const latestReadyJob = voiceProfileLatestReadyJob(profile);
  const workerState = workerHealth.lifecycle_state || "sleeping";

  if (voiceProfileIsStarting(profile)) {
    return {
      label: "Starting voice engine",
      tone: "warn",
      copy: "Waking the voice engine and queueing a fresh sample.",
      error: "",
    };
  }

  if (voiceProfileIsGenerating(profile)) {
    const queuedWhileStarting =
      latestTestJob?.status === "queued" &&
      workerState === "starting";
    return {
      label: "Generating sample",
      tone: latestTestJob?.status === "processing" ? "active" : "warn",
      copy: queuedWhileStarting
        ? "Starting the voice engine and queueing a fresh sample."
        : latestTestJob?.progress || "Generating a fresh sample for this voice.",
      error: "",
    };
  }
  if (latestTestJob?.status === "failed") {
    return {
      label: "Needs attention",
      tone: "error",
      copy: "The latest sample failed. Click Play test to try again.",
      error: latestTestJob.error_message || "",
    };
  }
  if (latestTestJob?.status === "completed" && !latestTestJob.result_available) {
    return {
      label: "Needs attention",
      tone: "warn",
      copy: "The latest sample finished, but the audio file is no longer available.",
      error: "",
    };
  }
  if (latentJob?.status === "failed") {
    return {
      label: "Needs attention",
      tone: "error",
      copy: "The saved voice needs another clone attempt.",
      error: latentJob.error_message || "",
    };
  }
  if (latentJob?.status === "processing" || latentJob?.status === "queued") {
    const queuedWhileStarting =
      latentJob.status === "queued" &&
      workerState === "starting";
    return {
      label: "Preparing",
      tone: latentJob.status === "processing" ? "active" : "warn",
      copy: queuedWhileStarting
        ? "Starting the voice engine and preparing the cache in the background."
        : latentJob.progress || "Saving the voice and preparing the cache in the background.",
      error: "",
    };
  }
  if (workerHealth.startup_error && !latestReadyJob) {
    return {
      label: "Needs attention",
      tone: "error",
      copy: "The reference audio is saved, but the voice engine is unavailable on this machine.",
      error: "",
    };
  }
  if (latestReadyJob) {
    return {
      label: "Ready to test",
      tone: "success",
      copy: `Latest sample ready ${relativeTime(latestReadyJob.finished_at || latestReadyJob.updated_at || latestReadyJob.created_at)}. Click Play test for a fresh one.`,
      error: "",
    };
  }
  if (profile.has_latents) {
    return {
      label: "Ready to test",
      tone: "success",
      copy: "Profile saved. Click Play test to generate a fresh sample.",
      error: "",
    };
  }
  return {
    label: "Ready to test",
    tone: "neutral",
    copy: "Profile saved. Click Play test to generate the first sample.",
    error: "",
  };
}

function syncVoiceProfileAudioAutoplay() {
  if (state.route.view !== "voice-profiles") return;
  const pendingEntries = Object.entries(state.pendingVoiceTestJobs || {});
  if (!pendingEntries.length) return;

  for (const [profileId, jobId] of pendingEntries) {
    const profile = (state.voiceProfiles || []).find((item) => item.id === profileId);
    const job = profile?.latest_test_job;
    if (!job || job.job_id !== jobId) continue;

    if (job.status === "failed") {
      delete state.pendingVoiceTestJobs[profileId];
      state.autoPlayedVoiceTestJobs[profileId] = jobId;
      continue;
    }

    if (job.status === "completed" && job.result_available) {
      delete state.pendingVoiceTestJobs[profileId];
      if (state.autoPlayedVoiceTestJobs[profileId] === jobId) continue;
      state.autoPlayedVoiceTestJobs[profileId] = jobId;
      const audio = document.querySelector(`[data-profile-audio="${profileId}"]`);
      if (!audio) continue;
      audio.currentTime = 0;
      const playback = audio.play();
      if (playback?.catch) playback.catch(() => {});
    }
  }
}

function voiceProfileAudioIsPlaying() {
  if (state.route.view !== "voice-profiles") return false;
  return Array.from(document.querySelectorAll(".profile-audio-player")).some(
    (audio) => audio && !audio.paused && !audio.ended && audio.currentTime > 0
  );
}

function projectConfigInteractionIsActive() {
  const activeElement = document.activeElement;
  return Boolean(activeElement?.closest?.(".project-config-grid"));
}

function renderVoiceProfiles() {
  const profiles = state.voiceProfiles || [];
  const wh = state.workerHealth || {};
  const workerInfo = describeWorkerHealth(wh);
  const showWorkerBanner = Boolean(workerInfo.visible);

  const cards = profiles
    .map((p) => {
      const cardState = voiceProfileCardState(p, wh);
      const latestReadyJob = voiceProfileLatestReadyJob(p);
      const starting = voiceProfileIsStarting(p);
      const generating = voiceProfileIsGenerating(p);
      const actionBusy = starting || generating;
      const playLabel = actionBusy ? (starting ? "Starting voice engine" : "Generating sample") : "Play test";
      const playIcon = actionBusy ? "refresh" : "play";
      return `
      <div class="profile-card">
        <div class="profile-card-head">
          <h3 class="profile-card-title">${esc(p.name)}</h3>
          <div class="profile-card-actions">
            <button
              type="button"
              class="button button-primary button-small icon-only tooltip-anchor profile-card-action profile-card-action-primary${actionBusy ? " is-busy" : ""}"
              data-test-voice="${esc(p.id)}"
              data-tooltip="${esc(playLabel)}"
              aria-label="${esc(playLabel)}"
              title="${esc(playLabel)}"
              ${actionBusy ? "disabled" : ""}
            >${iconContent(playIcon, playLabel, { iconOnly: true, iconClass: actionBusy ? "button-icon-spin" : "" })}</button>
            <button
              type="button"
              class="button button-ghost button-small icon-only tooltip-anchor profile-card-action"
              data-open-voice-tuning="${esc(p.id)}"
              data-tooltip="Tune voice"
              aria-label="Tune voice"
              title="Tune voice"
            >${iconContent("settings", "Tune voice", { iconOnly: true })}</button>
            <button
              type="button"
              class="button button-danger button-small icon-only tooltip-anchor profile-card-action"
              data-delete-voice-profile="${esc(p.id)}"
              data-tooltip="Delete voice"
              aria-label="Delete voice"
              title="Delete voice"
            >${iconContent("delete", "Delete voice", { iconOnly: true })}</button>
          </div>
        </div>
        <div class="profile-card-body">
          <div class="profile-card-status">
            ${statusBadge(cardState.label, cardState.tone)}
            <p class="profile-card-copy">${esc(cardState.copy)}</p>
          </div>
          ${latestReadyJob ? `
            <audio
              class="profile-audio-player"
              controls
              preload="none"
              data-profile-audio="${esc(p.id)}"
              src="${esc(latestReadyJob.download_url)}"
            ></audio>
            <div class="profile-card-meta">Fresh sample generated ${esc(relativeTime(latestReadyJob.finished_at || latestReadyJob.updated_at || latestReadyJob.created_at))}.</div>
          ` : ""}
          ${cardState.error ? `<div class="profile-inline-message" data-tone="error">${esc(cardState.error)}</div>` : ""}
        </div>
      </div>
    `;
    })
    .join("");

  $("view").innerHTML = `
    ${showWorkerBanner ? renderWorkerHealthBanner(workerHealth) : ""}

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
    ${state.modal.kind === "voice-profile-tuning" ? renderVoiceProfileTuningModal() : ""}
  `;
}

function renderCreateVoiceProfileModal() {
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

function renderVoiceProfileTuningModal() {
  const profileId = state.modal.profileId;
  const profile = (state.voiceProfiles || []).find((item) => item.id === profileId);
  if (!profile) return "";

  const config = resolveVoiceProfileTtsConfig(profile);
  const limits = voiceTtsLimits();

  return `
    <div class="modal-backdrop" data-modal-backdrop="true">
      <div class="modal-panel">
        <div class="modal-header">
          <h2>Tune ${esc(profile.name)}</h2>
          <button type="button" class="button button-ghost icon-only" data-close-modal="true">${iconContent("close", "Close", { iconOnly: true })}</button>
        </div>
        <form id="voice-profile-tuning-form" class="stack">
          <input type="hidden" id="voice-tuning-profile-id" value="${esc(profile.id)}" />
          <label class="field">
            <span class="field-label">Preset</span>
            <select id="voice-tuning-preset">${voiceTtsPresetOptions(config.preset)}</select>
          </label>
          <div class="helper">Use presets to keep narration believable, then fine-tune only if this voice needs it.</div>
          <details class="surface" style="padding:14px 16px;">
            <summary style="cursor:pointer;font-weight:600;">Advanced tuning</summary>
            <div class="form-grid" style="margin-top:14px;">
              <label class="field">
                <span class="field-label">Temperature</span>
                <input id="voice-tuning-temperature" type="number" step="0.01" min="${esc(limits.temperature.min)}" max="${esc(limits.temperature.max)}" value="${esc(voiceTtsNumberValue(config.temperature, 2))}" />
              </label>
              <label class="field">
                <span class="field-label">Top P</span>
                <input id="voice-tuning-top-p" type="number" step="0.01" min="${esc(limits.top_p.min)}" max="${esc(limits.top_p.max)}" value="${esc(voiceTtsNumberValue(config.top_p, 2))}" />
              </label>
              <label class="field">
                <span class="field-label">Top K</span>
                <input id="voice-tuning-top-k" type="number" step="1" min="${esc(limits.top_k.min)}" max="${esc(limits.top_k.max)}" value="${esc(config.top_k)}" />
              </label>
              <label class="field">
                <span class="field-label">Speed</span>
                <input id="voice-tuning-speed" type="number" step="0.01" min="${esc(limits.speed.min)}" max="${esc(limits.speed.max)}" value="${esc(voiceTtsNumberValue(config.speed, 2))}" />
              </label>
              <label class="field">
                <span class="field-label">Chunk max chars</span>
                <input id="voice-tuning-chunk-max-chars" type="number" step="1" min="${esc(limits.chunk_max_chars.min)}" max="${esc(limits.chunk_max_chars.max)}" value="${esc(config.chunk_max_chars)}" />
              </label>
              <label class="field">
                <span class="field-label">Silence gap (seconds)</span>
                <input id="voice-tuning-silence-gap" type="number" step="0.01" min="${esc(limits.silence_gap_seconds.min)}" max="${esc(limits.silence_gap_seconds.max)}" value="${esc(voiceTtsNumberValue(config.silence_gap_seconds, 2))}" />
              </label>
            </div>
          </details>
          <div class="button-row" style="margin-top:18px;">
            <button type="submit" class="button button-primary has-icon" value="save" name="voiceTuningAction">${iconContent("save", "Save")}</button>
            <button type="submit" class="button button-ghost has-icon" value="save-play" name="voiceTuningAction">${iconContent("play", "Save and play test")}</button>
            <button type="button" class="button button-ghost" data-close-modal="true">Cancel</button>
          </div>
        </form>
      </div>
    </div>
  `;
}

function translationProfileProviderSpec(providerId, fallbackLabel = "") {
  const spec = TRANSLATION_PROFILE_PROVIDER_CATALOG.find((item) => item.id === providerId);
  if (spec) return spec;
  return {
    id: providerId || "legacy",
    label: fallbackLabel || (providerId ? `Legacy: ${titleCase(providerId)}` : "Legacy provider"),
    mode: "legacy",
    placeholder: false,
    description: "Legacy translation profile retained for compatibility.",
  };
}

function buildTranslationProfileEditor(profile = null) {
  const providerSpec = profile
    ? translationProfileProviderSpec(profile.provider, profile.provider_label)
    : translationProfileProviderSpec("openai");
  const activeProvider = providerSpec.mode === "legacy" ? "legacy" : providerSpec.id;
  return {
    mode: profile ? "edit" : "create",
    profileId: profile?.id || null,
    activeProvider,
    sourceProvider: profile?.provider || "openai",
    sourceProviderLabel: profile?.provider_label || providerSpec.label,
    name: profile?.name || "",
    apiKeyDraft: "",
    hasSavedApiKey: Boolean(profile?.has_api_key),
    apiKeyMasked: profile?.api_key_masked || "",
    selectedModel: profile?.model || "",
    discoveredModels: [],
    discoverySucceeded: false,
    discoveryError: "",
    discoveryStatus: profile?.provider === "openai" && profile?.has_api_key
      ? "Saved key available. Load models to edit this profile."
      : "Paste an OpenAI API key and load the model list.",
    recommendedModel: "",
    isDiscovering: false,
    modelSearch: "",
    sortBy: "recommended",
  };
}

function activeTranslationProfileEditor() {
  return state.translationProfileEditor || buildTranslationProfileEditor();
}

function translationProfileCanReuseSavedKey(editor = state.translationProfileEditor) {
  return Boolean(
    editor
    && editor.mode === "edit"
    && editor.sourceProvider === "openai"
    && editor.hasSavedApiKey
    && editor.profileId,
  );
}

function translationProfileEditorCanSave(editor = state.translationProfileEditor) {
  if (!editor || editor.activeProvider !== "openai") return false;
  if (!String(editor.name || "").trim()) return false;
  if (!editor.discoverySucceeded || !String(editor.selectedModel || "").trim()) return false;
  return Boolean(String(editor.apiKeyDraft || "").trim() || translationProfileCanReuseSavedKey(editor));
}

function filteredTranslationModels(editor) {
  const query = String(editor?.modelSearch || "").trim().toLowerCase();
  const sortBy = editor?.sortBy || "recommended";
  const models = [...(editor?.discoveredModels || [])].filter((model) => {
    if (!query) return true;
    const haystack = [
      model.id,
      model.label,
      model.capability_label,
      model.best_for,
    ].join(" ").toLowerCase();
    return haystack.includes(query);
  });
  models.sort((left, right) => {
    if (sortBy === "price") {
      return (
        (left.price_score - right.price_score) ||
        (right.speed_score - left.speed_score) ||
        String(left.label || left.id).localeCompare(String(right.label || right.id))
      );
    }
    if (sortBy === "speed") {
      return (
        (right.speed_score - left.speed_score) ||
        (left.price_score - right.price_score) ||
        String(left.label || left.id).localeCompare(String(right.label || right.id))
      );
    }
    if (sortBy === "name") {
      return String(left.label || left.id).localeCompare(String(right.label || right.id));
    }
    return (
      (right.recommended === left.recommended ? 0 : right.recommended ? 1 : -1) ||
      (left.price_score - right.price_score) ||
      (right.speed_score - left.speed_score) ||
      String(left.label || left.id).localeCompare(String(right.label || right.id))
    );
  });
  return models;
}

function renderTranslationDiscoveryStatus(editor) {
  if (!editor || editor.activeProvider !== "openai") return "";
  if (editor.discoveryError) {
    return `<div class="profile-inline-message" data-tone="error">${esc(editor.discoveryError)}</div>`;
  }
  if (editor.isDiscovering) {
    return `<div class="profile-inline-message">Checking the OpenAI key and loading available models...</div>`;
  }
  if (editor.discoverySucceeded) {
    const count = (editor.discoveredModels || []).length;
    return `<div class="profile-inline-message">${esc(`${count} model${count === 1 ? "" : "s"} loaded. Hover a model to inspect quality, speed, and cost hints.`)}</div>`;
  }
  return `<div class="profile-inline-message">${esc(editor.discoveryStatus || "Paste an OpenAI API key and load the model list.")}</div>`;
}

function renderTranslationModelControls(editor) {
  if (!editor || editor.activeProvider !== "openai") return "";
  return `
    <label class="field">
      <span class="field-label">Search models</span>
      <input id="tp-model-search" value="${esc(editor.modelSearch)}" placeholder="Search by name or usage hint" ${editor.discoverySucceeded ? "" : "disabled"} />
    </label>
    <label class="field">
      <span class="field-label">Sort</span>
      <select id="tp-model-sort" ${editor.discoverySucceeded ? "" : "disabled"}>
        <option value="recommended" ${editor.sortBy === "recommended" ? "selected" : ""}>Recommended</option>
        <option value="price" ${editor.sortBy === "price" ? "selected" : ""}>Cheapest first</option>
        <option value="speed" ${editor.sortBy === "speed" ? "selected" : ""}>Fastest first</option>
        <option value="name" ${editor.sortBy === "name" ? "selected" : ""}>Name</option>
      </select>
    </label>
  `;
}

function renderTranslationModelPanel(editor) {
  if (!editor || editor.activeProvider !== "openai") return "";
  if (!editor.discoverySucceeded) {
    return `<div class="translation-model-empty helper">Load models first. The save action stays locked until the key is checked and a model is selected.</div>`;
  }
  const visibleModels = filteredTranslationModels(editor);
  if (!visibleModels.length) {
    return `<div class="translation-model-empty helper">No models match the current search.</div>`;
  }
  const selected = (editor.discoveredModels || []).find((model) => model.id === editor.selectedModel);
  return `
    <div class="translation-model-panel-head">
      <div class="badge-row">
        ${selected ? statusBadge(`Selected: ${selected.label}`, "active") : statusBadge("Choose a model", "warn")}
        ${editor.recommendedModel ? statusBadge(`Default: ${editor.recommendedModel}`, "success") : ""}
      </div>
    </div>
    <div class="translation-model-list">
      ${visibleModels.map((model) => `
        <button
          type="button"
          class="translation-model-option tooltip-anchor${editor.selectedModel === model.id ? " is-active" : ""}"
          data-select-translation-model="${esc(model.id)}"
          data-tooltip="${esc(`${model.best_for} Price ${model.price_score}/5. Speed ${model.speed_score}/5. ${model.capability_label}.`)}"
          aria-pressed="${editor.selectedModel === model.id ? "true" : "false"}"
        >
          <div class="translation-model-option-head">
            <div class="translation-model-option-title">${esc(model.label)}</div>
            <div class="badge-row">
              ${model.recommended ? statusBadge("Recommended", "success") : ""}
              ${statusBadge(model.capability_label, "active")}
            </div>
          </div>
          <div class="translation-model-option-copy">${esc(model.id)}</div>
          <div class="translation-model-option-copy">${esc(shortText(model.best_for, 18))}</div>
          <div class="badge-row">
            <span class="badge">Price ${esc(model.price_score)}/5</span>
            <span class="badge">Speed ${esc(model.speed_score)}/5</span>
          </div>
        </button>
      `).join("")}
    </div>
  `;
}

function renderTranslationProviderTabs(editor) {
  const tabs = TRANSLATION_PROFILE_PROVIDER_CATALOG.map((spec) => `
    <button
      type="button"
      class="translation-provider-tab${editor.activeProvider === spec.id ? " is-active" : ""}"
      data-translation-provider-tab="${esc(spec.id)}"
      aria-pressed="${editor.activeProvider === spec.id ? "true" : "false"}"
    >
      <div class="translation-provider-tab-head">
        <span>${esc(spec.label)}</span>
        ${spec.placeholder ? statusBadge("Placeholder", "warn") : statusBadge("Live", "success")}
      </div>
      <div class="translation-provider-tab-copy">${esc(spec.description)}</div>
    </button>
  `).join("");
  const legacy = editor.activeProvider === "legacy"
    ? `
      <div class="translation-provider-tab is-active is-legacy">
        <div class="translation-provider-tab-head">
          <span>${esc(editor.sourceProviderLabel || "Legacy profile")}</span>
          ${statusBadge("Legacy", "warn")}
        </div>
        <div class="translation-provider-tab-copy">This stored profile still exists, but the new setup flow only edits OpenAI profiles.</div>
      </div>
    `
    : "";
  return `<div class="translation-provider-grid">${tabs}${legacy}</div>`;
}

function renderTranslationProfileEditorBody(editor) {
  if (editor.activeProvider === "openai") {
    return `
      <div class="form-grid">
        <label class="field">
          <span class="field-label">Profile name</span>
          <input id="tp-profile-name" value="${esc(editor.name)}" placeholder="e.g. Spanish main" required />
        </label>
        <label class="field">
          <span class="field-label">OpenAI API key</span>
          <input id="tp-api-key" type="password" value="${esc(editor.apiKeyDraft)}" placeholder="${translationProfileCanReuseSavedKey(editor) ? "Leave blank to keep the saved key" : "Paste an OpenAI key"}" autocomplete="off" spellcheck="false" />
        </label>
      </div>
      <div class="translation-key-meta">
        ${translationProfileCanReuseSavedKey(editor) ? statusBadge(`Saved key ${editor.apiKeyMasked}`, "active") : ""}
        <span class="helper">${esc(editor.mode === "edit" ? "Leave the field blank to keep the current key, or paste a new one and reload models." : "This key is only used to load the models available to this OpenAI account.")}</span>
      </div>
      <div class="button-row translation-discovery-actions">
        <button type="button" class="button has-icon" id="tp-discover-button" data-translation-discover="true">${iconContent("refresh", editor.discoverySucceeded ? "Refresh models" : "Check key")}</button>
      </div>
      <div id="tp-discovery-status">${renderTranslationDiscoveryStatus(editor)}</div>
      <div id="tp-model-controls" class="translation-model-controls">
        ${renderTranslationModelControls(editor)}
      </div>
      <div id="tp-model-panel">${renderTranslationModelPanel(editor)}</div>
    `;
  }
  if (editor.activeProvider === "legacy") {
    return `
      <div class="profile-inline-message" data-tone="error">This saved profile uses a legacy provider. The new setup flow keeps it visible and deletable, but only OpenAI profiles can be edited here.</div>
      <div class="form-grid">
        <label class="field">
          <span class="field-label">Profile name</span>
          <input value="${esc(editor.name)}" readonly />
        </label>
        <label class="field">
          <span class="field-label">Provider</span>
          <input value="${esc(editor.sourceProviderLabel || editor.sourceProvider)}" readonly />
        </label>
        <label class="field">
          <span class="field-label">Model</span>
          <input value="${esc(editor.selectedModel || "Unknown")}" readonly />
        </label>
        <label class="field">
          <span class="field-label">Saved key</span>
          <input value="${esc(editor.apiKeyMasked || "Not available")}" readonly />
        </label>
      </div>
    `;
  }
  const activeSpec = translationProfileProviderSpec(editor.activeProvider);
  const defaultCommand = "claude -p";
  const defaultModel = "haiku";
  return `
    <div class="profile-inline-message" data-tone="warn">Placeholder only. This tab is a UI preview and cannot be saved yet.</div>
    <div class="form-grid">
      <label class="field">
        <span class="field-label">Profile name</span>
        <input value="${esc(editor.name || `${activeSpec.label} preview`)}" readonly />
      </label>
      <label class="field">
        <span class="field-label">CLI command</span>
        <input value="${esc(defaultCommand)}" readonly />
      </label>
      <label class="field">
        <span class="field-label">Preferred model</span>
        <input value="${esc(defaultModel)}" readonly />
      </label>
      <label class="field">
        <span class="field-label">Auth source</span>
        <input value="Uses the local CLI session" readonly />
      </label>
    </div>
  `;
}

function renderTranslationProfileModal() {
  const editor = activeTranslationProfileEditor();
  const title = editor.mode === "edit" ? "Edit translation profile" : "Create translation profile";
  const submitLabel = editor.mode === "edit" ? "Save profile" : "Create profile";
  return `
    <div class="modal-backdrop" data-modal-backdrop="true">
      <div class="modal-panel translation-profile-modal">
        <div class="modal-header">
          <h2>${esc(title)}</h2>
          <button type="button" class="button button-ghost icon-only" data-close-modal="true">${iconContent("close", "Close", { iconOnly: true })}</button>
        </div>
        <form id="translation-profile-editor-form" class="stack">
          ${renderTranslationProviderTabs(editor)}
          ${renderTranslationProfileEditorBody(editor)}
          <div class="button-row translation-profile-actions">
            <button type="submit" id="tp-submit-button" class="button button-primary has-icon"${translationProfileEditorCanSave(editor) ? "" : " disabled"}>${iconContent(editor.mode === "edit" ? "save" : "add", submitLabel)}</button>
            <button type="button" class="button button-ghost" data-close-modal="true">Cancel</button>
          </div>
        </form>
      </div>
    </div>
  `;
}

function defaultStageProviderOpenAiStatus(stageProviderOpenAi = {}) {
  const modelCount = Number(stageProviderOpenAi.modelCount || 0);
  const recommendedModel = String(stageProviderOpenAi.recommendedModel || "").trim();
  const lastSyncedAt = stageProviderOpenAi.lastSyncedAt
    ? ` Last sync ${formatDate(stageProviderOpenAi.lastSyncedAt)}.`
    : "";
  if (stageProviderOpenAi.hasSavedApiKey && modelCount > 0) {
    return `${modelCount} cached OpenAI model option${modelCount === 1 ? "" : "s"} ready for workflow stages.${recommendedModel ? ` Default ${recommendedModel}.` : ""}${lastSyncedAt}`.trim();
  }
  if (stageProviderOpenAi.hasSavedApiKey) {
    return `Saved OpenAI key available for workflow stages. Refresh models to scan the current catalog.${lastSyncedAt}`.trim();
  }
  if (modelCount > 0) {
    return `Cached OpenAI models are available, but workflow stages still need a saved API key.${lastSyncedAt}`.trim();
  }
  return "Paste an OpenAI API key to scan current models. Save settings to use OpenAI on scene planning, consistency guide, and prompt stages.";
}

function hydrateStageProviderOpenAiState(settings = {}, previous = null) {
  const hasSavedApiKey = Boolean(settings.stage_provider_openai_has_api_key);
  const stageProviderOpenAi = {
    apiKeyDraft: String(previous?.apiKeyDraft || ""),
    hasSavedApiKey,
    apiKeyMasked: settings.stage_provider_openai_api_key_masked || "",
    modelCount: Number(settings.stage_provider_openai_model_count || 0),
    recommendedModel: settings.stage_provider_openai_recommended_model || "",
    lastSyncedAt: settings.stage_provider_openai_last_synced_at || "",
    isDiscovering: Boolean(previous?.isDiscovering),
    discoveryError: previous?.isDiscovering ? String(previous?.discoveryError || "") : "",
    discoveryStatus: previous?.isDiscovering ? String(previous?.discoveryStatus || "") : "",
  };
  if (!stageProviderOpenAi.discoveryStatus) {
    stageProviderOpenAi.discoveryStatus = defaultStageProviderOpenAiStatus(stageProviderOpenAi);
  }
  return stageProviderOpenAi;
}

function activeStageProviderOpenAi() {
  if (!state.stageProviderOpenAi) {
    state.stageProviderOpenAi = hydrateStageProviderOpenAiState(state.settings || {}, null);
  }
  return state.stageProviderOpenAi;
}

function renderStageProviderOpenAiMeta(stageProviderOpenAi = state.stageProviderOpenAi) {
  if (!stageProviderOpenAi) return "";
  const badges = [
    stageProviderOpenAi.hasSavedApiKey
      ? statusBadge(`Saved key ${stageProviderOpenAi.apiKeyMasked}`, "active")
      : statusBadge("No saved key", "warn"),
    stageProviderOpenAi.modelCount
      ? statusBadge(
        `${stageProviderOpenAi.modelCount} cached model${stageProviderOpenAi.modelCount === 1 ? "" : "s"}`,
        "success"
      )
      : "",
    stageProviderOpenAi.recommendedModel
      ? statusBadge(`Default ${stageProviderOpenAi.recommendedModel}`, "success")
      : "",
  ].filter(Boolean).join("");
  const helperCopy = stageProviderOpenAi.lastSyncedAt
    ? `Last model sync ${formatDate(stageProviderOpenAi.lastSyncedAt)}.`
    : "Used by scene planning, consistency guide, video prompts, and image prompts when their provider is set to OpenAI API.";
  return `
    <div id="stage-provider-openai-meta" class="translation-key-meta">
      ${badges}
      <span class="helper">${esc(helperCopy)}</span>
    </div>
  `;
}

function renderStageProviderOpenAiStatus(stageProviderOpenAi = state.stageProviderOpenAi) {
  if (!stageProviderOpenAi) return "";
  if (stageProviderOpenAi.discoveryError) {
    return `<div class="profile-inline-message" data-tone="error">${esc(stageProviderOpenAi.discoveryError)}</div>`;
  }
  if (stageProviderOpenAi.isDiscovering) {
    return `<div class="profile-inline-message">Checking the OpenAI key and caching workflow-stage models...</div>`;
  }
  return `<div class="profile-inline-message">${esc(stageProviderOpenAi.discoveryStatus || defaultStageProviderOpenAiStatus(stageProviderOpenAi))}</div>`;
}

function captureStageProviderOpenAiDraft() {
  const stageProviderOpenAi = activeStageProviderOpenAi();
  const apiKeyInput = $("stage-provider-openai-api-key");
  if (!apiKeyInput) return stageProviderOpenAi;
  const previousDraft = stageProviderOpenAi.apiKeyDraft;
  stageProviderOpenAi.apiKeyDraft = apiKeyInput.value;
  if (stageProviderOpenAi.apiKeyDraft !== previousDraft) {
    stageProviderOpenAi.discoveryError = "";
    stageProviderOpenAi.discoveryStatus = stageProviderOpenAi.apiKeyDraft.trim()
      ? "Unsaved OpenAI key ready. Refresh models to scan this account, then save settings to use it in workflow stages."
      : defaultStageProviderOpenAiStatus(stageProviderOpenAi);
  }
  return stageProviderOpenAi;
}

function syncStageProviderOpenAiActionState() {
  const stageProviderOpenAi = state.stageProviderOpenAi;
  if (!stageProviderOpenAi) return;
  const discoverButton = $("stage-provider-openai-discover-button");
  if (!discoverButton) return;
  const hasDraft = Boolean(String(stageProviderOpenAi.apiKeyDraft || "").trim());
  const label = stageProviderOpenAi.isDiscovering
    ? "Checking..."
    : stageProviderOpenAi.modelCount > 0
      ? "Refresh models"
      : (hasDraft || stageProviderOpenAi.hasSavedApiKey ? "Check key" : "Check key");
  discoverButton.disabled = stageProviderOpenAi.isDiscovering;
  discoverButton.innerHTML = iconContent("refresh", label);
}

function refreshStageProviderOpenAiDom() {
  const statusNode = $("stage-provider-openai-status");
  if (statusNode) {
    statusNode.innerHTML = renderStageProviderOpenAiStatus(state.stageProviderOpenAi);
  }
  syncStageProviderOpenAiActionState();
}

async function discoverStageProviderOpenAiModels() {
  const stageProviderOpenAi = captureStageProviderOpenAiDraft();
  if (!stageProviderOpenAi) return;
  const apiKeyDraft = String(stageProviderOpenAi.apiKeyDraft || "").trim();
  if (!apiKeyDraft && !stageProviderOpenAi.hasSavedApiKey) {
    stageProviderOpenAi.discoveryError = "Paste an OpenAI API key first.";
    stageProviderOpenAi.discoveryStatus = "";
    refreshStageProviderOpenAiDom();
    return;
  }
  stageProviderOpenAi.isDiscovering = true;
  stageProviderOpenAi.discoveryError = "";
  stageProviderOpenAi.discoveryStatus = apiKeyDraft
    ? "Checking the pasted OpenAI key."
    : "Checking the saved OpenAI key.";
  refreshStageProviderOpenAiDom();
  try {
    const payload = {};
    if (apiKeyDraft) payload.api_key = apiKeyDraft;
    const result = await api("/api/providers/openai/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await refreshData({ force: true });
    state.stageProviderOpenAi = hydrateStageProviderOpenAiState(state.settings || {}, state.stageProviderOpenAi);
    state.stageProviderOpenAi.apiKeyDraft = stageProviderOpenAi.apiKeyDraft;
    state.stageProviderOpenAi.isDiscovering = false;
    state.stageProviderOpenAi.discoveryError = "";
    state.stageProviderOpenAi.discoveryStatus = result.from_saved_key
      ? "Models loaded using the saved OpenAI key."
      : result.api_key_saved
        ? "Models loaded using the pasted OpenAI key. Save settings only if you want to replace the stored key."
        : "Models loaded using the pasted OpenAI key. Save settings to use this key in workflow stages.";
    renderApp();
    setNotice(`Loaded ${result.models?.length || 0} OpenAI model${(result.models?.length || 0) === 1 ? "" : "s"}.`, "success");
  } catch (error) {
    stageProviderOpenAi.isDiscovering = false;
    stageProviderOpenAi.discoveryError = error.message;
    refreshStageProviderOpenAiDom();
  }
}

function renderTranslationProfiles() {
  const profiles = state.translationProfiles || [];
  const cards = profiles
    .map((profile) => {
      const providerSpec = translationProfileProviderSpec(profile.provider, profile.provider_label);
      const readyLabel = profile.provider_runnable ? "Ready" : "Needs setup";
      const readyTone = profile.provider_runnable ? "success" : "warn";
      const providerTone = profile.provider_mode === "legacy" ? "warn" : "active";
      const modelBadge = profile.model
        ? statusBadge(profile.model, "neutral")
        : statusBadge("No model", "warn");
      return `
        <div class="profile-card translation-profile-card">
          <div class="profile-card-head">
            <button
              type="button"
              class="translation-profile-summary"
              data-edit-translation-profile="${esc(profile.id)}"
              aria-label="${esc(`Open ${profile.name} details`)}"
              title="Open details"
            >
              <div class="translation-profile-summary-copy">
                <h3 class="profile-card-title">${esc(profile.name)}</h3>
                <div class="badge-row translation-profile-badges">
                  ${statusBadge(profile.provider_label || providerSpec.label, providerTone)}
                  ${modelBadge}
                  ${statusBadge(readyLabel, readyTone)}
                </div>
              </div>
            </button>
            <div class="profile-card-actions">
              ${profile.provider_editable ? `<button type="button" class="button button-small icon-only profile-card-action" data-edit-translation-profile="${esc(profile.id)}" aria-label="Edit" title="Edit">${iconContent("edit", "Edit", { iconOnly: true })}</button>` : ""}
              <button type="button" class="button button-danger button-small icon-only profile-card-action" data-delete-translation-profile="${esc(profile.id)}" aria-label="Delete" title="Delete">${iconContent("delete", "Delete", { iconOnly: true })}</button>
            </div>
          </div>
        </div>
      `;
    })
    .join("");

  $("view").innerHTML = `
    <div class="detail-section">
      <div class="section-header translation-profiles-toolbar">
        <div class="translation-profiles-count helper">${esc(`${profiles.length} profile${profiles.length === 1 ? "" : "s"}`)}</div>
        <div class="button-row">
          <button type="button" class="button button-primary button-small has-icon" data-create-translation-profile="true">${iconContent("add", "Create profile")}</button>
        </div>
      </div>
      ${profiles.length
        ? `<div class="profiles-grid" style="margin-top:12px;">${cards}</div>`
        : `<p class="helper" style="margin-top:8px;">No translation profiles yet.</p>`}
    </div>
    ${state.modal.kind === "translation-profile-editor" ? renderTranslationProfileModal() : ""}
  `;
}

// ── Episode pipeline board ────────────────────────────────────────

function episodeColumnForCard(ep) {
  if (!ep) return "draft";
  if (ep.board_status === "Needs Attention" || ep.pipeline_status === "failed") return "needs_attention";
  if (ep.board_status === "Done" || ep.current_stage === "export" || ep.pipeline_status === "done") return "export";
  if (ep.board_status === "Review" || ep.current_stage === "review" || ep.pipeline_status === "review") return "review";
  return episodeDisplayStage(ep);
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
  const statusKey = languageStageStatusKey(stage);
  const normalizedStatuses = langStatuses.map((ls) => String(ls?.[statusKey] || "").toLowerCase());
  const done = normalizedStatuses.filter((status) => status === "done" || status === "completed").length;
  const queued = normalizedStatuses.filter((status) => status === "queued").length;
  const running = normalizedStatuses.filter((status) => status === "running" || status === "processing").length;
  const failed = normalizedStatuses.filter((status) => status === "failed" || status === "error").length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  let label = `${done}/${total}`;
  if (running) label += ` (${running} running)`;
  if (queued) label += ` (${queued} queued)`;
  if (failed) label += ` (${failed} failed)`;
  return `
    <div class="lang-progress">
      <div class="lang-progress-bar"><div class="lang-progress-fill" style="width:${pct}%"></div></div>
      <span class="lang-progress-label">${esc(label)}</span>
    </div>`;
}

function queueActionLabel(ep) {
  if ((ep.pipeline_status || "idle") === "paused") return "Resume workflow";
  if ((ep.pipeline_status || "idle") === "failed") return "Resume workflow";
  if ((ep.pipeline_status || "idle") === "review") return "Run again";
  if ((ep.pipeline_status || "idle") === "done") return "Run again";
  return "Start workflow";
}

function readinessMessages(readiness, limit = 99) {
  return (readiness?.blockers || []).slice(0, limit).map((item) => workflowTerminology(item.message)).filter(Boolean);
}

function queueActionPendingLabel(ep) {
  if ((ep.pipeline_status || "idle") === "paused") return "Resuming workflow";
  if ((ep.pipeline_status || "idle") === "failed") return "Resuming workflow";
  if ((ep.pipeline_status || "idle") === "review") return "Starting workflow again";
  if ((ep.pipeline_status || "idle") === "done") return "Starting workflow again";
  return "Starting workflow";
}

function queueActionPendingMessage(ep, startStage) {
  const startCopy = stageLabel(startStage);
  if ((ep.pipeline_status || "idle") === "paused") return `Resuming the workflow from ${startCopy}.`;
  if ((ep.pipeline_status || "idle") === "failed") return `Resuming the workflow from ${startCopy}.`;
  if ((ep.pipeline_status || "idle") === "review") return `Starting the workflow again from ${startCopy}.`;
  if ((ep.pipeline_status || "idle") === "done") return `Starting the workflow again from ${startCopy}.`;
  return `Starting the workflow from ${startCopy}.`;
}

function queueActionSuccessMessage(ep, startStage) {
  const startCopy = stageLabel(startStage);
  if ((ep.pipeline_status || "idle") === "paused") return `Workflow resumed. Waiting for ${startCopy}.`;
  if ((ep.pipeline_status || "idle") === "failed") return `Workflow resumed. Waiting for ${startCopy}.`;
  if ((ep.pipeline_status || "idle") === "review") return `Workflow started again. Waiting for ${startCopy}.`;
  if ((ep.pipeline_status || "idle") === "done") return `Workflow started again. Waiting for ${startCopy}.`;
  return `Workflow started. Waiting for ${startCopy}.`;
}

function queueActionReadyTitle(ep) {
  const label = queueActionLabel(ep);
  if (label === "Resume workflow") return "Ready to resume";
  if (label === "Run again") return "Ready to run again";
  return "Ready to start";
}

function queueActionImmediateFailureMessage(ep, fallbackStage) {
  const failedStage = stageLabel(ep?.current_stage || fallbackStage || ep?.queued_from_stage || "workflow");
  return `Workflow failed in ${failedStage}.`;
}

function reconcileEpisodeWorkflowActionStates() {
  let raisedFailureNotice = false;
  workflowActionEpisodes().forEach((episode) => {
    const actionState = episodeWorkflowActionState(episode.id);
    if (!actionState) return;
    const status = episode.pipeline_status || "idle";
    if (status === "failed" && episode.last_error) {
      const failureMessage = queueActionImmediateFailureMessage(episode, episode.current_stage || episode.queued_from_stage);
      if (
        actionState.pending ||
        actionState.tone !== "error" ||
        actionState.message !== failureMessage
      ) {
        setEpisodeWorkflowActionState(episode.id, {
          ...actionState,
          pending: false,
          tone: "error",
          message: failureMessage,
        });
      }
      if (
        !raisedFailureNotice &&
        (
          state.notice?.tone !== "error" ||
          state.notice?.text !== episode.last_error
        )
      ) {
        setNotice(episode.last_error, "error");
        raisedFailureNotice = true;
      }
      return;
    }
    if (status === "paused") {
      const pausedMessage = `Workflow paused. Ready to resume from ${stageLabel(episodeQueueStartStage(episode))}.`;
      if (actionState.tone !== "warn" || actionState.message !== pausedMessage || actionState.pending) {
        setEpisodeWorkflowActionState(episode.id, {
          ...actionState,
          pending: false,
          tone: "warn",
          message: pausedMessage,
        });
      }
      return;
    }
    if (episode.pause_requested && isWorkflowActiveStatus(status)) {
      const pauseMessage = pauseRequestedCopy(episode);
      if (actionState.tone !== "warn" || actionState.message !== pauseMessage) {
        setEpisodeWorkflowActionState(episode.id, {
          ...actionState,
          pending: false,
          tone: "warn",
          message: pauseMessage,
        });
      }
      return;
    }
    if (actionState.pending && !["queued", "running", "paused_for_tts"].includes(status)) {
      setEpisodeWorkflowActionState(episode.id, {
        ...actionState,
        pending: false,
      });
    }
  });
}

function queueActionStatusTooltip(ep) {
  const status = ep.pipeline_status || "idle";
  const activeStage = stageLabel(episodeDisplayStage(ep) || ep.queued_from_stage || "consistency_guide");
  if (status === "queued") return `Workflow queued. Waiting for ${activeStage} to start.`;
  if (status === "running") return `Workflow running in ${activeStage}.`;
  if (status === "paused") return `Workflow paused before ${activeStage}.`;
  if (status === "paused_for_tts") return episodeWorkflowStatusCopy(ep) || "Waiting for TTS jobs to finish.";
  return queueActionLabel(ep);
}

function queueActionMeta(ep, readiness = ep.queue_readiness || { ok: true, blockers: [], warnings: [] }) {
  const actionState = episodeWorkflowActionState(ep.id);
  const status = ep.pipeline_status || "idle";
  const startStage = episodeQueueStartStage(ep);
  const readyIcon = ["failed", "paused", "review", "done"].includes(status) ? "rerun" : "play";
  if (actionState?.pending) {
    const isPauseAction = actionState.intent === "pause";
    return {
      label: isPauseAction ? "Pause requested" : queueActionPendingLabel(ep),
      tooltip: actionState.message || (isPauseAction ? pauseRequestedCopy(ep) : queueActionPendingMessage(ep, startStage)),
      icon: "refresh",
      iconClass: "button-icon-spin",
      disabled: true,
      startStage,
    };
  }
  if (ep.pause_requested && isWorkflowActiveStatus(status)) {
    return {
      label: "Pause requested",
      tooltip: pauseRequestedCopy(ep),
      icon: "refresh",
      iconClass: "button-icon-spin",
      disabled: true,
      startStage,
    };
  }
  if (status === "queued" || status === "running" || status === "paused_for_tts") {
    return {
      label: status === "running" ? "Workflow running" : status === "paused_for_tts" ? "Narration running" : "Workflow queued",
      tooltip: queueActionStatusTooltip(ep),
      icon: "refresh",
      iconClass: ["running", "paused_for_tts"].includes(status) ? "button-icon-spin" : "",
      disabled: true,
      startStage,
    };
  }
  if (readiness?.ok === false) {
    return {
      label: queueActionLabel(ep),
      tooltip: readinessMessages(readiness, 1)[0] || "Fix the workflow blockers before starting.",
      icon: readyIcon,
      iconClass: "",
      disabled: true,
      startStage,
    };
  }
  return {
    label: queueActionLabel(ep),
    tooltip: queueActionLabel(ep),
    icon: readyIcon,
    iconClass: "",
    disabled: false,
    startStage,
  };
}

function renderIconOnlyActionButton({
  icon,
  label,
  tooltip = label,
  toneClass = "button-ghost",
  className = "",
  dataAttributes = {},
  disabled = false,
  iconClass = "",
}) {
  const classes = ["button", toneClass, "icon-only"];
  if (className) classes.push(className);
  const attrMarkup = disabled
    ? "disabled"
    : Object.entries(dataAttributes)
      .filter(([, value]) => value !== null && value !== undefined && value !== "")
      .map(([name, value]) => `data-${name}="${esc(value)}"`)
      .join(" ");
  return `
    <span class="tooltip-anchor button-tooltip-shell" data-tooltip="${esc(tooltip)}" title="${esc(tooltip)}">
      <button type="button" class="${classes.join(" ")}" ${attrMarkup} aria-label="${esc(label)}">${iconContent(icon, label, { iconOnly: true, iconClass })}</button>
    </span>
  `;
}

function renderEpisodeWorkflowActionButton(ep, { className = "", readiness = ep.queue_readiness || { ok: true, blockers: [], warnings: [] } } = {}) {
  const meta = queueActionMeta(ep, readiness);
  return renderIconOnlyActionButton({
    icon: meta.icon,
    label: meta.label,
    tooltip: meta.tooltip,
    toneClass: "button-primary",
    className: `episode-action-button episode-workflow-action ${className}`.trim(),
    dataAttributes: {
      "queue-episode": ep.id,
      stage: meta.startStage,
    },
    disabled: meta.disabled,
    iconClass: meta.iconClass,
  });
}

function renderEpisodeDeleteActionButton(episodeId, { className = "" } = {}) {
  return renderIconOnlyActionButton({
    icon: "delete",
    label: "Delete episode",
    toneClass: "button-danger",
    className: `episode-action-button ${className}`.trim(),
    dataAttributes: { "delete-episode": episodeId },
  });
}

function renderEpisodeWorkflowNotice(episodeId) {
  const actionState = episodeWorkflowActionState(episodeId);
  if (!actionState?.message) return "";
  return `<div class="notice episode-workflow-feedback" data-tone="${esc(actionState.tone || "success")}">${esc(actionState.message)}</div>`;
}

function workflowStageSelectId(episodeId, surface = "detail") {
  return domSafeId("workflow-stage", episodeId, surface);
}

function selectedWorkflowStage(episode) {
  const episodeId = episode?.id;
  if (episodeId && state.episodeWorkflowStageSelections?.[episodeId]) {
    return state.episodeWorkflowStageSelections[episodeId];
  }
  return episodeQueueStartStage(episode);
}

function workflowStageOptions(selectedStage) {
  return EPISODE_RUNNABLE_STAGE_IDS
    .map((stageId) => `<option value="${esc(stageId)}" ${stageId === selectedStage ? "selected" : ""}>${esc(stageLabel(stageId))}</option>`)
    .join("");
}

function workflowResumeButtonLabel(episode) {
  const status = String(episode?.pipeline_status || "idle").toLowerCase();
  if (status === "paused" || status === "failed") return "Resume from stop";
  if (status === "review" || status === "done") return "Run again";
  return "Start workflow";
}

function workflowSelectedStepButtonLabel(episode) {
  const status = String(episode?.pipeline_status || "idle").toLowerCase();
  if (status === "draft" || status === "idle") return "Start selected step";
  return "Run selected step";
}

function renderEpisodeWorkflowControlPanel(
  episode,
  {
    surface = "detail",
    readiness = episode.queue_readiness || { ok: true, blockers: [], warnings: [] },
  } = {},
) {
  const status = String(episode?.pipeline_status || "idle").toLowerCase();
  const selectedStage = selectedWorkflowStage(episode);
  const selectId = workflowStageSelectId(episode.id, surface);
  const activeWorkflow = isWorkflowActiveStatus(status);
  const tone = episode.pause_requested || activeWorkflow ? "warn" : readiness?.ok === false ? "error" : "success";
  const startDisabled = activeWorkflow || readiness?.ok === false;
  const selectedStepDisabled = activeWorkflow || readiness?.ok === false;
  const pauseButton = activeWorkflow
    ? `<button
        type="button"
        class="button button-ghost button-small"
        data-pause-episode="${esc(episode.id)}"
        ${episode.pause_requested ? "disabled" : ""}
      >${esc(episode.pause_requested ? "Pause requested" : "Pause after current step")}</button>`
    : "";
  const helperCopy = activeWorkflow
    ? (episode.pause_requested
      ? pauseRequestedCopy(episode)
      : status === "paused_for_tts"
        ? "Narration is still running. A pause request will land before Alignment."
        : "Pause stops at the next safe stage boundary so you can resume or rerun from a specific step.")
    : "Selected step reruns that step and everything after it. Use it when you want to redo Translation, TTS, Consistency Guide, or a later stage.";

  return `
    <section class="project-readiness-panel workflow-control-panel" data-tone="${esc(tone)}">
      <div class="project-readiness-head">
        <span class="badge" data-tone="${esc(tone)}">Controls</span>
        <strong>Workflow control</strong>
      </div>
      <div class="workflow-control-grid">
        <label class="field workflow-stage-field">
          <span class="field-label">Run from step</span>
          <select id="${esc(selectId)}" data-workflow-stage-select="${esc(episode.id)}">${workflowStageOptions(selectedStage)}</select>
        </label>
        <div class="workflow-control-actions">
          <button
            type="button"
            class="button button-primary button-small"
            data-queue-episode="${esc(episode.id)}"
            data-stage="${esc(selectedStage)}"
            ${startDisabled ? "disabled" : ""}
          >${esc(workflowResumeButtonLabel(episode))}</button>
          <button
            type="button"
            class="button button-ghost button-small"
            data-queue-episode="${esc(episode.id)}"
            data-stage-select="${esc(selectId)}"
            data-reset-outputs="true"
            ${selectedStepDisabled ? "disabled" : ""}
          >${esc(workflowSelectedStepButtonLabel(episode))}</button>
          ${pauseButton}
        </div>
      </div>
      <div class="helper workflow-control-helper">${esc(helperCopy)}</div>
    </section>
  `;
}

function renderReadinessNotice(readiness, { limit = 2, compact = false } = {}) {
  const blockers = readinessMessages(readiness, limit);
  if (!blockers.length) return "";
  return `<div class="queue-readiness ${compact ? "queue-readiness-compact" : ""}">${blockers.map((message) => `<div class="queue-readiness-item">${esc(message)}</div>`).join("")}</div>`;
}

function renderReadinessWarning(readiness) {
  const warning = readiness?.warnings?.[0];
  if (!warning?.message) return "";
  return `<div class="queue-warning">${esc(warning.message)}</div>`;
}

function renderEpisodeCard(ep, options = {}) {
  const showProjectLabel = Boolean(options.showProjectLabel);
  const currentStage = episodeDisplayStage(ep);
  const isPerLang = EPISODE_PER_LANG_STAGES.includes(currentStage);
  const progress = isPerLang ? langProgressHtml(ep.language_statuses, currentStage) : "";
  const activeTtsProgress = renderActiveTtsProgress(ep);
  const tone = pipelineTone(ep.pipeline_status);
  const error = ep.last_error ? `<div class="episode-card-error" title="${esc(ep.last_error)}">${esc(summarizeCardIssue(ep.last_error, 80))}</div>` : "";
  const nicheLabel = (ep.niche_project_title && showProjectLabel) ? `<span class="episode-card-niche">${esc(ep.niche_project_title)}</span>` : "";
  const langCount = (ep.configured_languages || []).length;
  const isRunning = ep.pipeline_status === "running";
  const queueReadiness = ep.queue_readiness || { ok: true, blockers: [], warnings: [] };
  const elapsedHtml = isRunning && ep.updated_at
    ? `<span class="running-elapsed episode-elapsed" data-started-at="${esc(ep.updated_at)}">${esc(relativeTime(ep.updated_at))}</span>`
    : "";
  const queueButton = renderEpisodeWorkflowActionButton(ep, { className: "button-tiny episode-card-action", readiness: queueReadiness });
  const workflowStatus = renderEpisodeWorkflowStatus(ep);

  return `
    <div class="episode-card surface" data-open-episode="${esc(ep.id)}" data-project-id="${esc(ep.niche_project_id || options.projectId || "")}">
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
      ${workflowStatus}
      ${progress}
      ${activeTtsProgress}
      ${error}
      ${renderReadinessNotice(queueReadiness, { compact: true })}
      ${renderReadinessWarning(queueReadiness)}
      <div class="episode-card-footer">
        <span class="helper" style="font-size:0.7rem;opacity:0.5;">${esc(relativeTime(ep.updated_at))}</span>
        <div class="episode-quick-actions">
          ${queueButton}
          ${renderEpisodeDeleteActionButton(ep.id, { className: "button-tiny episode-card-action" })}
        </div>
      </div>
    </div>`;
}

function renderPipelineBoard() {
  const episodes = state.boardEpisodes || [];

  $("view").innerHTML = `
    <section class="surface board-surface">
      <div class="section-head" style="margin-bottom: 12px; justify-content: flex-end;">
        <div class="badge-row">
          ${statusBadge('Total episodes: ' + episodes.length, "active")}
        </div>
        <div class="button-row">
          <button type="button" class="button button-primary has-icon" data-nav="niche-projects">${iconContent("settings", "Niche Projects")}</button>
        </div>
      </div>
      <div id="pipeline-board" class="kanban-board pipeline-board" style="margin-top:0px;">
        ${EPISODE_PIPELINE_COLUMNS.map((col) => {
          const colEpisodes = episodes.filter((ep) => episodeColumnForCard(ep) === col.id);
          const cards = colEpisodes.map((ep) => renderEpisodeCard(ep, { showProjectLabel: true })).join("");
          const empty = !cards ? '<div class="kanban-empty">No episodes at this step.</div>' : "";
          return '<section class="kanban-column">' +
            '<div class="kanban-column-head">' +
              renderColumnHeading(col) +
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
            <div class="niche-language-picker-head">
              <span class="field-label">Target languages</span>
              <span class="tiny">Master language is included automatically.</span>
            </div>
            <div class="niche-language-picker">
              <div class="niche-language-search-row">
                <input
                  id="niche-lang-search"
                  list="niche-lang-options"
                  placeholder="Type a language or code"
                  autocomplete="off"
                />
                <datalist id="niche-lang-options"></datalist>
                <button type="button" class="button button-ghost button-inline has-icon" data-add-niche-language="true">
                  ${iconContent("add", "Add")}
                </button>
              </div>
              <div class="helper">Pick a language from the suggestions and it will be added to the list below.</div>
              <div id="niche-target-language-list" class="niche-language-pill-list"></div>
            </div>
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
          <h2>Create episode</h2>
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
          <div class="helper">This creates a Draft card only. Start it explicitly from the board when the project is ready.</div>
          <div class="button-row" style="margin-top:18px;">
            <button type="submit" class="button button-primary has-icon">${iconContent("add", "Create episode")}</button>
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
  if (statuses.some((s) => s === "queued")) return "warn";
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
      <div class="notice" style="margin-top:12px;">Model fields accept manual ids, so newer CLI models do not have to wait for a dashboard update. OpenAI stage models refresh from the shared key in Settings.</div>
      <div style="margin-top:10px;">
        <label class="field" style="max-width:200px;">
          <span class="field-label">Leading video scenes</span>
          <input id="niche-leading-video" type="number" min="1" value="${project.leading_video_scene_count || 20}" />
        </label>
      </div>
    </div>
  `;
}

function workflowReadinessSectionOptions({ readiness, episode = null } = {}) {
  if (episode) {
    const status = episode.pipeline_status || "idle";
    if (episode.pause_requested && isWorkflowActiveStatus(status)) {
      return {
        title: "Pause requested",
        emptyCopy: pauseRequestedCopy(episode),
        tone: "warn",
        badgeLabel: "Pausing",
      };
    }
    if (status === "queued") {
      return {
        title: "Workflow in progress",
        emptyCopy: queueActionStatusTooltip(episode),
        tone: "warn",
        badgeLabel: "Queued",
      };
    }
    if (status === "running") {
      return {
        title: "Workflow in progress",
        emptyCopy: queueActionStatusTooltip(episode),
        tone: "active",
        badgeLabel: "Running",
      };
    }
    if (status === "paused_for_tts") {
      return {
        title: "Workflow in progress",
        emptyCopy: episodeWorkflowStatusCopy(episode) || "Waiting for TTS jobs to finish.",
        tone: "warn",
        badgeLabel: "TTS",
      };
    }
    if (status === "paused") {
      return {
        title: "Workflow paused",
        emptyCopy: `Ready to resume from ${stageLabel(episodeQueueStartStage(episode))}.`,
        tone: "warn",
        badgeLabel: "Paused",
      };
    }
    if (readiness?.ok === false) {
      return {
        title: "Workflow blockers",
        tone: "error",
        badgeLabel: "Blocked",
      };
    }
    return {
      title: queueActionReadyTitle(episode),
      emptyCopy: `This episode is ready to ${queueActionLabel(episode).toLowerCase()}.`,
      tone: "success",
      badgeLabel: "Ready",
    };
  }
  if (readiness?.ok === false) {
    return {
      title: "Workflow blockers",
      tone: "error",
      badgeLabel: "Blocked",
    };
  }
  return {
    title: "Ready to start",
    emptyCopy: "Draft episodes are ready to start from the board.",
    tone: "success",
    badgeLabel: "Ready",
  };
}

function renderQueueReadinessSection(readiness, { title = "Workflow readiness", emptyCopy = "Project is ready to start from Draft.", tone = null, badgeLabel = null } = {}) {
  const blockers = readinessMessages(readiness, 99);
  const warning = readiness?.warnings?.[0]?.message;
  const resolvedTone = tone || (readiness?.ok === false ? "error" : "success");
  const resolvedBadgeLabel = badgeLabel || (resolvedTone === "error" ? "Blocked" : "Ready");
  const body = blockers.length
    ? `<div class="queue-readiness-blocks">${blockers.map((message) => `<div class="queue-readiness-row">${esc(message)}</div>`).join("")}</div>`
    : `<div class="helper">${esc(emptyCopy)}</div>`;
  return `
    <div class="project-readiness-panel" data-tone="${esc(resolvedTone)}">
      <div class="project-readiness-head">
        <span class="badge" data-tone="${esc(resolvedTone)}">${esc(resolvedBadgeLabel)}</span>
        <strong>${esc(title)}</strong>
      </div>
      ${body}
      ${warning ? `<div class="queue-warning" style="margin-top:8px;">${esc(warning)}</div>` : ""}
    </div>
  `;
}

function renderProjectWorkflowReadinessSection(readiness) {
  return renderQueueReadinessSection(readiness, workflowReadinessSectionOptions({ readiness }));
}

function renderEpisodeWorkflowReadinessSection(episode, readiness) {
  return renderQueueReadinessSection(readiness, workflowReadinessSectionOptions({ readiness, episode }));
}

function parseStageRunCommandPayload(run) {
  if (!run?.command_json) return null;
  if (typeof run.command_json === "object") return run.command_json;
  try {
    return JSON.parse(run.command_json);
  } catch {
    return null;
  }
}

function stageRunDurationLabel(run) {
  if (!run?.started_at) return "";
  if (run.finished_at) {
    return Math.round((new Date(run.finished_at) - new Date(run.started_at)) / 1000) + "s";
  }
  return "running…";
}

function stageRunPathLabel(path, segments = 4) {
  const normalized = String(path || "").trim().replaceAll("\\", "/");
  if (!normalized) return "";
  const parts = normalized.split("/").filter(Boolean);
  return parts.length > segments ? `.../${parts.slice(-segments).join("/")}` : normalized;
}

function stageRunTimestampMs(value) {
  const timestamp = value ? new Date(value).getTime() : Number.NaN;
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function stageRunLatestPreviewAt(run) {
  const candidates = [run?.stdout_updated_at, run?.stderr_updated_at].filter(Boolean);
  if (!candidates.length) return "";
  return candidates.sort((left, right) => stageRunTimestampMs(right) - stageRunTimestampMs(left))[0];
}

function stageRunByteSizeLabel(bytes) {
  const size = Number(bytes);
  if (!Number.isFinite(size) || size < 0) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) {
    const kb = size / 1024;
    return `${kb >= 10 ? Math.round(kb) : kb.toFixed(1)} KB`;
  }
  const mb = size / (1024 * 1024);
  return `${mb >= 10 ? Math.round(mb) : mb.toFixed(1)} MB`;
}

function stageRunPreviewFileLabel(path, sizeBytes) {
  const pathLabel = stageRunPathLabel(path, 4);
  const sizeLabel = stageRunByteSizeLabel(sizeBytes);
  return sizeLabel ? `${pathLabel} • ${sizeLabel}` : pathLabel;
}

function stageRunPreviewSourceLabel(run) {
  const hasStdout = Boolean(String(run?.stdout_preview || "").trim());
  const hasStderr = Boolean(String(run?.stderr_preview || "").trim());
  const hasStdoutPath = Boolean(run?.stdout_path);
  const hasStderrPath = Boolean(run?.stderr_path);
  if (hasStdout && hasStderr) return "stdout + stderr preview";
  if (hasStdout) return "stdout preview";
  if (hasStderr) return "stderr preview";
  if (hasStdoutPath && hasStderrPath) return "preview files attached";
  if (hasStdoutPath) return "stdout file attached";
  if (hasStderrPath) return "stderr file attached";
  return "run snapshot only";
}

function stageRunPreviewFilesSummary(run) {
  const parts = [];
  if (run?.stdout_path) {
    parts.push(`stdout ${stageRunPreviewFileLabel(run.stdout_path, run.stdout_size_bytes)}`);
  }
  if (run?.stderr_path) {
    parts.push(`stderr ${stageRunPreviewFileLabel(run.stderr_path, run.stderr_size_bytes)}`);
  }
  return parts.join(" • ");
}

function stageRunCapturedOutput(run) {
  const stdout = String(run?.stdout_preview || "").trim();
  const stderr = String(run?.stderr_preview || "").trim();
  if (stdout && stderr) return `$ stderr\n${stderr}\n\n$ stdout\n${stdout}`;
  return stderr || stdout || "";
}

function stageRunSnapshotOutput(run, payload = null) {
  const commandPayload = payload || parseStageRunCommandPayload(run);
  const lines = [];
  lines.push(`stage: ${stageLabel(run?.stage || "workflow")}`);
  lines.push(`status: ${titleCase(run?.status || "unknown")}`);
  if (run?.provider) lines.push(`provider: ${providerLabel(run.provider)}`);
  if (commandPayload?.model) lines.push(`model: ${commandPayload.model}`);
  const workdir = commandPayload?.workdir || run?.workdir;
  if (workdir) lines.push(`workdir: ${stageRunPathLabel(workdir, 5)}`);
  if (commandPayload?.artifact_dir) lines.push(`artifacts: ${stageRunPathLabel(commandPayload.artifact_dir, 5)}`);
  if (Array.isArray(commandPayload?.schema_keys) && commandPayload.schema_keys.length) {
    lines.push(`schema: ${commandPayload.schema_keys.join(", ")}`);
  }
  if (Array.isArray(commandPayload?.command) && commandPayload.command.length) {
    lines.push(`command: ${commandPayload.command.join(" ")}`);
  } else if (run?.status === "running") {
    lines.push("Waiting for provider output. The request is still active.");
  }
  return lines.join("\n");
}

function stageRunTerminalOutput(run, payload = null) {
  return stageRunCapturedOutput(run) || stageRunSnapshotOutput(run, payload);
}

function primaryStageRun(stageRuns) {
  if (!Array.isArray(stageRuns) || !stageRuns.length) return null;
  return stageRuns.find((run) => run.status === "running") || stageRuns[0];
}

function renderStageRunPreviewPanel(label, text, { path = "", sizeBytes = null, updatedAt = "", tone = "default" } = {}) {
  const meta = [];
  if (path) {
    meta.push(`<span title="${esc(path)}">${esc(stageRunPreviewFileLabel(path, sizeBytes))}</span>`);
  }
  if (updatedAt) {
    meta.push(`<span title="${esc(updatedAt)}">${esc(relativeTime(updatedAt))}</span>`);
  }
  return `
    <div class="live-run-stream-card"${tone !== "default" ? ` data-tone="${esc(tone)}"` : ""}>
      <div class="live-run-stream-head">
        <div class="live-run-stream-title">${esc(label)}</div>
        ${meta.length ? `<div class="live-run-stream-meta">${meta.join('<span aria-hidden="true">•</span>')}</div>` : ""}
      </div>
      <pre class="run-output run-output-live">${esc(text)}</pre>
    </div>
  `;
}

function renderStageRunSnapshotPanel(run, payload) {
  const command = Array.isArray(payload?.command) && payload.command.length
    ? payload.command.join(" ")
    : "Command payload not captured yet.";
  const workdir = payload?.workdir || run?.workdir;
  const facts = [
    { label: "Command", value: command, title: command },
    { label: "Workdir", value: workdir ? stageRunPathLabel(workdir, 5) : "Not provided", title: workdir || "" },
    {
      label: "Artifacts",
      value: payload?.artifact_dir ? stageRunPathLabel(payload.artifact_dir, 5) : "Not provided",
      title: payload?.artifact_dir || "",
    },
    {
      label: "Preview files",
      value: stageRunPreviewFilesSummary(run) || "Waiting for stdout/stderr files",
      title: [run?.stdout_path, run?.stderr_path].filter(Boolean).join(" | "),
    },
  ];
  if (Array.isArray(payload?.schema_keys) && payload.schema_keys.length) {
    facts.push({
      label: "Schema",
      value: payload.schema_keys.join(", "),
      title: payload.schema_keys.join(", "),
    });
  }
  return `
    <div class="live-run-stream-card" data-tone="muted">
      <div class="live-run-stream-head">
        <div class="live-run-stream-title">Execution snapshot</div>
        <div class="live-run-stream-meta">${esc(stageRunPreviewSourceLabel(run))}</div>
      </div>
      <div class="run-hint">No preview lines have been written yet. This confirms what the runner is executing and where output should land.</div>
      <div class="live-run-fact-grid">
        ${facts.map((fact) => `
          <div class="live-run-fact">
            <div class="live-run-fact-label">${esc(fact.label)}</div>
            <div class="live-run-fact-value"${fact.title ? ` title="${esc(fact.title)}"` : ""}>${esc(fact.value)}</div>
          </div>
        `).join("")}
      </div>
      <pre class="run-output run-output-live">${esc(stageRunSnapshotOutput(run, payload))}</pre>
    </div>
  `;
}

function renderStageRunActivityPanel(stageRuns) {
  const run = primaryStageRun(stageRuns);
  if (!run) return "";
  const payload = parseStageRunCommandPayload(run);
  const duration = stageRunDurationLabel(run);
  const isRunning = run.status === "running";
  const stdout = String(run?.stdout_preview || "").trim();
  const stderr = String(run?.stderr_preview || "").trim();
  const hasCapturedOutput = Boolean(stdout || stderr);
  const previewAt = stageRunLatestPreviewAt(run);
  const outputSource = stageRunPreviewSourceLabel(run);
  const previewFilesSummary = stageRunPreviewFilesSummary(run);
  const providerName = run?.provider ? providerLabel(run.provider) : "provider runner";
  const runAgeHtml = run?.started_at
    ? (
      isRunning
        ? `<span class="running-elapsed live-run-metric-time" data-started-at="${esc(run.started_at)}" title="${esc(run.started_at)}">${esc(relativeTime(run.started_at))}</span>`
        : `<span class="live-run-metric-time" title="${esc(run.started_at)}">${esc(relativeTime(run.started_at))}</span>`
    )
    : '<span class="live-run-metric-fallback">Not available</span>';
  const previewAgeHtml = previewAt
    ? (
      isRunning
        ? `<span class="running-elapsed live-run-metric-time" data-started-at="${esc(previewAt)}" title="${esc(previewAt)}">${esc(relativeTime(previewAt))}</span>`
        : `<span class="live-run-metric-time" title="${esc(previewAt)}">${esc(relativeTime(previewAt))}</span>`
    )
    : `<span class="live-run-metric-fallback">${isRunning ? "Waiting for first write" : "No preview captured"}</span>`;
  const previewPanels = [];
  if (stdout) {
    previewPanels.push(renderStageRunPreviewPanel("stdout preview", stdout, {
      path: run.stdout_path,
      sizeBytes: run.stdout_size_bytes,
      updatedAt: run.stdout_updated_at,
    }));
  }
  if (stderr) {
    previewPanels.push(renderStageRunPreviewPanel("stderr preview", stderr, {
      path: run.stderr_path,
      sizeBytes: run.stderr_size_bytes,
      updatedAt: run.stderr_updated_at,
      tone: "error",
    }));
  }
  if (!previewPanels.length) {
    previewPanels.push(renderStageRunSnapshotPanel(run, payload));
  }
  return `
    <div class="board-modal-section live-run-section">
      <div class="section-header" style="margin-bottom:12px;">
        <div class="eyebrow" style="margin:0;">${isRunning ? "Live activity" : "Latest activity"}</div>
        <div class="badge-row" style="margin:0;">
          <span class="badge badge-${toneFromRunStatus(run.status)}">${esc(titleCase(run.status || "unknown"))}</span>
          <span class="badge">${esc(stageLabel(run.stage || "workflow"))}</span>
          ${run.provider ? '<span class="badge badge-small">' + esc(providerLabel(run.provider)) + '</span>' : ""}
          ${payload?.model ? '<span class="badge badge-small">' + esc(payload.model) + '</span>' : ""}
          ${duration ? '<span class="helper live-run-duration">' + esc(duration) + '</span>' : ""}
        </div>
      </div>
      <div class="helper live-run-copy">
        ${esc(
          isRunning
            ? `${stageLabel(run.stage || "workflow")} is running via ${providerName} in the background. This panel mirrors the run preview files instead of opening a separate terminal window.`
            : `${stageLabel(run.stage || "workflow")} ${String(run.status || "completed").toLowerCase()} ${relativeTime(run.finished_at || run.started_at)}.`
        )}
      </div>
      <div class="live-run-metric-grid">
        <div class="live-run-metric-card"${isRunning ? ' data-tone="live"' : ""}>
          <div class="live-run-metric-label">Run age</div>
          <div class="live-run-metric-value-row">
            ${isRunning ? '<span class="running-pulse" aria-hidden="true"></span>' : ""}
            ${runAgeHtml}
          </div>
          <div class="live-run-metric-copy">${esc(duration ? `Current status: ${titleCase(run.status || "unknown")} • ${duration}` : `Current status: ${titleCase(run.status || "unknown")}`)}</div>
        </div>
        <div class="live-run-metric-card">
          <div class="live-run-metric-label">Output source</div>
          <div class="live-run-metric-value">${esc(outputSource)}</div>
          <div class="live-run-metric-copy">${esc(hasCapturedOutput ? "Preview text is coming from stdout/stderr artifact files." : "No preview text yet. You are seeing execution metadata and output targets.")}</div>
        </div>
        <div class="live-run-metric-card">
          <div class="live-run-metric-label">Last preview write</div>
          <div class="live-run-metric-value-row">${previewAgeHtml}</div>
          <div class="live-run-metric-copy">${esc(previewFilesSummary || "Preview files will appear here once the provider starts writing.")}</div>
        </div>
      </div>
      <div class="live-run-output-stack">
        ${previewPanels.join("")}
      </div>
    </div>
  `;
}

function renderStageRunCards(stageRuns, { limit = 30 } = {}) {
  if (!Array.isArray(stageRuns) || !stageRuns.length) {
    return '<p class="helper">No stage runs recorded yet.</p>';
  }
  return stageRuns.slice(0, limit).map((run) => {
    const payload = parseStageRunCommandPayload(run);
    const duration = stageRunDurationLabel(run);
    const hasCapturedOutput = Boolean(stageRunCapturedOutput(run));
    const shouldOpen = run.status === "running" || run.status === "failed";
    return `<details class="run-detail-card"${shouldOpen ? " open" : ""}>
      <summary>
        <div class="run-detail-head">
          <span class="badge badge-${toneFromRunStatus(run.status)}">${esc(run.stage || "?")}</span>
          <span class="badge">${esc(run.status || "?")}</span>
          ${run.provider ? '<span class="badge badge-small">' + esc(providerLabel(run.provider)) + '</span>' : ''}
          ${payload?.model ? '<span class="badge badge-small">' + esc(payload.model) + '</span>' : ''}
          ${run.language_code ? '<span class="badge badge-small">' + esc(run.language_code) + '</span>' : ''}
          ${duration ? '<span class="helper" style="font-size:0.75rem;">' + esc(duration) + '</span>' : ''}
          <span class="helper" style="font-size:0.75rem;margin-left:auto;">${esc(relativeTime(run.started_at))}</span>
        </div>
      </summary>
      <div class="run-detail-body">
        ${run.error_message ? '<div class="notice" data-tone="error" style="margin-top:6px;font-size:0.8rem;">' + esc(run.error_message) + '</div>' : ''}
        ${!hasCapturedOutput ? '<div class="run-meta" style="margin-top:6px;">Showing the run snapshot because no CLI output preview is available yet.</div>' : ''}
        <pre class="run-output">${esc(stageRunTerminalOutput(run, payload))}</pre>
      </div>
    </details>`;
  }).join("");
}

function renderColumnHeading(col) {
  const label = esc(col.label);
  if (!col.copy) {
    return `<div class="kanban-column-heading"><div class="kanban-column-title">${label}</div></div>`;
  }
  return `
    <div class="kanban-column-heading">
      <div class="kanban-column-title tooltip-anchor" tabindex="0" data-tooltip="${esc(col.copy)}" aria-label="${esc(`${col.label}. ${col.copy}`)}">${label}</div>
    </div>
  `;
}

function renderProjectBoardKanban(project, episodes) {
  return `
    <div id="pipeline-board" class="kanban-board project-kanban-board">
      ${EPISODE_PIPELINE_COLUMNS.map((col) => {
        const colEpisodes = episodes.filter((ep) => episodeColumnForCard(ep) === col.id);
        const cards = colEpisodes.map((ep) => renderEpisodeCard(ep, { projectId: project.id })).join("");
        const empty = !cards ? '<div class="kanban-empty">No episodes at this step.</div>' : "";
        const draftAction = col.id === "draft"
          ? `<button type="button" class="button button-ghost icon-only project-column-action tooltip-anchor" data-open-submit-episode="${esc(project.id)}" data-tooltip="Add episode" aria-label="Add episode">${iconContent("add", "Add episode", { iconOnly: true })}</button>`
          : "";
        return `
          <section class="kanban-column project-kanban-column">
            <div class="kanban-column-head">
              ${renderColumnHeading(col)}
              <div class="project-column-tools">
                ${draftAction}
                ${statusBadge(String(colEpisodes.length), episodeColumnTone(col.id, episodes))}
              </div>
            </div>
            <div class="kanban-card-list">${cards}${empty}</div>
          </section>
        `;
      }).join("")}
    </div>
  `;
}

function renderNicheProjectDetail() {
  const detail = state.nicheProjectDetail;
  if (!detail) {
    $("view").innerHTML = state.isLoadingRoute
      ? renderLoadingSurface("Loading project board", "Pulling episodes, workflow readiness, and language setup for this project.")
      : '<div class="surface" style="padding:2rem;"><p class="helper">Niche project not found.</p></div>';
    return;
  }
  const project = detail.project;
  const episodes = detail.episodes || [];
  const stats = detail.statistics || {};
  const byStatus = stats.by_status || {};
  const draftCount = byStatus.idle || 0;
  const failedCount = byStatus.failed || 0;
  const projectReadiness = project.queue_readiness || { ok: true, blockers: [], warnings: [] };
  const queueBlocked = projectReadiness.ok === false;
  const batchDisabled = queueBlocked ? "disabled" : "";
  const disclosurePanels = ensureProjectConfigDisclosures(project.id);

  $("view").innerHTML = `
    <section class="surface project-board-shell">
      <div class="section-head project-board-head">
        <div>
          <div class="eyebrow">Project Board</div>
          <h2 class="project-board-title">${esc(project.title || project.id)}</h2>
          <div class="badge-row" style="margin-top:10px;">
            <span class="badge">Master: ${esc(project.master_language || "en")}</span>
            <span class="badge">${episodes.length} episode${episodes.length === 1 ? "" : "s"}</span>
          </div>
        </div>
        <div class="button-row">
          <button type="button" class="button button-ghost has-icon" data-nav="niche-projects">${iconContent("back", "Back to projects")}</button>
          <button type="button" class="button button-primary has-icon" data-open-submit-episode="${esc(project.id)}">${iconContent("add", "Create episode")}</button>
        </div>
      </div>

      ${renderProjectStats(detail)}
      ${renderProjectWorkflowReadinessSection(projectReadiness)}

      <div class="project-board-toolbar">
        ${draftCount > 0 ? '<button type="button" class="button button-ghost button-small has-icon" data-batch-queue-drafts="' + esc(project.id) + '" ' + batchDisabled + '>' + iconContent("play", "Start all drafts (" + draftCount + ")") + '</button>' : ''}
        ${failedCount > 0 ? '<button type="button" class="button button-ghost button-small has-icon" data-batch-queue-failed="' + esc(project.id) + '" ' + batchDisabled + '>' + iconContent("rerun", "Restart failed (" + failedCount + ")") + '</button>' : ''}
        ${queueBlocked ? '<span class="helper">Fix the blockers above before starting or restarting episode workflows.</span>' : '<span class="helper">Draft cards stay idle until you start the workflow explicitly.</span>'}
      </div>

      ${renderProjectBoardKanban(project, episodes)}
    </section>

    <section class="project-config-grid">
      ${renderProjectConfigDisclosure({
        projectId: project.id,
        panelName: "language",
        label: "Language setup",
        body: renderLanguageConfigSection(project, detail.voice_profiles || [], detail.translation_profiles || []),
        isOpen: disclosurePanels.language,
      })}
      ${renderProjectConfigDisclosure({
        projectId: project.id,
        panelName: "provider",
        label: "Provider setup",
        body: renderProviderConfigSection(project),
        isOpen: disclosurePanels.provider,
      })}
    </section>

    ${state.modal.kind === "submit-episode" ? renderSubmitEpisodeModal() : ""}
    ${renderEpisodeDetailOverlay()}
    ${state.modal.kind === "translation-preview" ? renderTranslationPreviewModal() : ""}
  `;

  // Sync provider/model selects after render
  syncProviderModelSelect("niche-scene_planning-provider", "niche-scene_planning-model");
  syncProviderModelSelect("niche-visual_bible-provider", "niche-visual_bible-model");
  syncProviderModelSelect("niche-video_prompt-provider", "niche-video_prompt-model");
  syncProviderModelSelect("niche-image_prompt-provider", "niche-image_prompt-model");
  if (state.episodeOverlayId && state.episodeDetail?.episode?.id === state.episodeOverlayId) {
    ensureEpisodeSupplementalDataLoaded(state.episodeOverlayId);
  }
}

function renderEpisodeDetailOverlay() {
  const detail = state.episodeDetail;
  if (!state.episodeOverlayId) return "";
  if (!detail || detail.episode?.id !== state.episodeOverlayId) {
    if (!state.isLoadingRoute) return "";
    return `
      <div class="board-modal-backdrop" data-episode-overlay-backdrop="true">
        <div class="board-modal-shell">
          <div class="board-modal-head">
            <div>
              <h2 style="margin:0;">Loading episode</h2>
              <div class="helper" style="margin-top:10px;">Fetching pipeline status, stage runs, and language progress.</div>
            </div>
            <button type="button" class="modal-close-button" data-close-episode-overlay="true">${iconContent("close", "Close", { iconOnly: true })}</button>
          </div>
          <div class="board-modal-main">
            ${renderLoadingSurface("Opening overlay", "The board stays in place while the episode data refreshes in the background.")}
          </div>
        </div>
      </div>
    `;
  }
  const episode = detail.episode;
  const langStatuses = detail.language_statuses || [];
  const stageRuns = detail.stage_runs || [];
  const currentStage = episode.current_stage || "draft";
  const readiness = episode.queue_readiness || { ok: true, blockers: [], warnings: [] };
  const liveTtsJob = activeTtsJob(episode);
  const activeTtsMarkup = renderActiveTtsProgress(episode);
  const allStages = EPISODE_PIPELINE_COLUMNS.filter((c) => c.id !== "needs_attention");
  const currentIdx = allStages.findIndex((c) => c.id === currentStage);
  const progressPct = allStages.length ? Math.max(0, Math.round(((currentIdx < 0 ? 0 : currentIdx + (episode.pipeline_status === "review" || episode.pipeline_status === "done" ? 1 : 0)) / allStages.length) * 100)) : 0;
  const queueActionButton = renderEpisodeWorkflowActionButton(episode, { className: "episode-detail-action", readiness });
  const deleteActionButton = renderEpisodeDeleteActionButton(episode.id, { className: "episode-detail-action" });
  const stageStrip = allStages.map((s, i) => {
    let st = "pending";
    if (episode.pipeline_status === "done") st = "done";
    else if (i < currentIdx) st = "done";
    else if (i === currentIdx && episode.pipeline_status === "running") st = "active";
    else if (i === currentIdx && episode.pipeline_status === "paused_for_tts" && liveTtsJob?.worker_active) st = "active";
    else if (i === currentIdx && (episode.pipeline_status === "review" || episode.pipeline_status === "done")) st = "done";
    else if (i === currentIdx && episode.pipeline_status === "failed") st = "failed";
    return '<div class="stage-strip-item" data-state="' + st + '" title="' + esc(s.label) + '">' + esc(s.short) + '</div>';
  }).join("");
  const workerIssueMarkup = renderWorkerHealthBanner(detail.worker_health || {}, {
    forceVisible: episode.pipeline_status === "paused_for_tts",
  });
  const langRows = langStatuses.map((ls) => {
    const canRetryTranslation = !["running", "queued", "paused_for_tts"].includes(episode.pipeline_status || "idle") && (ls.translation_status === "failed" || ls.translation_status === "skipped");
    const canRetryTts = !["running", "queued", "paused_for_tts"].includes(episode.pipeline_status || "idle") && (ls.tts_status === "failed" || ls.tts_status === "skipped");
    const hasTranslation = ls.translation_status === "done" && ls.language_code !== (episode.master_language || "en");
    const ttsProgressLabel = languageTtsJobCopy(ls);
    const ttsProgress = ttsProgressLabel ? ' <span class="helper" style="font-size:0.7rem;">(' + esc(ttsProgressLabel) + ')</span>' : '';
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
  const liveRunHtml = renderStageRunActivityPanel(stageRuns);
  const runsHtml = renderStageRunCards(stageRuns);

  return `
    <div class="board-modal-backdrop" data-episode-overlay-backdrop="true">
      <div class="board-modal-shell">
        <div class="board-modal-head">
          <div>
            <h2 style="margin:0;">${esc(episode.title || episode.id)}</h2>
            <div class="badge-row" style="margin-top:10px;">
              <span class="badge badge-${pipelineTone(episode.pipeline_status)}">${esc(titleCase(episode.pipeline_status || "idle"))}</span>
              <span class="badge">${esc(EPISODE_STAGE_LABELS[currentStage] || titleCase(currentStage))}</span>
              <span class="badge">Master: ${esc(episode.master_language || "en")}</span>
              ${langStatuses.length ? '<span class="badge badge-small">' + langStatuses.length + ' lang' + (langStatuses.length > 1 ? 's' : '') + '</span>' : ''}
            </div>
          </div>
          <button type="button" class="modal-close-button" data-close-episode-overlay="true">${iconContent("close", "Close", { iconOnly: true })}</button>
        </div>
        <div class="board-modal-layout">
          <div class="board-modal-main">
            ${renderEpisodeWorkflowNotice(episode.id)}
            ${episode.last_error ? '<details class="notice-error-details"><summary class="notice notice-error-summary" data-tone="error">Error details</summary><div class="notice" data-tone="error">' + esc(episode.last_error) + '</div></details>' : ""}
            ${renderEpisodeWorkflowReadinessSection(episode, readiness)}
            ${renderEpisodeWorkflowControlPanel(episode, { surface: "overlay", readiness })}
            <div class="board-modal-section">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
                <div class="eyebrow" style="margin:0;">Pipeline progress ${progressPct}%</div>
                <div class="episode-detail-actions">
                  ${queueActionButton}
                  ${deleteActionButton}
                </div>
              </div>
              <div class="pipeline-progress-bar" style="margin-top:12px;">
                <div class="pipeline-progress-fill" style="width:${progressPct}%"></div>
              </div>
              <div class="stage-strip" style="margin-top:12px;">${stageStrip}</div>
            </div>
            ${liveRunHtml}
            <div class="board-modal-section">
              <div class="section-header" style="margin-bottom:12px;">
                <div class="eyebrow" style="margin:0;">Stage runs</div>
              </div>
              ${runsHtml}
            </div>
            <div class="board-modal-section">
              <div class="section-header" style="margin-bottom:12px;">
                <div class="eyebrow" style="margin:0;">Per-language status</div>
              </div>
              ${activeTtsMarkup}
              ${workerIssueMarkup}
              <table class="lang-table">
                <thead><tr><th>Lang</th><th>Translation</th><th>TTS</th><th>SRT</th><th>Timeline</th><th>Error</th></tr></thead>
                <tbody>${langRows || '<tr><td colspan="6" class="helper">No language data.</td></tr>'}</tbody>
              </table>
            </div>
            <div id="episode-review-section" class="board-modal-section"></div>
            <div class="board-modal-section">
              <div id="episode-files-shell">${renderEpisodeFilesSection(episode.id)}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

// ── Episode detail view ───────────────────────────────────────────

function renderEpisodeDetail() {
  const detail = state.episodeDetail;
  if (!detail) {
    $("view").innerHTML = state.isLoadingRoute
      ? renderLoadingSurface("Loading episode", "Fetching pipeline state, per-language progress, and stage runs.")
      : '<div class="surface" style="padding:2rem;"><p class="helper">Episode not found.</p></div>';
    return;
  }
  const episode = detail.episode;
  const langStatuses = detail.language_statuses || [];
  const stageRuns = detail.stage_runs || [];
  const currentStage = episode.current_stage || "draft";
  const readiness = episode.queue_readiness || { ok: true, blockers: [], warnings: [] };
  const liveTtsJob = activeTtsJob(episode);
  const activeTtsMarkup = renderActiveTtsProgress(episode);

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
    else if (i === currentIdx && episode.pipeline_status === "paused_for_tts" && liveTtsJob?.worker_active) st = "active";
    else if (i === currentIdx && (episode.pipeline_status === "review" || episode.pipeline_status === "done")) st = "done";
    else if (i === currentIdx && episode.pipeline_status === "failed") st = "failed";
    return '<div class="stage-strip-item" data-state="' + st + '" title="' + esc(s.label) + '">' + esc(s.short) + '</div>';
  }).join("");

  // Worker health
  const wh = detail.worker_health || {};
  const workerIssueMarkup = renderWorkerHealthBanner(wh, {
    forceVisible: episode.pipeline_status === "paused_for_tts",
  });

  // Per-language table
  const isPipelineIdle = !["running", "queued", "paused_for_tts"].includes(episode.pipeline_status || "idle");
  const langRows = langStatuses.map((ls) => {
    const canRetryTranslation = isPipelineIdle && (ls.translation_status === "failed" || ls.translation_status === "skipped");
    const canRetryTts = isPipelineIdle && (ls.tts_status === "failed" || ls.tts_status === "skipped");
    const hasTranslation = ls.translation_status === "done" && ls.language_code !== (episode.master_language || "en");
    const ttsProgressLabel = languageTtsJobCopy(ls);
    const ttsProgress = ttsProgressLabel ? ' <span class="helper" style="font-size:0.7rem;">(' + esc(ttsProgressLabel) + ')</span>' : '';

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
  const liveRunHtml = renderStageRunActivityPanel(stageRuns);
  const runsHtml = renderStageRunCards(stageRuns);

  const isRunning = episode.pipeline_status === "running";
  const queueActionButton = renderEpisodeWorkflowActionButton(episode, { className: "episode-detail-action", readiness });
  const deleteActionButton = renderEpisodeDeleteActionButton(episode.id, { className: "episode-detail-action" });

  // Output files section
  const filesSection = `
    <div class="detail-section">
      <div id="episode-files-shell">${renderEpisodeFilesSection(episode.id)}</div>
    </div>`;

  const reviewSection = '<div id="episode-review-section" class="detail-section"></div>';

  $("view").innerHTML = `
    <div class="episode-detail-layout" style="display:grid; gap:24px; padding:8px 0;">
      <div>
        <h2 style="margin:0; font-size:1.6rem;">${esc(episode.title || episode.id)}</h2>
        <div class="badge-row" style="margin-top:10px;">
          <span class="badge badge-${pipelineTone(episode.pipeline_status)}">${esc(titleCase(episode.pipeline_status || "idle"))}</span>
          <span class="badge">${esc(EPISODE_STAGE_LABELS[currentStage] || titleCase(currentStage))}</span>
          <span class="badge">Master: ${esc(episode.master_language || "en")}</span>
          ${langStatuses.length ? '<span class="badge badge-small">' + langStatuses.length + ' lang' + (langStatuses.length > 1 ? 's' : '') + '</span>' : ''}
          ${isRunning ? '<span class="running-elapsed" data-started-at="' + esc(episode.updated_at) + '">…</span>' : ''}
        </div>
        ${episode.last_error ? '<div class="notice" data-tone="error" style="margin-top:12px;">' + esc(episode.last_error) + '</div>' : ""}
        ${renderEpisodeWorkflowNotice(episode.id)}
      </div>

      ${renderEpisodeWorkflowReadinessSection(episode, readiness)}
      ${renderEpisodeWorkflowControlPanel(episode, { surface: "page", readiness })}

      <div class="surface" style="padding:16px;">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <div class="eyebrow">Pipeline progress — ${progressPct}%</div>
          <div class="episode-detail-actions">
            ${queueActionButton}
            ${deleteActionButton}
          </div>
        </div>
        <div class="pipeline-progress-bar" style="margin-top:12px;">
          <div class="pipeline-progress-fill" style="width:${progressPct}%"></div>
        </div>
        <div class="stage-strip" style="margin-top:12px;">${stageStrip}</div>
      </div>

      ${liveRunHtml}

      <div class="surface" style="padding:16px;">
        <div class="section-header" style="margin-bottom:12px;">
          <div class="eyebrow" style="margin:0;">Per-language status</div>
        </div>
        ${activeTtsMarkup}
        ${workerIssueMarkup}
        ${langTable}
      </div>

      ${reviewSection}

      ${filesSection.replace('class="detail-section"', 'class="surface" style="padding:16px;"')}

      <div class="surface" style="padding:16px;">
        <div class="eyebrow" style="margin-bottom:12px;">Stage runs (${stageRuns.length})</div>
        ${runsHtml}
      </div>

      <div>
        <button type="button" class="button button-ghost has-icon" data-open-niche-project="${esc(episode.niche_project_id || "")}">${iconContent("back", "Back to project")}</button>
      </div>
    </div>
    ${state.modal.kind === "translation-preview" ? renderTranslationPreviewModal() : ""}
  `;

  // Auto-load files
  ensureEpisodeSupplementalDataLoaded(episode.id);
}

function renderEpisodeFilePreviewBody(episodeId, selectedFile, previewState) {
  if (!selectedFile) {
    return '<p class="helper">Pick a file on the left to inspect its contents.</p>';
  }
  if (previewState?.loading) {
    return '<p class="helper">Loading file preview…</p>';
  }
  if (previewState?.error) {
    return '<div class="profile-inline-message" data-tone="error">' + esc(previewState.error) + '</div>';
  }

  const preview = previewState?.data;
  if (!preview) {
    return '<p class="helper">Preview will appear here as soon as the file metadata finishes syncing.</p>';
  }
  if (preview.preview_type === "audio") {
    return `
      <div class="episode-file-preview-empty">
        <audio class="profile-audio-player episode-file-audio-player" controls src="${esc(episodeFileDownloadUrl(episodeId, selectedFile.relative_path))}"></audio>
        <p class="helper">Audio preview ready. Download keeps the original file name.</p>
      </div>
    `;
  }
  if (preview.preview_type === "empty") {
    return '<div class="episode-file-preview-empty">' + esc(preview.summary || "This file exists but does not contain data yet.") + '</div>';
  }
  if (preview.preview_type === "binary") {
    return '<div class="episode-file-preview-empty">' + esc(preview.summary || "Preview is not available for this file type.") + '</div>';
  }
  return `
    ${preview.truncated ? '<div class="helper" style="margin-bottom:8px;">Preview truncated for performance.</div>' : ""}
    <pre class="run-output episode-file-preview-output">${esc(preview.text || "")}</pre>
  `;
}

function renderEpisodeFilesSection(episodeId) {
  const fileState = episodeFilesState(episodeId);
  const files = fileState.items || [];
  const selectedPath = files.some((file) => file.relative_path === fileState.selectedPath)
    ? fileState.selectedPath
    : pickEpisodeFileSelection(files);
  const selectedFile = files.find((file) => file.relative_path === selectedPath) || null;
  const previewState = selectedFile ? fileState.previewByPath?.[selectedFile.relative_path] || null : null;
  const populatedCount = files.filter((file) => !file.is_empty).length;
  const emptyCount = files.filter((file) => file.is_empty).length;
  const syncCopy = fileState.syncedAt
    ? `Auto-sync on. Last scan ${relativeTime(fileState.syncedAt)}.`
    : "Auto-sync on. Waiting for the first file scan.";

  const listMarkup = files.map((file) => {
    const isSelected = file.relative_path === selectedPath;
    const freshnessCopy = file.modified_at ? `Updated ${relativeTime(file.modified_at)}` : "Timestamp unavailable";
    const pathCopy = file.directory ? file.relative_path : file.name;
    return `
      <button
        type="button"
        class="episode-file-row"
        data-selected="${isSelected ? "true" : "false"}"
        data-open-episode-file="${esc(file.relative_path)}"
        data-episode-file-episode="${esc(episodeId)}"
        title="${esc(file.relative_path)}"
      >
        <span class="episode-file-row-icon">${iconMarkup(episodeFileIcon(file))}</span>
        <span class="episode-file-row-copy">
          <span class="episode-file-row-head">
            <span class="episode-file-row-name">${esc(file.name)}</span>
            <span class="badge-row">
              ${statusBadge(episodeFileTypeLabel(file), episodeFileBadgeTone(file))}
            </span>
          </span>
          <span class="episode-file-row-meta">${esc(file.parent_label)} • ${esc(formatBytes(file.size))} • ${esc(freshnessCopy)}</span>
          ${file.directory ? '<span class="episode-file-row-path">' + esc(pathCopy) + '</span>' : ""}
        </span>
      </button>
    `;
  }).join("");

  return `
    <section class="episode-files-shell">
      <div class="episode-files-header">
        <div>
          <div class="eyebrow">Output files</div>
          <p class="episode-files-copy">${esc(syncCopy)}</p>
        </div>
        <div class="badge-row">
          ${statusBadge(`${files.length} file${files.length === 1 ? "" : "s"}`, files.length ? "active" : "warn")}
          ${files.length ? statusBadge(`${populatedCount} with data`, populatedCount ? "success" : "warn") : ""}
          ${emptyCount ? statusBadge(`${emptyCount} empty`, "warn") : ""}
        </div>
      </div>
      ${fileState.error ? '<div class="profile-inline-message" data-tone="error">' + esc(fileState.error) + '</div>' : ""}
      ${!files.length ? `
        <div class="episode-file-preview-empty">
          No workflow files yet. This panel updates automatically while the episode is open.
        </div>
      ` : `
        <div class="episode-files-layout">
          <div class="episode-files-list" role="listbox" aria-label="Episode output files" data-episode-id="${esc(episodeId)}">
            ${listMarkup}
          </div>
          <div class="episode-file-preview-card">
            ${selectedFile ? `
              <div class="episode-file-preview-head">
                <div>
                  <div class="eyebrow">Preview</div>
                  <div class="episode-file-preview-title">${esc(selectedFile.name)}</div>
                  <div class="helper">${esc(selectedFile.relative_path)} • ${esc(formatBytes(selectedFile.size))} • ${esc(formatDate(selectedFile.modified_at))}</div>
                </div>
                <button
                  type="button"
                  class="button button-ghost button-small has-icon"
                  data-download-episode-file="${esc(selectedFile.relative_path)}"
                  data-episode-file-episode="${esc(episodeId)}"
                >${iconContent("download", "Download")}</button>
              </div>
            ` : ""}
            ${renderEpisodeFilePreviewBody(episodeId, selectedFile, previewState)}
          </div>
        </div>
      `}
    </section>
  `;
}

function syncEpisodeFilesSection(episodeId) {
  const container = $("episode-files-shell");
  if (!container) return;
  const currentList = container.querySelector(".episode-files-list[data-episode-id]");
  if (currentList?.dataset.scrollReady === "true") {
    setEpisodeFilesListScrollTop(episodeId, currentList.scrollTop || 0);
  }
  container.innerHTML = renderEpisodeFilesSection(episodeId);
  restoreEpisodeFilesListScroll(container);
}

async function ensureEpisodeFilePreviewLoaded(episodeId, relativePath) {
  if (!episodeId || !relativePath) return;
  const fileState = episodeFilesState(episodeId);
  const file = (fileState.items || []).find((entry) => entry.relative_path === relativePath);
  if (!file) return;

  const signature = episodeFileSignature(file);
  const currentPreview = fileState.previewByPath?.[relativePath];
  if (currentPreview?.loading) return;
  if (currentPreview?.fileSignature === signature && currentPreview?.data) return;

  updateEpisodeFilesState(episodeId, (current) => ({
    ...current,
    previewByPath: {
      ...current.previewByPath,
      [relativePath]: {
        ...(current.previewByPath?.[relativePath] || {}),
        loading: true,
        error: "",
        data: null,
        fileSignature: signature,
      },
    },
  }));
  syncEpisodeFilesSection(episodeId);

  try {
    const preview = await api(episodeFilePreviewUrl(episodeId, relativePath));
    updateEpisodeFilesState(episodeId, (current) => ({
      ...current,
      previewByPath: {
        ...current.previewByPath,
        [relativePath]: {
          loading: false,
          error: "",
          data: preview,
          fileSignature: signature,
        },
      },
    }));
  } catch (err) {
    updateEpisodeFilesState(episodeId, (current) => ({
      ...current,
      previewByPath: {
        ...current.previewByPath,
        [relativePath]: {
          ...(current.previewByPath?.[relativePath] || {}),
          loading: false,
          error: err.message,
          data: null,
          fileSignature: signature,
        },
      },
    }));
  }
  syncEpisodeFilesSection(episodeId);
}

async function openEpisodeFilePreview(episodeId, relativePath) {
  if (!episodeId || !relativePath) return;
  updateEpisodeFilesState(episodeId, (current) => ({
    ...current,
    selectedPath: relativePath,
  }));
  syncEpisodeFilesSection(episodeId);
  await ensureEpisodeFilePreviewLoaded(episodeId, relativePath);
}

async function loadEpisodeFiles(episodeId) {
  if (!episodeId) return;
  const initialState = episodeFilesState(episodeId);
  if (initialState.loading) {
    syncEpisodeFilesSection(episodeId);
    return;
  }

  updateEpisodeFilesState(episodeId, (current) => ({
    ...current,
    loading: true,
    error: "",
  }));

  try {
    const data = await api("/api/episodes/" + encodeURIComponent(episodeId) + "/files");
    const files = data.files || [];
    const currentState = episodeFilesState(episodeId);
    const selectedPath = files.some((file) => file.relative_path === currentState.selectedPath)
      ? currentState.selectedPath
      : pickEpisodeFileSelection(files);
    const nextPreviewByPath = { ...(currentState.previewByPath || {}) };
    const nextFileMap = new Map(files.map((file) => [file.relative_path, file]));

    Object.keys(nextPreviewByPath).forEach((relativePath) => {
      const file = nextFileMap.get(relativePath);
      if (!file) {
        delete nextPreviewByPath[relativePath];
        return;
      }
      const cachedPreview = nextPreviewByPath[relativePath];
      if (cachedPreview?.fileSignature && cachedPreview.fileSignature !== episodeFileSignature(file)) {
        delete nextPreviewByPath[relativePath];
      }
    });

    setEpisodeFilesState(episodeId, {
      ...currentState,
      items: files,
      loading: false,
      error: "",
      syncedAt: Date.now(),
      selectedPath,
      previewByPath: nextPreviewByPath,
    });
  } catch (err) {
    updateEpisodeFilesState(episodeId, (current) => ({
      ...current,
      loading: false,
      error: err.message,
      syncedAt: Date.now(),
    }));
  }

  syncEpisodeFilesSection(episodeId);
  const refreshedState = episodeFilesState(episodeId);
  if (refreshedState.selectedPath) {
    ensureEpisodeFilePreviewLoaded(episodeId, refreshedState.selectedPath);
  }
}

async function loadReviewData(episodeId) {
  const container = $("episode-review-section");
  if (!container) return;
  
  const detail = state.episodeDetail;
  if (!detail) return;
  const ep = detail.episode;
  
  container.style.display = "block";
  if (!["review", "export", "done"].includes(ep.pipeline_status)) {
    const stage = stageLabel(ep.current_stage || ep.queued_from_stage || "consistency_guide");
    container.innerHTML = `
      <div class="section-header" style="margin-bottom:12px;">
        <div class="eyebrow">Phase 9: Review & Export</div>
      </div>
      <p class="helper">Review data will appear here after the pipeline reaches the review stage. Currently at: ${esc(stage)}.</p>
    `;
    return;
  }
  
  container.innerHTML = '<p class="helper">Loading review data…</p>';
  try {
    const data = await api("/api/episodes/" + encodeURIComponent(episodeId) + "/review-data");
    
    const guideStr = data.consistency_guide ? JSON.stringify(data.consistency_guide, null, 2) : "{}";
    const timelineStr = data.timeline_draft ? JSON.stringify(data.timeline_draft, null, 2) : "[]";
    const promptStr = data.prompt_list || "";
    
    // Can edit if current stage is review and it hasn't successfully exported yet
    const canEdit = ep.current_stage === "review" && ep.pipeline_status !== "done";
    const readonlyAttr = canEdit ? "" : "readonly";
    
    container.innerHTML = `
      <div class="section-header" style="margin-bottom:12px;">
        <div class="eyebrow">Phase 9: Review & Export</div>
        <div class="button-row">
          ${canEdit ? '<button type="button" class="button button-primary button-small has-icon" data-save-review="' + esc(episodeId) + '">' + iconContent("save", "Save Edits") + '</button>' : ''}
          ${canEdit ? '<button type="button" class="button button-primary button-small has-icon" style="background:var(--success);color:var(--bg)" data-finalize-export="' + esc(episodeId) + '">' + iconContent("finalize", "Finalize & Export") + '</button>' : ''}
          ${ep.pipeline_status === "done" || ep.current_stage === "export" ? '<button type="button" class="button button-primary button-small has-icon" data-download-export="' + esc(episodeId) + '">' + iconContent("download", "Download ZIP") + '</button>' : ''}
        </div>
      </div>
      
      <div class="review-editors-grid">
        <div class="editor-col">
          <div class="eyebrow" style="margin-bottom:6px;">Consistency Guide (JSON)</div>
          <textarea id="review-guide" class="review-textarea" spellcheck="false" ${readonlyAttr}>${esc(guideStr)}</textarea>
        </div>
        <div class="editor-col">
          <div class="eyebrow" style="margin-bottom:6px;">Timeline Editor (JSON)</div>
          <textarea id="review-timeline" class="review-textarea" spellcheck="false" ${readonlyAttr}>${esc(timelineStr)}</textarea>
        </div>
        <div class="editor-col">
          <div class="eyebrow" style="margin-bottom:6px;">Prompt List</div>
          <textarea id="review-prompts" class="review-textarea" spellcheck="false" ${readonlyAttr}>${esc(promptStr)}</textarea>
        </div>
      </div>

      <div style="margin-top:16px;">
        <details class="run-detail-card">
          <summary>
            <div class="run-detail-head"><strong>Per-Language Timelines (Read-Only Preview)</strong> <span class="badge badge-small">${Object.keys(data.per_language_timelines || {}).length}</span></div>
          </summary>
          <div class="run-detail-body" style="max-height: 400px; overflow-y: auto;">
            <pre class="run-output">${esc(JSON.stringify(data.per_language_timelines, null, 2))}</pre>
          </div>
        </details>
      </div>
    `;
    
  } catch (err) {
    container.innerHTML = '<p class="helper" style="color:var(--error);">' + esc(err.message) + '</p>';
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
  const tone = status === "done"
    ? "success"
    : status === "running"
      ? "info"
      : status === "queued"
        ? "warn"
        : status === "failed"
          ? "error"
          : "neutral";
  return '<span class="badge badge-' + tone + '">' + esc(titleCase(status || "pending")) + '</span>';
}

function captureDashboardScroll() {
  const board = $("pipeline-board");
  if (board) {
    state.boardScrollLeft = board.scrollLeft || 0;
  }
  const modalMain = document.querySelector(".board-modal-main");
  if (modalMain) {
    state.modalMainScrollTop = modalMain.scrollTop || 0;
  }
  captureEpisodeFilesListScroll();
}

function restoreDashboardScroll() {
  if (["pipeline-board", "niche-project", "episode"].includes(state.route.view)) {
    const board = $("pipeline-board");
    if (board) {
      board.scrollLeft = state.boardScrollLeft || 0;
      board.addEventListener(
        "scroll",
        () => {
          state.boardScrollLeft = board.scrollLeft || 0;
        },
        { passive: true }
      );
    }
  }

  const modalMain = document.querySelector(".board-modal-main");
  if (modalMain) {
    modalMain.scrollTop = state.modalMainScrollTop || 0;
    modalMain.addEventListener("scroll", () => {
      state.modalMainScrollTop = modalMain.scrollTop || 0;
    }, { passive: true });
  }
  restoreEpisodeFilesListScroll();
}

function renderSettings() {
  const settings = state.settings || {};
  const stageProviderOpenAi = activeStageProviderOpenAi();
  const openaiHealth = state.health?.providers?.openai || {};
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
          <div class="notice" style="margin-top:14px;">CLI model catalogs can drift as local tools update. Use the suggestions when they help, but every stage model field also accepts manual ids.</div>
          <div class="workflow-setup-grid workflow-setup-grid-compact">
            ${renderSetupCard({
              icon: "refresh",
              title: "OpenAI workflow access",
              copy: "Shared key for scene planning, consistency guide, and prompt stages whenever their provider is set to OpenAI API.",
              tone: stageProviderOpenAi.hasSavedApiKey ? "active" : "warn",
              fields: `
                <label class="field">
                  <span class="field-label">OpenAI API key</span>
                  <input
                    id="stage-provider-openai-api-key"
                    type="password"
                    value="${esc(stageProviderOpenAi.apiKeyDraft)}"
                    placeholder="${stageProviderOpenAi.hasSavedApiKey ? "Leave blank to keep the saved key" : "Paste an OpenAI key"}"
                    autocomplete="off"
                    spellcheck="false"
                  />
                </label>
                ${renderStageProviderOpenAiMeta(stageProviderOpenAi)}
                <div class="button-row translation-discovery-actions">
                  <button type="button" class="button has-icon" id="stage-provider-openai-discover-button" data-stage-provider-openai-discover="true">${iconContent("refresh", stageProviderOpenAi.modelCount ? "Refresh models" : "Check key")}</button>
                </div>
                <div id="stage-provider-openai-status">${renderStageProviderOpenAiStatus(stageProviderOpenAi)}</div>
              `,
            })}
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
            <div class="metric-label">Codex (API)</div>
            <div class="metric-value">${esc(openaiHealth.has_api_key ? "Ready" : "Key needed")}</div>
            <div class="metric-copy">Uses the OpenAI API. Shares the same key as the OpenAI provider.</div>
          </article>
          <article class="summary-card">
            <div class="metric-label">Claude CLI</div>
            <div class="metric-value">${esc(state.health?.providers?.claude?.logged_in ? "Ready" : state.health?.providers?.claude?.available ? "Login" : "Missing")}</div>
            <div class="metric-copy">Used by default for planning and world-building steps.</div>
          </article>
          <article class="summary-card">
            <div class="metric-label">OpenAI API</div>
            <div class="metric-value">${esc(openaiHealth.has_api_key ? "Ready" : "Key needed")}</div>
            <div class="metric-copy">${esc(openaiHealth.has_api_key ? `${openaiHealth.model_count || 0} cached stage model option${(openaiHealth.model_count || 0) === 1 ? "" : "s"}.` : "Shared key for workflow stages is not saved yet.")}</div>
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
  if (view === "pipeline-board") renderNicheProjects();
  else if (view === "niche-projects") renderNicheProjects();
  else if (view === "niche-project") renderNicheProjectDetail();
  else if (view === "episode" && state.nicheProjectDetail) renderNicheProjectDetail();
  else if (view === "episode") renderEpisodeDetail();

  else if (view === "voice-profiles") renderVoiceProfiles();
  else if (view === "translation-profiles") renderTranslationProfiles();

  else if (view === "settings") renderSettings();
  else if (view === "templates") renderTemplates();

  else renderNicheProjects();
  document.body.classList.toggle("modal-open", Boolean(state.modal.kind) || Boolean(state.episodeOverlayId));
  if (state.modal.kind === "create-niche") syncCreateNicheLanguagePicker();
  syncVoiceProfileAudioAutoplay();
  refreshTranslationProfileEditorDom();
  syncAllProviderModelSelects();
  resetElapsedTimer();
  restoreDashboardScroll();
}

async function refreshData({ preserveNotice = true, routeLoading = false, force = false } = {}) {
  const generation = ++refreshGeneration;
  const route = { ...state.route };
  const overlayId = state.episodeOverlayId;
  const now = Date.now();
  const shouldFetchHealth = force || !state.health || !cacheIsFresh(state.healthFetchedAt, HEALTH_CACHE_TTL_MS);
  const shouldFetchSettings = force || !state.settings || ["settings", "templates"].includes(route.view) || !cacheIsFresh(state.settingsFetchedAt, SETTINGS_CACHE_TTL_MS);
  const shouldFetchBoardEpisodes = force || route.view === "pipeline-board";
  const shouldFetchTargetLanguages = (
    force ||
    route.view === "niche-project" ||
    route.view === "niche-projects"
  ) && (!state.targetLanguages.length || !cacheIsFresh(state.targetLanguagesFetchedAt, TARGET_LANGUAGES_CACHE_TTL_MS));

  activeRefreshes += 1;
  if (routeLoading) {
    blockingRefreshes += 1;
  }
  state.isRefreshingData = true;
  state.isLoadingRoute = blockingRefreshes > 0;

  try {
    const healthPromise = shouldFetchHealth ? api("/api/health") : Promise.resolve(state.health);
    const settingsPromise = shouldFetchSettings ? api("/api/settings") : Promise.resolve({
      settings: state.settings || {},
      model_catalog: state.modelCatalog || DEFAULT_MODEL_CATALOG,
      templates: state.templates || [],
    });
    const nicheProjectsPromise = api("/api/niche-projects");
    const boardEpisodesPromise = shouldFetchBoardEpisodes
      ? api("/api/board/episodes")
      : Promise.resolve({ episodes: state.boardEpisodes || [] });
    const targetLanguagesPromise = shouldFetchTargetLanguages
      ? api("/api/target-languages")
      : Promise.resolve({ languages: state.targetLanguages || [] });
    const voiceProfilesPromise = route.view === "voice-profiles"
      ? api("/api/voice-profiles")
      : Promise.resolve({ profiles: state.voiceProfiles || [] });
    const workerHealthPromise = route.view === "voice-profiles"
      ? api("/api/worker-health")
      : Promise.resolve(state.workerHealth);
    const translationProfilesPromise = route.view === "translation-profiles"
      ? api("/api/translation-profiles")
      : Promise.resolve({ profiles: state.translationProfiles || [] });

    const episodeTargetId = route.view === "episode" ? route.episodeId : overlayId;
    const episodeDetailPromise = episodeTargetId
      ? api(`/api/episodes/${encodeURIComponent(episodeTargetId)}`)
      : Promise.resolve(null);
    const projectDetailPromise = route.view === "niche-project" && route.nicheProjectId
      ? api(`/api/niche-projects/${encodeURIComponent(route.nicheProjectId)}`).catch((error) => ({ __error: error }))
      : Promise.resolve(null);

    const [
      health,
      settings,
      nicheProjects,
      boardEpisodes,
      targetLanguages,
      voiceProfiles,
      workerHealth,
      translationProfiles,
      projectDetailResultRaw,
    ] = await Promise.all([
      healthPromise,
      settingsPromise,
      nicheProjectsPromise,
      boardEpisodesPromise,
      targetLanguagesPromise,
      voiceProfilesPromise,
      workerHealthPromise,
      translationProfilesPromise,
      projectDetailPromise,
    ]);

    if (projectDetailResultRaw?.__error) {
      if (!preserveNotice) throw projectDetailResultRaw.__error;
      if (generation === refreshGeneration) {
        setNotice(projectDetailResultRaw.__error.message, "error");
        window.location.hash = routeToHash({ view: "niche-projects" });
      }
      return;
    }

    let episodeDetailResult = null;
    if (episodeTargetId) {
      try {
        episodeDetailResult = await episodeDetailPromise;
      } catch (error) {
        if (route.view === "episode") {
          if (!preserveNotice) throw error;
          if (generation === refreshGeneration) {
            setNotice(error.message, "error");
            window.location.hash = routeToHash({ view: "niche-projects" });
          }
          return;
        }
        if (generation === refreshGeneration) {
          state.episodeOverlayId = null;
          state.episodeDetail = null;
          resetEpisodeSupplementalState(episodeTargetId);
        }
      }
    }

    let activeProjectId = route.view === "niche-project"
      ? route.nicheProjectId
      : episodeDetailResult?.episode?.niche_project_id;
    let nicheProjectDetailResult = projectDetailResultRaw;
    if (!nicheProjectDetailResult && activeProjectId) {
      try {
        nicheProjectDetailResult = await api(`/api/niche-projects/${encodeURIComponent(activeProjectId)}`);
      } catch (error) {
        if (!preserveNotice) throw error;
        if (generation === refreshGeneration) {
          setNotice(error.message, "error");
          window.location.hash = routeToHash({ view: "niche-projects" });
        }
        return;
      }
    }

    if (generation !== refreshGeneration) return;

    if (shouldFetchHealth) {
      state.health = health;
      state.healthFetchedAt = now;
    }
    if (shouldFetchSettings) {
      state.settings = settings.settings || {};
      state.modelCatalog = settings.model_catalog || DEFAULT_MODEL_CATALOG;
      state.templates = settings.templates || [];
      state.stageProviderOpenAi = hydrateStageProviderOpenAiState(state.settings, state.stageProviderOpenAi);
      state.settingsFetchedAt = now;
    }

    state.nicheProjects = nicheProjects.projects || [];
    if (shouldFetchBoardEpisodes) {
      state.boardEpisodes = boardEpisodes.episodes || [];
    }
    if (shouldFetchTargetLanguages) {
      state.targetLanguages = targetLanguages.languages || [];
      state.targetLanguagesFetchedAt = now;
    }

    if (route.view === "voice-profiles") {
      state.voiceProfiles = voiceProfiles.profiles || [];
      state.workerHealth = workerHealth || null;
    }
    if (route.view === "translation-profiles") {
      state.translationProfiles = translationProfiles.profiles || [];
    }

    if (episodeTargetId) {
      state.episodeDetail = episodeDetailResult;
    } else {
      state.episodeDetail = null;
    }

    if (activeProjectId) {
      state.nicheProjectDetail = nicheProjectDetailResult;
      if (nicheProjectDetailResult?.voice_profiles) {
        state.voiceProfiles = nicheProjectDetailResult.voice_profiles;
      }
      if (nicheProjectDetailResult?.translation_profiles) {
        state.translationProfiles = nicheProjectDetailResult.translation_profiles;
      }
      rememberLastOpenProject(activeProjectId);
    } else {
      state.nicheProjectDetail = null;
      resetProjectConfigDisclosures();
    }

    reconcileEpisodeWorkflowActionStates();
  } finally {
    activeRefreshes = Math.max(0, activeRefreshes - 1);
    if (routeLoading) {
      blockingRefreshes = Math.max(0, blockingRefreshes - 1);
    }
    state.isRefreshingData = activeRefreshes > 0;
    state.isLoadingRoute = blockingRefreshes > 0;
    syncAutoRefreshInterval();
  }
}

function resetAutoRefresh() {
  if (refreshTimer) window.clearInterval(refreshTimer);
  if (!autoRefreshAllowed(state.route)) return;
  currentRefreshIntervalMs = desiredRefreshIntervalMs();
  refreshTimer = window.setInterval(() => {
    if (
      state.modal.kind ||
      state.isLoadingRoute ||
      state.isRefreshingData ||
      voiceProfileAudioIsPlaying() ||
      projectConfigInteractionIsActive()
    ) return;
    refreshData().then(renderApp).catch(() => {});
  }, currentRefreshIntervalMs);
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
  const nextRoute = parseRoute();
  const nextEpisodeId = nextRoute.view === "episode" ? nextRoute.episodeId : null;
  const nextProjectId = nextRoute.view === "niche-project" ? nextRoute.nicheProjectId : null;
  state.route = nextRoute;
  if (nextEpisodeId) {
    if (state.episodeDetail?.episode?.id !== nextEpisodeId) {
      state.episodeDetail = null;
      resetEpisodeSupplementalState();
    }
    state.episodeOverlayId = nextEpisodeId;
  } else if (!["niche-project", "episode"].includes(state.route.view)) {
    state.episodeOverlayId = null;
    state.episodeDetail = null;
    state.translationPreview = null;
    resetEpisodeSupplementalState();
  }
  if (nextProjectId && state.nicheProjectDetail?.project?.id !== nextProjectId) {
    state.nicheProjectDetail = null;
  }
  state.isLoadingRoute = true;
  renderApp();
  await refreshData({ routeLoading: true });
  renderApp();
  resetAutoRefresh();
}

async function openEpisodeOverlay(episodeId, projectId = null) {
  if (state.episodeDetail?.episode?.id !== episodeId) {
    state.episodeDetail = null;
    resetEpisodeSupplementalState();
  }
  state.episodeOverlayId = episodeId;
  if (projectId && state.route.view !== "niche-project") {
    state.isLoadingRoute = true;
    renderApp();
    window.location.hash = routeToHash({ view: "niche-project", nicheProjectId: projectId });
    return;
  }
  state.isLoadingRoute = true;
  renderApp();
  await refreshData({ routeLoading: true });
  renderApp();
  resetAutoRefresh();
}

function closeEpisodeOverlay() {
  const projectId = state.nicheProjectDetail?.project?.id || state.episodeDetail?.episode?.niche_project_id;
  resetEpisodeSupplementalState(state.episodeOverlayId);
  state.episodeOverlayId = null;
  state.episodeDetail = null;
  state.translationPreview = null;
  if (state.route.view === "episode") {
    window.location.hash = routeToHash(projectId ? { view: "niche-project", nicheProjectId: projectId } : { view: "niche-projects" });
    return;
  }
  renderApp();
  resetAutoRefresh();
}

async function triggerEpisodeWorkflowStart(episodeId, { explicitStage = null, resetOutputs = false } = {}) {
  const episode = findEpisodeReference(episodeId);
  const startStage = episodeQueueStartStage(episode, explicitStage);
  const pendingMessage = queueActionPendingMessage(episode || {}, startStage);
  state.episodeWorkflowStageSelections = {
    ...state.episodeWorkflowStageSelections,
    [episodeId]: startStage,
  };
  resetEpisodeSupplementalState(episodeId);
  setEpisodeWorkflowActionState(episodeId, {
    intent: "start",
    pending: true,
    tone: "warn",
    message: pendingMessage,
  });
  if (episode) {
    applyOptimisticEpisodeWorkflowStart(episodeId, startStage);
  }
  renderApp();
  syncAutoRefreshInterval();
  try {
    const result = await api(`/api/episodes/${encodeURIComponent(episodeId)}/queue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start_stage: explicitStage || null,
        reset_outputs: Boolean(resetOutputs),
      }),
    });
    await refreshData();
    const refreshedEpisode = findEpisodeReference(episodeId) || episode || {};
    const immediateFailure = (refreshedEpisode.pipeline_status || "idle") === "failed" && refreshedEpisode.last_error;
    const actionMessage = immediateFailure
      ? queueActionImmediateFailureMessage(refreshedEpisode, result.start_stage || startStage)
      : queueActionSuccessMessage(episode || refreshedEpisode, result.start_stage || startStage);
    const actionTone = immediateFailure ? "error" : "success";
    setEpisodeWorkflowActionState(episodeId, {
      intent: "start",
      pending: false,
      tone: actionTone,
      message: actionMessage,
    });
    renderApp();
    setNotice(immediateFailure ? refreshedEpisode.last_error : actionMessage, actionTone);
  } catch (error) {
    try {
      await refreshData();
    } catch (refreshError) {
      console.error(refreshError);
    }
    setEpisodeWorkflowActionState(episodeId, {
      intent: "start",
      pending: false,
      tone: "error",
      message: error.message || "Could not start the workflow.",
    });
    renderApp();
    setNotice(error.message || "Could not start the workflow.", "error");
  }
}

async function requestEpisodeWorkflowPause(episodeId) {
  const episode = findEpisodeReference(episodeId);
  const pendingMessage = pauseRequestedCopy(episode || {});
  setEpisodeWorkflowActionState(episodeId, {
    intent: "pause",
    pending: true,
    tone: "warn",
    message: pendingMessage,
  });
  renderApp();
  syncAutoRefreshInterval();
  try {
    const result = await api(`/api/episodes/${encodeURIComponent(episodeId)}/pause`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    await refreshData();
    const refreshedEpisode = findEpisodeReference(episodeId) || episode || {};
    const finalMessage = refreshedEpisode.pipeline_status === "paused"
      ? `Workflow paused. Ready to resume from ${stageLabel(episodeQueueStartStage(refreshedEpisode))}.`
      : (result.message || pauseRequestedCopy(refreshedEpisode || episode || {}));
    const finalTone = refreshedEpisode.pipeline_status === "paused" ? "warn" : "warn";
    setEpisodeWorkflowActionState(episodeId, {
      intent: "pause",
      pending: false,
      tone: finalTone,
      message: finalMessage,
    });
    renderApp();
    setNotice(finalMessage, finalTone);
  } catch (error) {
    try {
      await refreshData();
    } catch (refreshError) {
      console.error(refreshError);
    }
    setEpisodeWorkflowActionState(episodeId, {
      intent: "pause",
      pending: false,
      tone: "error",
      message: error.message || "Could not pause the workflow.",
    });
    renderApp();
    setNotice(error.message || "Could not pause the workflow.", "error");
  }
}

async function createVoiceProfile(event) {
  event.preventDefault();
  const audioFile = $("vp-audio")?.files?.[0];
  if (!audioFile) throw new Error("Choose a reference audio file.");
  const fd = new FormData();
  fd.append("name", $("vp-name").value.trim());
  fd.append("audio_file", audioFile);
  const created = await api("/api/voice-profiles", { method: "POST", body: fd });
  state.modal = { kind: null };
  await refreshData();
  renderApp();
  if (created.runtime_warning) {
    setNotice(`Voice profile created. ${created.runtime_warning}`, "warn");
    return;
  }
  setNotice("Voice profile created.", "success");
}

function readVoiceProfileTuningForm() {
  return {
    preset: $("voice-tuning-preset")?.value || "natural_stable",
    temperature: Number($("voice-tuning-temperature")?.value),
    top_p: Number($("voice-tuning-top-p")?.value),
    top_k: Number($("voice-tuning-top-k")?.value),
    speed: Number($("voice-tuning-speed")?.value),
    chunk_max_chars: Number($("voice-tuning-chunk-max-chars")?.value),
    silence_gap_seconds: Number($("voice-tuning-silence-gap")?.value),
  };
}

async function saveVoiceProfileTuning(event) {
  event.preventDefault();
  const profileId = $("voice-tuning-profile-id")?.value;
  if (!profileId) throw new Error("No voice profile selected.");

  const profile = (state.voiceProfiles || []).find((item) => item.id === profileId);
  const action = event.submitter?.value || "save";
  if (action === "save-play" && profile && (voiceProfileIsStarting(profile) || voiceProfileIsGenerating(profile))) {
    throw new Error("Wait for the current sample to finish before queueing another test.");
  }

  const payload = {
    tts_config: readVoiceProfileTuningForm(),
  };
  await api(`/api/voice-profiles/${encodeURIComponent(profileId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  state.modal = { kind: null };
  await refreshData();
  renderApp();

  if (action === "save-play") {
    await testVoiceProfile(profileId);
    setNotice("Voice tuning saved. Generating a fresh test sample.", "success");
    return;
  }
  setNotice("Voice tuning saved.", "success");
}

async function testVoiceProfile(profileId) {
  if (!profileId) throw new Error("No profile selected.");
  state.submittingVoiceTestProfileId = profileId;
  renderApp();
  try {
    const submitted = await api(`/api/voice-profiles/${encodeURIComponent(profileId)}/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    state.pendingVoiceTestJobs[profileId] = submitted.job_id;
    delete state.autoPlayedVoiceTestJobs[profileId];
    await refreshData();
  } finally {
    state.submittingVoiceTestProfileId = null;
    renderApp();
  }
}

function openTranslationProfileEditor(profileId = null) {
  const profile = profileId
    ? (state.translationProfiles || []).find((item) => item.id === profileId) || null
    : null;
  state.translationProfileEditor = buildTranslationProfileEditor(profile);
  state.modal = { kind: "translation-profile-editor" };
  renderApp();
  resetAutoRefresh();
  if (profile?.provider === "openai" && profile?.has_api_key) {
    state.translationProfileEditor.discoveryStatus = "Loading models with the saved OpenAI key.";
    refreshTranslationProfileEditorDom();
    discoverTranslationProfileModels().catch((error) => {
      if (state.translationProfileEditor) {
        state.translationProfileEditor.discoveryError = error.message;
        state.translationProfileEditor.discoverySucceeded = false;
        state.translationProfileEditor.isDiscovering = false;
        refreshTranslationProfileEditorDom();
      }
    });
  }
}

function captureTranslationProfileEditorDraft({ invalidateDiscovery = false } = {}) {
  const editor = state.translationProfileEditor;
  if (!editor) return null;
  const nameInput = $("tp-profile-name");
  if (nameInput) editor.name = nameInput.value;
  const apiKeyInput = $("tp-api-key");
  if (apiKeyInput) {
    const previous = editor.apiKeyDraft;
    editor.apiKeyDraft = apiKeyInput.value;
    if (invalidateDiscovery && editor.apiKeyDraft !== previous) {
      editor.discoverySucceeded = false;
      editor.discoveryError = "";
      editor.discoveredModels = [];
      editor.recommendedModel = "";
      editor.selectedModel = "";
      editor.discoveryStatus = editor.apiKeyDraft.trim()
        ? "Check the key to load models for this account."
        : (translationProfileCanReuseSavedKey(editor)
          ? "Saved key available. Load models to edit this profile."
          : "Paste an OpenAI API key and load the model list.");
    }
  }
  const searchInput = $("tp-model-search");
  if (searchInput) editor.modelSearch = searchInput.value;
  const sortSelect = $("tp-model-sort");
  if (sortSelect) editor.sortBy = sortSelect.value;
  return editor;
}

function syncTranslationProfileEditorActionState() {
  const editor = state.translationProfileEditor;
  if (!editor) return;
  const submitButton = $("tp-submit-button");
  if (submitButton) {
    submitButton.disabled = !translationProfileEditorCanSave(editor);
  }
  const discoverButton = $("tp-discover-button");
  if (discoverButton) {
    const canReuseSavedKey = translationProfileCanReuseSavedKey(editor);
    const label = editor.isDiscovering
      ? "Checking..."
      : editor.discoverySucceeded
        ? "Refresh models"
        : (canReuseSavedKey && !String(editor.apiKeyDraft || "").trim() ? "Load models" : "Check key");
    discoverButton.disabled = editor.isDiscovering;
    discoverButton.innerHTML = iconContent("refresh", label);
  }
}

function refreshTranslationProfileEditorDom() {
  if (state.modal.kind !== "translation-profile-editor" || !state.translationProfileEditor) return;
  const statusNode = $("tp-discovery-status");
  if (statusNode) {
    statusNode.innerHTML = renderTranslationDiscoveryStatus(state.translationProfileEditor);
  }
  const controlsNode = $("tp-model-controls");
  if (controlsNode) {
    controlsNode.innerHTML = renderTranslationModelControls(state.translationProfileEditor);
  }
  const modelPanel = $("tp-model-panel");
  if (modelPanel) {
    modelPanel.innerHTML = renderTranslationModelPanel(state.translationProfileEditor);
  }
  syncTranslationProfileEditorActionState();
}

function setTranslationProfileEditorProvider(providerId) {
  const editor = captureTranslationProfileEditorDraft();
  if (!editor) return;
  editor.activeProvider = providerId;
  renderApp();
}

function selectTranslationProfileModel(modelId) {
  const editor = state.translationProfileEditor;
  if (!editor) return;
  editor.selectedModel = modelId;
  refreshTranslationProfileEditorDom();
}

async function discoverTranslationProfileModels() {
  const editor = captureTranslationProfileEditorDraft({ invalidateDiscovery: false });
  if (!editor || editor.activeProvider !== "openai") return;
  const apiKeyDraft = String(editor.apiKeyDraft || "").trim();
  if (!apiKeyDraft && !translationProfileCanReuseSavedKey(editor)) {
    editor.discoveryError = "Paste an OpenAI API key first.";
    editor.discoverySucceeded = false;
    refreshTranslationProfileEditorDom();
    return;
  }
  editor.isDiscovering = true;
  editor.discoveryError = "";
  editor.discoveryStatus = apiKeyDraft
    ? "Checking the pasted OpenAI key."
    : "Checking the saved OpenAI key.";
  refreshTranslationProfileEditorDom();
  try {
    const payload = {};
    if (apiKeyDraft) payload.api_key = apiKeyDraft;
    if (!apiKeyDraft && translationProfileCanReuseSavedKey(editor)) payload.profile_id = editor.profileId;
    const result = await api("/api/translation-profiles/openai/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    editor.discoveredModels = result.models || [];
    editor.recommendedModel = result.recommended_model || "";
    const selectedStillValid = editor.discoveredModels.some((model) => model.id === editor.selectedModel);
    editor.selectedModel = selectedStillValid
      ? editor.selectedModel
      : (editor.recommendedModel || editor.discoveredModels[0]?.id || "");
    editor.discoverySucceeded = editor.discoveredModels.length > 0;
    editor.discoveryStatus = result.from_saved_key
      ? "Models loaded using the saved OpenAI key."
      : "Models loaded using the pasted OpenAI key.";
  } catch (error) {
    editor.discoverySucceeded = false;
    editor.discoveryError = error.message;
    editor.discoveredModels = [];
    editor.recommendedModel = "";
    editor.selectedModel = "";
  } finally {
    editor.isDiscovering = false;
    refreshTranslationProfileEditorDom();
  }
}

async function saveTranslationProfileEditor(event) {
  event.preventDefault();
  const editor = captureTranslationProfileEditorDraft();
  if (!editor) return;
  if (!translationProfileEditorCanSave(editor)) {
    throw new Error(
      editor.activeProvider === "openai"
        ? "Check the OpenAI key, load models, and pick one before saving."
        : "This provider is a placeholder preview and cannot be saved yet.",
    );
  }
  const payload = {
    name: String(editor.name || "").trim(),
    provider: "openai",
    model: String(editor.selectedModel || "").trim(),
  };
  const apiKeyDraft = String(editor.apiKeyDraft || "").trim();
  if (editor.mode === "create") {
    payload.api_key = apiKeyDraft;
    await api("/api/translation-profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setNotice("Translation profile created.", "success");
  } else {
    if (apiKeyDraft) payload.api_key = apiKeyDraft;
    await api(`/api/translation-profiles/${encodeURIComponent(editor.profileId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setNotice("Translation profile updated.", "success");
  }
  state.translationProfileEditor = null;
  state.modal = { kind: null };
  await refreshData({ force: true });
  renderApp();
}

async function createNicheProject(event) {
  event.preventDefault();
  const name = $("niche-name").value.trim();
  const masterLang = $("niche-master-lang").value;
  const langs = [
    masterLang,
    ...readSelectedNicheLanguages().filter((code) => code && code !== masterLang),
  ];

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
  rememberLastOpenProject(result.project.id);
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
  state.modal = { kind: null };
  await refreshData();
  renderApp();
  setNotice("Episode created in Draft. Start the workflow explicitly from the board.", "success");
}

async function saveSettings(event) {
  event.preventDefault();
  const stageProviderOpenAi = captureStageProviderOpenAiDraft();
  const stageProviderOpenAiApiKey = String(stageProviderOpenAi?.apiKeyDraft || "").trim();
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
      stage_provider_openai_api_key: stageProviderOpenAiApiKey || null,
    }),
  });
  if (stageProviderOpenAi) {
    stageProviderOpenAi.apiKeyDraft = "";
    stageProviderOpenAi.discoveryError = "";
    stageProviderOpenAi.isDiscovering = false;
  }
  await refreshData({ force: true });
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
    state.translationProfileEditor = null;
    state.translationPreview = null;
    renderApp();
    resetAutoRefresh();
    return;
  }
  if (event.target.matches("[data-episode-overlay-backdrop]")) {
    closeEpisodeOverlay();
    return;
  }
  const target = event.target.closest("[data-nav], [data-sidebar-toggle], [data-refresh], [data-theme-toggle], [data-prepare-language], [data-close-modal], [data-close-episode-overlay], [data-delete-voice-profile], [data-create-voice-profile], [data-open-voice-tuning], [data-test-voice], [data-create-translation-profile], [data-edit-translation-profile], [data-delete-translation-profile], [data-translation-provider-tab], [data-translation-discover], [data-stage-provider-openai-discover], [data-select-translation-model], [data-open-niche-project], [data-open-create-niche], [data-delete-niche-project], [data-open-episode], [data-open-submit-episode], [data-queue-episode], [data-pause-episode], [data-delete-episode], [data-save-lang-config], [data-save-provider-config], [data-add-language], [data-remove-language], [data-add-niche-language], [data-remove-niche-language], [data-batch-queue-drafts], [data-batch-queue-failed], [data-retry-language], [data-preview-translation], [data-open-episode-file], [data-download-episode-file], [data-save-review], [data-finalize-export], [data-download-export], [data-project-config-toggle]");
  if (!target) return;
  event.preventDefault();
  try {
    if (target.dataset.closeModal) {
      if (state.modal.kind === "translation-preview") state.translationPreview = null;
      state.translationProfileEditor = null;
      state.modal = { kind: null };
      renderApp();
      resetAutoRefresh();
      return;
    }
    if (target.dataset.closeEpisodeOverlay) {
      closeEpisodeOverlay();
      return;
    }
    if (target.dataset.sidebarToggle) {
      const isExpanded = document.body.classList.contains("sidebar-expanded");
      if (isExpanded) {
        document.body.classList.remove("sidebar-expanded");
        window.localStorage.setItem("tool1-sidebar", "collapsed");
      } else {
        document.body.classList.add("sidebar-expanded");
        window.localStorage.setItem("tool1-sidebar", "expanded");
      }
      return;
    }
    if (target.dataset.nav) {
      resetEpisodeSupplementalState(state.episodeOverlayId);
      state.episodeOverlayId = null;
      state.translationPreview = null;
      window.location.hash = routeToHash({ view: target.dataset.nav });
      return;
    }
    if (target.dataset.refresh) {
      await refreshData({ preserveNotice: false, force: true });
      renderApp();
      setNotice("Refreshed.", "success");
      return;
    }
    if (target.dataset.themeToggle) {
      applyTheme(state.theme === "dark" ? "light" : "dark");
      renderApp();
      return;
    }
    if (target.dataset.openEpisodeFile) {
      const episodeId = target.dataset.episodeFileEpisode || state.episodeOverlayId || state.episodeDetail?.episode?.id;
      await openEpisodeFilePreview(episodeId, target.dataset.openEpisodeFile);
      return;
    }
    if (target.dataset.downloadEpisodeFile) {
      const episodeId = target.dataset.episodeFileEpisode || state.episodeOverlayId || state.episodeDetail?.episode?.id;
      if (!episodeId) throw new Error("Episode file is unavailable.");
      window.open(episodeFileDownloadUrl(episodeId, target.dataset.downloadEpisodeFile), "_blank", "noopener");
      return;
    }
    if (target.dataset.projectConfigToggle) {
      const disclosure = target.closest("[data-project-config-disclosure]");
      const isOpen = toggleProjectConfigDisclosure(
        target.dataset.projectId || disclosure?.dataset.projectId || null,
        target.dataset.projectConfigToggle
      );
      syncProjectConfigDisclosureDom(disclosure, isOpen);
      return;
    }
    if (target.dataset.prepareLanguage) {
      await prepareLanguage();
      return;
    }
    if (target.dataset.deleteVoiceProfile) {
      if (!confirm("Delete this voice profile?")) return;
      delete state.pendingVoiceTestJobs[target.dataset.deleteVoiceProfile];
      delete state.autoPlayedVoiceTestJobs[target.dataset.deleteVoiceProfile];
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
    if (target.dataset.openVoiceTuning) {
      state.modal = { kind: "voice-profile-tuning", profileId: target.dataset.openVoiceTuning };
      renderApp();
      resetAutoRefresh();
      return;
    }
    if (target.dataset.testVoice) {
      await testVoiceProfile(target.dataset.testVoice);
      return;
    }
    if (target.dataset.createTranslationProfile) {
      openTranslationProfileEditor();
      return;
    }
    if (target.dataset.editTranslationProfile) {
      openTranslationProfileEditor(target.dataset.editTranslationProfile);
      return;
    }
    if (target.dataset.translationProviderTab) {
      setTranslationProfileEditorProvider(target.dataset.translationProviderTab);
      return;
    }
    if (target.dataset.translationDiscover) {
      await discoverTranslationProfileModels();
      return;
    }
    if (target.dataset.stageProviderOpenaiDiscover) {
      await discoverStageProviderOpenAiModels();
      return;
    }
    if (target.dataset.selectTranslationModel) {
      selectTranslationProfileModel(target.dataset.selectTranslationModel);
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
    if (target.dataset.addNicheLanguage) {
      addCreateNicheLanguage();
      return;
    }
    if (target.dataset.removeNicheLanguage) {
      removeCreateNicheLanguage(target.dataset.removeNicheLanguage);
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
      await openEpisodeOverlay(
        target.dataset.openEpisode,
        target.dataset.projectId || state.nicheProjectDetail?.project?.id || null,
      );
      return;
    }
    if (target.dataset.openSubmitEpisode) {
      state.modal = { kind: "submit-episode", nicheProjectId: target.dataset.openSubmitEpisode };
      renderApp();
      resetAutoRefresh();
      return;
    }
    if (target.dataset.queueEpisode) {
      target.disabled = true;
      const selectedStage = target.dataset.stageSelect
        ? document.getElementById(target.dataset.stageSelect)?.value || target.dataset.stage || null
        : target.dataset.stage || null;
      await triggerEpisodeWorkflowStart(target.dataset.queueEpisode, {
        explicitStage: selectedStage,
        resetOutputs: target.dataset.resetOutputs === "true",
      });
      return;
    }
    if (target.dataset.pauseEpisode) {
      target.disabled = true;
      await requestEpisodeWorkflowPause(target.dataset.pauseEpisode);
      return;
    }
    if (target.dataset.deleteEpisode) {
      if (!confirm("Delete this episode? This cannot be undone.")) return;
      resetEpisodeSupplementalState(target.dataset.deleteEpisode);
      await api('/api/episodes/' + encodeURIComponent(target.dataset.deleteEpisode), { method: "DELETE" });
      if (state.episodeOverlayId === target.dataset.deleteEpisode) {
        state.episodeOverlayId = null;
        state.episodeDetail = null;
      }
      if (state.route.view === "episode") {
        const fallbackProjectId = state.nicheProjectDetail?.project?.id;
        window.location.hash = routeToHash(fallbackProjectId ? { view: "niche-project", nicheProjectId: fallbackProjectId } : { view: "niche-projects" });
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
      setNotice(
        "Started " + (result.queued_count || 0) + " draft workflow(s)." +
        ((result.blocked_count || 0) ? " " + result.blocked_count + " blocked." : ""),
        (result.blocked_count || 0) ? "warn" : "success",
      );
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
      setNotice(
        "Restarted " + (result.queued_count || 0) + " failed workflow(s)." +
        ((result.blocked_count || 0) ? " " + result.blocked_count + " blocked." : ""),
        (result.blocked_count || 0) ? "warn" : "success",
      );
      return;
    }
    if (target.dataset.retryLanguage) {
      const episodeId = target.dataset.retryLanguage;
      const langCode = target.dataset.retryLang;
      const stage = target.dataset.retryStage;
      resetEpisodeSupplementalState(episodeId);
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
    if (target.dataset.saveReview) {
      const episodeId = target.dataset.saveReview;
      const guideRaw = $("review-guide").value;
      const timelineRaw = $("review-timeline").value;
      const prompts = $("review-prompts").value;
      
      let consistency_guide, timeline_draft;
      try { consistency_guide = JSON.parse(guideRaw); } catch (e) { throw new Error("Invalid Consistency Guide JSON"); }
      try { timeline_draft = JSON.parse(timelineRaw); } catch (e) { throw new Error("Invalid Timeline Draft JSON"); }
      
      await api('/api/episodes/' + encodeURIComponent(episodeId) + '/review-data', {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ consistency_guide, timeline_draft, prompt_list: prompts }),
      });
      resetEpisodeSupplementalState(episodeId);
      setNotice("Review data saved successfully.", "success");
      return;
    }
    if (target.dataset.finalizeExport) {
      const episodeId = target.dataset.finalizeExport;
      if (!confirm("Finalize and generate export ZIP?")) return;
      resetEpisodeSupplementalState(episodeId);
      await api('/api/episodes/' + encodeURIComponent(episodeId) + '/finalize-export', { method: "POST" });
      await refreshData();
      renderApp();
      setNotice("Export complete and ready for download.", "success");
      return;
    }
    if (target.dataset.downloadExport) {
      const episodeId = target.dataset.downloadExport;
      window.open('/api/episodes/' + encodeURIComponent(episodeId) + '/export/download', '_blank');
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
    else if (form.id === "voice-profile-tuning-form") await saveVoiceProfileTuning(event);
    else if (form.id === "translation-profile-editor-form") await saveTranslationProfileEditor(event);
    else if (form.id === "create-niche-form") await createNicheProject(event);
    else if (form.id === "submit-episode-form") await submitEpisode(event);
    else if (form.id === "settings-form") await saveSettings(event);
    else if (form.id === "templates-form") await saveTemplate(event);
  } catch (error) {
    event.preventDefault();
    setNotice(error.message, "error");
  }
});

document.addEventListener("input", (event) => {
  if (event.target.id === "tp-profile-name") {
    captureTranslationProfileEditorDraft();
    syncTranslationProfileEditorActionState();
    return;
  }
  if (event.target.id === "tp-api-key") {
    captureTranslationProfileEditorDraft({ invalidateDiscovery: true });
    refreshTranslationProfileEditorDom();
    return;
  }
  if (event.target.id === "tp-model-search") {
    captureTranslationProfileEditorDraft();
    refreshTranslationProfileEditorDom();
    return;
  }
  if (event.target.id === "stage-provider-openai-api-key") {
    captureStageProviderOpenAiDraft();
    refreshStageProviderOpenAiDom();
  }
});

document.addEventListener("change", (event) => {
  if (event.target.dataset.workflowStageSelect) {
    state.episodeWorkflowStageSelections = {
      ...state.episodeWorkflowStageSelections,
      [event.target.dataset.workflowStageSelect]: event.target.value,
    };
    return;
  }
  if (event.target.id === "tp-model-sort") {
    captureTranslationProfileEditorDraft();
    refreshTranslationProfileEditorDom();
    return;
  }
  if (event.target.id === "niche-master-lang") {
    syncCreateNicheLanguagePicker();
    return;
  }
  if (event.target.id === "voice-tuning-preset") {
    applyVoiceTtsPresetToForm(event.target.value);
    return;
  }
  if (event.target.id === "niche-lang-search") {
    addCreateNicheLanguage(event.target.value, { silent: true });
    return;
  }
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
  if (event.key === "Enter" && event.target.id === "niche-lang-search") {
    event.preventDefault();
    addCreateNicheLanguage(event.target.value);
    return;
  }
  if (event.key === "Escape") {
    if (state.modal.kind) {
      if (state.modal.kind === "translation-preview") state.translationPreview = null;
      state.translationProfileEditor = null;
      state.modal = { kind: null };
      renderApp();
      resetAutoRefresh();
      return;
    }
    if (state.episodeOverlayId) {
      closeEpisodeOverlay();
    }
  }
});

window.addEventListener("hashchange", () => {
  syncRouteAndRender().catch((error) => setNotice(error.message, "error"));
});

function bootSidebar() {
  const stored = window.localStorage.getItem("tool1-sidebar");
  if (stored === "expanded") {
    document.body.classList.add("sidebar-expanded");
  } else {
    document.body.classList.remove("sidebar-expanded");
  }
}

window.addEventListener("DOMContentLoaded", () => {
  bootTheme();
  bootSidebar();
  if (!window.location.hash) {
    window.location.hash = routeToHash({ view: "niche-projects" });
    return;
  }
  syncRouteAndRender().catch((error) => setNotice(error.message, "error"));
});
