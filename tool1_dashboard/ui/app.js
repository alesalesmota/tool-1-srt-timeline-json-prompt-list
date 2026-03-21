const BOARD_STATUSES = ["Draft", "Queued", "Running", "Review", "Done", "Needs Attention"];
const STAGES = [
  "alignment",
  "planning_prep",
  "scene_planning",
  "visual_bible",
  "video_prompt_generation",
  "image_prompt_generation",
];
const JOB_TABS = ["overview", "timeline", "bible", "prompts", "runs"];
const PROVIDERS = ["claude", "codex"];
const REFRESH_INTERVAL_MS = 5000;

const state = {
  jobs: [],
  health: null,
  settings: null,
  templates: [],
  detail: null,
  route: { view: "dashboard" },
  boardFilter: "All",
  promptDraftFull: "",
  theme: "dark",
  notice: { text: "", tone: "neutral" },
};

let refreshTimer = null;
let noticeTimer = null;

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
  return `#/${route.view || "dashboard"}`;
}

function parseRoute() {
  const hash = window.location.hash.replace(/^#/, "").replace(/^\/+/, "");
  if (!hash) return { view: "dashboard" };
  const parts = hash.split("/").filter(Boolean);
  if (parts[0] === "jobs" && parts[1]) {
    const tab = JOB_TABS.includes(parts[2]) ? parts[2] : "overview";
    return { view: "job", jobId: decodeURIComponent(parts[1]), tab };
  }
  if (["dashboard", "create", "settings", "templates"].includes(parts[0])) {
    return { view: parts[0] };
  }
  return { view: "dashboard" };
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

function countByBoard() {
  const counts = Object.fromEntries(BOARD_STATUSES.map((status) => [status, 0]));
  for (const job of state.jobs) counts[job.board_status] = (counts[job.board_status] || 0) + 1;
  return counts;
}

function statusBadge(label, tone = "neutral") {
  return `<span class="badge" data-tone="${tone}">${esc(label)}</span>`;
}

function healthBadge(label, okState) {
  const tone = okState === "warn" ? "warn" : okState ? "success" : "error";
  return statusBadge(label, tone);
}

function artifactLink(jobId, key, label) {
  return `<a class="link-button" href="/api/jobs/${encodeURIComponent(jobId)}/artifacts/${key}" target="_blank" rel="noreferrer">${esc(label)}</a>`;
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
  return {
    title: "Workflow board",
    copy: "Board-first view for diagnosing the pipeline. Open a card only when you want to inspect or edit it.",
  };
}

function autoRefreshAllowed(route) {
  if (route.view === "dashboard") return true;
  if (route.view === "job" && ["overview", "runs"].includes(route.tab || "overview")) return true;
  return false;
}

function renderSidebar() {
  const counts = countByBoard();
  const providers = state.health?.providers || {};
  const alignment = state.health?.alignment || {};
  const navItems = [
    { view: "dashboard", label: "Board", count: state.jobs.length },
    { view: "create", label: "Create job", count: counts.Draft || 0 },
    { view: "settings", label: "Settings", count: "" },
    { view: "templates", label: "Templates", count: state.templates.length },
  ];

  $("sidebar").innerHTML = `
    <section class="sidebar-brand">
      <div class="eyebrow">CLI-first workspace</div>
      <div class="brand-title">Tool 1</div>
      <div class="brand-copy">Board-first workflow with separate pages for creation, review, and system controls. The board is the main place to scan the pipeline left to right.</div>
    </section>

    <section class="sidebar-section">
      <div class="eyebrow">Navigation</div>
      <div class="sidebar-nav">
        ${navItems
          .map(
            (item) => `
            <button type="button" class="nav-link" data-nav="${item.view}" aria-current="${state.route.view === item.view ? "page" : "false"}">
              <span>${esc(item.label)}</span>
              ${item.count !== "" ? `<span class="nav-count">${esc(item.count)}</span>` : ""}
            </button>`
          )
          .join("")}
      </div>
    </section>

    <section class="sidebar-section">
      <div class="eyebrow">Pipeline snapshot</div>
      <div class="badge-row" style="margin-top:10px;">
        ${BOARD_STATUSES.map((status) => statusBadge(`${status}: ${counts[status] || 0}`, toneFromBoardStatus(status))).join("")}
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
      <button type="button" class="button button-ghost" data-refresh="true">Refresh</button>
      <button type="button" class="button ${state.theme === "dark" ? "button-primary" : "button-ghost"}" data-theme-toggle="true">
        ${state.theme === "dark" ? "Light mode" : "Dark mode"}
      </button>
    </div>
  `;
}

function renderJobCard(job) {
  return `
    <article class="kanban-card">
      <div class="section-head" style="margin-bottom:8px;">
        <div>
          <button type="button" class="job-title-button" data-open-job="${esc(job.id)}">
            <h3>${esc(job.title)}</h3>
          </button>
          <div class="tiny mono">${esc(job.id)}</div>
        </div>
        ${statusBadge(titleCase(job.current_stage), toneFromRunStatus(job.pipeline_status))}
      </div>
      <div class="job-meta">
        <div>Stage: ${esc(titleCase(job.current_stage))}</div>
        <div>Providers: ${esc(providerLabel(job.scene_planning_provider))} / ${esc(providerLabel(job.video_prompt_provider))}</div>
        <div>First video scenes: ${esc(job.leading_video_scene_count || 0)} · Warnings: ${esc(job.warning_count || 0)}</div>
        <div>Updated: ${esc(formatDate(job.updated_at))}</div>
        ${job.last_error ? `<div>Issue: ${esc(shortText(job.last_error, 110))}</div>` : ""}
      </div>
      <div class="button-row">
        <button type="button" class="button button-primary" data-open-job="${esc(job.id)}">Open</button>
        <button type="button" class="button" data-queue-job="${esc(job.id)}" data-stage="${esc(job.review_ready ? "scene_planning" : "alignment")}">Run</button>
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

function renderDashboard() {
  const counts = countByBoard();
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
          <button type="button" class="button button-primary" data-nav="create">New job</button>
          <button type="button" class="button" data-nav="settings">Settings</button>
        </div>
      </div>
      <div class="helper section-copy">This is the main surface again. Each column is always visible so you can diagnose the workflow by scanning left to right, like a Trello board.</div>
      <div class="badge-row" style="margin-top:16px;">
        ${statusBadge(`Total cards: ${state.jobs.length}`, "active")}
        ${statusBadge(`Warnings: ${warningTotal}`, warningTotal ? "warn" : "success")}
        ${healthBadge(`Codex ${providers.codex?.logged_in ? "ready" : providers.codex?.available ? "login" : "missing"}`, providers.codex?.logged_in ? true : providers.codex?.available ? "warn" : false)}
        ${healthBadge(`Claude ${providers.claude?.logged_in ? "ready" : providers.claude?.available ? "login" : "missing"}`, providers.claude?.logged_in ? true : providers.claude?.available ? "warn" : false)}
        ${healthBadge(`MFA ${alignment.mfa ? "ready" : "check"}`, alignment.mfa ? true : "warn")}
      </div>
      <div class="kanban-board" style="margin-top:18px;">
        ${BOARD_STATUSES.map(
          (status) => `
            <section class="kanban-column">
              <div class="kanban-column-head">
                <div>
                  <div class="kanban-column-title">${esc(status)}</div>
                  <div class="tiny">${esc(counts[status] || 0)} card(s)</div>
                </div>
                ${statusBadge(`${counts[status] || 0}`, toneFromBoardStatus(status))}
              </div>
              <div class="kanban-card-list">
                ${state.jobs
                  .filter((job) => job.board_status === status)
                  .map(renderJobCard)
                  .join("") || `<div class="kanban-empty">No cards here.</div>`}
              </div>
            </section>`
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
          <div class="helper">1. Create a card in <strong>Create job</strong>.</div>
          <div class="helper">2. Watch it move across the board columns.</div>
          <div class="helper">3. Open a card only when you want to inspect, rerun, or edit it.</div>
          <div class="helper">4. Use the column placement to spot workflow bottlenecks quickly.</div>
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
  `;
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
            <button type="button" class="button" data-prepare-language="true">Prepare selected language</button>
            <button type="button" class="button button-ghost" data-nav="dashboard">Back to dashboard</button>
          </div>
        </section>
      </section>

      <section class="surface">
        <div class="section-head">
          <div>
            <div class="eyebrow">Pipeline defaults</div>
            <h2 class="section-title">Per-job overrides</h2>
          </div>
        </div>
        <div class="form-grid-3">
          <label class="field">
            <span class="field-label">Scene planning provider</span>
            <select id="create-scene-planning">${providerOptions(defaults.default_scene_planning_provider || "claude")}</select>
          </label>
          <label class="field">
            <span class="field-label">Visual bible provider</span>
            <select id="create-visual-bible">${providerOptions(defaults.default_visual_bible_provider || "claude")}</select>
          </label>
          <label class="field">
            <span class="field-label">Video prompt provider</span>
            <select id="create-video-prompt">${providerOptions(defaults.default_video_prompt_provider || "codex")}</select>
          </label>
          <label class="field">
            <span class="field-label">Image prompt provider</span>
            <select id="create-image-prompt">${providerOptions(defaults.default_image_prompt_provider || "codex")}</select>
          </label>
          <label class="field">
            <span class="field-label">First scenes as video</span>
            <input id="create-leading-video-count" type="number" min="0" value="${esc(defaults.leading_video_scene_count || 20)}" />
          </label>
        </div>
        <div class="button-row" style="margin-top:18px;">
          <button type="submit" class="button button-primary">Create draft</button>
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
          <div class="form-grid-3">
            <label class="field">
              <span class="field-label">Scene planning</span>
              <select id="settings-scene">${providerOptions(settings.default_scene_planning_provider || "claude")}</select>
            </label>
            <label class="field">
              <span class="field-label">Visual bible</span>
              <select id="settings-bible">${providerOptions(settings.default_visual_bible_provider || "claude")}</select>
            </label>
            <label class="field">
              <span class="field-label">Video prompts</span>
              <select id="settings-video">${providerOptions(settings.default_video_prompt_provider || "codex")}</select>
            </label>
            <label class="field">
              <span class="field-label">Image prompts</span>
              <select id="settings-image">${providerOptions(settings.default_image_prompt_provider || "codex")}</select>
            </label>
            <label class="field">
              <span class="field-label">First scenes as video</span>
              <input id="settings-leading-video" type="number" min="0" value="${esc(settings.leading_video_scene_count || 20)}" />
            </label>
            <label class="field">
              <span class="field-label">Prompt batch size</span>
              <input id="settings-batch-size" type="number" min="1" value="${esc(settings.prompt_batch_size || 24)}" />
            </label>
            <label class="field">
              <span class="field-label">Planning chunk seconds</span>
              <input id="settings-chunk-seconds" type="number" min="1" value="${esc(settings.planning_chunk_seconds || 360)}" />
            </label>
            <label class="field">
              <span class="field-label">Planning overlap seconds</span>
              <input id="settings-overlap-seconds" type="number" min="0" value="${esc(settings.planning_overlap_seconds || 30)}" />
            </label>
          </div>
          <div class="button-row">
            <button type="submit" class="button button-primary">Save settings</button>
            <button type="button" class="button" data-nav="templates">Open templates</button>
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
          <button type="button" class="button" data-nav="settings">Back to settings</button>
        </div>
      </div>
      <div class="helper section-copy">These instructions are now separated from the dashboard so you only touch them when you really mean to change stage behavior.</div>
      <form id="templates-form" class="stack" style="margin-top:18px;">
        <div class="form-grid">
          <label class="field">
            <span class="field-label">Stage</span>
            <select id="template-stage">
              <option value="scene_planning" ${selectedStage === "scene_planning" ? "selected" : ""}>Scene planning</option>
              <option value="visual_bible" ${selectedStage === "visual_bible" ? "selected" : ""}>Visual bible</option>
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
          <button type="submit" class="button button-primary">Save template</button>
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
    job.visual_bible_path ? artifactLink(job.id, "visual_bible", "Visual bible") : "",
    job.prompt_list_draft_path ? artifactLink(job.id, "prompt_list_draft", "Prompt draft") : "",
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

function renderJobOverview(job, artifacts, runs) {
  const latest = latestRunMap(runs);
  return `
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
            <button type="button" class="button button-primary" data-job-action="queue" data-stage="alignment">Run full</button>
            <button type="button" class="button" data-job-action="queue" data-stage="scene_planning">Rerun scenes</button>
            <button type="button" class="button" data-job-action="queue" data-stage="visual_bible">Rerun visual bible</button>
            <button type="button" class="button" data-job-action="queue" data-stage="video_prompt_generation">Rerun video prompts</button>
            <button type="button" class="button" data-job-action="queue" data-stage="image_prompt_generation">Rerun image prompts</button>
            <button type="button" class="button button-danger" data-job-action="finalize">Finalize export</button>
          </div>
          <form id="job-config-form" class="stack" style="margin-top:18px;">
            <div class="form-grid-3">
              <label class="field">
                <span class="field-label">Board column</span>
                <select id="job-board-status">${BOARD_STATUSES.map((status) => `<option value="${status}" ${job.board_status === status ? "selected" : ""}>${esc(status)}</option>`).join("")}</select>
              </label>
              <label class="field">
                <span class="field-label">Scene planning</span>
                <select id="job-scene-provider">${providerOptions(job.scene_planning_provider)}</select>
              </label>
              <label class="field">
                <span class="field-label">Visual bible</span>
                <select id="job-bible-provider">${providerOptions(job.visual_bible_provider)}</select>
              </label>
              <label class="field">
                <span class="field-label">Video prompts</span>
                <select id="job-video-provider">${providerOptions(job.video_prompt_provider)}</select>
              </label>
              <label class="field">
                <span class="field-label">Image prompts</span>
                <select id="job-image-provider">${providerOptions(job.image_prompt_provider)}</select>
              </label>
              <label class="field">
                <span class="field-label">First scenes as video</span>
                <input id="job-leading-video" type="number" min="0" value="${esc(job.leading_video_scene_count || 20)}" />
              </label>
            </div>
            <div class="button-row">
              <button type="button" class="button button-primary" data-job-action="save-config">Save job config</button>
              <button type="button" class="button" data-job-action="move-card">Move card</button>
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
            ${renderValidationPreview("Visual bible", artifacts.visual_bible_validation)}
            ${renderValidationPreview("Prompt draft", artifacts.prompt_validation)}
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
  const world = artifacts.visual_bible?.world_style || {};
  return `
    <section class="surface">
      <div class="section-head">
        <div>
          <div class="eyebrow">Visual bible</div>
          <h2 class="section-title">Style and character consistency</h2>
        </div>
        <div class="button-row">
          <button type="button" class="button" data-add-character="true">Add character</button>
          <button type="button" class="button button-primary" data-job-action="save-bible">Save visual bible</button>
        </div>
      </div>
      <div class="helper">${esc([...(artifacts.visual_bible_validation?.errors || []), ...(artifacts.visual_bible_validation?.warnings || [])].join(" | ") || "No validation issues logged.")}</div>
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
          <textarea id="bible-continuity">${esc((artifacts.visual_bible?.continuity_rules || []).join("\n"))}</textarea>
        </label>
        <label class="field">
          <span class="field-label">Environment rules (one per line)</span>
          <textarea id="bible-environment">${esc((artifacts.visual_bible?.environment_rules || []).join("\n"))}</textarea>
        </label>
      </div>
      <div style="margin-top:18px;">${renderCharacterCards(artifacts.visual_bible?.characters || [])}</div>
    </section>
  `;
}

function renderJobPrompts(artifacts) {
  const promptText = state.promptDraftFull || artifacts.prompt_list || "";
  return `
    <section class="surface">
      <div class="section-head">
        <div>
          <div class="eyebrow">Prompts</div>
          <h2 class="section-title">One line per prompt, now on its own page</h2>
        </div>
        <div class="button-row">
          <button type="button" class="button button-primary" data-job-action="save-prompts">Save prompt draft</button>
        </div>
      </div>
      <div class="helper">${esc([...(artifacts.prompt_validation?.errors || []), ...(artifacts.prompt_validation?.warnings || [])].join(" | ") || "No validation issues logged.")}</div>
      <textarea id="prompt-editor" class="is-large" style="margin-top:18px;">${esc(promptText.trim())}</textarea>
      <details class="editor-card" style="margin-top:18px;">
        <summary>
          <div class="summary-main">
            <div class="summary-title">Prompt blueprint preview</div>
            <div class="summary-copy">Structured metadata stays separate from the final text list.</div>
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
    content = `<section class="surface"><div class="section-head"><div><div class="eyebrow">Timeline</div><h2 class="section-title">Scene review</h2></div><div class="button-row"><button type="button" class="button button-primary" data-job-action="save-timeline">Save timeline draft</button></div></div><div class="helper">${esc([...(artifacts.timeline_validation?.errors || []), ...(artifacts.timeline_validation?.warnings || [])].join(" | ") || "No validation issues logged.")}</div><div style="margin-top:18px;">${renderSceneEditor(artifacts.timeline || [])}</div></section>`;
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
          <button type="button" class="button" data-nav="dashboard">Back to dashboard</button>
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
          (tab) => `<button type="button" class="tab-link ${routeTab === tab ? "is-active" : ""}" data-job-tab="${tab}">${esc(titleCase(tab))}</button>`
        ).join("")}
      </div>
    </nav>

    ${content}
  `;
}

function renderApp() {
  renderSidebar();
  renderTopbar();
  if (state.route.view === "create") renderCreate();
  else if (state.route.view === "settings") renderSettings();
  else if (state.route.view === "templates") renderTemplates();
  else if (state.route.view === "job") renderJob();
  else renderDashboard();
}

async function refreshData({ preserveNotice = true } = {}) {
  const route = state.route;
  const [board, health, settings] = await Promise.all([api("/api/board"), api("/api/health"), api("/api/settings")]);
  state.jobs = board.jobs || [];
  state.health = health;
  state.settings = settings.settings || {};
  state.templates = settings.templates || [];

  if (route.view === "job" && route.jobId) {
    try {
      state.detail = await api(`/api/jobs/${encodeURIComponent(route.jobId)}`);
      if (route.tab === "prompts" && state.detail.job?.prompt_list_draft_path) {
        state.promptDraftFull = await apiText(`/api/jobs/${encodeURIComponent(route.jobId)}/artifacts/prompt_list_draft`);
      } else {
        state.promptDraftFull = "";
      }
    } catch (error) {
      state.detail = null;
      state.promptDraftFull = "";
      if (!preserveNotice) throw error;
      setNotice(error.message, "error");
      window.location.hash = routeToHash({ view: "dashboard" });
      return;
    }
  } else {
    state.detail = null;
    state.promptDraftFull = "";
  }
}

function resetAutoRefresh() {
  if (refreshTimer) window.clearInterval(refreshTimer);
  if (!autoRefreshAllowed(state.route)) return;
  refreshTimer = window.setInterval(() => {
    refreshData().then(renderApp).catch(() => {});
  }, REFRESH_INTERVAL_MS);
}

async function syncRouteAndRender() {
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
  payload.append("leading_video_scene_count", $("create-leading-video-count").value || "20");
  payload.append("audio_file", audioFile);
  payload.append("script_file", scriptFile);
  return payload;
}

async function createJob(event) {
  event.preventDefault();
  const created = await api("/api/jobs", { method: "POST", body: readCreateFormData() });
  setNotice("Draft card created.", "success");
  window.location.hash = routeToHash({ view: "job", jobId: created.job.id, tab: "overview" });
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
  setNotice("Visual bible saved.", "success");
}

async function savePrompts() {
  const prompts = $("prompt-editor")
    .value.split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  await api(`/api/jobs/${encodeURIComponent(state.detail.job.id)}/review/prompts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompts }),
  });
  await refreshData();
  renderApp();
  setNotice("Prompt draft saved.", "success");
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
  const target = event.target.closest("[data-nav], [data-open-job], [data-filter-status], [data-job-tab], [data-refresh], [data-theme-toggle], [data-prepare-language], [data-queue-job], [data-job-action], [data-add-character]");
  if (!target) return;
  event.preventDefault();
  try {
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
    if (target.dataset.addCharacter) {
      addCharacterCard();
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
  }
});

window.addEventListener("hashchange", () => {
  syncRouteAndRender().catch((error) => setNotice(error.message, "error"));
});

window.addEventListener("DOMContentLoaded", () => {
  bootTheme();
  if (!window.location.hash) {
    window.location.hash = routeToHash({ view: "dashboard" });
    return;
  }
  syncRouteAndRender().catch((error) => setNotice(error.message, "error"));
});
