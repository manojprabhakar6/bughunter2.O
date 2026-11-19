const API_STORAGE_KEY = "bug-hunter-api-url";
const TOKEN_STORAGE_KEY = "bh_token";
const USER_STORAGE_KEY = "bh_email";
const DEMO_STORAGE_KEY = "bh_demo";
const RESULT_STORAGE_KEY = "bh_last_result";
const NOTIFICATIONS_STORAGE_KEY = "bh_notifications";
const localHostname = ["127.0.0.1", "localhost", "bug-hunter.localhost"].includes(
  window.location.hostname,
);
const DEFAULT_API_URL = localHostname
  ? `http://${window.location.hostname === "bug-hunter.localhost" ? "127.0.0.1" : window.location.hostname}:8000`
  : window.location.origin;
const MAX_SOURCE_BYTES = 5_000_000;
const MAX_HIGHLIGHT_SOURCE_CHARS = 200_000;
const MAX_NOTIFICATIONS = 8;
const API_TIMEOUT_MS = 60_000;

const icons = {
  logo: '<path d="M12 3.5a6.5 6.5 0 0 0-6.5 6.5v3.5a6.5 6.5 0 0 0 13 0V10A6.5 6.5 0 0 0 12 3.5Z"/><path d="M8.5 3.8 7 2m8.5 1.8L17 2M5.5 9H3m16 0h-2.5M7 17l-2 2m12-2 2 2M9.5 10h.01M14.5 10h.01M9 14c1.7 1.4 4.3 1.4 6 0"/>',
  grid: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
  scan: '<path d="M8 4H5a1 1 0 0 0-1 1v3m12-4h3a1 1 0 0 1 1 1v3M4 16v3a1 1 0 0 0 1 1h3m12-4v3a1 1 0 0 1-1 1h-3"/><path d="M8 12h8"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1 1.2.9-1.2 2.1-1.4-.5a7.8 7.8 0 0 1-1.6.9l-.2 1.5h-2.5l-.3-1.5a7.8 7.8 0 0 1-1.6-.9l-1.4.5-1.2-2.1 1.2-.9a7.5 7.5 0 0 1 0-1.8l-1.2-.9 1.2-2.1 1.4.5a7.8 7.8 0 0 1 1.6-.9l.3-1.5h2.5l.2 1.5a7.8 7.8 0 0 1 1.6.9l1.4-.5 1.2 2.1-1.2.9a7.5 7.5 0 0 1 0 1.8Z"/>',
  bell: '<path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-8 12h4"/>',
  logout: '<path d="M10 17l5-5-5-5m5 5H3"/><path d="M13 4h5a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-5"/>',
  upload: '<path d="M12 16V4m-5 5 5-5 5 5"/><path d="M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  shield: '<path d="M12 3 19 6v5c0 4.5-2.8 7.5-7 10-4.2-2.5-7-5.5-7-10V6l7-3Z"/><path d="m9 12 2 2 4-4"/>',
  code: '<path d="m8 8-4 4 4 4m8-8 4 4-4 4m-2-11-4 14"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  alert: '<path d="M12 3 2.8 19h18.4L12 3Z"/><path d="M12 9v4m0 3h.01"/>',
  file: '<path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10Z"/><path d="M13 3v7h7m-11 4 2-2 2 2m-2-2v5"/>',
  trash: '<path d="M3 6h18m-2 0-.9 14H5.9L5 6m4 0V4h6v2m-5 4v7m4-7v7"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
  download: '<path d="M12 4v11m-5-5 5 5 5-5"/><path d="M5 16v4h14v-4"/>',
  eye: '<path d="M2.5 12s3.4-6 9.5-6 9.5 6 9.5 6-3.4 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.5"/>',
  eyeOff: '<path d="m3 3 18 18M10.6 6.2A10.7 10.7 0 0 1 12 6c6.1 0 9.5 6 9.5 6a15 15 0 0 1-3 3.6M6.2 6.2C3.9 7.7 2.5 12 2.5 12s3.4 6 9.5 6c1 0 2-.2 2.8-.5"/><path d="M10 10a2.8 2.8 0 0 0 4 4"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
};

const state = {
  route: window.location.pathname,
  email: localStorage.getItem(USER_STORAGE_KEY) || "",
  isDemo: localStorage.getItem(DEMO_STORAGE_KEY) === "true",
  language: "python",
  filename: "pasted.py",
  code: "# Paste your Python code here\n",
  programGoal: "",
  simulationInput: "",
  expectedOutput: "",
  verifyExpectedOutput: false,
  file: null,
  lastResult: loadLastResult(),
  busy: false,
  error: "",
  success: "",
  history: [],
  notifications: loadNotifications(),
  notificationsOpen: false,
  notificationError: "",
};

const app = document.querySelector("#app");

function icon(name, size = 18) {
  return `<svg aria-hidden="true" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${icons[name] || icons.code}</svg>`;
}

function loadLastResult() {
  try {
    const saved = sessionStorage.getItem(RESULT_STORAGE_KEY);
    return saved ? JSON.parse(saved) : null;
  } catch {
    sessionStorage.removeItem(RESULT_STORAGE_KEY);
    return null;
  }
}

function loadNotifications() {
  try {
    const saved = sessionStorage.getItem(NOTIFICATIONS_STORAGE_KEY);
    const notifications = saved ? JSON.parse(saved) : [];
    return Array.isArray(notifications)
      ? notifications
          .filter((item) => (
            Number.isSafeInteger(item.id) &&
            typeof item.filename === "string" &&
            ["python", "c"].includes(item.language) &&
            Number.isSafeInteger(item.findingCount) &&
            Number.isSafeInteger(item.fixedCount) &&
            typeof item.createdAt === "string"
          ))
          .map((item) => ({ ...item, read: item.read === true }))
          .slice(0, MAX_NOTIFICATIONS)
      : [];
  } catch (error) {
    console.warn("Could not load recent notifications.", error);
    sessionStorage.removeItem(NOTIFICATIONS_STORAGE_KEY);
    return [];
  }
}

function saveNotifications() {
  try {
    sessionStorage.setItem(
      NOTIFICATIONS_STORAGE_KEY,
      JSON.stringify(state.notifications),
    );
    state.notificationError = "";
  } catch (error) {
    console.warn("Could not save recent notifications.", error);
    state.notificationError = "Recent notifications could not be saved in this browser.";
  }
}

function recordAnalysisNotification(result) {
  const findings = result.findings || [];
  const notification = {
    id: result.id,
    filename: result.filename,
    language: result.language,
    findingCount: findings.length,
    fixedCount: findings.filter((finding) => finding.fixed).length,
    createdAt: result.updatedAt || result.createdAt,
    read: false,
  };
  state.notifications = [
    notification,
    ...state.notifications.filter((item) => item.id !== result.id),
  ].slice(0, MAX_NOTIFICATIONS);
  saveNotifications();
}

function apiBase() {
  return (localStorage.getItem(API_STORAGE_KEY) || DEFAULT_API_URL).replace(/\/+$/, "");
}

class ApiError extends Error {
  constructor(status, payload) {
    const detail = payload?.detail;
    super(
      typeof detail === "string"
        ? detail
        : detail?.message || `Request failed (${status}).`,
    );
    this.status = status;
    this.detail = detail;
  }
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = localStorage.getItem(TOKEN_STORAGE_KEY);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), API_TIMEOUT_MS);
  let response;
  let payload;
  try {
    response = await fetch(`${apiBase()}${path}`, {
      ...options,
      headers,
      signal: controller.signal,
    });
    if (response.status !== 204) payload = await response.json();
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error.name === "AbortError") {
      throw new Error("The API request timed out. Please check the backend and try again.");
    }
    if (error instanceof SyntaxError) {
      throw new Error("The API returned an invalid response. Please try again.");
    }
    throw new Error(
      "Cannot reach the analysis API. Start the backend at " + apiBase() + ".",
    );
  } finally {
    window.clearTimeout(timeoutId);
  }

  if (response.status === 204) return null;
  if (!response.ok) {
    if (response.status === 401 && token) {
      clearSession();
      navigate("/login");
    }
    throw new ApiError(response.status, payload);
  }
  return payload;
}

