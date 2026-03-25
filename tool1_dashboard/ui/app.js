const BOARD_STATUSES = ["Draft", "Queued", "Running", "Review", "Done", "Needs Attention"];
const WORKFLOW_COLUMNS = [
  { id: "draft", label: "Draft", short: "Draft", copy: "Job created, waiting to start." },
  { id: "alignment", label: "Precise SRT", short: "SRT", copy: "Audio and script alignment with exact subtitle timing." },
  { id: "planning_prep", label: "Chunking", short: "Chunks", copy: "Preparing planning chunks from the subtitle timeline." },
  { id: "scene_planning", label: "Scene planning", short: "Scenes", copy: "Turning subtitle blocks into scene structure." },
  { id: "visual_bible", label: "Consistency guide", short: "Guide", copy: "Locking characters, places, recurring elements, and continuity." },
  { id: "video_prompt_generation", label: "Video prompts", short: "Video", copy: "Generating prompts for the leading video scenes." },
  { id: "image_prompt_generation", label: "Image prompts", short: "Image", copy: "Generating prompts for the remaining image scenes." },
  { id: "review", label: "Review", short: "Review", copy: "Human review and edits before export." },
  { id: "export", label: "Exported", short: "Export", copy: "Final files created and ready to use." },
  { id: "needs_attention", label: "Needs attention", short: "Attention", copy: "Failed or blocked jobs that need intervention." },
];
const STAGES = [
  "alignment",
  "planning_prep",
  "scene_planning",
  "visual_bible",
  "video_prompt_generation",
  "image_prompt_generation",
];
const STAGE_LABELS = {
  draft: "Draft",
  alignment: "Precise SRT",
  planning_prep: "Chunking",
  scene_planning: "Scene planning",
  visual_bible: "Consistency guide",
  video_prompt_generation: "Video prompts",
  image_prompt_generation: "Image prompts",
  review: "Review",
  export: "Exported",
};
const JOB_TABS = ["overview", "timeline", "bible", "prompts", "runs"];
const JOB_TAB_META = {
  overview: { label: "Overview", icon: "overview" },
  timeline: { label: "Timeline", icon: "timeline" },
  bible: { label: "Guide", icon: "bible" },
  prompts: { label: "Prompts", icon: "prompts" },
  runs: { label: "Runs", icon: "runs" },
};
const MASTER_BUILD_TABS = ["overview", "timeline", "bible", "prompts", "runs"];
const LOC_BUILD_TABS = ["overview", "translation", "tts", "timeline", "runs"];
const LOC_PIPELINE_STAGES = ["translation", "tts", "alignment", "localized_timeline"];
const LOC_STAGE_LABELS = {
  translation: "Translation",
  tts: "TTS / Narration",
  alignment: "Alignment",
  localized_timeline: "Localized Timeline",
};
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
  jobs: [],
  health: null,
  settings: null,
  modelCatalog: DEFAULT_MODEL_CATALOG,
  templates: [],
  detail: null,
  route: { view: "projects" },
  boardFilter: "All",
  promptDraftVideoFull: "",
  promptDraftImageFull: "",
  theme: "dark",
  notice: { text: "", tone: "neutral" },
  modal: { kind: null },
  boardScrollLeft: 0,
  projects: [],
  projectDetail: null,
  buildDetail: null,
  voiceProfiles: [],
  translationProfiles: [],
  targetLanguages: [],
  workerHealth: null,
  nicheProjects: [],
  nicheProjectDetail: null,
  boardEpisodes: [],
  episodeDetail: null,
};

let refreshTimer = null;
let noticeTimer = null;
let elapsedTimer = null;
let liveLogTimer = null;
let liveLogOpen = false;

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
  if (route.view === "job" && route.jobId) {
    return `#/jobs/${encodeURIComponent(route.jobId)}/${route.tab || "overview"}`;
  }
  if (route.view === "project" && route.projectId) {
    return `#/projects/${encodeURIComponent(route.projectId)}`;
  }
  if (route.view === "build" && route.buildId) {
    return `#/builds/${encodeURIComponent(route.buildId)}/${route.tab || "overview"}`;
  }
  if (route.view === "niche-project" && route.nicheProjectId) {
    return `#/niche-projects/${encodeURIComponent(route.nicheProjectId)}`;
  }
  if (route.view === "episode" && route.episodeId) {
    return `#/episodes/${encodeURIComponent(route.episodeId)}`;
  }
  return `#/${route.view || "projects"}`;
}