function clearSession() {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
  localStorage.removeItem(USER_STORAGE_KEY);
  localStorage.removeItem(DEMO_STORAGE_KEY);
  sessionStorage.removeItem(NOTIFICATIONS_STORAGE_KEY);
  state.email = "";
  state.isDemo = false;
  state.notifications = [];
  state.notificationsOpen = false;
  state.notificationError = "";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => {
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[character];
  });
}

const PYTHON_KEYWORDS = new Set([
  "and", "as", "assert", "async", "await", "break", "class", "continue",
  "def", "del", "elif", "else", "except", "False", "finally", "for", "from",
  "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or",
  "pass", "raise", "return", "True", "try", "while", "with", "yield", "None",
]);
const PYTHON_BUILTINS = new Set([
  "abs", "all", "any", "bool", "dict", "enumerate", "float", "input", "int",
  "len", "list", "max", "min", "open", "print", "range", "round", "set",
  "str", "sum", "tuple", "type", "zip",
]);
const C_KEYWORDS = new Set([
  "auto", "break", "case", "char", "const", "continue", "default", "do",
  "double", "else", "enum", "extern", "float", "for", "goto", "if", "int",
  "long", "register", "return", "short", "signed", "sizeof", "static",
  "struct", "switch", "typedef", "union", "unsigned", "void", "volatile",
  "while", "_Bool",
]);
const PYTHON_TOKEN_PATTERN =
  /#[^\r\n]*|"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:\b\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|\b[A-Za-z_]\w*\b/g;
const C_TOKEN_PATTERN =
  /^[\t ]*#[^\r\n]*|\/\/[^\r\n]*|\/\*[\s\S]*?\*\/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|0[xX][0-9a-fA-F]+|\b\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|\b[A-Za-z_]\w*\b/gm;

function highlightedCode(source, language) {
  if (source.length > MAX_HIGHLIGHT_SOURCE_CHARS) return escapeHtml(source);
  const isC = language === "c";
  const pattern = isC ? C_TOKEN_PATTERN : PYTHON_TOKEN_PATTERN;
  const keywords = isC ? C_KEYWORDS : PYTHON_KEYWORDS;
  const builtins = isC
    ? new Set(["main", "printf", "scanf", "puts", "malloc", "free"])
    : PYTHON_BUILTINS;
  let output = "";
  let cursor = 0;

  for (const match of source.matchAll(pattern)) {
    const token = match[0];
    output += escapeHtml(source.slice(cursor, match.index));
    let tokenClass = "";

    if (isC && /^[\t ]*#/.test(token)) tokenClass = "preprocessor";
    else if (/^(#|\/\/|\/\*)/.test(token)) tokenClass = "comment";
    else if (/^(["'`])/.test(token)) tokenClass = "string";
    else if (/^(?:\d|\.\d)/.test(token)) tokenClass = "number";
    else if (keywords.has(token)) tokenClass = "keyword";
    else if (builtins.has(token)) tokenClass = "builtin";
    else if (/^\w+$/.test(token) && /^\s*\(/.test(source.slice(match.index + token.length))) {
      tokenClass = "function";
    }

    output += tokenClass
      ? `<span class="code-token code-${tokenClass}">${escapeHtml(token)}</span>`
      : escapeHtml(token);
    cursor = match.index + token.length;
  }

  return output + escapeHtml(source.slice(cursor));
}

function refreshCodeHighlight(source) {
  const wrapper = document.querySelector(".code-editor-wrap");
  const highlight = document.querySelector("#code-highlight");
  const lineNumbers = document.querySelector("#code-line-numbers");
  const editor = document.querySelector("#code-input");
  const status = document.querySelector("#highlight-status");
  if (!wrapper || !highlight || !lineNumbers || !editor) return;

  const usePlainText = source.length > MAX_HIGHLIGHT_SOURCE_CHARS;
  wrapper.classList.toggle("editor-plain", usePlainText);
  highlight.hidden = usePlainText;
  if (status) status.hidden = !usePlainText;
  lineNumbers.hidden = usePlainText;
  if (usePlainText) {
    lineNumbers.textContent = "";
  } else {
    lineNumbers.textContent = Array.from(
      { length: source.split("\n").length },
      (_, index) => index + 1,
    ).join("\n");
    highlight.innerHTML = highlightedCode(source, state.language);
    highlight.scrollTop = editor.scrollTop;
    highlight.scrollLeft = editor.scrollLeft;
    lineNumbers.scrollTop = editor.scrollTop;
  }
}

function notificationPanel() {
  if (!state.notificationsOpen) return "";
  const unreadCount = state.notifications.filter((item) => !item.read).length;
  return `
    <section id="notifications-panel" class="notifications-panel" aria-label="Recent analysis notifications">
      <header class="notifications-heading">
        <div><strong>Notifications</strong><span>Recent analysis activity</span></div>
        <button class="text-link mark-read-button" type="button" data-action="mark-notifications-read" ${unreadCount ? "" : "disabled"}>Mark all read</button>
      </header>
      ${state.notificationError ? `<p class="notification-error">${escapeHtml(state.notificationError)}</p>` : ""}
      ${state.notifications.length
        ? `<div class="notifications-list">${state.notifications.map((item) => `
            <button class="notification-entry ${item.read ? "" : "notification-unread"}" type="button" data-notification-id="${item.id}">
              <span class="notification-icon">${icon(item.findingCount ? "scan" : "check", 16)}</span>
              <span class="notification-copy">
                <strong>Analysis complete</strong>
                <span>${escapeHtml(item.filename)} · ${escapeHtml(String(item.language).toUpperCase())}</span>
                <small>${item.findingCount} finding${item.findingCount === 1 ? "" : "s"} · ${item.fixedCount} applied · ${escapeHtml(new Date(item.createdAt).toLocaleString())}</small>
              </span>
              ${item.read ? "" : '<span class="notification-unread-dot" aria-label="Unread"></span>'}
            </button>
          `).join("")}</div>`
        : `<p class="notifications-empty">No recent analyses yet. Completed analyses will appear here.</p>`}
    </section>
  `;
}

function initials(email) {
  return (email.split("@")[0] || "BH").slice(0, 2).toUpperCase();
}

function navigate(path) {
  state.route = path;
  state.error = "";
  state.success = "";
  state.notificationsOpen = false;
  window.history.pushState({}, "", path);
  window.scrollTo(0, 0);
  render();
}

function showMessage(message, kind = "error") {
  const node = document.querySelector("#page-message");
  if (!node) return;
  node.className = `notice notice-${kind}`;
  node.textContent = message;
  node.hidden = false;
}

function prepareFixSound() {
  if (!window.AudioContext) return null;
  try {
    const context = new window.AudioContext();
    if (context.state === "suspended") {
      context.resume().catch((error) => {
        console.warn("Could not enable the fix confirmation sound.", error);
        closeFixSound(context);
      });
    }
    return context;
  } catch (error) {
    console.warn("Could not prepare the fix confirmation sound.", error);
    return null;
  }
}

function closeFixSound(context) {
  if (!context || context.state === "closed") return;
  context.close().catch((error) => {
    console.warn("Could not close the fix confirmation audio.", error);
  });
}

function playFixSuccessSound(context) {
  if (!context || context.state === "closed") return;
  const play = () => {
    if (context.state === "closed") return;
    try {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      const now = context.currentTime;
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(740, now);
      oscillator.frequency.exponentialRampToValueAtTime(1_180, now + 0.07);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.11, now + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.addEventListener("ended", () => closeFixSound(context), {
        once: true,
      });
      oscillator.start(now);
      oscillator.stop(now + 0.18);
    } catch (error) {
      console.warn("Could not play the fix confirmation sound.", error);
      closeFixSound(context);
    }
  };

  if (context.state === "suspended") {
    context.resume().then(play).catch((error) => {
      console.warn("Could not resume the fix confirmation sound.", error);
      closeFixSound(context);
    });
    return;
  }
  play();
}

function successfullyRepaired(result) {
  const findings = result.findings || [];
  return (
    findings.length > 0 &&
    findings.every((finding) => finding.fixed) &&
    result.correctedCode !== result.originalCode &&
    result.simulation?.status !== "error"
  );
}

function authPage(isRegister) {
  const title = isRegister ? "Create your account" : "Welcome back";
  const subtitle = isRegister
    ? "Join Bug Hunter and start debugging"
    : "Sign in to continue debugging";
  const submitText = isRegister ? "Create account" : "Sign in";

  return `
    <main class="auth-screen">
      <section class="auth-card">
        <a class="brand brand-auth" href="/login" data-link>
          <span class="brand-mark">${icon("logo", 24)}</span>
          <span class="brand-name">BUG HUNTER <b>2.0</b></span>
        </a>
        <div class="auth-heading">
          <h1>${title}</h1>
          <p>${subtitle}</p>
        </div>
        <div id="page-message" class="notice" role="status" hidden></div>
        <form id="auth-form" data-register="${isRegister}">
          <label for="email">Email address</label>
          <input
            id="email"
            name="email"
            type="email"
            placeholder="you@example.com"
            autocomplete="email"
            value="${escapeHtml(state.pendingEmail || "")}"
            required
          />
          <div class="label-row">
            <label for="password">Password</label>
            ${isRegister ? '<span class="field-hint">At least 8 characters</span>' : '<a href="#" class="text-link" data-action="forgot-password">Forgot password?</a>'}
          </div>
          <div class="password-field">
            <input
              id="password"
              name="password"
              type="password"
              placeholder="Enter your password"
              autocomplete="${isRegister ? "new-password" : "current-password"}"
              minlength="8"
              required
            />
            <button type="button" class="icon-button password-toggle" aria-label="Show password" data-action="toggle-password">${icon("eye", 17)}</button>
          </div>
          ${isRegister ? `
            <label for="confirm-password">Confirm password</label>
            <input
              id="confirm-password"
              name="confirmPassword"
              type="password"
              placeholder="Re-enter your password"
              autocomplete="new-password"
              minlength="8"
              required
            />
          ` : ""}
          <button class="button button-primary auth-submit" type="submit">
            ${icon("shield", 17)}
            <span>${submitText}</span>
          </button>
          <div class="auth-divider"><span>or</span></div>
          <button class="button button-secondary auth-demo" type="button" data-action="demo">
            ${icon("scan", 16)} <span>Try the interactive demo</span>
          </button>
          <p class="demo-hint">Temporary session with sample Python and C projects</p>
        </form>
        <p class="auth-switch">
          ${isRegister
            ? 'Already have an account? <a class="text-link" href="/login" data-link>Sign in</a>'
            : 'New to Bug Hunter? <a class="text-link" href="/register" data-link>Create account</a>'}
        </p>
      </section>
      <p class="auth-footnote">Python uses a restricted preview; C output tests run only in the isolated sandbox.</p>
    </main>
  `;
}

function sidebar(active) {
  const items = [
    ["dashboard", "Dashboard", "/dashboard", "grid"],
    ["analyze", "Analyze Code", "/analyze", "scan"],
    ["history", "History", "/history", "clock"],
    ["settings", "Settings", "/settings", "settings"],
  ];
  return `
    <aside class="sidebar">
      <nav class="side-nav" aria-label="Main navigation">
        ${items.map(([id, label, href, glyph]) => `
          <a class="nav-item ${active === id ? "active" : ""}" href="${href}" data-link>
            ${icon(glyph, 20)}
            <span>${label}</span>
          </a>
        `).join("")}
      </nav>
      <div class="sidebar-footer">
        <span class="online-dot"></span>
        <span>${state.isDemo ? "Temporary demo workspace" : "Analysis ready"}</span>
      </div>
    </aside>
  `;
}

function shell(content, active) {
  const email = state.email || localStorage.getItem(USER_STORAGE_KEY) || "user@example.com";
  const unreadCount = state.notifications.filter((item) => !item.read).length;
  return `
    <div class="app-shell">
      <header class="topbar">
        <a class="brand" href="/dashboard" data-link>
          <span class="brand-mark">${icon("logo", 24)}</span>
          <span class="brand-name">BUG HUNTER <b>2.0</b></span>
        </a>
        <div class="topbar-actions">
          <div class="notification-wrap">
            <button class="icon-button notification-button" type="button" data-action="toggle-notifications" aria-label="Notifications" aria-expanded="${state.notificationsOpen}" aria-controls="notifications-panel" title="${unreadCount ? `${unreadCount} unread notification${unreadCount === 1 ? "" : "s"}` : "No new notifications"}">
              ${icon("bell", 19)}
              ${unreadCount ? `<span class="notification-badge">${unreadCount > 9 ? "9+" : unreadCount}</span>` : ""}
            </button>
            ${notificationPanel()}
          </div>
          ${state.isDemo ? '<span class="demo-badge">DEMO</span>' : ""}
          <button class="avatar" data-link href="/settings" aria-label="Account settings">${initials(email)}</button>
          <button class="icon-button logout-button" data-action="logout" aria-label="Sign out" title="Sign out">${icon("logout", 19)}</button>
        </div>
      </header>
      ${sidebar(active)}
      <main class="main-content">${content}</main>
    </div>
  `;
}

function languageToggle() {
  return `
    <div class="segmented-control" role="group" aria-label="Programming language">
      <button class="${state.language === "python" ? "selected" : ""}" data-language="python" type="button">Python</button>
      <button class="${state.language === "c" ? "selected" : ""}" data-language="c" type="button">C</button>
    </div>
  `;
}

function analysisOverview() {
  const languageLabel = state.language === "c" ? "GCC / Clang" : "AST + Ruff";
  return `
    <aside class="overview-card">
      <h2>Analysis overview</h2>
      <div class="overview-stat">
        <span class="stat-icon stat-blue">${icon("upload", 19)}</span>
        <span><small>Supported files</small><strong>.PY &nbsp; .C</strong></span>
      </div>
      <div class="overview-stat">
        <span class="stat-icon stat-purple">${icon("scan", 19)}</span>
        <span><small>Analysis engine</small><strong>${languageLabel}</strong></span>
      </div>
      <div class="safe-callout">
        <span class="safe-check">${icon("check", 15)}</span>
        <div><strong>Safe environment</strong><p>Python uses a restricted preview; C execution requires an enabled test and the isolated sandbox.</p></div>
      </div>
    </aside>
  `;
}

function dashboardPage() {
  return shell(`
    <div class="page-heading">
      <div>
        <p class="eyebrow">OVERVIEW</p>
        <h1>Dashboard</h1>
        <p class="page-subtitle">Start a code analysis or review your previous reports.</p>
      </div>
    </div>
    <div class="workspace-grid">
      <section class="panel dashboard-welcome">
        <span class="dashboard-welcome-icon">${icon("scan", 23)}</span>
        <p class="eyebrow">GET STARTED</p>
        <h2>Analyze your code</h2>
        <p>Submit Python or C source to check for issues and review corrections, verification status, and a detailed comparison.</p>
        <div class="dashboard-actions">
          <a class="button button-primary" href="/analyze" data-link>${icon("scan", 16)} Open code analyzer</a>
          <a class="button button-secondary" href="/history" data-link>${icon("clock", 16)} View history</a>
        </div>
      </section>
      ${analysisOverview()}
    </div>
    <div class="workspace-tip">${icon("shield", 16)} <span>Python preview uses a restricted interpreter. C runs only for an enabled output test inside the isolated sandbox.</span></div>
  `, "dashboard");
}

function analysisWorkspacePage() {
  const comment = state.language === "python"
    ? "# Paste your Python code here"
    : "// Paste your C code here";
  const code = state.code || `${comment}\n`;
  const sourceExtension = state.language === "c" ? ".c" : ".py";
  const highlightLargeFile = code.length > MAX_HIGHLIGHT_SOURCE_CHARS;
  return shell(`
    <div class="page-heading">
      <div><p class="eyebrow">WORKSPACE</p><h1>Code Analysis</h1></div>
      <button class="button button-primary analyze-button" data-action="analyze" ${state.busy ? "disabled" : ""}>
        ${icon("scan", 17)} <span>${state.busy ? "Analyzing..." : "Analyze code"}</span>
      </button>
    </div>
    <div id="page-message" class="notice" role="status" ${state.error ? "" : "hidden"}>${escapeHtml(state.error)}</div>
    <div class="workspace-grid">
      <section class="source-card panel">
        <div class="source-heading">
          <div><h2>Source code</h2><p>Select or paste your code</p></div>
          ${languageToggle()}
        </div>
        <div class="drop-zone" id="drop-zone">
          <span class="upload-icon">${icon("upload", 25)}</span>
          <div class="drop-copy">
            <strong>${state.file ? escapeHtml(state.filename) : "Drop code file here"}</strong>
            <span>or <label for="source-file" class="text-link">browse files</label> · ${sourceExtension} in ${state.language === "c" ? "C" : "Python"} mode, up to 5 MB</span>
          </div>
          <input id="source-file" class="visually-hidden" type="file" accept=".py,.c,text/x-python,text/x-c" />
          <span class="drop-file-icon">${icon("file", 20)}</span>
        </div>
        <label class="goal-label" for="program-goal">What should this program do? <span>Required</span></label>
        <textarea id="program-goal" class="program-goal-input" rows="2" minlength="3" maxlength="2000" required placeholder="For example: calculate the average of a list of test scores">${escapeHtml(state.programGoal)}</textarea>
        <p class="goal-hint">This helps flag likely logic issues. Describe the expected behavior, not just the language.</p>
        <label class="goal-label simulation-input-label" for="simulation-input">
          ${state.language === "python" ? "Optional preview inputs" : "Optional test input"}
          <span>${state.language === "python" ? "One per input() call" : "Program stdin, one value per line"}</span>
        </label>
        <textarea id="simulation-input" class="program-goal-input simulation-input" rows="2" maxlength="10000" placeholder="${state.language === "python" ? "Enter one test value per line" : "Enter text to pass to the C program"}">${escapeHtml(state.simulationInput)}</textarea>
        <p class="goal-hint">${state.language === "python"
          ? "Used only by the restricted Python preview."
          : "Used only when the expected-output check runs C in the network-disabled sandbox."}</p>
        <label class="expected-output-toggle"><input id="verify-output" type="checkbox" ${state.verifyExpectedOutput ? "checked" : ""}><span>${state.language === "python" ? "Verify against expected Python output" : "Verify against expected C output (sandbox)"}</span></label>
        <textarea id="expected-output" class="program-goal-input expected-output-input" rows="3" maxlength="20000" ${state.verifyExpectedOutput ? "" : "disabled"} placeholder="Enter expected output; trailing newlines are ignored">${escapeHtml(state.expectedOutput)}</textarea>
        <p class="goal-hint">${state.language === "python"
          ? "A matching restricted preview verifies behavior-changing fixes. If the preview cannot run your code, best-effort corrections may still be shown as unverified; review them before use."
          : "C runs only after you enable this check, inside a network-disabled container with CPU, memory, process, time, and output limits. Docker must be installed and the Bug Hunter sandbox image built."}</p>
        <div class="paste-label">${icon("file", 15)} <span>Paste code</span></div>
        <div class="code-editor-wrap ${highlightLargeFile ? "editor-plain" : ""}">
          <pre id="code-line-numbers" class="code-line-numbers" aria-hidden="true" ${highlightLargeFile ? "hidden" : ""}>${highlightLargeFile ? "" : Array.from({ length: code.split("\n").length }, (_, index) => index + 1).join("\n")}</pre>
          <pre id="code-highlight" class="code-editor-highlight" aria-hidden="true" ${highlightLargeFile ? "hidden" : ""}>${highlightLargeFile ? "" : highlightedCode(code, state.language)}</pre>
          <textarea id="code-input" class="code-editor" spellcheck="false" autocapitalize="off" autocomplete="off" autocorrect="off" aria-label="Source code">${escapeHtml(code)}</textarea>
        </div>
        <div class="editor-footer">
          <span>${state.filename ? escapeHtml(state.filename) : "Untitled"}</span>
          <span id="highlight-status" ${highlightLargeFile ? "" : "hidden"}>Plain editor for large files</span>
          <span id="byte-count">${new TextEncoder().encode(code).byteLength.toLocaleString()} / ${MAX_SOURCE_BYTES.toLocaleString()} bytes</span>
        </div>
      </section>
      ${analysisOverview()}
    </div>
    <div class="workspace-tip">${icon("shield", 16)} <span>Python preview interprets only a bounded subset. Best-effort changes may be unverified when a test cannot run. C is compiler-checked and runs only for an enabled output test inside the restricted sandbox.</span></div>
  `, "analyze");
}

function findingsSummary(result) {
  const findings = result.findings || [];
  const applied = findings.filter((finding) => finding.fixed).length;
  const unverified = findings.filter(
    (finding) => finding.fixed && finding.verified === false,
  ).length;
  const unresolved = findings.length - applied;
  const syntaxError = findings.some((finding) => !finding.fixed && (finding.code === "SYNTAX_ERROR" || finding.code === "C_ERROR" || finding.code === "C_FATAL_ERROR"));
  const appliedSummary = unverified
    ? `${applied} applied · ${unverified} unverified`
    : `${applied} fixed`;
  return `
    <div class="result-summary">
      <div class="summary-icon ${syntaxError ? "summary-warn" : "summary-ok"}">${icon(syntaxError ? "alert" : "check", 22)}</div>
      <div class="summary-copy">
        <strong>${syntaxError ? "Issues detected" : findings.length ? "Analysis complete" : "No configured issues flagged"}</strong>
        <span>${findings.length ? `${findings.length} finding${findings.length === 1 ? "" : "s"} · ${appliedSummary} · ${unresolved} to review` : "Static checks cannot prove logical correctness."}</span>
      </div>
      <span class="language-pill">${result.language === "c" ? "C" : "Python"}</span>
    </div>
  `;
}

function analysisFeedback(result) {
  const findings = result.findings || [];
  const fixed = findings.filter((finding) => finding.fixed).length;
  const unverified = findings.filter(
    (finding) => finding.fixed && finding.verified === false,
  ).length;
  const remaining = findings.length - fixed;

  if (result.simulation?.testPassed === false) {
    return `
      <div class="analysis-feedback feedback-review">
        <span class="feedback-mark">${icon("alert", 19)}</span>
        <div><strong>The candidate was tested, but its output did not match.</strong><p>The failing change was rejected rather than presented as corrected code. Compare the actual and expected output above; the code comparison contains only accepted changes.</p></div>
      </div>
    `;
  }
  if (result.expectedOutput !== null && result.expectedOutput !== undefined && result.simulation?.testPassed === null) {
    const verificationMessage = result.language === "c"
      ? "The C sandbox could not verify this output. Best-effort corrections remain visible and marked unverified; check Docker availability and compiler diagnostics."
      : "The restricted Python preview could not verify this output. Best-effort corrections remain visible and marked unverified.";
    return `
      <div class="analysis-feedback feedback-review">
        <span class="feedback-mark">${icon("alert", 19)}</span>
        <div><strong>The expected-output test could not be verified.</strong><p>${verificationMessage}</p></div>
      </div>
    `;
  }

  if (result.simulation?.status === "error") {
    return `
      <div class="analysis-feedback feedback-review">
        <span class="feedback-mark">${icon("alert", 19)}</span>
        <div><strong>The preview stopped with an error.</strong><p>${escapeHtml(result.simulation.message)} Review the source and test input; the restricted preview is not a substitute for testing in your own environment.</p></div>
      </div>
    `;
  }

  if (findings.length === 0) {
    const compliment = result.language === "c"
      ? result.simulation?.testPassed === true
        ? "Great job—your C source passed the configured compiler check and sandbox output test."
        : "Great job—your C source passed the configured compiler checks."
      : "Clean, readable work—keep up the great coding.";
    return `
      <div class="analysis-feedback feedback-perfect">
        <span class="feedback-mark">${icon("check", 19)}</span>
        <div><strong>Perfect! No issues were flagged.</strong><p>${compliment} Static analysis cannot guarantee that code is free of every bug.</p></div>
      </div>
    `;
  }

  if (remaining === 0) {
    const fixMessage = unverified
      ? `${unverified} best-effort correction${unverified === 1 ? " remains" : "s remain"} unverified. Review the comparison and test assumptions before using this code.`
      : "Review the corrected code and comparison below before using it.";
    return `
      <div class="analysis-feedback feedback-fixed">
        <span class="feedback-mark">${icon("check", 19)}</span>
        <div><strong>${fixed} correction${fixed === 1 ? "" : "s"} applied.</strong><p>${fixMessage}</p></div>
      </div>
    `;
  }

  return `
    <div class="analysis-feedback feedback-review">
      <span class="feedback-mark">${icon("alert", 19)}</span>
      <div><strong>${fixed ? `${fixed} correction${fixed === 1 ? "" : "s"} applied; ` : ""}${remaining} ${remaining === 1 ? "finding needs" : "findings need"} your review.</strong><p>See each finding's location below. Applied corrections are shown with their verification status; unresolved issues are left for you to review.</p></div>
    </div>
  `;
}

function findingList(findings) {
  if (!findings.length) {
    return `<div class="empty-findings">${icon("check", 19)}<span>No findings to display.</span></div>`;
  }
  return `
    <div class="finding-list">
      ${findings.map((finding) => `
        <article class="finding-row">
          <span class="finding-marker ${finding.fixed ? finding.verified === false ? "unverified" : "fixed" : ""}">${icon(finding.fixed ? "check" : "alert", 16)}</span>
          <div class="finding-text">
            <div class="finding-title"><strong>${escapeHtml(finding.code)}</strong>${finding.fixed
              ? finding.verified === false
                ? '<span class="unverified-pill">Best-effort · unverified</span>'
                : finding.verified === true
                  ? '<span class="fixed-pill">Verified fix</span>'
                  : '<span class="fixed-pill">Applied</span>'
              : ""}</div>
            <p>${escapeHtml(finding.message)}</p>
          </div>
          ${finding.line ? `<span class="line-number">Line ${finding.line}${finding.column ? `:${finding.column}` : ""}</span>` : ""}
        </article>
      `).join("")}
    </div>
  `;
}

function diffView(result) {
  const diff = result.comparison || "";
  if (!diff) {
    const hasFindings = (result.findings || []).length > 0;
    if (result.simulation?.testPassed === false) {
      return `<div class="empty-diff">No correction was accepted: its output did not match the expected result. The actual and expected output are shown above; the corrected code remains unchanged.</div>`;
    }
    return `<div class="empty-diff">${hasFindings
      ? "No source changes were made. Review the findings above; add a more specific program goal or test case when intent is ambiguous."
      : "No corrections were needed; the source passed the configured checks."}</div>`;
  }
  return `<pre class="diff-code">${diff.split("\n").map((line) => {
    const type = line.startsWith("+") && !line.startsWith("+++") ? "diff-added"
      : line.startsWith("-") && !line.startsWith("---") ? "diff-removed"
        : line.startsWith("@@") ? "diff-hunk" : "";
    return `<span class="${type}">${escapeHtml(line || " ")}</span>`;
  }).join("\n")}</pre>`;
}

function simulationPreview(result) {
  const preview = result.simulation;
  if (!preview) return "";
  const statusLabels = {
    completed: "Preview complete",
    needs_input: "More input needed",
    unsupported: "Preview unavailable",
    error: "Preview stopped",
    not_run: "Preview not run",
  };
  const status = preview.testPassed === true
    ? "Expected output matched"
    : preview.testPassed === false
      ? "Output test failed"
      : statusLabels[preview.status] || "Preview status";
  const statusClass = preview.testPassed === true
    ? "simulation-completed"
    : preview.testPassed === false
      ? "simulation-error"
      : `simulation-${preview.status}`;
  const cOutputTest = result.language === "c" &&
    result.expectedOutput !== null &&
    result.expectedOutput !== undefined;
  const title = result.language === "python"
    ? "Safe Python preview"
    : cOutputTest
      ? "Sandboxed C preview"
      : "C compiler check";
  const description = result.language === "python"
    ? "Only a bounded set of Python operations is interpreted; arbitrary code is not run."
    : cOutputTest
      ? "C is compiled and run only for this test inside a restricted, network-disabled container."
      : "C source is checked by the compiler only because no output test was requested.";
  return `
    <section class="result-section simulation-section">
      <div class="result-section-heading"><div><h2>${title}</h2><p>${description}</p></div><span class="simulation-status ${statusClass}">${escapeHtml(status)}</span></div>
      <p class="simulation-message">${escapeHtml(preview.message)}</p>
      ${preview.output ? `<pre class="simulation-output">${escapeHtml(preview.output)}</pre>` : ""}
      ${result.simulationInput ? `<p class="simulation-input-summary"><strong>Test input:</strong> <span>${escapeHtml(result.simulationInput)}</span></p>` : ""}
      ${result.expectedOutput !== null && result.expectedOutput !== undefined ? `<div class="simulation-test-expected"><strong>Expected output</strong><pre class="simulation-output">${escapeHtml(result.expectedOutput)}</pre></div>` : ""}
    </section>
  `;
}

function analysisPage() {
  const result = state.lastResult;
  if (!result) {
    return shell(`
      <div class="page-heading">
        <div><p class="eyebrow">ANALYSIS REPORT</p><h1>Detected issues</h1></div>
        <a class="button button-secondary" href="/dashboard" data-link>Back to dashboard</a>
      </div>
      <section class="panel empty-state"><span class="empty-state-icon">${icon("code", 27)}</span><h2>No analysis yet</h2><p>Submit Python or C code to see static analysis results.</p><a class="button button-primary" href="/analyze" data-link>Analyze code</a></section>
    `, "analyze");
  }

  return shell(`
    <div class="page-heading">
      <div><p class="eyebrow">ANALYSIS REPORT</p><h1>Detected issues</h1></div>
      <a class="button button-secondary" href="/dashboard" data-link>Back to dashboard</a>
    </div>
    <div id="page-message" class="notice" role="status" hidden></div>
    <section class="panel result-panel">
      ${findingsSummary(result)}
      ${analysisFeedback(result)}
      <section class="result-section">
        <div class="result-section-heading"><div><h2>Program goal</h2><p>Expected behavior supplied for this analysis.</p></div></div>
        <p class="program-goal-report">${escapeHtml(result.programGoal || "No program goal was provided for this older submission.")}</p>
      </section>
      ${simulationPreview(result)}
      <section class="result-section">
        <div class="result-section-heading"><div><h2>Findings and locations</h2><p>Review every reported issue and its source position.</p></div><span class="section-count">${(result.findings || []).length}</span></div>
        ${findingList(result.findings || [])}
      </section>
      <section class="result-section">
        <div class="result-section-heading"><div><h2>Code comparison</h2><p>Changes between your original and corrected source.</p></div></div>
        ${diffView(result)}
      </section>
      <section class="result-section corrected-section">
        <div class="result-section-heading"><div><h2>Corrected code</h2><p>Best-effort and verified changes are shown with their comparison; review unverified fixes.</p></div><div class="result-actions"><button class="button button-secondary button-small" data-action="reanalyze-original" title="Re-run analysis using the unchanged original source">${icon("scan", 15)} <span>Reanalyze original</span></button><button class="button button-secondary button-small" data-action="continue-in-editor">${icon("code", 15)} Continue in editor</button><button class="button button-secondary button-small" data-action="download-corrected">${icon("download", 15)} Download .${result.language === "c" ? "c" : "py"}</button></div></div>
        <pre class="source-preview">${highlightedCode(result.correctedCode, result.language)}</pre>
        <button class="button button-secondary button-small copy-code-button" data-action="copy-corrected">${icon("copy", 15)} Copy corrected code</button>
      </section>
      <details class="original-source">
        <summary>View original source code</summary>
        <pre class="source-preview">${highlightedCode(result.originalCode, result.language)}</pre>
      </details>
      <div class="result-footer">
        <span>${escapeHtml(result.filename)} · ${new Date(result.createdAt).toLocaleString()}</span>
        <div class="result-actions">
          <button class="button button-secondary button-small" data-action="download-original">${icon("download", 15)} Download original</button>
        </div>
      </div>
    </section>
  `, "analyze");
}

function historyPage() {
  return shell(`
    <div class="page-heading">
      <div><p class="eyebrow">YOUR WORKSPACE</p><h1>Analysis history</h1><p class="page-subtitle">Review your previous code submissions.</p></div>
      <a class="button button-primary" href="/analyze" data-link>${icon("scan", 16)} Analyze code</a>
    </div>
    <div id="page-message" class="notice" role="status" hidden></div>
    <section class="panel history-panel">
      <div class="panel-title-row"><div><h2>Recent submissions</h2><p>Only you can see your saved analyses.</p></div><button class="button button-secondary button-small" data-action="refresh-history">Refresh</button></div>
      <div id="history-list" class="history-list"><div class="loading-row">Loading your history...</div></div>
    </section>
  `, "history");
}

function settingsPage() {
  const email = escapeHtml(state.email || localStorage.getItem(USER_STORAGE_KEY) || "");
  return shell(`
    <div class="page-heading">
      <div><p class="eyebrow">PREFERENCES</p><h1>Settings</h1><p class="page-subtitle">Manage your account and API connection.</p></div>
    </div>
    <div id="page-message" class="notice" role="status" hidden></div>
    <section class="panel settings-panel">
      <div class="settings-section">
        <div class="settings-heading"><span class="settings-icon">${icon("settings", 18)}</span><div><h2>Account</h2><p>Your signed-in Bug Hunter account</p></div></div>
        <label for="account-email">Email address</label>
        <input id="account-email" class="form-input" value="${email}" readonly />
      </div>
      <div class="settings-section">
        <div class="settings-heading"><span class="settings-icon">${icon("code", 18)}</span><div><h2>API connection</h2><p>Set the address of the Bug Hunter backend.</p></div></div>
        <form id="api-settings-form">
          <label for="api-url">Backend URL</label>
          <div class="inline-form"><input id="api-url" class="form-input" type="url" value="${escapeHtml(apiBase())}" required /><button class="button button-primary" type="submit">Save URL</button></div>
        </form>
      </div>
      <div class="settings-section settings-danger">
        <div class="settings-heading"><span class="settings-icon">${icon("logout", 18)}</span><div><h2>Sign out</h2><p>End this session on this device.</p></div></div>
        <button class="button button-secondary" data-action="logout">Sign out</button>
      </div>
    </section>
  `, "settings");
}

function render() {
  const route = window.location.pathname;
  state.route = route;
  const hasToken = Boolean(localStorage.getItem(TOKEN_STORAGE_KEY));

  if (route === "/register") {
    app.innerHTML = authPage(true);
    return;
  }
  if (route === "/login" || !hasToken) {
    if (route !== "/login") {
      window.history.replaceState({}, "", "/login");
      state.route = "/login";
    }
    app.innerHTML = authPage(false);
    return;
  }

  if (route === "/analysis") app.innerHTML = analysisPage();
  else if (route === "/history") app.innerHTML = historyPage();
  else if (route === "/settings") app.innerHTML = settingsPage();
  else if (route === "/analyze") app.innerHTML = analysisWorkspacePage();
  else app.innerHTML = dashboardPage();

  if (route === "/history") loadHistory();
}

async function loadHistory() {
  const list = document.querySelector("#history-list");
  if (!list) return;
  try {
    state.history = await api("/api/submissions");
    renderHistory();
  } catch (error) {
    if (document.querySelector("#history-list")) {
      list.innerHTML = `<div class="notice notice-error">${escapeHtml(error.message)}</div>`;
    }
  }
}

function renderHistory() {
  const list = document.querySelector("#history-list");
  if (!list) return;
  list.innerHTML = state.history.length
    ? state.history.map((item) => `
        <div class="history-entry">
          <button class="history-item" type="button" data-submission-id="${item.id}">
            <span class="history-file-icon">${icon("file", 18)}</span>
            <span class="history-main"><strong>${escapeHtml(item.filename)}</strong><small>${escapeHtml(item.language.toUpperCase())} · ${new Date(item.createdAt).toLocaleString()}</small></span>
            <span class="history-findings">${item.findings.length} finding${item.findings.length === 1 ? "" : "s"}</span>
            <span class="history-arrow">${icon("arrow", 17)}</span>
          </button>
          <button class="history-delete" type="button" data-action="delete-submission" data-submission-id="${item.id}" aria-label="Delete ${escapeHtml(item.filename)} from history" title="Delete from history">${icon("trash", 16)}</button>
        </div>
      `).join("")
      : `<div class="empty-findings">${icon("clock", 18)}<span>No submissions yet. Your analyses will appear here.</span></div>`;
}

async function deleteHistoryItem(button) {
  if (!button || !window.confirm("Delete this analysis from your history? This cannot be undone.")) {
    return;
  }
  const submissionId = Number(button.dataset.submissionId);
  button.disabled = true;
  try {
    await api(`/api/submissions/${submissionId}`, { method: "DELETE" });
    state.history = state.history.filter((item) => item.id !== submissionId);
    state.notifications = state.notifications.filter(
      (item) => item.id !== submissionId,
    );
    saveNotifications();
    renderHistory();
    showMessage("Analysis deleted from your history.", "success");
  } catch (error) {
    if (button.isConnected) button.disabled = false;
    showMessage(error.message);
  }
}

async function openNotification(button) {
  const submissionId = Number(button.dataset.notificationId);
  button.disabled = true;
  try {
    const submission = await api(`/api/submissions/${submissionId}`);
    state.notifications = state.notifications.map((item) => (
      item.id === submissionId ? { ...item, read: true } : item
    ));
    saveNotifications();
    state.lastResult = submission;
    state.resultTab = "findings";
    sessionStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify(submission));
    navigate("/analysis");
  } catch (error) {
    if (error.status === 404) {
      state.notifications = state.notifications.filter(
        (item) => item.id !== submissionId,
      );
      saveNotifications();
    }
    state.notificationError = error.message;
    render();
  }
}

async function authenticate(event) {
  event.preventDefault();
  const form = event.target;
  const isRegister = form.dataset.register === "true";
  const email = form.elements.email.value.trim();
  const password = form.elements.password.value;
  if (isRegister && password !== form.elements.confirmPassword.value) {
    showMessage("The passwords do not match.");
    return;
  }

  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  button.querySelector("span").textContent = isRegister ? "Creating account..." : "Signing in...";
  try {
    const result = await api(`/api/auth/${isRegister ? "register" : "login"}`, {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    localStorage.setItem(TOKEN_STORAGE_KEY, result.accessToken);
    localStorage.setItem(USER_STORAGE_KEY, result.user.email);
    localStorage.setItem(DEMO_STORAGE_KEY, "false");
    state.email = result.user.email;
    state.isDemo = false;
    state.pendingEmail = "";
    navigate("/dashboard");
  } catch (error) {
    if (!isRegister && error.status === 404 && error.detail?.code === "registration_required") {
      state.pendingEmail = email;
      state.authNotice = "We couldn't find an account for that email. Create an account to continue.";
      navigate("/register");
      const notice = document.querySelector("#page-message");
      if (notice) {
        notice.textContent = state.authNotice;
        notice.hidden = false;
      }
    } else {
      showMessage(error.message);
      button.disabled = false;
      button.querySelector("span").textContent = isRegister ? "Create account" : "Sign in";
    }
  }
}

async function analyzeSource() {
  const code = document.querySelector("#code-input")?.value ?? state.code;
  const programGoal = document.querySelector("#program-goal")?.value.trim() ?? state.programGoal.trim();
  const simulationInput = document.querySelector("#simulation-input")?.value ?? state.simulationInput;
  state.code = code;
  state.programGoal = programGoal;
  state.simulationInput = simulationInput;
  state.error = "";
  state.success = "";
  if (programGoal.length < 3) {
    showMessage("Describe what the program should do in at least 3 characters.");
    return;
  }
  if (programGoal.length > 2000) {
    showMessage("The program goal must not exceed 2,000 characters.");
    return;
  }
  if (!code.trim()) {
    showMessage("Paste code or upload a source file before analyzing.");
    return;
  }
  if (new TextEncoder().encode(code).byteLength > MAX_SOURCE_BYTES) {
    showMessage("Source code must not exceed 5 MB.");
    return;
  }

  const soundContext = prepareFixSound();
  state.busy = true;
  render();
  try {
    const result = await api("/api/submissions", {
      method: "POST",
      body: JSON.stringify({
        code,
        programGoal,
        simulationInput,
        expectedOutput: state.verifyExpectedOutput ? state.expectedOutput : null,
        filename: state.filename,
        language: state.language,
      }),
    });
    if (successfullyRepaired(result)) playFixSuccessSound(soundContext);
    else closeFixSound(soundContext);
    state.lastResult = result;
    state.resultTab = "findings";
    sessionStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify(result));
    recordAnalysisNotification(result);
    state.busy = false;
    navigate("/analysis");
  } catch (error) {
    closeFixSound(soundContext);
    state.busy = false;
    render();
    showMessage(error.message);
  }
}

async function readSourceFile(file) {
  const extension = file.name.split(".").pop().toLowerCase();
  if (!["py", "c"].includes(extension)) {
    showMessage("Choose a Python .py or C .c source file.");
    return;
  }
  const fileLanguage = extension === "c" ? "c" : "python";
  if (fileLanguage !== state.language) {
    showMessage(
      `This is a ${fileLanguage === "c" ? "C" : "Python"} file. Switch the language selector to ${fileLanguage === "c" ? "C" : "Python"} before loading it.`,
    );
    return;
  }
  if (file.size > MAX_SOURCE_BYTES) {
    showMessage("Source files must not exceed 5 MB.");
    return;
  }
  try {
    const source = await file.text();
    if (new TextEncoder().encode(source).byteLength > MAX_SOURCE_BYTES) {
      showMessage("Source files must not exceed 5 MB when encoded as UTF-8.");
      return;
    }
    state.file = file;
    state.filename = file.name;
    state.code = source;
    state.error = "";
    render();
  } catch {
    showMessage("The selected source file could not be read.");
  }
}

async function logout() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch {
    // The local session is cleared even when the API is unreachable.
  }
  clearSession();
  navigate("/login");
}

async function startDemo() {
  const button = document.querySelector('[data-action="demo"]');
  if (!button) return;
  button.disabled = true;
  button.querySelector("span").textContent = "Preparing your demo...";
  try {
    const result = await api("/api/auth/demo", { method: "POST" });
    localStorage.setItem(TOKEN_STORAGE_KEY, result.accessToken);
    localStorage.setItem(USER_STORAGE_KEY, result.user.email);
    localStorage.setItem(DEMO_STORAGE_KEY, "true");
    state.email = result.user.email;
    state.isDemo = true;
    navigate("/dashboard");
  } catch (error) {
    showMessage(error.message);
    button.disabled = false;
    button.querySelector("span").textContent = "Try the interactive demo";
  }
}

function downloadSource(code, originalFilename, version, language) {
  const extension = language === "c" ? ".c" : ".py";
  const baseName = (originalFilename || `source${extension}`)
    .split(/[\\/]/)
    .pop()
    .replace(/\.[^.]*$/, "")
    .replace(/[^\w.-]+/g, "_") || "source";
  const filename = `${baseName}_${version}${extension}`;
  const file = new Blob([code], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  return filename;
}

async function reanalyzeOriginal(button) {
  const result = state.lastResult;
  if (!result || !button) return;

  let programGoal = result.programGoal || "";
  if (!programGoal) {
    const enteredGoal = window.prompt(
      "Describe what this program should do before reanalyzing:",
    );
    if (enteredGoal === null) return;
    programGoal = enteredGoal.trim();
    if (programGoal.length < 3) {
      showMessage("Describe the program goal in at least 3 characters.");
      return;
    }
  }

  const soundContext = prepareFixSound();
  button.disabled = true;
  button.querySelector("span").textContent = "Reanalyzing...";
  try {
    const updated = await api(`/api/submissions/${result.id}`, {
      method: "PUT",
      body: JSON.stringify({
        code: result.originalCode,
        programGoal,
        simulationInput: result.simulationInput || "",
        expectedOutput: result.expectedOutput ?? null,
        filename: result.filename,
        language: result.language,
      }),
    });
    if (successfullyRepaired(updated)) playFixSuccessSound(soundContext);
    else closeFixSound(soundContext);
    state.lastResult = updated;
    sessionStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify(updated));
    recordAnalysisNotification(updated);
    if (window.location.pathname === "/analysis") {
      render();
      window.scrollTo(0, 0);
    }
  } catch (error) {
    closeFixSound(soundContext);
    if (button.isConnected) {
      button.disabled = false;
      button.querySelector("span").textContent = "Reanalyze original";
    }
    showMessage(error.message);
  }
}

app.addEventListener("click", async (event) => {
  const link = event.target.closest("[data-link]");
  if (link) {
    event.preventDefault();
    const href = link.getAttribute("href");
    if (href) {
      if (href === "/login" || href === "/register" || localStorage.getItem(TOKEN_STORAGE_KEY)) {
        navigate(href);
      }
    }
    return;
  }

  const notificationItem = event.target.closest("[data-notification-id]");
  if (notificationItem) {
    await openNotification(notificationItem);
    return;
  }

  const languageButton = event.target.closest("[data-language]");
  if (languageButton) {
    const nextLanguage = languageButton.dataset.language;
    if (nextLanguage === state.language) return;
    state.language = nextLanguage;
    state.filename = state.language === "c" ? "pasted.c" : "pasted.py";
    state.file = null;
    state.code = state.language === "c"
      ? "// Paste your C code here\n"
      : "# Paste your Python code here\n";
    state.expectedOutput = "";
    state.verifyExpectedOutput = false;
    render();
    return;
  }

  const tabButton = event.target.closest("[data-tab]");
  if (tabButton) {
    state.resultTab = tabButton.dataset.tab;
    render();
    return;
  }

  const historyButton = event.target.closest(".history-item[data-submission-id]");
  if (historyButton) {
    const submission = state.history.find(
      (item) => item.id === Number(historyButton.dataset.submissionId),
    );
    if (submission) {
      state.lastResult = submission;
      state.resultTab = "findings";
      sessionStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify(submission));
      navigate("/analysis");
    }
    return;
  }

  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "toggle-notifications") {
    state.notificationsOpen = !state.notificationsOpen;
    state.notificationError = "";
    render();
  }
  else if (action === "mark-notifications-read") {
    state.notifications = state.notifications.map((item) => ({ ...item, read: true }));
    saveNotifications();
    render();
  }
  else if (action === "analyze") await analyzeSource();
  else if (action === "logout") await logout();
  else if (action === "demo") await startDemo();
  else if (action === "refresh-history") await loadHistory();
  else if (action === "delete-submission") await deleteHistoryItem(event.target.closest('[data-action="delete-submission"]'));
  else if (action === "reanalyze-original") {
    await reanalyzeOriginal(event.target.closest('[data-action="reanalyze-original"]'));
  }
  else if (action === "continue-in-editor" && state.lastResult) {
    state.code = state.lastResult.correctedCode;
    state.language = state.lastResult.language;
    state.filename = state.lastResult.filename;
    state.programGoal = state.lastResult.programGoal || "";
    state.simulationInput = state.lastResult.simulationInput || "";
    state.expectedOutput = state.lastResult.expectedOutput || "";
    state.verifyExpectedOutput = state.lastResult.expectedOutput !== null &&
      state.lastResult.expectedOutput !== undefined;
    state.file = null;
    navigate("/analyze");
  }
  else if (action === "toggle-password") {
    const password = document.querySelector("#password");
    if (password) {
      const visible = password.type === "password";
      password.type = visible ? "text" : "password";
      const toggle = document.querySelector(".password-toggle");
      if (toggle) {
        toggle.setAttribute("aria-label", visible ? "Hide password" : "Show password");
        toggle.innerHTML = icon(visible ? "eyeOff" : "eye", 17);
      }
    }
  } else if (action === "forgot-password") {
    event.preventDefault();
    showMessage("Password recovery is not available yet. Contact your administrator.");
  } else if (action === "copy-corrected") {
    try {
      await navigator.clipboard.writeText(state.lastResult?.correctedCode || "");
      showMessage("Corrected code copied.", "success");
    } catch {
      showMessage("Clipboard access is unavailable in this browser.");
    }
  } else if (action === "download-original" && state.lastResult) {
    const filename = downloadSource(
      state.lastResult.originalCode,
      state.lastResult.filename,
      "original",
      state.lastResult.language,
    );
    showMessage(`Download started: ${filename}`, "success");
  } else if (action === "download-corrected" && state.lastResult) {
    const filename = downloadSource(
      state.lastResult.correctedCode,
      state.lastResult.filename,
      "corrected",
      state.lastResult.language,
    );
    showMessage(`Download started: ${filename}`, "success");
  }
});

app.addEventListener("submit", async (event) => {
  if (event.target.id === "auth-form") {
    await authenticate(event);
    return;
  }
  if (event.target.id === "api-settings-form") {
    event.preventDefault();
    const value = document.querySelector("#api-url").value.trim().replace(/\/+$/, "");
    try {
      new URL(value);
      localStorage.setItem(API_STORAGE_KEY, value);
      showMessage("Backend URL saved.", "success");
    } catch {
      showMessage("Enter a valid backend URL.");
    }
  }
});

app.addEventListener("input", (event) => {
  if (event.target.id === "code-input") {
    state.code = event.target.value;
    refreshCodeHighlight(state.code);
    const counter = document.querySelector("#byte-count");
    if (counter) {
      counter.textContent = `${new TextEncoder().encode(state.code).byteLength.toLocaleString()} / ${MAX_SOURCE_BYTES.toLocaleString()} bytes`;
    }
  }
  if (event.target.id === "program-goal") {
    state.programGoal = event.target.value;
  }
  if (event.target.id === "simulation-input") {
    state.simulationInput = event.target.value;
  }
  if (event.target.id === "expected-output") {
    state.expectedOutput = event.target.value;
  }
});

app.addEventListener("keydown", (event) => {
  const editor = event.target;
  if (!(editor instanceof HTMLTextAreaElement) || editor.id !== "code-input") return;

  const source = editor.value;
  const start = editor.selectionStart;
  const end = editor.selectionEnd;
  const insertText = (text, cursorOffset = text.length) => {
    event.preventDefault();
    editor.setRangeText(text, start, end, "end");
    editor.setSelectionRange(start + cursorOffset, start + cursorOffset);
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  };

  if (event.key === "Tab") {
    event.preventDefault();
    if (start === end) {
      if (event.shiftKey) {
        const lineStart = source.lastIndexOf("\n", start - 1) + 1;
        const lineEnd = source.indexOf("\n", lineStart);
        const line = source.slice(lineStart, lineEnd === -1 ? source.length : lineEnd);
        const indent = line.match(/^(?: {1,4}|\t)/)?.[0];
        if (indent && start <= lineStart + indent.length) {
          editor.setRangeText("", lineStart, lineStart + indent.length, "preserve");
          const cursor = Math.max(lineStart, start - indent.length);
          editor.setSelectionRange(cursor, cursor);
          editor.dispatchEvent(new Event("input", { bubbles: true }));
        }
      } else {
        insertText("    ");
      }
      return;
    }

    const firstLineStart = source.lastIndexOf("\n", start - 1) + 1;
    const lastLineStart = source.lastIndexOf("\n", end - 1) + 1;
    const lineEnd = source.indexOf("\n", lastLineStart);
    const lastLineEnd = lineEnd === -1 ? source.length : lineEnd;
    const block = source.slice(firstLineStart, lastLineEnd);
    const lines = block.split("\n");
    const updatedLines = event.shiftKey
      ? lines.map((line) => line.replace(/^(?: {1,4}|\t)/, ""))
      : lines.map((line) => `    ${line}`);
    const updatedBlock = updatedLines.join("\n");
    editor.setRangeText(updatedBlock, firstLineStart, lastLineEnd, "preserve");
    editor.setSelectionRange(firstLineStart, firstLineStart + updatedBlock.length);
    editor.dispatchEvent(new Event("input", { bubbles: true }));
    return;
  }

  const pairs = { "(": ")", "[": "]", "{": "}" };
  if (Object.prototype.hasOwnProperty.call(pairs, event.key) && start === end) {
    insertText(`${event.key}${pairs[event.key]}`, 1);
    return;
  }
  if ([")", "]", "}"].includes(event.key) && source[start] === event.key && start === end) {
    event.preventDefault();
    editor.setSelectionRange(start + 1, start + 1);
    return;
  }
  if (
    event.key === "Backspace" &&
    start === end &&
    Object.values(pairs).includes(source[start]) &&
    Object.entries(pairs).some(([opening, closing]) => (
      source[start - 1] === opening && source[start] === closing
    ))
  ) {
    event.preventDefault();
    editor.setRangeText("", start - 1, start + 1, "end");
    editor.dispatchEvent(new Event("input", { bubbles: true }));
    return;
  }
  if (event.key !== "Enter") return;

  const lineStart = source.lastIndexOf("\n", start - 1) + 1;
  const lineBeforeCursor = source.slice(lineStart, start);
  const indentation = lineBeforeCursor.match(/^[\t ]*/)?.[0] || "";
  const trimmedLine = lineBeforeCursor.trimEnd();
  const opensBlock = state.language === "python"
    ? trimmedLine.endsWith(":")
    : trimmedLine.endsWith("{");
  if (!opensBlock) {
    insertText(`\n${indentation}`);
    return;
  }

  const nestedIndentation = `${indentation}    `;
  const remainder = source.slice(end);
  const closingBrace = state.language === "c" && remainder.match(/^[\t ]*\}/);
  if (closingBrace) {
    const braceOffset = end + closingBrace[0].length - 1;
    const replacement = `\n${nestedIndentation}\n${indentation}`;
    event.preventDefault();
    editor.setRangeText(replacement, start, braceOffset, "end");
    const cursor = start + 1 + nestedIndentation.length;
    editor.setSelectionRange(cursor, cursor);
    editor.dispatchEvent(new Event("input", { bubbles: true }));
    return;
  }
  insertText(`\n${nestedIndentation}`);
});

app.addEventListener("scroll", (event) => {
  if (event.target.id === "code-input") {
    const highlight = document.querySelector("#code-highlight");
    const lineNumbers = document.querySelector("#code-line-numbers");
    if (highlight && !highlight.hidden) {
      highlight.scrollTop = event.target.scrollTop;
      highlight.scrollLeft = event.target.scrollLeft;
    }
    if (lineNumbers && !lineNumbers.hidden) {
      lineNumbers.scrollTop = event.target.scrollTop;
    }
  }
}, true);

app.addEventListener("change", async (event) => {
  if (event.target.id === "verify-output") {
    state.verifyExpectedOutput = event.target.checked;
    render();
    return;
  }
  if (event.target.id === "source-file" && event.target.files?.[0]) {
    await readSourceFile(event.target.files[0]);
    event.target.value = "";
  }
});

app.addEventListener("dragover", (event) => {
  if (event.target.closest("#drop-zone")) {
    event.preventDefault();
    event.target.closest("#drop-zone").classList.add("drag-over");
  }
});

app.addEventListener("dragleave", (event) => {
  if (event.target.closest("#drop-zone")) {
    event.target.closest("#drop-zone").classList.remove("drag-over");
  }
});

app.addEventListener("drop", async (event) => {
  const zone = event.target.closest("#drop-zone");
  if (!zone) return;
  event.preventDefault();
  zone.classList.remove("drag-over");
  if (event.dataTransfer.files?.[0]) {
    await readSourceFile(event.dataTransfer.files[0]);
  }
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.notificationsOpen) {
    state.notificationsOpen = false;
    render();
  }
});

window.addEventListener("popstate", render);

async function start() {
  const token = localStorage.getItem(TOKEN_STORAGE_KEY);
  if (token) {
    try {
      const user = await api("/api/auth/me");
      state.email = user.email;
      state.isDemo = user.isDemo === true;
      localStorage.setItem(USER_STORAGE_KEY, user.email);
      localStorage.setItem(DEMO_STORAGE_KEY, String(state.isDemo));
    } catch {
      clearSession();
    }
  }
  render();
}

start();