function parseRoute() {
  const hash = window.location.hash.replace(/^#/, "").replace(/^\/+/, "");
  if (!hash) return { view: "projects" };
  const parts = hash.split("/").filter(Boolean);
  if (parts[0] === "jobs" && parts[1]) {
    const tab = JOB_TABS.includes(parts[2]) ? parts[2] : "overview";
    return { view: "job", jobId: decodeURIComponent(parts[1]), tab };
  }
  if (parts[0] === "projects" && parts[1]) {
    return { view: "project", projectId: decodeURIComponent(parts[1]) };
  }
  if (parts[0] === "projects") {
    return { view: "projects" };
  }
  if (parts[0] === "builds" && parts[1]) {
    const allTabs = [...new Set([...MASTER_BUILD_TABS, ...LOC_BUILD_TABS])];
    const tab = allTabs.includes(parts[2]) ? parts[2] : "overview";
    return { view: "build", buildId: decodeURIComponent(parts[1]), tab };
  }
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
  if (["dashboard", "settings", "templates"].includes(parts[0])) {
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

function stageLabel(stage) {
  return STAGE_LABELS[stage] || titleCase(stage);
}

function countByBoard() {
  const counts = Object.fromEntries(BOARD_STATUSES.map((status) => [status, 0]));
  for (const job of state.jobs) counts[job.board_status] = (counts[job.board_status] || 0) + 1;
  return counts;
}

function workflowColumnForJob(job) {
  if (!job) return "draft";
  if (job.board_status === "Needs Attention" || job.pipeline_status === "failed") return "needs_attention";
  if (job.board_status === "Done" || job.current_stage === "export" || job.pipeline_status === "done") return "export";
  if (job.board_status === "Review" || job.current_stage === "review" || job.pipeline_status === "review") return "review";
  if (WORKFLOW_COLUMNS.some((column) => column.id === job.current_stage)) return job.current_stage;
  return "draft";
}

function countByWorkflow() {
  const counts = Object.fromEntries(WORKFLOW_COLUMNS.map((column) => [column.id, 0]));
  for (const job of state.jobs) {
    const columnId = workflowColumnForJob(job);
    counts[columnId] = (counts[columnId] || 0) + 1;
  }
  return counts;
}

function workflowTone(columnId) {
  if (columnId === "needs_attention") return "error";
  if (columnId === "export") return "success";
  if (columnId === "review") return "active";
  if (columnId === "draft") return "neutral";
  return "warn";
}

function statusLabelForJob(job) {
  if (job.board_status === "Needs Attention") return "Blocked";
  if (job.board_status === "Done") return "Done";
  if (job.board_status === "Review") return "Review";
  if (job.board_status === "Queued") return "Queued";
  if (job.board_status === "Running") return "Running";
  return "Draft";
}

function activeWorkerLabel(job) {
  switch (workflowColumnForJob(job)) {
    case "alignment":
      return "ffmpeg / MFA / WhisperX";
    case "planning_prep":
      return "Local chunker";
    case "scene_planning":
      return providerLabel(job.scene_planning_provider);
    case "visual_bible":
      return providerLabel(job.visual_bible_provider);
    case "video_prompt_generation":
      return providerLabel(job.video_prompt_provider);
    case "image_prompt_generation":
      return providerLabel(job.image_prompt_provider);
    case "review":
      return "Manual review";
    case "export":
      return "Local export";
    case "needs_attention":
      return stageLabel(job.current_stage);
    default:
      return "Not started";
  }
}

function rerunStageForJob(job) {
  if (STAGES.includes(job.current_stage)) return job.current_stage;
  if (job.current_stage === "review" || job.current_stage === "export") return "scene_planning";
  return "alignment";
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

function artifactLink(jobId, key, label) {
  return `<a class="link-button has-icon" href="/api/jobs/${encodeURIComponent(jobId)}/artifacts/${key}" target="_blank" rel="noreferrer">${iconContent("download", label)}</a>`;
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Request failed.");
  return data;
}

async function apiText(url) {
  const response = await fetch(url);
  const text = await response.text();
  if (!response.ok) throw new Error(text || "Request failed.");
  return text;
}

function latestRunMap(runs) {
  const latest = new Map();
  for (const run of runs || []) {
    if (!latest.has(run.stage)) latest.set(run.stage, run);
  }
  return latest;
}

function routeTitle(route) {
  if (route.view === "projects") {
    return {
      title: "Projects",
      copy: "Manage your Creator Studio projects. Each project has a master build and optional localization builds.",
    };
  }
  if (route.view === "project" && state.projectDetail) {
    return {
      title: state.projectDetail.title || "Project",
      copy: `Project workspace — master build and localizations.`,
    };
  }
  if (route.view === "build" && state.buildDetail?.build) {
    const b = state.buildDetail.build;
    const btype = b.build_type === "master" ? "Master" : `Localization (${b.language_code || "?"})`;
    return {
      title: `${btype} build`,
      copy: `Build workspace / ${route.tab || "overview"}`,
    };
  }
  if (route.view === "voice-profiles") {
    return {
      title: "Voice profiles",
      copy: "Manage voice profiles for TTS narration. Upload reference audio and test voice cloning.",
    };
  }
  if (route.view === "translation-profiles") {
    return {
      title: "Translation profiles",
      copy: "Manage translation provider configurations for localization builds.",
    };
  }
  if (route.view === "create") {
    return {
      title: "Create new job",
      copy: "Upload narration and script, choose providers, then create a clean draft card.",
    };
  }
  if (route.view === "settings") {
    return {
      title: "Settings",
      copy: "Global provider defaults, chunking limits, and runtime controls live here instead of on the main dashboard.",
    };
  }
  if (route.view === "templates") {
    return {
      title: "Agent templates",
      copy: "Edit stage instructions separately so prompt logic does not compete with job review space.",
    };
  }
  if (route.view === "job" && state.detail?.job) {
    return {
      title: state.detail.job.title,
      copy: `Workspace for ${state.detail.job.id}. Review, rerun, and export from one focused page.`,
    };
  }
  if (route.view === "dashboard") {
    return {
      title: "Workflow board",
      copy: "Board-first view for diagnosing the pipeline. Create new cards directly in the Draft column, then watch them move step by step across the workflow.",
    };
  }
  return {
    title: "Projects",
    copy: "Manage your Creator Studio projects.",
  };
}

function autoRefreshAllowed(route) {
  if (route.view === "dashboard") return true;
  if (route.view === "projects") return true;
  if (route.view === "project") return true;
  if (route.view === "job" && ["overview", "runs"].includes(route.tab || "overview")) return true;
  if (route.view === "build" && ["overview", "tts", "runs"].includes(route.tab || "overview")) return true;
  if (route.view === "voice-profiles") return true;
  return false;
}

function navIsActive(navView) {
  const v = state.route.view;
  if (navView === "projects" && (v === "projects" || v === "project" || v === "build")) return true;
  if (navView === "pipeline-board" && (v === "pipeline-board" || v === "episode")) return true;
  if (navView === "niche-projects" && (v === "niche-projects" || v === "niche-project")) return true;
  return v === navView;
}

function renderSidebar() {
  const counts = countByBoard();
  const workflowCounts = countByWorkflow();
  const providers = state.health?.providers || {};
  const alignment = state.health?.alignment || {};
  const navItems = [
    { view: "pipeline-board", label: "Pipeline Board", icon: "board", count: state.boardEpisodes.length },
    { view: "niche-projects", label: "Niche Projects", icon: "settings", count: state.nicheProjects.length },
    { view: "voice-profiles", label: "Voice Profiles", icon: "settings", count: state.voiceProfiles.length },
    { view: "translation-profiles", label: "Translation Profiles", icon: "settings", count: state.translationProfiles.length },
    { view: "projects", label: "Projects (legacy)", icon: "board", count: state.projects.length },
    { view: "dashboard", label: "Board (legacy)", icon: "board", count: state.jobs.length },
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
      <div class="eyebrow">Workflow steps</div>
      <div class="badge-row" style="margin-top:10px;">
        ${WORKFLOW_COLUMNS.map((column) => statusBadge(`${column.short}: ${workflowCounts[column.id] || 0}`, workflowTone(column.id))).join("")}
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

function renderJobCard(job) {
  const currentColumn = workflowColumnForJob(job);
  const currentStep = stageLabel(job.current_stage);
  const statusLabel = statusLabelForJob(job);
  const runButtonLabel = currentColumn === "draft" ? "Run" : "Rerun";
  const warningBadge = Number(job.warning_count || 0)
    ? statusBadge(`${job.warning_count} warning${Number(job.warning_count) === 1 ? "" : "s"}`, "warn")
    : "";
  return `
    <article class="kanban-card kanban-card-compact">
      <button type="button" class="job-title-button" data-open-job="${esc(job.id)}">
        <h3>${esc(job.title)}</h3>
      </button>
      <div class="badge-row job-badge-row">
        ${statusBadge(statusLabel, toneFromBoardStatus(job.board_status))}
        ${warningBadge}
      </div>
      <div class="job-meta job-meta-compact">
        <div>Step: ${esc(currentStep)}</div>
        <div>Updated: ${esc(formatDate(job.updated_at))}</div>
        ${job.last_error ? `<div class="job-issue"><span class="job-issue-label">Issue:</span> <span class="job-issue-copy">${esc(summarizeCardIssue(job.last_error, 110))}</span></div>` : ""}
      </div>
      <div class="button-row button-row-compact-actions">
        <button type="button" class="button button-primary icon-only" data-queue-job="${esc(job.id)}" data-stage="${esc(rerunStageForJob(job))}" aria-label="${esc(runButtonLabel)}" title="${esc(runButtonLabel)}">${iconContent(currentColumn === "draft" ? "play" : "rerun", runButtonLabel, { iconOnly: true })}</button>
        <button type="button" class="button button-danger icon-only" data-delete-job="${esc(job.id)}" data-job-title="${esc(job.title)}" aria-label="Delete card" title="Delete card">${iconContent("delete", "Delete card", { iconOnly: true })}</button>
      </div>
    </article>
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
  syncProviderModelSelect("create-scene-planning", "create-scene-model");
  syncProviderModelSelect("create-visual-bible", "create-bible-model");
  syncProviderModelSelect("create-video-prompt", "create-video-model");
  syncProviderModelSelect("create-image-prompt", "create-image-model");
  syncProviderModelSelect("settings-scene", "settings-scene-model");
  syncProviderModelSelect("settings-bible", "settings-bible-model");
  syncProviderModelSelect("settings-video", "settings-video-model");
  syncProviderModelSelect("settings-image", "settings-image-model");
  syncProviderModelSelect("job-scene-provider", "job-scene-model");
  syncProviderModelSelect("job-bible-provider", "job-bible-model");
  syncProviderModelSelect("job-video-provider", "job-video-model");
  syncProviderModelSelect("job-image-provider", "job-image-model");
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

function renderCreateLauncherCard() {
  return `
    <article class="kanban-card kanban-add-card">
      <button type="button" class="button button-ghost add-card-button has-icon" data-open-create-modal="true">
        ${iconContent("add", "Add card")}
      </button>
      <div class="tiny">Open a popup to upload files and set this card's workflow options.</div>
    </article>
  `;
}

function renderCreateModal() {
  const defaults = state.settings || {};
  return `
    <div class="board-modal-backdrop" data-modal-backdrop="true">
      <div class="board-modal-shell" role="dialog" aria-modal="true" aria-labelledby="create-modal-title">
        <div class="board-modal-head">
          <div>
            <div class="eyebrow">Draft card</div>
            <h2 id="create-modal-title" class="section-title">Create a new job</h2>
          </div>
          <button type="button" class="modal-close-button" aria-label="Close" title="Close" data-close-modal="true">${iconContent("close", "Close", { iconOnly: true })}</button>
        </div>
        <form id="create-form" class="board-modal-layout">
          <section class="board-modal-main">
            <div class="board-modal-section">
              <div class="section-head" style="margin-bottom:0;">
                <div>
                  <div class="eyebrow">Files</div>
                  <h3>Core inputs</h3>
                </div>
              </div>
              <div class="helper">This popup handles the full setup. The board card stays compact after creation.</div>
              <div class="form-grid" style="margin-top:16px;">
                <label class="field">
                  <span class="field-label">Video title</span>
                  <input id="create-title" name="title" required />
                </label>
                <label class="field">
                  <span class="field-label">Language</span>
                  <select id="create-language" name="language_code">${languageOptions("en")}</select>
                </label>
                <label class="field">
                  <span class="field-label">Narration audio</span>
                  <input id="create-audio" name="audio_file" type="file" accept=".wav,.mp3,audio/*" required />
                </label>
                <label class="field">
                  <span class="field-label">Script document</span>
                  <input id="create-script" name="script_file" type="file" accept=".txt,.docx" required />
                </label>
              </div>
            </div>

            <div class="board-modal-section">
              <div class="section-head" style="margin-bottom:0;">
                <div>
                  <div class="eyebrow">Settings</div>
                  <h3>Workflow setup</h3>
                </div>
              </div>
              <div class="helper">Each block matches one real workflow step, so you can decide who handles that step without reading the same long label over and over.</div>
              <div class="workflow-setup-grid" style="margin-top:16px;">
                ${renderStageSetupCard({
                  icon: "scene",
                  title: "Scene planning",
                  copy: "Turns aligned subtitles into scene-sized chunks.",
                  providerId: "create-scene-planning",
                  providerValue: defaults.default_scene_planning_provider || "claude",
                  modelId: "create-scene-model",
                  modelValue: defaults.default_scene_planning_model || "haiku",
                })}
                ${renderStageSetupCard({
                  icon: "bible",
                  title: "Consistency guide",
                  copy: "Locks the characters, places, props, and continuity rules.",
                  providerId: "create-visual-bible",
                  providerValue: defaults.default_visual_bible_provider || "claude",
                  modelId: "create-bible-model",
                  modelValue: defaults.default_visual_bible_model || "haiku",
                })}
                ${renderStageSetupCard({
                  icon: "play",
                  title: "Video prompts",
                  copy: "Writes prompts for the leading video scenes.",
                  providerId: "create-video-prompt",
                  providerValue: defaults.default_video_prompt_provider || "codex",
                  modelId: "create-video-model",
                  modelValue: defaults.default_video_prompt_model || "gpt-5.4",
                })}
                ${renderStageSetupCard({
                  icon: "prompts",
                  title: "Image prompts",
                  copy: "Writes prompts for the remaining image scenes.",
                  providerId: "create-image-prompt",
                  providerValue: defaults.default_image_prompt_provider || "codex",
                  modelId: "create-image-model",
                  modelValue: defaults.default_image_prompt_model || "gpt-5.4",
                })}
                ${renderSetupCard({
                  icon: "timeline",
                  title: "Output strategy",
                  copy: "Choose how many opening scenes should default to video before the rest switch to image.",
                  tone: "warn",
                  fields: `
                    <label class="field">
                      <span class="field-label">Video-first scenes</span>
                      <input id="create-leading-video-count" type="number" min="0" value="${esc(defaults.leading_video_scene_count || 20)}" />
                    </label>
                  `,
                })}
              </div>
            </div>

            <div class="button-row board-modal-actions">
              <button type="button" class="button button-ghost has-icon" data-close-modal="true">${iconContent("close", "Cancel")}</button>
              <button type="submit" class="button button-primary has-icon">${iconContent("add", "Create draft")}</button>
            </div>
          </section>

          <aside class="board-modal-side">
            <section class="board-modal-side-card">
              <div class="eyebrow">What happens next</div>
              <div class="stack" style="margin-top:12px;">
                <div class="helper">1. The card is created in <strong>Draft</strong>.</div>
                <div class="helper">2. You can open it, inspect it, or queue it from the board.</div>
                <div class="helper">3. The board stays compact because the setup lives here, not inside the column.</div>
              </div>
            </section>

            <section class="board-modal-side-card">
              <div class="eyebrow">Language prep</div>
              <div class="helper" style="margin-top:12px;">If MFA resources are not ready for the selected language, prepare them before running the card.</div>
              <div class="button-row" style="margin-top:14px;">
                <button type="button" class="button has-icon" data-prepare-language="true">${iconContent("language", "Prepare selected language")}</button>
              </div>
            </section>
          </aside>
        </form>
      </div>
    </div>
  `;
}

/* ── New view stubs (filled in subsequent steps) ────────────────── */

function renderProjects() {
  const projects = state.projects || [];
  const grid = projects.length
    ? projects
        .map((p) => {
          const master = p.master_build;
          const masterStatus = master ? master.pipeline_status || "idle" : "—";
          const masterStage = master ? (STAGE_LABELS[master.current_stage] || master.current_stage || "—") : "—";
          const locCount = p.localization_count || 0;
          const locSummary = (p.localizations_summary || [])
            .map((l) => `<span class="badge badge-small">${esc(l.language_code)} · ${esc(l.pipeline_status || "idle")}</span>`)
            .join(" ");
          return `
          <div class="surface project-card" data-open-project="${esc(p.id)}">
            <div class="project-card-head">
              <h3 class="project-card-title">${esc(p.title || p.id)}</h3>
              <button type="button" class="button button-danger button-small icon-only" data-delete-project="${esc(p.id)}" data-project-title="${esc(p.title)}" aria-label="Delete project" title="Delete project">${iconContent("delete", "Delete", { iconOnly: true })}</button>
            </div>
            <div class="badge-row" style="margin-top:8px;">
              <span class="badge">${esc(p.source_language || "en")}</span>
              <span class="badge badge-${pipelineTone(masterStatus)}">${esc(titleCase(masterStatus))}</span>
            </div>
            <div class="helper" style="margin-top:6px;">Master: ${esc(masterStage)} · ${locCount} localization${locCount !== 1 ? "s" : ""}</div>
            ${locSummary ? `<div class="badge-row" style="margin-top:6px;">${locSummary}</div>` : ""}
            <div class="helper" style="margin-top:6px;font-size:0.75rem;opacity:0.6;">${esc(relativeTime(p.updated_at))}</div>
          </div>`;
        })
        .join("")
    : `<div class="surface" style="padding:2rem;text-align:center;"><p class="helper">No projects yet. Create one to get started.</p></div>`;

  $("view").innerHTML = `
    <div class="projects-header">
      <button type="button" class="button button-primary has-icon" data-open-create-project="true">${iconContent("add", "Create project")}</button>
    </div>
    <div class="projects-grid">${grid}</div>
    ${state.modal.kind === "create-project" ? renderCreateProjectModal() : ""}
  `;
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

function renderCreateProjectModal() {
  const defaults = state.settings || {};
  return `
    <div class="modal-backdrop" data-modal-backdrop="true">
      <div class="modal-panel">
        <div class="modal-header">
          <h2>Create project</h2>
          <button type="button" class="button button-ghost icon-only" data-close-modal="true">${iconContent("close", "Close", { iconOnly: true })}</button>
        </div>
        <form id="create-project-form" class="stack">
          <div class="form-grid">
            <label class="field">
              <span class="field-label">Project title</span>
              <input id="cp-title" name="title" required />
            </label>
            <label class="field">
              <span class="field-label">Source language</span>
              <select id="cp-language" name="source_language">${languageOptions("en")}</select>
            </label>
            <label class="field">
              <span class="field-label">Narration audio</span>
              <input id="cp-audio" name="audio_file" type="file" accept=".wav,.mp3,audio/*" required />
            </label>
            <label class="field">
              <span class="field-label">Script document</span>
              <input id="cp-script" name="script_file" type="file" accept=".txt,.docx" required />
            </label>
          </div>
          <div class="eyebrow" style="margin-top:18px;">Pipeline providers</div>
          <div class="workflow-setup-grid" style="margin-top:8px;">
            ${renderStageSetupCard({
              icon: "scene",
              title: "Scene planning",
              copy: "Turns aligned subtitles into scene-sized chunks.",
              providerId: "cp-scene-provider",
              providerValue: defaults.default_scene_planning_provider || "claude",
              modelId: "cp-scene-model",
              modelValue: defaults.default_scene_planning_model || "haiku",
            })}
            ${renderStageSetupCard({
              icon: "bible",
              title: "Consistency guide",
              copy: "Locks characters, places, props, continuity.",
              providerId: "cp-bible-provider",
              providerValue: defaults.default_visual_bible_provider || "claude",
              modelId: "cp-bible-model",
              modelValue: defaults.default_visual_bible_model || "haiku",
            })}
            ${renderStageSetupCard({
              icon: "play",
              title: "Video prompts",
              copy: "Writes prompts for leading video scenes.",
              providerId: "cp-video-provider",
              providerValue: defaults.default_video_prompt_provider || "codex",
              modelId: "cp-video-model",
              modelValue: defaults.default_video_prompt_model || "gpt-5.4",
            })}
            ${renderStageSetupCard({
              icon: "prompts",
              title: "Image prompts",
              copy: "Writes prompts for remaining image scenes.",
              providerId: "cp-image-provider",
              providerValue: defaults.default_image_prompt_provider || "codex",
              modelId: "cp-image-model",
              modelValue: defaults.default_image_prompt_model || "gpt-5.4",
            })}
            ${renderSetupCard({
              icon: "timeline",
              title: "Output strategy",
              copy: "Video-first scene count.",
              tone: "warn",
              fields: '<label class="field"><span class="field-label">Video-first scenes</span><input id="cp-leading-video" type="number" min="0" value="' + esc(defaults.leading_video_scene_count || 20) + '" /></label>',
            })}
          </div>
          <div class="button-row" style="margin-top:18px;">
            <button type="submit" class="button button-primary has-icon">${iconContent("add", "Create project")}</button>
            <button type="button" class="button button-ghost" data-close-modal="true">Cancel</button>
          </div>
        </form>
      </div>
    </div>
  `;
}

function renderProjectDetail() {
  const detail = state.projectDetail;
  if (!detail) {
    $("view").innerHTML = `<div class="surface" style="padding:2rem;"><p class="helper">Project not found.</p></div>`;
    return;
  }
  const project = detail.project || detail;
  const master = detail.master_build;
  const locs = detail.localizations || [];

  const masterStage = master ? (STAGE_LABELS[master.current_stage] || master.current_stage || "—") : "—";
  const masterPipeline = master ? (master.pipeline_status || "idle") : "idle";

  const masterStages = STAGES;
  const masterCurrentIdx = master ? masterStages.indexOf(master.current_stage) : -1;

  const stageStrip = masterStages
    .map((s, i) => {
      let st = "pending";
      if (masterPipeline === "completed" || masterPipeline === "done") st = "done";
      else if (i < masterCurrentIdx) st = "done";
      else if (i === masterCurrentIdx && masterPipeline === "running") st = "active";
      else if (i === masterCurrentIdx) st = "done";
      return `<div class="stage-strip-item" data-state="${st}">${esc(STAGE_LABELS[s] || s)}</div>`;
    })
    .join("");

  const locCards = locs
    .map((l) => `
      <div class="build-card" data-open-build="${esc(l.id)}">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <strong>${esc(l.language_code || "?")}</strong>
          <button type="button" class="button button-danger button-small icon-only" data-delete-build="${esc(l.id)}" aria-label="Delete" title="Delete build">${iconContent("delete", "Delete", { iconOnly: true })}</button>
        </div>
        <div class="badge-row" style="margin-top:6px;">
          <span class="badge badge-${pipelineTone(l.pipeline_status)}">${esc(titleCase(l.pipeline_status || "idle"))}</span>
          <span class="badge">${esc(l.current_stage || "—")}</span>
        </div>
      </div>
    `)
    .join("");

  $("view").innerHTML = `
    <div class="detail-section">
      <div class="eyebrow">Project</div>
      <h2 style="margin:4px 0 0;">${esc(project.title || project.id)}</h2>
      <div class="badge-row" style="margin-top:8px;">
        <span class="badge">${esc(project.source_language || "en")}</span>
      </div>
    </div>

    <div class="detail-section">
      <div class="eyebrow">Master build</div>
      ${master ? `
        <div class="stage-strip">${stageStrip}</div>
        <div class="badge-row">
          <span class="badge badge-${pipelineTone(masterPipeline)}">${esc(titleCase(masterPipeline))}</span>
          <span class="helper">Current stage: ${esc(masterStage)}</span>
        </div>
        <div class="button-row" style="margin-top:12px;">
          <button type="button" class="button button-primary has-icon" data-queue-build="${esc(master.id)}">${iconContent("play", "Queue / Rerun")}</button>
          <button type="button" class="button has-icon" data-open-build="${esc(master.id)}">${iconContent("overview", "Open build")}</button>
        </div>
      ` : `<p class="helper">No master build found.</p>`}
    </div>

    <div class="detail-section">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div class="eyebrow">Localizations (${locs.length})</div>
        <button type="button" class="button button-primary button-small has-icon" data-open-loc-modal="${esc(project.id)}">${iconContent("add", "Add localization")}</button>
      </div>
      ${locs.length ? `<div class="loc-list" style="margin-top:12px;">${locCards}</div>` : `<p class="helper" style="margin-top:8px;">No localizations yet.</p>`}
    </div>

    <div class="detail-section">
      <button type="button" class="button button-ghost has-icon" data-nav="projects">${iconContent("back", "Back to projects")}</button>
    </div>

    ${state.modal.kind === "add-localization" ? renderAddLocalizationModal() : ""}
  `;
}

function renderAddLocalizationModal() {
  const langs = state.targetLanguages || [];
  const vps = state.voiceProfiles || [];
  const tps = state.translationProfiles || [];

  const langOpts = langs.map((l) => `<option value="${esc(l.code)}">${esc(l.label || l.code)}</option>`).join("");
  const vpOpts = `<option value="">— None —</option>` + vps.map((v) => `<option value="${esc(v.id)}">${esc(v.name)} (${esc(v.language_code)})</option>`).join("");
  const tpOpts = `<option value="">— None —</option>` + tps.map((t) => `<option value="${esc(t.id)}">${esc(t.name)} (${esc(t.provider)})</option>`).join("");

  return `
    <div class="modal-backdrop" data-modal-backdrop="true">
      <div class="modal-panel">
        <div class="modal-header">
          <h2>Add localization</h2>
          <button type="button" class="button button-ghost icon-only" data-close-modal="true">${iconContent("close", "Close", { iconOnly: true })}</button>
        </div>
        <form id="add-localization-form" class="stack">
          <div class="form-grid">
            <label class="field">
              <span class="field-label">Target language</span>
              <select id="loc-language" required>${langOpts}</select>
            </label>
            <label class="field">
              <span class="field-label">Voice profile</span>
              <select id="loc-voice-profile">${vpOpts}</select>
            </label>
            <label class="field">
              <span class="field-label">Translation profile</span>
              <select id="loc-translation-profile">${tpOpts}</select>
            </label>
          </div>
          <div class="button-row" style="margin-top:18px;">
            <button type="submit" class="button button-primary has-icon">${iconContent("add", "Create localization")}</button>
            <button type="button" class="button button-ghost" data-close-modal="true">Cancel</button>
          </div>
        </form>
      </div>
    </div>
  `;
}

function renderBuildDetail() {
  const detail = state.buildDetail;
  if (!detail?.build) {
    $("view").innerHTML = `<div class="surface" style="padding:2rem;"><p class="helper">Build not found.</p></div>`;
    return;
  }
  const build = detail.build;
  const runs = detail.stage_runs || [];
  const tab = state.route.tab || "overview";
  const isMaster = build.build_type === "master";
  const tabs = isMaster ? MASTER_BUILD_TABS : LOC_BUILD_TABS;

  const tabBar = tabs
    .map(
      (t) =>
        `<button type="button" class="build-tab ${t === tab ? "is-active" : ""}" data-build-tab="${t}">${esc(titleCase(t))}</button>`
    )
    .join("");

  let content = "";
  if (tab === "overview") content = renderBuildOverviewTab(build, runs);
  else if (tab === "runs") content = renderBuildRunsTab(runs);
  else if (isMaster && tab === "timeline") content = renderBuildArtifactTab(build, "timeline_path", "Timeline");
  else if (isMaster && tab === "bible") content = renderBuildArtifactTab(build, "visual_bible_path", "Consistency guide");
  else if (isMaster && tab === "prompts") content = renderBuildPromptsTab(build);
  else if (!isMaster && tab === "translation") content = renderBuildTranslationTab(build);
  else if (!isMaster && tab === "tts") content = renderBuildTTSTab(build, detail._ttsJob);
  else if (!isMaster && tab === "timeline") content = renderBuildArtifactTab(build, "timeline_path", "Localized timeline");
  else content = `<div class="surface" style="padding:2rem;"><p class="helper">Tab not implemented yet.</p></div>`;

  $("view").innerHTML = `
    <div class="detail-section">
      <div style="display:flex;align-items:center;gap:12px;">
        <button type="button" class="button button-ghost icon-only" data-nav="projects" aria-label="Back" title="Back to projects">${iconContent("back", "Back", { iconOnly: true })}</button>
        <div>
          <div class="eyebrow">${esc(isMaster ? "Master build" : `Localization — ${build.language_code || "?"}`)}</div>
          <div class="badge-row" style="margin-top:4px;">
            <span class="badge badge-${pipelineTone(build.pipeline_status)}">${esc(titleCase(build.pipeline_status || "idle"))}</span>
            <span class="badge">${esc(build.current_stage || "—")}</span>
          </div>
        </div>
      </div>
    </div>
    <div class="build-tabs">${tabBar}</div>
    ${content}
  `;
}

function renderBuildOverviewTab(build, runs) {
  const isMaster = build.build_type === "master";
  const stages = isMaster ? STAGES : LOC_PIPELINE_STAGES;
  const labels = isMaster ? STAGE_LABELS : LOC_STAGE_LABELS;
  const currentIdx = stages.indexOf(build.current_stage);
  const pipelineStatus = build.pipeline_status || "idle";

  const strip = stages
    .map((s, i) => {
      let st = "pending";
      if (pipelineStatus === "completed" || pipelineStatus === "done") st = "done";
      else if (i < currentIdx) st = "done";
      else if (i === currentIdx && pipelineStatus === "running") st = "active";
      else if (i === currentIdx) st = "done";
      return `<div class="stage-strip-item" data-state="${st}">${esc(labels[s] || s)}</div>`;
    })
    .join("");

  const latestRun = runs.length ? runs[runs.length - 1] : null;
  const runInfo = latestRun
    ? `<div class="helper" style="margin-top:8px;">Latest run: ${esc(latestRun.stage)} — ${esc(latestRun.status)} ${latestRun.started_at ? `(${esc(relativeTime(latestRun.started_at))})` : ""}</div>`
    : "";

  return `
    <div class="detail-section">
      <div class="eyebrow">Pipeline progress</div>
      <div class="stage-strip">${strip}</div>
      ${runInfo}
    </div>
    <div class="detail-section">
      <div class="button-row">
        <button type="button" class="button button-primary has-icon" data-queue-build="${esc(build.id)}">${iconContent("play", "Queue / Rerun")}</button>
      </div>
    </div>
  `;
}

function renderBuildRunsTab(runs) {
  if (!runs.length) return `<div class="surface" style="padding:2rem;"><p class="helper">No stage runs yet.</p></div>`;
  const rows = runs
    .slice()
    .reverse()
    .map(
      (r) => `
      <tr>
        <td>${esc(r.stage)}</td>
        <td><span class="badge badge-${pipelineTone(r.status)}">${esc(r.status)}</span></td>
        <td>${esc(relativeTime(r.started_at))}</td>
        <td>${r.finished_at ? esc(relativeTime(r.finished_at)) : "—"}</td>
        <td class="helper" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(r.error_message || "")}</td>
      </tr>`
    )
    .join("");
  return `
    <div class="surface" style="overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:0.85rem;">
        <thead>
          <tr style="text-align:left;border-bottom:1px solid var(--line);">
            <th style="padding:8px;">Stage</th>
            <th style="padding:8px;">Status</th>
            <th style="padding:8px;">Started</th>
            <th style="padding:8px;">Finished</th>
            <th style="padding:8px;">Error</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderBuildArtifactTab(build, pathKey, label) {
  const path = build[pathKey];
  if (!path) return `<div class="surface" style="padding:2rem;"><p class="helper">No ${label.toLowerCase()} artifact available yet.</p></div>`;
  return `
    <div class="surface" style="padding:2rem;">
      <div class="eyebrow">${esc(label)}</div>
      <p class="helper" style="margin-top:8px;">Artifact path: <code>${esc(path)}</code></p>
    </div>
  `;
}

function renderBuildPromptsTab(build) {
  const draftPath = build.prompt_list_draft_path;
  const blueprintPath = build.prompt_blueprint_path;
  return `
    <div class="detail-section">
      <div class="eyebrow">Prompt artifacts</div>
      ${draftPath ? `<p class="helper" style="margin-top:8px;">Draft: <code>${esc(draftPath)}</code></p>` : `<p class="helper" style="margin-top:8px;">No prompt draft yet.</p>`}
      ${blueprintPath ? `<p class="helper" style="margin-top:4px;">Blueprint: <code>${esc(blueprintPath)}</code></p>` : ""}
    </div>
  `;
}

function renderBuildTranslationTab(build) {
  const profileId = build.translation_profile_id;
  return `
    <div class="detail-section">
      <div class="eyebrow">Translation</div>
      <p class="helper" style="margin-top:8px;">Translation profile: ${profileId ? `<code>${esc(profileId)}</code>` : "not assigned"}</p>
      ${build.translated_script_path ? `<p class="helper" style="margin-top:4px;">Translated script: <code>${esc(build.translated_script_path)}</code></p>` : `<p class="helper" style="margin-top:4px;">Not translated yet.</p>`}
    </div>
  `;
}

function renderBuildTTSTab(build, ttsJob) {
  const jobId = build.tts_job_id;
  if (!jobId) {
    return `<div class="surface" style="padding:2rem;"><p class="helper">No TTS job associated with this build.</p></div>`;
  }
  const job = ttsJob || {};
  const status = job.status || "unknown";
  const meta = job.meta || {};
  const progress = meta.chunks_done && meta.total_chunks ? Math.round((meta.chunks_done / meta.total_chunks) * 100) : 0;

  return `
    <div class="detail-section">
      <div class="eyebrow">TTS job</div>
      <div class="badge-row" style="margin-top:8px;">
        <span class="badge badge-${pipelineTone(status)}">${esc(titleCase(status))}</span>
        <span class="helper">Job: ${esc(jobId)}</span>
      </div>
      ${status === "processing" || status === "queued" ? `
        <div class="progress-bar" style="margin-top:12px;">
          <div class="progress-bar-fill" style="width:${progress}%;"></div>
        </div>
        <p class="helper">${progress}% — ${meta.chunks_done || 0} / ${meta.total_chunks || "?"} chunks</p>
      ` : ""}
      ${build.narration_path ? `<p class="helper" style="margin-top:8px;">Output: <code>${esc(build.narration_path)}</code></p>` : ""}
      <div class="button-row" style="margin-top:12px;">
        ${status === "processing" ? `<button type="button" class="button button-ghost" data-tts-action="pause">Pause</button>` : ""}
        ${status === "paused" ? `<button type="button" class="button button-primary" data-tts-action="resume">Resume</button>` : ""}
        ${["processing", "paused", "queued"].includes(status) ? `<button type="button" class="button button-danger" data-tts-action="cancel">Cancel</button>` : ""}
      </div>
    </div>
  `;
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

/* ── Legacy views ───────────────────────────────────────────────── */

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

  return `
    <div class="episode-card surface" data-open-episode="${esc(ep.id)}">
      <div class="episode-card-head">
        <strong class="episode-card-title">${esc(ep.title || ep.id)}</strong>
        ${nicheLabel}
      </div>
      <div class="badge-row" style="margin-top:6px;">
        <span class="badge badge-${tone}">${esc(EPISODE_STAGE_LABELS[currentStage] || titleCase(currentStage))}</span>
        <span class="badge">${esc(titleCase(ep.pipeline_status || "idle"))}</span>
      </div>
      ${progress}
      ${error}
      <div class="helper" style="margin-top:4px;font-size:0.7rem;opacity:0.5;">${esc(relativeTime(ep.updated_at))}</div>
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

function renderNicheProjectDetail() {
  const detail = state.nicheProjectDetail;
  if (!detail) {
    $("view").innerHTML = '<div class="surface" style="padding:2rem;"><p class="helper">Niche project not found.</p></div>';
    return;
  }
  const project = detail.project;
  const episodes = detail.episodes || [];
  const langs = (project.configured_languages || []);

  const episodeCards = episodes.length
    ? episodes.map((ep) => {
        const stage = ep.current_stage || "draft";
        const tone = pipelineTone(ep.pipeline_status);
        return '<div class="build-card" data-open-episode="' + esc(ep.id) + '">' +
          '<div style="display:flex;justify-content:space-between;align-items:center;">' +
            '<strong>' + esc(ep.title || ep.id) + '</strong>' +
            '<button type="button" class="button button-danger button-small icon-only" data-delete-episode="' + esc(ep.id) + '" aria-label="Delete">' + iconContent("delete", "Delete", { iconOnly: true }) + '</button>' +
          '</div>' +
          '<div class="badge-row" style="margin-top:6px;">' +
            '<span class="badge badge-' + tone + '">' + esc(EPISODE_STAGE_LABELS[stage] || titleCase(stage)) + '</span>' +
            '<span class="badge">' + esc(titleCase(ep.pipeline_status || "idle")) + '</span>' +
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
        ${langs.map((l) => '<span class="badge badge-small">' + esc(l) + '</span>').join("")}
      </div>
    </div>

    <div class="detail-section">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div class="eyebrow">Episodes (${episodes.length})</div>
        <button type="button" class="button button-primary button-small has-icon" data-open-submit-episode="${esc(project.id)}">${iconContent("add", "Submit script")}</button>
      </div>
      <div class="loc-list" style="margin-top:12px;">${episodeCards}</div>
    </div>

    <div class="detail-section">
      <button type="button" class="button button-ghost has-icon" data-nav="niche-projects">${iconContent("back", "Back to niche projects")}</button>
    </div>

    ${state.modal.kind === "submit-episode" ? renderSubmitEpisodeModal() : ""}
  `;
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

  // Stage strip
  const allStages = EPISODE_PIPELINE_COLUMNS.filter((c) => c.id !== "needs_attention");
  const currentIdx = allStages.findIndex((c) => c.id === currentStage);
  const stageStrip = allStages.map((s, i) => {
    let st = "pending";
    if (episode.pipeline_status === "done" || episode.pipeline_status === "review" && i <= currentIdx) st = "done";
    else if (i < currentIdx) st = "done";
    else if (i === currentIdx && episode.pipeline_status === "running") st = "active";
    else if (i === currentIdx && (episode.pipeline_status === "review" || episode.pipeline_status === "done")) st = "done";
    return '<div class="stage-strip-item" data-state="' + st + '">' + esc(s.short) + '</div>';
  }).join("");

  // Per-language table
  const langRows = langStatuses.map((ls) => {
    return '<tr>' +
      '<td><strong>' + esc(ls.language_code) + '</strong></td>' +
      '<td>' + langStatusBadge(ls.translation_status) + '</td>' +
      '<td>' + langStatusBadge(ls.tts_status) + '</td>' +
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

  // Stage runs
  const runsHtml = stageRuns.length ? stageRuns.slice(0, 20).map((r) =>
    '<div class="badge-row" style="margin-top:4px;">' +
      '<span class="badge badge-' + toneFromRunStatus(r.status) + '">' + esc(r.stage || "?") + '</span>' +
      '<span class="badge">' + esc(r.status || "?") + '</span>' +
      '<span class="helper" style="font-size:0.75rem;">' + esc(relativeTime(r.started_at)) + '</span>' +
    '</div>'
  ).join("") : '<p class="helper">No stage runs yet.</p>';

  const queueDisabled = episode.pipeline_status === "running" ? "disabled" : "";

  $("view").innerHTML = `
    <div class="detail-section">
      <div class="eyebrow">Episode</div>
      <h2 style="margin:4px 0 0;">${esc(episode.title || episode.id)}</h2>
      <div class="badge-row" style="margin-top:8px;">
        <span class="badge badge-${pipelineTone(episode.pipeline_status)}">${esc(titleCase(episode.pipeline_status || "idle"))}</span>
        <span class="badge">${esc(EPISODE_STAGE_LABELS[currentStage] || titleCase(currentStage))}</span>
        <span class="badge">Master: ${esc(episode.master_language || "en")}</span>
      </div>
      ${episode.last_error ? '<div class="notice" data-tone="error" style="margin-top:8px;">' + esc(episode.last_error) + '</div>' : ""}
    </div>

    <div class="detail-section">
      <div class="eyebrow">Pipeline stages</div>
      <div class="stage-strip">${stageStrip}</div>
      <div class="button-row" style="margin-top:12px;">
        <button type="button" class="button button-primary has-icon" data-queue-episode="${esc(episode.id)}" ${queueDisabled}>${iconContent("play", "Queue / Rerun")}</button>
        <button type="button" class="button button-danger has-icon" data-delete-episode="${esc(episode.id)}">${iconContent("delete", "Delete episode")}</button>
      </div>
    </div>

    <div class="detail-section">
      <div class="eyebrow">Per-language status</div>
      ${langTable}
    </div>

    <div class="detail-section">
      <div class="eyebrow">Stage runs</div>
      ${runsHtml}
    </div>

    <div class="detail-section">
      <button type="button" class="button button-ghost has-icon" data-nav="pipeline-board">${iconContent("back", "Back to board")}</button>
    </div>
  `;
}

function langStatusBadge(status) {
  const tone = status === "done" ? "success" : status === "running" ? "info" : status === "failed" ? "error" : "neutral";
  return '<span class="badge badge-' + tone + '">' + esc(titleCase(status || "pending")) + '</span>';
}

function renderDashboard() {
  const workflowCounts = countByWorkflow();
  const providers = state.health?.providers || {};
  const alignment = state.health?.alignment || {};
  const warningTotal = state.jobs.reduce((total, job) => total + Number(job.warning_count || 0), 0);

  $("view").innerHTML = `
    <section class="surface board-surface">
      <div class="section-head">
        <div>
          <div class="eyebrow">Board</div>
          <h2 class="section-title">Trello-style workflow view</h2>
        </div>
        <div class="button-row">
          <button type="button" class="button icon-only" data-nav="settings" aria-label="Settings" title="Settings">${iconContent("settings", "Settings", { iconOnly: true })}</button>
        </div>
      </div>
      <div class="helper section-copy">This is the main surface again. Create the card directly inside the Draft column, then follow it through precise SRT, chunking, scene planning, prompts, review, and export.</div>
      <div class="badge-row" style="margin-top:16px;">
        ${statusBadge(`Total cards: ${state.jobs.length}`, "active")}
        ${statusBadge(`Warnings: ${warningTotal}`, warningTotal ? "warn" : "success")}
        ${healthBadge(`Codex ${providers.codex?.logged_in ? "ready" : providers.codex?.available ? "login" : "missing"}`, providers.codex?.logged_in ? true : providers.codex?.available ? "warn" : false)}
        ${healthBadge(`Claude ${providers.claude?.logged_in ? "ready" : providers.claude?.available ? "login" : "missing"}`, providers.claude?.logged_in ? true : providers.claude?.available ? "warn" : false)}
        ${healthBadge(`MFA ${alignment.mfa ? "ready" : "check"}`, alignment.mfa ? true : "warn")}
      </div>
      <div id="kanban-board" class="kanban-board" style="margin-top:18px;">
        ${WORKFLOW_COLUMNS.map(
          (column) => {
            const cards = state.jobs
              .filter((job) => workflowColumnForJob(job) === column.id)
              .map(renderJobCard)
              .join("");
            const composer = column.id === "draft" ? renderCreateLauncherCard() : "";
            const emptyState =
              !cards && column.id === "draft"
                ? `<div class="kanban-empty">New draft cards will appear below the add-card tile.</div>`
                : !cards
                  ? `<div class="kanban-empty">No jobs at this step.</div>`
                  : "";
            return `
            <section class="kanban-column">
              <div class="kanban-column-head">
                <div>
                  <div class="kanban-column-title">${esc(column.label)}</div>
                  <div class="tiny workflow-column-copy">${esc(column.copy)}</div>
                  <div class="tiny">${esc(workflowCounts[column.id] || 0)} card(s)</div>
                </div>
                ${statusBadge(`${workflowCounts[column.id] || 0}`, workflowTone(column.id))}
              </div>
              <div class="kanban-card-list">
                ${composer}
                ${cards}
                ${emptyState}
              </div>
            </section>`;
          }
        ).join("")}
      </div>
    </section>

    <section class="split-grid">
      <section class="surface">
        <div class="section-head">
          <div>
            <div class="eyebrow">How to use it</div>
            <h2 class="section-title">Simple board flow</h2>
          </div>
        </div>
        <div class="stack">
          <div class="helper">1. Create a card directly in the <strong>Draft</strong> column.</div>
          <div class="helper">2. Watch it move across the real workflow steps from left to right.</div>
          <div class="helper">3. Open a card only when you want to inspect, rerun, or edit it.</div>
          <div class="helper">4. Use the step columns to spot bottlenecks in SRT, chunking, scene planning, or prompt generation.</div>
        </div>
      </section>
      <section class="surface">
        <div class="section-head">
          <div>
            <div class="eyebrow">Health</div>
            <h2 class="section-title">Runtime status</h2>
          </div>
        </div>
        <div class="badge-row">
          ${healthBadge(`ffmpeg ${alignment.ffmpeg ? "ready" : "missing"}`, alignment.ffmpeg)}
          ${healthBadge(`WhisperX ${alignment.whisperx ? "ready" : "check"}`, alignment.whisperx ? true : "warn")}
          ${healthBadge(`Codex ${providers.codex?.logged_in ? "ready" : providers.codex?.available ? "login" : "missing"}`, providers.codex?.logged_in ? true : providers.codex?.available ? "warn" : false)}
          ${healthBadge(`Claude ${providers.claude?.logged_in ? "ready" : providers.claude?.available ? "login" : "missing"}`, providers.claude?.logged_in ? true : providers.claude?.available ? "warn" : false)}
        </div>
      </section>
    </section>
    ${state.modal.kind === "create" ? renderCreateModal() : ""}
  `;
}

function captureDashboardScroll() {
  const board = $("kanban-board");
  if (!board) return;
  state.boardScrollLeft = board.scrollLeft || 0;
}

function restoreDashboardScroll() {
  if (state.route.view !== "dashboard" && state.route.view !== "pipeline-board") return;
  const board = $("kanban-board") || $("pipeline-board");
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

function renderCreate() {
  const defaults = state.settings || {};
  $("view").innerHTML = `
    <form id="create-form" class="stack">
      <section class="hero-grid">
        <section class="surface">
          <div class="section-head">
            <div>
              <div class="eyebrow">Intake</div>
              <h2 class="section-title">Create a clean draft card</h2>
            </div>
          </div>
          <div class="helper section-copy">This page only handles intake. Review and editing happen in the job workspace after the card exists.</div>
          <div class="form-grid" style="margin-top:18px;">
            <label class="field">
              <span class="field-label">Video title</span>
              <input id="create-title" name="title" required />
            </label>
            <label class="field">
              <span class="field-label">Language</span>
              <select id="create-language" name="language_code">${languageOptions("en")}</select>
            </label>
            <label class="field">
              <span class="field-label">Narration audio</span>
              <input id="create-audio" name="audio_file" type="file" accept=".wav,.mp3,audio/*" required />
            </label>
            <label class="field">
              <span class="field-label">Script document</span>
              <input id="create-script" name="script_file" type="file" accept=".txt,.docx" required />
            </label>
          </div>
        </section>

        <section class="surface">
          <div class="section-head">
            <div>
              <div class="eyebrow">Quick actions</div>
              <h2 class="section-title">Language preparation</h2>
            </div>
          </div>
          <div class="helper section-copy">If MFA resources are not ready for the selected language, prepare them here before you queue the job.</div>
        <div class="button-row" style="margin-top:18px;">
          <button type="button" class="button has-icon" data-prepare-language="true">${iconContent("language", "Prepare selected language")}</button>
          <button type="button" class="button button-ghost has-icon" data-nav="dashboard">${iconContent("back", "Back to dashboard")}</button>
        </div>
        </section>
      </section>

      <section class="surface">
        <div class="section-head">
          <div>
            <div class="eyebrow">Pipeline defaults</div>
            <h2 class="section-title">Workflow setup</h2>
          </div>
        </div>
        <div class="helper section-copy">The setup now follows the real workflow, so each block answers one question: who handles that step, and with which model.</div>
        <div class="workflow-setup-grid" style="margin-top:18px;">
          ${renderStageSetupCard({
            icon: "scene",
            title: "Scene planning",
            copy: "Turns aligned subtitles into scene-sized chunks.",
            providerId: "create-scene-planning",
            providerValue: defaults.default_scene_planning_provider || "claude",
            modelId: "create-scene-model",
            modelValue: defaults.default_scene_planning_model || "haiku",
          })}
          ${renderStageSetupCard({
            icon: "bible",
            title: "Consistency guide",
            copy: "Locks the characters, places, props, and continuity rules.",
            providerId: "create-visual-bible",
            providerValue: defaults.default_visual_bible_provider || "claude",
            modelId: "create-bible-model",
            modelValue: defaults.default_visual_bible_model || "haiku",
          })}
          ${renderStageSetupCard({
            icon: "play",
            title: "Video prompts",
            copy: "Writes prompts for the leading video scenes.",
            providerId: "create-video-prompt",
            providerValue: defaults.default_video_prompt_provider || "codex",
            modelId: "create-video-model",
            modelValue: defaults.default_video_prompt_model || "gpt-5.4",
          })}
          ${renderStageSetupCard({
            icon: "prompts",
            title: "Image prompts",
            copy: "Writes prompts for the remaining image scenes.",
            providerId: "create-image-prompt",
            providerValue: defaults.default_image_prompt_provider || "codex",
            modelId: "create-image-model",
            modelValue: defaults.default_image_prompt_model || "gpt-5.4",
          })}
          ${renderSetupCard({
            icon: "timeline",
            title: "Output strategy",
            copy: "Choose how many opening scenes should default to video before the rest switch to image.",
            tone: "warn",
            fields: `
              <label class="field">
                <span class="field-label">Video-first scenes</span>
                <input id="create-leading-video-count" type="number" min="0" value="${esc(defaults.leading_video_scene_count || 20)}" />
              </label>
            `,
          })}
        </div>
        <div class="button-row" style="margin-top:18px;">
          <button type="submit" class="button button-primary has-icon">${iconContent("add", "Create draft")}</button>
        </div>
      </section>
    </form>
  `;
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

function renderArtifactLinks(job) {
  const links = [
    job.final_srt_path ? artifactLink(job.id, "final_srt", "Final SRT") : "",
    job.alignment_report_path ? artifactLink(job.id, "alignment_report", "Alignment report") : "",
    job.planning_manifest_path ? artifactLink(job.id, "planning_manifest", "Planning manifest") : "",
    job.timeline_draft_path ? artifactLink(job.id, "timeline_draft", "Timeline draft") : "",
    job.visual_bible_path ? artifactLink(job.id, "consistency_guide", "Consistency guide") : "",
    job.prompt_list_draft_path ? artifactLink(job.id, "video_prompt_list_draft", "Video prompt draft") : "",
    job.prompt_list_draft_path ? artifactLink(job.id, "image_prompt_list_draft", "Image prompt draft") : "",
    job.prompt_blueprint_path ? artifactLink(job.id, "prompt_blueprint", "Prompt blueprint") : "",
    job.export_timeline_path ? artifactLink(job.id, "export_timeline", "Exported timeline") : "",
    job.export_video_prompt_list_path ? artifactLink(job.id, "export_video_prompt_list", "Exported video prompts") : "",
    job.export_image_prompt_list_path ? artifactLink(job.id, "export_image_prompt_list", "Exported image prompts") : "",
  ].filter(Boolean);
  return links.length ? `<div class="link-row">${links.join("")}</div>` : `<div class="empty-state">No artifacts generated yet.</div>`;
}

function renderValidationPreview(label, report) {
  const errors = report?.errors || [];
  const warnings = report?.warnings || [];
  return `
    <article class="summary-card">
      <div class="metric-label">${esc(label)}</div>
      <div class="badge-row" style="margin-top:12px;">
        ${statusBadge(`${errors.length} errors`, errors.length ? "error" : "success")}
        ${statusBadge(`${warnings.length} warnings`, warnings.length ? "warn" : "success")}
      </div>
      <div class="metric-copy" style="margin-top:12px;">${esc((errors[0] || warnings[0] || "No issues logged.").slice(0, 180))}</div>
    </article>
  `;
}

function renderRunningBanner(job, runs) {
  if (job.pipeline_status !== "running") return "";
  const runningRun = (runs || []).find((run) => run.status === "running");
  const stageName = stageLabel(job.current_stage || "unknown");
  const providerName = runningRun?.provider ? providerLabel(runningRun.provider) : "";
  let elapsed = "";
  const startedAt = runningRun?.started_at;
  if (startedAt) {
    const startMs = new Date(startedAt.endsWith("Z") ? startedAt : startedAt + "Z").getTime();
    const diffSec = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
    const mins = Math.floor(diffSec / 60);
    const secs = diffSec % 60;
    elapsed = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
  }
  const command = runningRun?.command || {};
  const batchInfo = command.batch_index != null && command.batch_total != null
    ? ` · Batch ${command.batch_index + 1} of ${command.batch_total}`
    : "";
  return `
    <section class="surface running-banner" data-tone="active">
      <div class="running-banner-content">
        <span class="running-pulse"></span>
        <div class="running-banner-text">
          <strong>${esc(stageName)}</strong> is running${providerName ? ` via ${esc(providerName)}` : ""}${batchInfo}
          ${startedAt ? `<span class="running-elapsed" data-started-at="${esc(startedAt.endsWith("Z") ? startedAt : startedAt + "Z")}">${elapsed ? esc(elapsed) + " elapsed" : ""}</span>` : ""}
        </div>
        <button type="button" class="button button-ghost has-icon" data-live-log="true">${iconContent("eye", "View live output")}</button>
      </div>
      <div class="helper" style="margin-top:6px;">The CLI process is active in the background. This page auto-refreshes every 5 seconds. Check the <strong>Runs</strong> tab for stdout/stderr after completion.</div>
    </section>
  `;
}

function renderLiveLogPanel() {
  if (!liveLogOpen) return "";
  return `
    <section class="surface live-log-panel">
      <div class="section-head">
        <div>
          <div class="eyebrow">Live output</div>
          <h2 class="section-title">CLI process log</h2>
        </div>
        <button type="button" class="button button-ghost icon-only" data-live-log-close="true" aria-label="Close" title="Close">${iconContent("close", "Close", { iconOnly: true })}</button>
      </div>
      <div class="live-log-tabs">
        <button type="button" class="tab-link is-active" data-live-log-tab="stdout">stdout</button>
        <button type="button" class="tab-link" data-live-log-tab="stderr">stderr</button>
      </div>
      <pre class="live-log-output" id="live-log-content">Fetching...</pre>
    </section>
  `;
}

async function fetchAndRenderLiveLog() {
  const jobId = state.detail?.job?.id;
  if (!jobId || !liveLogOpen) return;
  try {
    const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/live-log`);
    const contentEl = $("live-log-content");
    if (!contentEl) return;
    const activeTab = document.querySelector(".live-log-tabs .tab-link.is-active");
    const tab = activeTab?.dataset?.liveLogTab || "stdout";
    const text = tab === "stderr" ? (data.stderr || "(empty)") : (data.stdout || "(empty)");
    contentEl.textContent = text;
    contentEl.scrollTop = contentEl.scrollHeight;
    if (!data.running) {
      contentEl.textContent += "\n\n--- Process finished ---";
    }
  } catch {
    const contentEl = $("live-log-content");
    if (contentEl) contentEl.textContent = "Failed to fetch log.";
  }
}

function startLiveLogPolling() {
  stopLiveLogPolling();
  fetchAndRenderLiveLog();
  liveLogTimer = window.setInterval(fetchAndRenderLiveLog, 2000);
}

function stopLiveLogPolling() {
  if (liveLogTimer) {
    window.clearInterval(liveLogTimer);
    liveLogTimer = null;
  }
}

function renderJobOverview(job, artifacts, runs) {
  const latest = latestRunMap(runs);
  return `
    ${renderRunningBanner(job, runs)}
    ${renderLiveLogPanel()}
    <section class="overview-grid">
      <section class="stack">
        <section class="surface">
          <div class="section-head">
            <div>
              <div class="eyebrow">Job controls</div>
              <h2 class="section-title">Reruns and overrides</h2>
            </div>
          </div>
          <div class="button-row">
            <button type="button" class="button button-primary has-icon" data-job-action="queue" data-stage="alignment">${iconContent("play", "Run full")}</button>
            <button type="button" class="button has-icon" data-job-action="queue" data-stage="scene_planning">${iconContent("scene", "Rerun scenes")}</button>
            <button type="button" class="button has-icon" data-job-action="queue" data-stage="visual_bible">${iconContent("bible", "Rerun consistency guide")}</button>
            <button type="button" class="button has-icon" data-job-action="queue" data-stage="video_prompt_generation">${iconContent("rerun", "Rerun video prompts")}</button>
            <button type="button" class="button has-icon" data-job-action="queue" data-stage="image_prompt_generation">${iconContent("rerun", "Rerun image prompts")}</button>
            <button type="button" class="button button-danger has-icon" data-job-action="finalize">${iconContent("finalize", "Finalize export")}</button>
          </div>
          <form id="job-config-form" class="stack" style="margin-top:18px;">
            <div class="workflow-control-bar">
              <label class="field">
                <span class="field-label">Board column</span>
                <select id="job-board-status">${BOARD_STATUSES.map((status) => `<option value="${status}" ${job.board_status === status ? "selected" : ""}>${esc(status)}</option>`).join("")}</select>
              </label>
              <div class="helper">Use this only for manual triage. The workflow columns still update automatically when stages run.</div>
            </div>
            <div class="workflow-setup-grid">
              ${renderStageSetupCard({
                icon: "scene",
                title: "Scene planning",
                copy: "This card's scene writer and model.",
                providerId: "job-scene-provider",
                providerValue: job.scene_planning_provider || "claude",
                modelId: "job-scene-model",
                modelValue: job.scene_planning_model || "haiku",
              })}
              ${renderStageSetupCard({
                icon: "bible",
                title: "Consistency guide",
                copy: "This card's writer for character, place, and object consistency.",
                providerId: "job-bible-provider",
                providerValue: job.visual_bible_provider || "claude",
                modelId: "job-bible-model",
                modelValue: job.visual_bible_model || "haiku",
              })}
              ${renderStageSetupCard({
                icon: "play",
                title: "Video prompts",
                copy: "This card's writer for the opening video scenes.",
                providerId: "job-video-provider",
                providerValue: job.video_prompt_provider || "codex",
                modelId: "job-video-model",
                modelValue: job.video_prompt_model || "gpt-5.4",
              })}
              ${renderStageSetupCard({
                icon: "prompts",
                title: "Image prompts",
                copy: "This card's writer for the remaining image scenes.",
                providerId: "job-image-provider",
                providerValue: job.image_prompt_provider || "codex",
                modelId: "job-image-model",
                modelValue: job.image_prompt_model || "gpt-5.4",
              })}
              ${renderSetupCard({
                icon: "timeline",
                title: "Output strategy",
                copy: "How many opening scenes should be treated as video before the rest become image prompts.",
                tone: "warn",
                fields: `
                  <label class="field">
                    <span class="field-label">Video-first scenes</span>
                    <input id="job-leading-video" type="number" min="0" value="${esc(job.leading_video_scene_count || 20)}" />
                  </label>
                `,
              })}
            </div>
            <div class="button-row">
              <button type="button" class="button button-primary has-icon" data-job-action="save-config">${iconContent("save", "Save job config")}</button>
              <button type="button" class="button has-icon" data-job-action="move-card">${iconContent("move", "Move card")}</button>
            </div>
          </form>
          ${job.last_error ? `<div class="notice" data-tone="error" style="margin-top:16px;">${esc(job.last_error)}</div>` : ""}
        </section>

        <section class="surface">
          <div class="section-head">
            <div>
              <div class="eyebrow">Artifacts</div>
              <h2 class="section-title">Downloads</h2>
            </div>
          </div>
          ${renderArtifactLinks(job)}
        </section>
      </section>

      <section class="stack">
        <section class="surface">
          <div class="section-head">
            <div>
              <div class="eyebrow">Validation</div>
              <h2 class="section-title">Current review state</h2>
            </div>
          </div>
          <div class="provider-grid">
            ${renderValidationPreview("Timeline", artifacts.timeline_validation)}
            ${renderValidationPreview("Consistency guide", artifacts.consistency_guide_validation || artifacts.visual_bible_validation)}
            ${renderValidationPreview("Prompt drafts", artifacts.prompt_validation)}
            <article class="summary-card">
              <div class="metric-label">Pipeline summary</div>
              <div class="badge-row" style="margin-top:12px;">
                ${STAGES.map((stage) => statusBadge(`${titleCase(stage)}: ${latest.get(stage)?.status || "not run"}`, toneFromRunStatus(latest.get(stage)?.status || "idle"))).join("")}
              </div>
            </article>
          </div>
        </section>

        <section class="preview-card">
          <div class="section-head">
            <div>
              <div class="eyebrow">Preview</div>
              <h2 class="section-title">Final SRT</h2>
            </div>
          </div>
          <pre>${esc(artifacts.final_srt || "No subtitle output yet.")}</pre>
        </section>
      </section>
    </section>
  `;
}

function renderSceneEditor(scenes) {
  if (!scenes?.length) return `<div class="empty-state">No timeline draft yet.</div>`;
  return `
    <div class="details-list">
      ${scenes
        .map(
          (scene, index) => `
            <details class="editor-card" data-scene-row="${index}">
              <summary>
                <div class="summary-main">
                  <div class="summary-title">${esc(scene.scene_id)} · ${esc(scene.start)}s to ${esc(scene.end)}s</div>
                  <div class="summary-copy">${esc(shortText(scene.text, 120))}</div>
                </div>
                ${statusBadge(scene.asset_type, scene.asset_type === "video" ? "active" : "neutral")}
              </summary>
              <div class="editor-card-body">
                <div class="form-grid">
                  <label class="field">
                    <span class="field-label">Asset type</span>
                    <select data-field="asset_type">
                      <option value="image" ${scene.asset_type === "image" ? "selected" : ""}>image</option>
                      <option value="video" ${scene.asset_type === "video" ? "selected" : ""}>video</option>
                    </select>
                  </label>
                  <label class="field">
                    <span class="field-label">Duration</span>
                    <input value="${esc(scene.duration)}" disabled />
                  </label>
                </div>
                <label class="field">
                  <span class="field-label">Scene text</span>
                  <textarea data-field="text">${esc(scene.text || "")}</textarea>
                </label>
                <div class="form-grid">
                  <label class="field">
                    <span class="field-label">Visual intent</span>
                    <textarea data-field="visual_intent">${esc(scene.visual_intent || "")}</textarea>
                  </label>
                  <label class="field">
                    <span class="field-label">Notes</span>
                    <textarea data-field="notes">${esc(scene.notes || "")}</textarea>
                  </label>
                </div>
              </div>
            </details>`
        )
        .join("")}
    </div>
  `;
}

function renderCharacterCards(characters) {
  const list = characters?.length
    ? characters
    : [{ character_id: "character_001", label: "", visual_description: "", wardrobe: "", demeanor: "", usage_notes: "" }];
  return `
    <div class="details-list" id="character-editor">
      ${list
        .map(
          (character, index) => `
            <details class="editor-card" data-character-row="${index}" ${index === 0 ? "open" : ""}>
              <summary>
                <div class="summary-main">
                  <div class="summary-title">${esc(character.label || `Character ${index + 1}`)}</div>
                  <div class="summary-copy">${esc(shortText(character.visual_description || "No visual description yet.", 110))}</div>
                </div>
              </summary>
              <div class="editor-card-body">
                <div class="form-grid">
                  <label class="field">
                    <span class="field-label">Character ID</span>
                    <input data-field="character_id" value="${esc(character.character_id || "")}" />
                  </label>
                  <label class="field">
                    <span class="field-label">Label</span>
                    <input data-field="label" value="${esc(character.label || "")}" />
                  </label>
                </div>
                <div class="form-grid">
                  <label class="field">
                    <span class="field-label">Visual description</span>
                    <textarea data-field="visual_description">${esc(character.visual_description || "")}</textarea>
                  </label>
                  <label class="field">
                    <span class="field-label">Wardrobe</span>
                    <textarea data-field="wardrobe">${esc(character.wardrobe || "")}</textarea>
                  </label>
                  <label class="field">
                    <span class="field-label">Demeanor</span>
                    <textarea data-field="demeanor">${esc(character.demeanor || "")}</textarea>
                  </label>
                  <label class="field">
                    <span class="field-label">Usage notes</span>
                    <textarea data-field="usage_notes">${esc(character.usage_notes || "")}</textarea>
                  </label>
                </div>
              </div>
            </details>`
        )
        .join("")}
    </div>
  `;
}

function renderJobBible(artifacts) {
  const guide = artifacts.consistency_guide || artifacts.visual_bible || {};
  const guideValidation = artifacts.consistency_guide_validation || artifacts.visual_bible_validation || {};
  const world = guide.world_style || {};
  return `
    <section class="surface">
      <div class="section-head">
        <div>
          <div class="eyebrow">Consistency guide</div>
          <h2 class="section-title">Characters and elements that must stay stable</h2>
        </div>
        <div class="button-row">
          <button type="button" class="button has-icon" data-add-character="true">${iconContent("add", "Add character")}</button>
          <button type="button" class="button button-primary has-icon" data-job-action="save-bible">${iconContent("save", "Save consistency guide")}</button>
        </div>
      </div>
      <div class="helper">${esc([...(guideValidation.errors || []), ...(guideValidation.warnings || [])].join(" | ") || "No validation issues logged.")}</div>
      <div class="form-grid" style="margin-top:18px;">
        <label class="field">
          <span class="field-label">World setting</span>
          <textarea id="bible-setting">${esc(world.setting || "")}</textarea>
        </label>
        <label class="field">
          <span class="field-label">Overall look</span>
          <textarea id="bible-look">${esc(world.look || "")}</textarea>
        </label>
        <label class="field">
          <span class="field-label">Palette</span>
          <textarea id="bible-palette">${esc(world.palette || "")}</textarea>
        </label>
        <label class="field">
          <span class="field-label">Lighting language</span>
          <textarea id="bible-lighting">${esc(world.lighting || "")}</textarea>
        </label>
        <label class="field">
          <span class="field-label">Camera language</span>
          <textarea id="bible-camera">${esc(world.camera_language || "")}</textarea>
        </label>
        <label class="field">
          <span class="field-label">Negative rules</span>
          <textarea id="bible-negative">${esc(world.negative_rules || "")}</textarea>
        </label>
      </div>
      <div class="form-grid" style="margin-top:14px;">
        <label class="field">
          <span class="field-label">Continuity rules (one per line)</span>
          <textarea id="bible-continuity">${esc((guide.continuity_rules || []).join("\n"))}</textarea>
        </label>
        <label class="field">
          <span class="field-label">Environment rules (one per line)</span>
          <textarea id="bible-environment">${esc((guide.environment_rules || []).join("\n"))}</textarea>
        </label>
      </div>
      <div style="margin-top:18px;">${renderCharacterCards(guide.characters || [])}</div>
    </section>
  `;
}

function renderJobPrompts(artifacts) {
  const videoPromptText = state.promptDraftVideoFull || artifacts.video_prompt_list_draft || "";
  const imagePromptText = state.promptDraftImageFull || artifacts.image_prompt_list_draft || "";
  return `
    <section class="surface">
      <div class="section-head">
        <div>
          <div class="eyebrow">Prompts</div>
          <h2 class="section-title">Separate video and image prompt drafts</h2>
        </div>
        <div class="button-row">
          <button type="button" class="button button-primary has-icon" data-job-action="save-prompts">${iconContent("save", "Save prompt drafts")}</button>
        </div>
      </div>
      <div class="helper">${esc([...(artifacts.prompt_validation?.errors || []), ...(artifacts.prompt_validation?.warnings || [])].join(" | ") || "No validation issues logged.")}</div>
      <div class="form-grid" style="margin-top:18px;">
        <label class="field">
          <span class="field-label">Video prompts</span>
          <textarea id="prompt-editor-video" class="is-large">${esc(videoPromptText.trim())}</textarea>
        </label>
        <label class="field">
          <span class="field-label">Image prompts</span>
          <textarea id="prompt-editor-image" class="is-large">${esc(imagePromptText.trim())}</textarea>
        </label>
      </div>
      <details class="editor-card" style="margin-top:18px;">
        <summary>
          <div class="summary-main">
            <div class="summary-title">Prompt blueprint preview</div>
            <div class="summary-copy">Structured metadata stays separate from the two editable prompt draft files.</div>
          </div>
        </summary>
        <div class="editor-card-body">
          <pre>${esc(JSON.stringify(artifacts.prompt_blueprint || [], null, 2))}</pre>
        </div>
      </details>
    </section>
  `;
}

function renderJobRuns(runs) {
  if (!runs?.length) return `<div class="empty-state">No runs recorded yet.</div>`;
  return `
    <section class="surface">
      <div class="section-head">
        <div>
          <div class="eyebrow">Run history</div>
          <h2 class="section-title">Raw stage runs</h2>
        </div>
      </div>
      <div class="helper">Older failures stay here for debugging even when the latest status card is healthy.</div>
      <div class="details-list" style="margin-top:18px;">
        ${runs
          .map(
            (run) => `
              <details class="editor-card">
                <summary>
                  <div class="summary-main">
                    <div class="summary-title">${esc(titleCase(run.stage))} · ${esc(run.provider || "local")}</div>
                    <div class="summary-copy">${esc(formatDate(run.started_at))}${run.finished_at ? ` → ${esc(formatDate(run.finished_at))}` : ""}</div>
                  </div>
                  ${statusBadge(run.status, toneFromRunStatus(run.status))}
                </summary>
                <div class="editor-card-body">
                  <label class="field">
                    <span class="field-label">Command metadata</span>
                    <pre>${esc(JSON.stringify(run.command || {}, null, 2))}</pre>
                  </label>
                  <label class="field">
                    <span class="field-label">Stdout</span>
                    <pre>${esc(run.stdout_preview || "No stdout.")}</pre>
                  </label>
                  <label class="field">
                    <span class="field-label">Stderr</span>
                    <pre>${esc(run.stderr_preview || "No stderr.")}</pre>
                  </label>
                </div>
              </details>`
          )
          .join("")}
      </div>
    </section>
  `;
}

function renderJob() {
  if (!state.detail?.job) {
    $("view").innerHTML = `<div class="empty-state">This job could not be loaded.</div>`;
    return;
  }

  const job = state.detail.job;
  const artifacts = state.detail.artifacts || {};
  const runs = state.detail.stage_runs || [];
  const latest = latestRunMap(runs);
  const routeTab = JOB_TABS.includes(state.route.tab) ? state.route.tab : "overview";

  let content = "";
  if (routeTab === "timeline") {
    content = `<section class="surface"><div class="section-head"><div><div class="eyebrow">Timeline</div><h2 class="section-title">Scene review</h2></div><div class="button-row"><button type="button" class="button button-primary has-icon" data-job-action="save-timeline">${iconContent("save", "Save timeline draft")}</button></div></div><div class="helper">${esc([...(artifacts.timeline_validation?.errors || []), ...(artifacts.timeline_validation?.warnings || [])].join(" | ") || "No validation issues logged.")}</div><div style="margin-top:18px;">${renderSceneEditor(artifacts.timeline || [])}</div></section>`;
  } else if (routeTab === "bible") {
    content = renderJobBible(artifacts);
  } else if (routeTab === "prompts") {
    content = renderJobPrompts(artifacts);
  } else if (routeTab === "runs") {
    content = renderJobRuns(runs);
  } else {
    content = renderJobOverview(job, artifacts, runs);
  }

  $("view").innerHTML = `
    <section class="surface">
      <div class="section-head">
        <div>
          <div class="eyebrow">Job workspace</div>
          <h2 class="section-title">${esc(job.title)}</h2>
          <div class="helper mono">${esc(job.id)}</div>
        </div>
        <div class="button-row">
          <button type="button" class="button icon-only" data-nav="dashboard" aria-label="Back to dashboard" title="Back to dashboard">${iconContent("back", "Back to dashboard", { iconOnly: true })}</button>
          <button type="button" class="button button-danger icon-only" data-delete-job="${esc(job.id)}" data-job-title="${esc(job.title)}" aria-label="Delete card" title="Delete card">${iconContent("delete", "Delete card", { iconOnly: true })}</button>
          ${statusBadge(job.board_status, toneFromBoardStatus(job.board_status))}
          ${statusBadge(titleCase(job.current_stage), toneFromRunStatus(latest.get(job.current_stage)?.status || job.pipeline_status))}
        </div>
      </div>
      <div class="metrics-grid">
        <article class="metric-card">
          <div class="metric-label">Board status</div>
          <div class="metric-value">${esc(job.board_status)}</div>
          <div class="metric-copy">Where this card currently sits.</div>
        </article>
        <article class="metric-card">
          <div class="metric-label">Pipeline</div>
          <div class="metric-value">${esc(titleCase(job.pipeline_status))}</div>
          <div class="metric-copy">Current automation state.</div>
        </article>
        <article class="metric-card">
          <div class="metric-label">Warnings</div>
          <div class="metric-value">${esc(job.warning_count || 0)}</div>
          <div class="metric-copy">Advisories across alignment, planning, and prompts.</div>
        </article>
        <article class="metric-card">
          <div class="metric-label">Scenes</div>
          <div class="metric-value">${esc((artifacts.timeline || []).length || 0)}</div>
          <div class="metric-copy">Current timeline size.</div>
        </article>
      </div>
      <div class="status-strip" style="margin-top:18px;">
        ${STAGES.map((stage) => {
          const run = latest.get(stage);
          return `
            <article class="status-card">
              <div class="status-card-title">${esc(titleCase(stage))}</div>
              <div class="status-card-state">${esc(titleCase(run?.status || "not run"))}</div>
              <div class="tiny">${esc(run?.provider ? providerLabel(run.provider) : "No run yet")}</div>
              <div class="badge-row">${statusBadge(run?.status || "idle", toneFromRunStatus(run?.status || "idle"))}</div>
            </article>`;
        }).join("")}
      </div>
    </section>

    <nav class="surface">
      <div class="tab-row">
        ${JOB_TABS.map(
          (tab) => `<button type="button" class="tab-link has-icon ${routeTab === tab ? "is-active" : ""}" data-job-tab="${tab}">${iconContent(JOB_TAB_META[tab]?.icon || "overview", JOB_TAB_META[tab]?.label || titleCase(tab))}</button>`
        ).join("")}
      </div>
    </nav>

    ${content}
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
  else if (view === "projects") renderProjects();
  else if (view === "project") renderProjectDetail();
  else if (view === "build") renderBuildDetail();
  else if (view === "voice-profiles") renderVoiceProfiles();
  else if (view === "translation-profiles") renderTranslationProfiles();
  else if (view === "create") renderCreate();
  else if (view === "settings") renderSettings();
  else if (view === "templates") renderTemplates();
  else if (view === "job") renderJob();
  else renderPipelineBoard();
  document.body.classList.toggle("modal-open", Boolean(state.modal.kind));
  syncAllProviderModelSelects();
  resetElapsedTimer();
  if (liveLogOpen && $("live-log-content")) startLiveLogPolling();
  else stopLiveLogPolling();
  restoreDashboardScroll();
}

async function refreshData({ preserveNotice = true } = {}) {
  const route = state.route;
  const [health, settings] = await Promise.all([api("/api/health"), api("/api/settings")]);
  state.health = health;
  state.settings = settings.settings || {};
  state.modelCatalog = settings.model_catalog || DEFAULT_MODEL_CATALOG;
  state.templates = settings.templates || [];

  // Always fetch projects list for sidebar count
  try {
    const projRes = await api("/api/projects");
    state.projects = projRes.projects || [];
  } catch { state.projects = []; }

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

  // View-specific fetches
  if (route.view === "dashboard" || route.view === "create") {
    const board = await api("/api/board");
    state.jobs = board.jobs || [];
  }

  if (route.view === "project" && route.projectId) {
    try {
      state.projectDetail = await api(`/api/projects/${encodeURIComponent(route.projectId)}`);
    } catch (error) {
      state.projectDetail = null;
      if (!preserveNotice) throw error;
      setNotice(error.message, "error");
      window.location.hash = routeToHash({ view: "projects" });
      return;
    }
  } else {
    state.projectDetail = null;
  }

  if (route.view === "build" && route.buildId) {
    try {
      state.buildDetail = await api(`/api/builds/${encodeURIComponent(route.buildId)}`);
      // If localization build on TTS tab, fetch TTS job status
      if (route.tab === "tts" && state.buildDetail?.build?.tts_job_id) {
        try {
          state.buildDetail._ttsJob = await api(`/api/tts-jobs/${encodeURIComponent(state.buildDetail.build.tts_job_id)}`);
        } catch { state.buildDetail._ttsJob = null; }
      }
    } catch (error) {
      state.buildDetail = null;
      if (!preserveNotice) throw error;
      setNotice(error.message, "error");
      window.location.hash = routeToHash({ view: "projects" });
      return;
    }
  } else {
    state.buildDetail = null;
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
  if (route.view === "project" || route.view === "voice-profiles") {
    try {
      const tlRes = await api("/api/target-languages");
      state.targetLanguages = tlRes.languages || [];
    } catch { state.targetLanguages = []; }
  }

  // Also fetch voice/translation profiles for project detail (localization modal)
  if (route.view === "project") {
    try {
      const [vpRes, tpRes] = await Promise.all([
        api("/api/voice-profiles"),
        api("/api/translation-profiles"),
      ]);
      state.voiceProfiles = vpRes.profiles || [];
      state.translationProfiles = tpRes.profiles || [];
    } catch { /* keep existing */ }
  }

  if (route.view === "job" && route.jobId) {
    try {
      state.detail = await api(`/api/jobs/${encodeURIComponent(route.jobId)}`);
      if (route.tab === "prompts" && state.detail.job?.prompt_list_draft_path) {
        const [videoDraft, imageDraft] = await Promise.all([
          apiText(`/api/jobs/${encodeURIComponent(route.jobId)}/artifacts/video_prompt_list_draft`),
          apiText(`/api/jobs/${encodeURIComponent(route.jobId)}/artifacts/image_prompt_list_draft`),
        ]);
        state.promptDraftVideoFull = videoDraft;
        state.promptDraftImageFull = imageDraft;
      } else {
        state.promptDraftVideoFull = "";
        state.promptDraftImageFull = "";
      }
    } catch (error) {
      state.detail = null;
      state.promptDraftVideoFull = "";
      state.promptDraftImageFull = "";
      if (!preserveNotice) throw error;
      setNotice(error.message, "error");
      window.location.hash = routeToHash({ view: "dashboard" });
      return;
    }
  } else {
    state.detail = null;
    state.promptDraftVideoFull = "";
    state.promptDraftImageFull = "";
  }
}

function resetAutoRefresh() {
  if (refreshTimer) window.clearInterval(refreshTimer);
  if (!autoRefreshAllowed(state.route)) return;
  refreshTimer = window.setInterval(() => {
    if (state.modal.kind) return;
    if (isJobConfigDirty()) return;
    refreshData().then(renderApp).catch(() => {});
  }, REFRESH_INTERVAL_MS);
}

function resetElapsedTimer() {
  if (elapsedTimer) window.clearInterval(elapsedTimer);
  elapsedTimer = window.setInterval(() => {
    const el = document.querySelector(".running-elapsed");
    if (!el || !el.dataset.startedAt) return;
    const startMs = new Date(el.dataset.startedAt).getTime();
    const diffSec = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
    const mins = Math.floor(diffSec / 60);
    const secs = diffSec % 60;
    el.textContent = mins > 0 ? `${mins}m ${secs}s elapsed` : `${secs}s elapsed`;
  }, 1000);
}

function isCreateFormDirty() {
  const defaults = state.settings || {};
  const title = $("create-title")?.value?.trim();
  const audioSelected = $("create-audio")?.files?.length;
  const scriptSelected = $("create-script")?.files?.length;
  const languageChanged = ($("create-language")?.value || "en") !== "en";
  const sceneChanged = ($("create-scene-planning")?.value || defaults.default_scene_planning_provider || "claude") !== (defaults.default_scene_planning_provider || "claude");
  const bibleChanged = ($("create-visual-bible")?.value || defaults.default_visual_bible_provider || "claude") !== (defaults.default_visual_bible_provider || "claude");
  const videoChanged = ($("create-video-prompt")?.value || defaults.default_video_prompt_provider || "codex") !== (defaults.default_video_prompt_provider || "codex");
  const imageChanged = ($("create-image-prompt")?.value || defaults.default_image_prompt_provider || "codex") !== (defaults.default_image_prompt_provider || "codex");
  const sceneModelChanged = ($("create-scene-model")?.value || defaults.default_scene_planning_model || "haiku") !== (defaults.default_scene_planning_model || "haiku");
  const bibleModelChanged = ($("create-bible-model")?.value || defaults.default_visual_bible_model || "haiku") !== (defaults.default_visual_bible_model || "haiku");
  const videoModelChanged = ($("create-video-model")?.value || defaults.default_video_prompt_model || "gpt-5.4") !== (defaults.default_video_prompt_model || "gpt-5.4");
  const imageModelChanged = ($("create-image-model")?.value || defaults.default_image_prompt_model || "gpt-5.4") !== (defaults.default_image_prompt_model || "gpt-5.4");
  const videoCountChanged = String($("create-leading-video-count")?.value || defaults.leading_video_scene_count || 20) !== String(defaults.leading_video_scene_count || 20);
  return Boolean(
    title ||
      audioSelected ||
      scriptSelected ||
      languageChanged ||
      sceneChanged ||
      bibleChanged ||
      videoChanged ||
      imageChanged ||
      sceneModelChanged ||
      bibleModelChanged ||
      videoModelChanged ||
      imageModelChanged ||
      videoCountChanged
  );
}

function isJobConfigDirty() {
  const job = state.detail?.job;
  if (!job || !$("job-scene-provider")) return false;
  return (
    ($("job-scene-provider").value || "") !== (job.scene_planning_provider || "claude") ||
    ($("job-bible-provider").value || "") !== (job.visual_bible_provider || "claude") ||
    ($("job-video-provider").value || "") !== (job.video_prompt_provider || "codex") ||
    ($("job-image-provider").value || "") !== (job.image_prompt_provider || "codex") ||
    ($("job-scene-model").value || "") !== (job.scene_planning_model || "haiku") ||
    ($("job-bible-model").value || "") !== (job.visual_bible_model || "haiku") ||
    ($("job-video-model").value || "") !== (job.video_prompt_model || "gpt-5.4") ||
    ($("job-image-model").value || "") !== (job.image_prompt_model || "gpt-5.4") ||
    String($("job-leading-video")?.value || 20) !== String(job.leading_video_scene_count || 20)
  );
}

async function syncRouteAndRender() {
  liveLogOpen = false;
  stopLiveLogPolling();
  state.route = parseRoute();
  await refreshData();
  renderApp();
  resetAutoRefresh();
}

function readCreateFormData() {
  const audioFile = $("create-audio")?.files?.[0];
  const scriptFile = $("create-script")?.files?.[0];
  if (!audioFile || !scriptFile) throw new Error("Choose both audio and script files.");
  const payload = new FormData();
  payload.append("title", $("create-title").value.trim());
  payload.append("language_code", $("create-language").value);
  payload.append("scene_planning_provider", $("create-scene-planning").value);
  payload.append("visual_bible_provider", $("create-visual-bible").value);
  payload.append("video_prompt_provider", $("create-video-prompt").value);
  payload.append("image_prompt_provider", $("create-image-prompt").value);
  payload.append("scene_planning_model", $("create-scene-model").value);
  payload.append("visual_bible_model", $("create-bible-model").value);
  payload.append("video_prompt_model", $("create-video-model").value);
  payload.append("image_prompt_model", $("create-image-model").value);
  payload.append("leading_video_scene_count", $("create-leading-video-count").value || "20");
  payload.append("audio_file", audioFile);
  payload.append("script_file", scriptFile);
  return payload;
}

async function createJob(event) {
  event.preventDefault();
  const created = await api("/api/jobs", { method: "POST", body: readCreateFormData() });
  state.modal = { kind: null };
  setNotice("Draft card created.", "success");
  window.location.hash = routeToHash({ view: "job", jobId: created.job.id, tab: "overview" });
}

async function createProject(event) {
  event.preventDefault();
  const audioFile = $("cp-audio")?.files?.[0];
  const scriptFile = $("cp-script")?.files?.[0];
  if (!audioFile || !scriptFile) throw new Error("Choose both audio and script files.");
  const fd = new FormData();
  fd.append("title", $("cp-title").value.trim());
  fd.append("source_language", $("cp-language").value);
  fd.append("scene_planning_provider", $("cp-scene-provider").value);
  fd.append("visual_bible_provider", $("cp-bible-provider").value);
  fd.append("video_prompt_provider", $("cp-video-provider").value);
  fd.append("image_prompt_provider", $("cp-image-provider").value);
  fd.append("scene_planning_model", $("cp-scene-model").value);
  fd.append("visual_bible_model", $("cp-bible-model").value);
  fd.append("video_prompt_model", $("cp-video-model").value);
  fd.append("image_prompt_model", $("cp-image-model").value);
  fd.append("leading_video_scene_count", $("cp-leading-video")?.value || "20");
  fd.append("audio_file", audioFile);
  fd.append("script_file", scriptFile);
  const result = await api("/api/projects", { method: "POST", body: fd });
  state.modal = { kind: null };
  setNotice("Project created.", "success");
  window.location.hash = routeToHash({ view: "project", projectId: result.project.id });
}

async function addLocalization(event) {
  event.preventDefault();
  const projectId = state.modal.projectId;
  if (!projectId) throw new Error("No project selected.");
  const payload = {
    target_language: $("loc-language").value,
    voice_profile_id: $("loc-voice-profile")?.value || null,
    translation_profile_id: $("loc-translation-profile")?.value || null,
  };
  await api(`/api/projects/${encodeURIComponent(projectId)}/localize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  state.modal = { kind: null };
  setNotice("Localization build created.", "success");
  await refreshData();
  renderApp();
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

async function queueJob(stage) {
  // Save current config before queuing so provider/model changes take effect
  if ($("job-scene-provider")) {
    await api(`/api/jobs/${encodeURIComponent(state.detail.job.id)}/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scene_planning_provider: $("job-scene-provider").value,
        visual_bible_provider: $("job-bible-provider").value,
        video_prompt_provider: $("job-video-provider").value,
        image_prompt_provider: $("job-image-provider").value,
        scene_planning_model: $("job-scene-model").value,
        visual_bible_model: $("job-bible-model").value,
        video_prompt_model: $("job-video-model").value,
        image_prompt_model: $("job-image-model").value,
        leading_video_scene_count: Number($("job-leading-video").value),
      }),
    });
  }
  await api(`/api/jobs/${encodeURIComponent(state.detail.job.id)}/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ start_stage: stage }),
  });
  await refreshData();
  renderApp();
  setNotice(`Queued from ${titleCase(stage)}.`, "success");
}

async function finalizeJob() {
  await api(`/api/jobs/${encodeURIComponent(state.detail.job.id)}/finalize`, { method: "POST" });
  await refreshData();
  renderApp();
  setNotice("Final export completed.", "success");
}

async function saveJobConfig() {
  await api(`/api/jobs/${encodeURIComponent(state.detail.job.id)}/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      scene_planning_provider: $("job-scene-provider").value,
      visual_bible_provider: $("job-bible-provider").value,
      video_prompt_provider: $("job-video-provider").value,
      image_prompt_provider: $("job-image-provider").value,
      scene_planning_model: $("job-scene-model").value,
      visual_bible_model: $("job-bible-model").value,
      video_prompt_model: $("job-video-model").value,
      image_prompt_model: $("job-image-model").value,
      leading_video_scene_count: Number($("job-leading-video").value),
    }),
  });
  await refreshData();
  renderApp();
  setNotice("Job config saved.", "success");
}

async function moveJobCard() {
  await api(`/api/jobs/${encodeURIComponent(state.detail.job.id)}/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ board_status: $("job-board-status").value }),
  });
  await refreshData();
  renderApp();
  setNotice("Board column updated.", "success");
}

async function deleteJobCard(jobId, jobTitle = "this card") {
  const confirmed = window.confirm(`Delete "${jobTitle}"?\n\nThis removes the card and its generated files from the project.`);
  if (!confirmed) return;
  await api(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
  state.jobs = state.jobs.filter((job) => job.id !== jobId);
  const deletingOpenJob = state.route.view === "job" && state.route.jobId === jobId;
  if (deletingOpenJob) {
    state.detail = null;
    state.promptDraftVideoFull = "";
    state.promptDraftImageFull = "";
    window.location.hash = routeToHash({ view: "dashboard" });
  } else {
    renderApp();
    await refreshData();
    renderApp();
  }
  setNotice(`Deleted "${jobTitle}".`, "success");
}

async function saveTimeline() {
  const scenes = Array.from(document.querySelectorAll("[data-scene-row]")).map((row, index) => {
    const current = state.detail.artifacts.timeline[index];
    return {
      scene_id: current.scene_id,
      start: current.start,
      end: current.end,
      duration: current.duration,
      asset_type: row.querySelector("[data-field='asset_type']").value,
      text: row.querySelector("[data-field='text']").value,
      visual_intent: row.querySelector("[data-field='visual_intent']").value,
      notes: row.querySelector("[data-field='notes']").value,
    };
  });
  await api(`/api/jobs/${encodeURIComponent(state.detail.job.id)}/review/timeline`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenes }),
  });
  await refreshData();
  renderApp();
  setNotice("Timeline draft saved.", "success");
}

function buildVisualBiblePayload() {
  const characters = Array.from(document.querySelectorAll("[data-character-row]")).map((row) => ({
    character_id: row.querySelector("[data-field='character_id']").value,
    label: row.querySelector("[data-field='label']").value,
    visual_description: row.querySelector("[data-field='visual_description']").value,
    wardrobe: row.querySelector("[data-field='wardrobe']").value,
    demeanor: row.querySelector("[data-field='demeanor']").value,
    usage_notes: row.querySelector("[data-field='usage_notes']").value,
  }));
  return {
    world_style: {
      setting: $("bible-setting").value,
      look: $("bible-look").value,
      palette: $("bible-palette").value,
      lighting: $("bible-lighting").value,
      camera_language: $("bible-camera").value,
      negative_rules: $("bible-negative").value,
    },
    continuity_rules: $("bible-continuity")
      .value.split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean),
    environment_rules: $("bible-environment")
      .value.split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean),
    characters,
  };
}

async function saveVisualBible() {
  await api(`/api/jobs/${encodeURIComponent(state.detail.job.id)}/review/visual-bible`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ visual_bible: buildVisualBiblePayload() }),
  });
  await refreshData();
  renderApp();
  setNotice("Consistency guide saved.", "success");
}

async function savePrompts() {
  const videoPrompts = $("prompt-editor-video")
    .value.split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const imagePrompts = $("prompt-editor-image")
    .value.split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  await api(`/api/jobs/${encodeURIComponent(state.detail.job.id)}/review/prompts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_prompts: videoPrompts, image_prompts: imagePrompts }),
  });
  await refreshData();
  renderApp();
  setNotice("Prompt drafts saved.", "success");
}

function addCharacterCard() {
  const container = $("character-editor");
  const nextIndex = container.querySelectorAll("[data-character-row]").length + 1;
  container.insertAdjacentHTML(
    "beforeend",
    `
      <details class="editor-card" data-character-row="${nextIndex - 1}" open>
        <summary>
          <div class="summary-main">
            <div class="summary-title">Character ${nextIndex}</div>
            <div class="summary-copy">Add a locked description for consistency.</div>
          </div>
        </summary>
        <div class="editor-card-body">
          <div class="form-grid">
            <label class="field">
              <span class="field-label">Character ID</span>
              <input data-field="character_id" value="character_${String(nextIndex).padStart(3, "0")}" />
            </label>
            <label class="field">
              <span class="field-label">Label</span>
              <input data-field="label" value="" />
            </label>
          </div>
          <div class="form-grid">
            <label class="field">
              <span class="field-label">Visual description</span>
              <textarea data-field="visual_description"></textarea>
            </label>
            <label class="field">
              <span class="field-label">Wardrobe</span>
              <textarea data-field="wardrobe"></textarea>
            </label>
            <label class="field">
              <span class="field-label">Demeanor</span>
              <textarea data-field="demeanor"></textarea>
            </label>
            <label class="field">
              <span class="field-label">Usage notes</span>
              <textarea data-field="usage_notes"></textarea>
            </label>
          </div>
        </div>
      </details>`
  );
}

document.addEventListener("click", async (event) => {
  if (event.target.matches("[data-modal-backdrop]")) {
    state.modal = { kind: null };
    renderApp();
    resetAutoRefresh();
    return;
  }
  const target = event.target.closest("[data-nav], [data-open-job], [data-filter-status], [data-job-tab], [data-refresh], [data-theme-toggle], [data-prepare-language], [data-queue-job], [data-job-action], [data-add-character], [data-open-create-modal], [data-close-modal], [data-delete-job], [data-live-log], [data-live-log-close], [data-live-log-tab], [data-open-project], [data-open-create-project], [data-delete-project], [data-open-build], [data-build-tab], [data-queue-build], [data-delete-build], [data-open-loc-modal], [data-tts-action], [data-worker-action], [data-delete-voice-profile], [data-create-voice-profile], [data-test-voice], [data-create-translation-profile], [data-delete-translation-profile], [data-open-niche-project], [data-open-create-niche], [data-delete-niche-project], [data-open-episode], [data-open-submit-episode], [data-queue-episode], [data-delete-episode]");
  if (!target) return;
  event.preventDefault();
  try {
    if (target.dataset.openCreateModal) {
      state.modal = { kind: "create" };
      renderApp();
      resetAutoRefresh();
      return;
    }
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
    if (target.dataset.openJob) {
      window.location.hash = routeToHash({ view: "job", jobId: target.dataset.openJob, tab: "overview" });
      return;
    }
    if (target.dataset.filterStatus) {
      state.boardFilter = target.dataset.filterStatus;
      renderApp();
      return;
    }
    if (target.dataset.jobTab) {
      window.location.hash = routeToHash({ view: "job", jobId: state.detail?.job?.id, tab: target.dataset.jobTab });
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
    if (target.dataset.queueJob) {
      await api(`/api/jobs/${encodeURIComponent(target.dataset.queueJob)}/queue`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_stage: target.dataset.stage || "alignment" }),
      });
      await refreshData();
      renderApp();
      setNotice("Job queued.", "success");
      return;
    }
    if (target.dataset.deleteJob) {
      await deleteJobCard(target.dataset.deleteJob, target.dataset.jobTitle || target.dataset.deleteJob);
      return;
    }
    if (target.dataset.openProject) {
      window.location.hash = routeToHash({ view: "project", projectId: target.dataset.openProject });
      return;
    }
    if (target.dataset.openCreateProject) {
      state.modal = { kind: "create-project" };
      renderApp();
      resetAutoRefresh();
      return;
    }
    if (target.dataset.deleteProject) {
      if (!confirm(`Delete project "${target.dataset.projectTitle || target.dataset.deleteProject}"? This cannot be undone.`)) return;
      await api(`/api/projects/${encodeURIComponent(target.dataset.deleteProject)}`, { method: "DELETE" });
      await refreshData();
      renderApp();
      setNotice("Project deleted.", "success");
      return;
    }
    if (target.dataset.openBuild) {
      window.location.hash = routeToHash({ view: "build", buildId: target.dataset.openBuild, tab: "overview" });
      return;
    }
    if (target.dataset.buildTab) {
      const buildId = state.buildDetail?.id || state.route.buildId;
      window.location.hash = routeToHash({ view: "build", buildId, tab: target.dataset.buildTab });
      return;
    }
    if (target.dataset.queueBuild) {
      await api(`/api/builds/${encodeURIComponent(target.dataset.queueBuild)}/queue`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_stage: target.dataset.stage || null }),
      });
      await refreshData();
      renderApp();
      setNotice("Build queued.", "success");
      return;
    }
    if (target.dataset.deleteBuild) {
      if (!confirm("Delete this build? This cannot be undone.")) return;
      await api(`/api/builds/${encodeURIComponent(target.dataset.deleteBuild)}`, { method: "DELETE" });
      await refreshData();
      renderApp();
      setNotice("Build deleted.", "success");
      return;
    }
    if (target.dataset.openLocModal) {
      state.modal = { kind: "add-localization", projectId: target.dataset.openLocModal };
      renderApp();
      resetAutoRefresh();
      return;
    }
    if (target.dataset.ttsAction) {
      const jobId = state.buildDetail?.build?.tts_job_id;
      if (!jobId) return;
      await api(`/api/tts-jobs/${encodeURIComponent(jobId)}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: target.dataset.ttsAction }),
      });
      await refreshData();
      renderApp();
      setNotice(`TTS ${target.dataset.ttsAction} sent.`, "success");
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
    if (target.dataset.addCharacter) {
      addCharacterCard();
      return;
    }
    if (target.dataset.liveLog != null) {
      liveLogOpen = true;
      renderApp();
      startLiveLogPolling();
      return;
    }
    if (target.dataset.liveLogClose != null) {
      liveLogOpen = false;
      stopLiveLogPolling();
      renderApp();
      return;
    }
    if (target.dataset.liveLogTab) {
      document.querySelectorAll(".live-log-tabs .tab-link").forEach((el) => el.classList.remove("is-active"));
      target.classList.add("is-active");
      fetchAndRenderLiveLog();
      return;
    }
    if (target.dataset.jobAction) {
      if (target.dataset.jobAction === "queue") await queueJob(target.dataset.stage);
      else if (target.dataset.jobAction === "finalize") await finalizeJob();
      else if (target.dataset.jobAction === "save-config") await saveJobConfig();
      else if (target.dataset.jobAction === "move-card") await moveJobCard();
      else if (target.dataset.jobAction === "save-timeline") await saveTimeline();
      else if (target.dataset.jobAction === "save-bible") await saveVisualBible();
      else if (target.dataset.jobAction === "save-prompts") await savePrompts();
      return;
    }
  } catch (error) {
    setNotice(error.message, "error");
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target;
  try {
    if (form.id === "create-form") await createJob(event);
    else if (form.id === "create-project-form") await createProject(event);
    else if (form.id === "add-localization-form") await addLocalization(event);
    else if (form.id === "create-voice-profile-form") await createVoiceProfile(event);
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
    "create-scene-planning": "create-scene-model",
    "create-visual-bible": "create-bible-model",
    "create-video-prompt": "create-video-model",
    "create-image-prompt": "create-image-model",
    "cp-scene-provider": "cp-scene-model",
    "cp-bible-provider": "cp-bible-model",
    "cp-video-provider": "cp-video-model",
    "cp-image-provider": "cp-image-model",
    "settings-scene": "settings-scene-model",
    "settings-bible": "settings-bible-model",
    "settings-video": "settings-video-model",
    "settings-image": "settings-image-model",
    "job-scene-provider": "job-scene-model",
    "job-bible-provider": "job-bible-model",
    "job-video-provider": "job-video-model",
    "job-image-provider": "job-image-model",
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
