"use strict";

/* ═══════════════════════════════════════════════════════════════
   SD Admin 前端（vanilla JS，无框架无 CDN）
   结构：常量 → 工具 → API → 认证 → Tab → 生成 → 历史 → 工作流管理
   安全约定：服务器/用户内容一律走 textContent，不用 innerHTML。
   ═══════════════════════════════════════════════════════════════ */

// ── 预设（镜像 config.py，仅用于前端渲染选项）─────────────────────
const SIZE_PRESETS = {
  "768x1152":  { label: "768×1152 2:3 竖版 (约1MP)", width: 768,  height: 1152 },
  "1152x768":  { label: "1152×768 3:2 横版 (约1MP)", width: 1152, height: 768 },
  "960x1280":  { label: "960×1280 3:4 竖版 (约1MP)", width: 960,  height: 1280 },
  "1280x960":  { label: "1280×960 4:3 横版 (约1MP)", width: 1280, height: 960 },
  "1024x1024": { label: "1024×1024 1:1 方形 (约1MP)", width: 1024, height: 1024 },
  "720x1280":  { label: "720×1280 9:16 竖版 (约1MP)", width: 720,  height: 1280 },
  "1280x720":  { label: "1280×720 16:9 横版 (约1MP)", width: 1280, height: 720 },
  "1512x648":  { label: "1512×648 21:9 宽屏 (约1MP)", width: 1512, height: 648 },
  "1152x1728": { label: "1152×1728 2:3 竖版 (约2MP)", width: 1152, height: 1728 },
  "1728x1152": { label: "1728×1152 3:2 横版 (约2MP)", width: 1728, height: 1152 },
  "1152x1536": { label: "1152×1536 3:4 竖版 (约2MP)", width: 1152, height: 1536 },
  "1536x1152": { label: "1536×1152 4:3 横版 (约2MP)", width: 1536, height: 1152 },
  "1408x1408": { label: "1408×1408 1:1 方形 (约2MP)", width: 1408, height: 1408 },
  "1080x1920": { label: "1080×1920 9:16 竖版 (约2MP)", width: 1080, height: 1920 },
  "1920x1080": { label: "1920×1080 16:9 横版 (约2MP)", width: 1920, height: 1080 },
  "2240x960":  { label: "2240×960 21:9 宽屏 (约2MP)", width: 2240, height: 960 },
};
const VIDEO_ASPECTS = {
  "9:16": "9:16 竖版", "16:9": "16:9 横版", "4:3": "4:3 横版",
  "3:4": "3:4 竖版", "1:1": "1:1 方形",
};
const VIDEO_RESOLUTIONS = { "480p": "480p", "768p": "768p（原生）" };
const VIDEO_FRAMES = {
  "124": { label: "~5秒",   frames: 124 },
  "192": { label: "~8秒",   frames: 192 },
  "277": { label: "~11秒",  frames: 277 },
  "362": { label: "~15秒",  frames: 362 },
};
const LORA_VARIANTS = { off: "关闭", normal: "正常", spread: "Spread" };
const PROMPT_OPTIMIZE = { off: "关闭", nsfw: "NSFW", sfw: "SFW" };
const ROLE_LABELS = { start: "首帧", end: "尾帧", image1: "图1", image2: "图2" };

// ── 全局状态 ─────────────────────────────────────────────────
const state = {
  csrf: "",
  workflows: [],        // GET /api/workflows
  settings: {},         // GET /api/settings（服务端已合并默认值）
  currentWf: null,      // 当前工作流 key
  slots: {},            // 图片槽 role -> File
  running: false,       // 生成进行中
  pollTimer: null,
  stopwatchTimer: null,
  taskStart: 0,
  history: [],          // GET /api/history
  editorKey: null,      // 编辑器当前 key
};

// ── 工具 ─────────────────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);

/** 创建元素：attrs 支持 class/text/dataset/on<Event>，其余走 setAttribute。 */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2).toLowerCase(), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

function toast(msg, type = "success") {
  const t = el("div", { class: `toast toast-${type}`, text: msg });
  $("#toast-root").append(t);
  setTimeout(() => {
    t.classList.add("toast-out");
    setTimeout(() => t.remove(), 300);
  }, 2500);
}

const pad2 = (n) => String(n).padStart(2, "0");

function fmtDateTime(ts) {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} `
       + `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function fmtRelTime(ts) {
  const diff = Date.now() - ts * 1000;
  const m = Math.floor(diff / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d} 天前`;
  return fmtDateTime(ts);
}

/** 秒表显示：平滑递增（本地计时，不依赖服务端 elapsed）。 */
function fmtDuration(sec) {
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  return m > 0 ? `${m}分${s.toFixed(1).padStart(4, "0")}秒` : `${s.toFixed(1)}秒`;
}

const fileUrl = (id) => `/api/history/${encodeURIComponent(id)}/file`;

// ── API ──────────────────────────────────────────────────────
class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/**
 * 同源 fetch 封装：cookie 默认携带；变更类请求（除 /api/login）自动带 CSRF 头。
 * 401 → 显示登录卡片；错误体 {detail} 解析为 ApiError。
 */
async function api(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const headers = { ...(opts.headers || {}) };
  let body = opts.body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  if (method !== "GET" && path !== "/api/login" && state.csrf) {
    headers["x-csrf-token"] = state.csrf;
  }
  const resp = await fetch(path, { method, headers, body, credentials: "same-origin" });
  if (resp.status === 401) {
    showLogin();
    let detail = "未登录或会话已过期";
    try { detail = (await resp.json()).detail || detail; } catch { /* 忽略 */ }
    throw new ApiError(detail, 401);
  }
  let data = null;
  const ct = resp.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    try { data = await resp.json(); } catch { data = null; }
  }
  if (!resp.ok) {
    const detail = (data && data.detail) || `请求失败 (${resp.status})`;
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), resp.status);
  }
  return data;
}

// ── 认证 ─────────────────────────────────────────────────────
function showLogin() {
  stopPollingTimers();
  state.running = false;
  $("#app").classList.add("hidden");
  $("#login-view").classList.remove("hidden");
  const pw = $("#login-password");
  pw.value = "";
  setTimeout(() => pw.focus(), 0);
}

async function doLogin() {
  const errEl = $("#login-error");
  const btn = $("#login-btn");
  errEl.textContent = "";
  btn.disabled = true;
  try {
    const data = await api("/api/login", {
      method: "POST",
      json: { password: $("#login-password").value },
    });
    state.csrf = data.csrf;
    await enterApp();
  } catch (e) {
    errEl.textContent = e.message; // 密码错误 / 429 限流
  } finally {
    btn.disabled = false;
  }
}

async function doLogout() {
  try { await api("/api/logout", { method: "POST" }); } catch { /* 忽略 */ }
  state.csrf = "";
  showLogin();
}

async function enterApp() {
  $("#login-view").classList.add("hidden");
  $("#app").classList.remove("hidden");
  await Promise.all([loadSettings(), loadWorkflows()]);
  switchTab("generate");
}

// ── Tab 切换 ─────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll(".tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.add("hidden"));
  $(`#tab-${name}`).classList.remove("hidden");
  if (name === "history") loadHistory();
  if (name === "workflows") { showEditorView(false); loadManage(); }
}

/* ════════════════════════════════════════════════════════════
   Tab 1：生成
   ════════════════════════════════════════════════════════════ */

async function loadSettings() {
  state.settings = await api("/api/settings");
}

async function loadWorkflows() {
  const data = await api("/api/workflows");
  state.workflows = (data && data.workflows) || [];
  const exists = state.workflows.some((w) => w.key === state.currentWf);
  const key = exists ? state.currentWf : (state.workflows[0] && state.workflows[0].key);
  state.currentWf = null;
  renderWfList();
  if (key) selectWorkflow(key);
  else $("#gen-inputs").replaceChildren(
    el("div", { class: "empty-state", text: "暂无已启用的工作流" }));
}

const getCurrentWf = () =>
  state.workflows.find((w) => w.key === state.currentWf) || null;

function renderWfList() {
  const list = $("#wf-list");
  list.replaceChildren();
  for (const wf of state.workflows) {
    const item = el("div", {
      class: `wf-item${wf.key === state.currentWf ? " active" : ""}`,
      onclick: () => selectWorkflow(wf.key),
    },
      el("div", { class: "wf-item-head" },
        el("span", { class: "wf-item-emoji", text: wf.emoji || "" }),
        el("span", { class: "wf-item-label", text: wf.label || wf.key })),
      wf.description ? el("div", { class: "wf-item-desc", text: wf.description }) : null);
    list.append(item);
  }
}

function selectWorkflow(key) {
  if (state.currentWf === key) { renderWfList(); return; }
  state.currentWf = key;
  state.slots = {};
  clearResult();
  renderWfList();
  const wf = getCurrentWf();
  if (!wf) return;
  renderWfInfo(wf);
  renderGenInputs(wf);
  renderParams(wf);
}

/** 工作流信息卡：how_to 说明在卡片顶部。 */
function renderWfInfo(wf) {
  const c = $("#wf-info");
  c.replaceChildren();
  c.append(el("div", { class: "card wf-info-card" },
    wf.how_to ? el("div", { class: "wf-howto", text: wf.how_to }) : null,
    el("div", { class: "wf-info-head" },
      el("span", { class: "wf-emoji", text: wf.emoji || "" }),
      el("span", { class: "wf-label", text: wf.label || wf.key }),
      el("span", { class: "wf-key mono", text: wf.key })),
    wf.description ? el("div", { class: "wf-desc", text: wf.description }) : null));
}

const getSlotRoles = (wf) =>
  (wf.load_image_roles && wf.load_image_roles.length) ? wf.load_image_roles : ["image"];

const slotLabel = (role) =>
  role === "image" ? "上传图片" : (ROLE_LABELS[role] || role);

/** 输入区：photo 工作流渲染上传槽；prompt 框两种类型都有（photo 选填）。 */
function renderGenInputs(wf) {
  const c = $("#gen-inputs");
  c.replaceChildren();

  if (wf.input_type === "photo") {
    const row = el("div", { class: "slot-row" });
    for (const role of getSlotRoles(wf)) row.append(buildSlot(role));
    c.append(row);
  }

  const isPhoto = wf.input_type === "photo";
  c.append(
    el("label", { class: "field-label", for: "prompt-input",
                  text: isPhoto ? "Prompt（选填）" : "Prompt" }),
    el("textarea", {
      id: "prompt-input", class: "ctl prompt-input", rows: "4",
      placeholder: isPhoto ? "补充描述（可留空）" : "输入提示词…",
    }));
}

/** 单个上传槽：点击选文件，选后显示缩略预览。 */
function buildSlot(role) {
  const input = el("input", { type: "file", accept: "image/*", class: "hidden" });
  const preview = el("div", { class: "slot-preview" },
    el("span", { class: "slot-plus", text: "+" }));
  const box = el("label", { class: "slot" }, preview,
    el("span", { class: "slot-label", text: slotLabel(role) }), input);
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (!file) return;
    state.slots[role] = file;
    if (preview.dataset.url) URL.revokeObjectURL(preview.dataset.url);
    const url = URL.createObjectURL(file);
    preview.dataset.url = url;
    const img = el("img", { class: "slot-img", alt: slotLabel(role) });
    img.src = url;
    preview.replaceChildren(img);
    box.classList.add("filled");
  });
  return box;
}

// ── 参数面板（按 user_configurable 渲染，变更防抖持久化）────────

let saveTimer = null;
let pendingPatch = {};

/** 防抖 500ms PUT /api/settings。 */
function queueSaveSettings(patch) {
  Object.assign(pendingPatch, patch);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    const p = pendingPatch;
    pendingPatch = {};
    saveSettingsNow(p);
  }, 500);
}

async function saveSettingsNow(patch) {
  if (!patch || !Object.keys(patch).length) return;
  try {
    await api("/api/settings", { method: "PUT", json: patch });
  } catch (e) {
    toast(`设置保存失败: ${e.message}`, "error");
  }
}

function renderParams(wf) {
  const panel = $("#gen-params");
  panel.replaceChildren();
  const keys = wf.user_configurable || [];
  if (!keys.length) return;

  const grid = el("div", { class: "param-grid" });
  for (const key of keys) {
    const ctl = buildParamControl(key, wf, keys);
    if (ctl) grid.append(ctl);
  }
  panel.append(el("details", { class: "param-panel", open: "" },
    el("summary", { text: "参数设置" }), grid));
}

function fieldWrap(labelText, control, note, wide) {
  return el("div", { class: `field${wide ? " wide" : ""}` },
    el("span", { class: "field-label", text: labelText }),
    control,
    note ? el("span", { class: "field-note", text: note }) : null);
}

function buildToggleField(labelText, key) {
  const input = el("input", { type: "checkbox", class: "switch-input" });
  input.checked = !!state.settings[key];
  input.addEventListener("change", () => {
    state.settings[key] = input.checked;
    queueSaveSettings({ [key]: input.checked });
  });
  return el("div", { class: "field field-toggle" },
    el("span", { class: "field-label", text: labelText }),
    el("label", { class: "switch" }, input, el("span", { class: "switch-slider" })));
}

function buildNumberField(labelText, key, { min, max, step = "1", note } = {}) {
  const input = el("input", {
    type: "number", class: "ctl", id: `param-${key}`, step,
    value: String(state.settings[key] ?? ""),
  });
  if (min != null) input.setAttribute("min", String(min));
  if (max != null) input.setAttribute("max", String(max));
  input.addEventListener("input", () => {
    const v = parseInt(input.value, 10);
    if (Number.isNaN(v)) return;
    state.settings[key] = v;
    queueSaveSettings({ [key]: v });
  });
  return fieldWrap(labelText, input, note);
}

function buildTextareaField(labelText, key, note) {
  const ta = el("textarea", { class: "ctl", rows: "3", id: `param-${key}` });
  ta.value = state.settings[key] ?? "";
  ta.addEventListener("input", () => {
    state.settings[key] = ta.value;
    queueSaveSettings({ [key]: ta.value });
  });
  return fieldWrap(labelText, ta, note, true);
}

function buildSelectField(labelText, options, currentValue, onChange, note) {
  const sel = el("select", { class: "ctl" });
  for (const [value, label] of options) {
    sel.append(el("option", { value, text: label }));
  }
  sel.value = currentValue;
  sel.addEventListener("change", () => onChange(sel.value));
  return fieldWrap(labelText, sel, note);
}

/** 按 user_configurable 的 key 分发到具体控件。 */
function buildParamControl(key, wf, allKeys) {
  const s = state.settings;

  switch (key) {
    case "comfy_seed":
      return buildNumberField("Seed（-1 = 随机）", "comfy_seed");

    case "comfy_translate":
      return buildToggleField("翻译提示词", "comfy_translate");

    case "comfy_prompt":
      return buildTextareaField("固定 Prompt", "comfy_prompt",
        "非空时覆盖上面的 Prompt 输入");

    case "comfy_model": {
      if (wf.model_selectable === false) return null;
      const sel = el("select", { class: "ctl", id: "param-comfy_model" });
      const cur = s.comfy_model || "";
      sel.append(el("option", { value: cur, text: cur || "（加载中…）" }));
      sel.addEventListener("change", () => {
        s.comfy_model = sel.value;
        queueSaveSettings({ comfy_model: sel.value });
      });
      loadModelsInto(wf.key, sel);
      return fieldWrap("模型", sel);
    }

    case "comfy_width":
    case "comfy_height": {
      // 尺寸预设一个下拉同时写两个字段；height 单独出现时也渲染
      if (key === "comfy_height" && allKeys.includes("comfy_width")) return null;
      const options = Object.entries(SIZE_PRESETS).map(([k, p]) => [k, p.label]);
      const curKey = Object.keys(SIZE_PRESETS).find((k) =>
        SIZE_PRESETS[k].width === s.comfy_width && SIZE_PRESETS[k].height === s.comfy_height);
      return buildSelectField("尺寸", options, curKey || "", (v) => {
        const p = SIZE_PRESETS[v];
        if (!p) return;
        s.comfy_width = p.width;
        s.comfy_height = p.height;
        queueSaveSettings({ comfy_width: p.width, comfy_height: p.height });
      });
    }

    case "comfy_video_aspect":
      return buildSelectField("视频比例", Object.entries(VIDEO_ASPECTS),
        s.comfy_video_aspect, (v) => {
          s.comfy_video_aspect = v;
          queueSaveSettings({ comfy_video_aspect: v });
        });

    case "comfy_video_resolution":
      return buildSelectField("视频画质", Object.entries(VIDEO_RESOLUTIONS),
        s.comfy_video_resolution, (v) => {
          s.comfy_video_resolution = v;
          queueSaveSettings({ comfy_video_resolution: v });
        });

    case "comfy_video_frames": {
      const curKey = Object.keys(VIDEO_FRAMES).find((k) =>
        VIDEO_FRAMES[k].frames === s.comfy_video_frames) || String(s.comfy_video_frames);
      return buildSelectField("视频长度",
        Object.entries(VIDEO_FRAMES).map(([k, p]) => [k, p.label]),
        curKey, (v) => {
          const frames = VIDEO_FRAMES[v] ? VIDEO_FRAMES[v].frames : parseInt(v, 10);
          s.comfy_video_frames = frames;
          queueSaveSettings({ comfy_video_frames: frames });
        });
    }

    case "comfy_upscale_enabled":
      return buildToggleField("高清修复", "comfy_upscale_enabled");

    case "comfy_pussydetailer_enabled":
      return buildToggleField("Pussy Detailer", "comfy_pussydetailer_enabled");

    case "comfy_facedetailer_enabled":
      return buildToggleField("Face Detailer", "comfy_facedetailer_enabled");

    case "comfy_krea2_lora_enabled":
      return buildToggleField("Krea2 LoRA", "comfy_krea2_lora_enabled");

    case "comfy_krea2_lora_strength":
      return buildNumberField("Krea2 LoRA 强度", "comfy_krea2_lora_strength",
        { min: -15, max: 10 });

    case "comfy_lora_variant":
      return buildSelectField("LoRA 变体", Object.entries(LORA_VARIANTS),
        s.comfy_lora_variant, (v) => {
          s.comfy_lora_variant = v;
          queueSaveSettings({ comfy_lora_variant: v });
        });

    case "comfy_prompt_optimize":
      return buildSelectField("Prompt 优化", Object.entries(PROMPT_OPTIMIZE),
        s.comfy_prompt_optimize, (v) => {
          s.comfy_prompt_optimize = v;
          queueSaveSettings({ comfy_prompt_optimize: v });
        });

    case "comfy_face_prompt":
      return buildTextareaField("脸部 Prompt", "comfy_face_prompt", "留空自动提取");

    default:
      return null;
  }
}

/** 工作流切换时重新加载模型列表。 */
async function loadModelsInto(wfKey, sel) {
  try {
    const data = await api(`/api/models/${encodeURIComponent(wfKey)}`);
    const models = (data && data.models) || [];
    const cur = state.settings.comfy_model || "";
    sel.replaceChildren();
    if (cur && !models.includes(cur)) {
      sel.append(el("option", { value: cur, text: cur }));
    }
    for (const m of models) sel.append(el("option", { value: m, text: m }));
    if (cur) sel.value = cur;
  } catch (e) {
    sel.replaceChildren(el("option", {
      value: state.settings.comfy_model || "",
      text: `加载失败: ${e.message}`,
    }));
  }
}

// ── 生成执行与轮询 ────────────────────────────────────────────

/** 面板当前 settings 子集（user_configurable 涉及的键）。 */
function collectSettings(wf) {
  const keys = new Set(wf.user_configurable || []);
  if (keys.has("comfy_width") || keys.has("comfy_height")) {
    keys.add("comfy_width");
    keys.add("comfy_height");
  }
  const out = {};
  for (const k of keys) {
    if (k in state.settings) out[k] = state.settings[k];
  }
  return out;
}

async function onGenerate() {
  if (state.running) return;
  const wf = getCurrentWf();
  if (!wf) { toast("请先选择工作流", "error"); return; }

  const promptInput = $("#prompt-input");
  const prompt = promptInput ? promptInput.value.trim() : "";
  if (wf.input_type !== "photo" && !prompt) {
    toast("请输入提示词", "error");
    if (promptInput) promptInput.focus();
    return;
  }
  if (wf.input_type === "photo") {
    for (const role of getSlotRoles(wf)) {
      if (!state.slots[role]) {
        toast(role === "image" ? "请上传图片" : `请上传${slotLabel(role)}`, "error");
        return;
      }
    }
  }

  const fd = new FormData();
  fd.append("wf_key", wf.key);
  fd.append("prompt", prompt);
  fd.append("settings", JSON.stringify(collectSettings(wf)));
  if (wf.input_type === "photo") {
    for (const [role, file] of Object.entries(state.slots)) {
      fd.append(role, file, file.name);
    }
  }

  clearResult();
  setGeneratingUI(true);
  try {
    const task = await api("/api/generate", { method: "POST", body: fd });
    startPolling(task.id);
  } catch (e) {
    setGeneratingUI(false);
    showGenError(e.message);
  }
}

function setGeneratingUI(running) {
  state.running = running;
  const btn = $("#generate-btn");
  btn.disabled = running;
  btn.textContent = running ? "生成中…" : "生成";
  $("#cancel-poll-btn").classList.toggle("hidden", !running);
  $("#gen-progress").classList.toggle("hidden", !running);
  if (running) {
    $("#gen-stage").textContent = "排队中...";
    $("#gen-stopwatch").textContent = "";
  }
}

function tickStopwatch() {
  const sec = (performance.now() - state.taskStart) / 1000;
  $("#gen-stopwatch").textContent = fmtDuration(sec);
}

function startPolling(taskId) {
  stopPollingTimers();
  state.taskStart = performance.now();
  tickStopwatch();
  state.stopwatchTimer = setInterval(tickStopwatch, 100);
  state.pollTimer = setInterval(() => pollOnce(taskId), 2000);
  pollOnce(taskId);
}

function stopPollingTimers() {
  clearInterval(state.pollTimer);
  clearInterval(state.stopwatchTimer);
  state.pollTimer = null;
  state.stopwatchTimer = null;
}

/** 取消轮询：仅停止前端轮询，后台任务继续。 */
function cancelPolling() {
  stopPollingTimers();
  setGeneratingUI(false);
  toast("已停止轮询（任务仍在后台运行）");
}

let pollFails = 0;

async function pollOnce(taskId) {
  try {
    const t = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
    pollFails = 0;
    $("#gen-stage").textContent = t.stage || "";
    if (t.status === "done") {
      stopPollingTimers();
      setGeneratingUI(false);
      showTaskResult(t);
    } else if (t.status === "error") {
      stopPollingTimers();
      setGeneratingUI(false);
      showGenError(t.error || "生成失败");
    }
  } catch (e) {
    if (e.status === 401 || e.status === 404) {
      stopPollingTimers();
      setGeneratingUI(false);
      if (e.status === 404) showGenError("任务不存在或已被清理");
      return;
    }
    if (++pollFails >= 15) {
      stopPollingTimers();
      setGeneratingUI(false);
      showGenError(`轮询失败: ${e.message}`);
    }
  }
}

function clearResult() {
  $("#gen-result").replaceChildren();
}

function showGenError(msg) {
  const c = $("#gen-result");
  c.replaceChildren(el("div", { class: "error-card", text: msg }));
}

/** 任务完成：拉历史记录拿元数据（seed/用时/模型），渲染结果卡。 */
async function showTaskResult(task) {
  let item = null;
  try {
    const data = await api("/api/history");
    state.history = (data && data.items) || [];
    item = state.history.find((i) => i.id === task.result_id) || null;
  } catch { /* 元数据缺失不阻断结果展示 */ }

  const wf = getCurrentWf();
  const kind = (item && item.kind) ||
    (wf && wf.output_type === "video" ? "video" : "image");
  const seed = item ? item.seed : null;
  const elapsedTxt = item && item.elapsed != null ? `${item.elapsed}秒` : "—";
  const label = (item && item.label) || (wf && wf.label) || "";
  const model = (item && item.settings && item.settings.comfy_model) || "";

  const mediaWrap = el("div", { class: "result-media" });
  if (kind === "video") {
    const v = el("video", {
      src: fileUrl(task.result_id), controls: "", autoplay: "",
      loop: "", muted: "", playsinline: "",
    });
    v.muted = true; // 属性方式确保自动播放生效
    mediaWrap.append(v);
  } else {
    mediaWrap.append(el("img", { src: fileUrl(task.result_id), alt: "生成结果" }));
  }

  const bar = el("div", { class: "param-bar mono" },
    el("span", { text: `seed: ${seed != null ? seed : "—"}` }),
    el("span", { class: "sep", text: "·" }),
    el("span", { text: `用时: ${elapsedTxt}` }),
    el("span", { class: "sep", text: "·" }),
    el("span", { text: label }),
    model ? el("span", { class: "sep", text: "·" }) : null,
    model ? el("span", { text: model }) : null);

  const actions = el("div", { class: "result-actions" });
  if (seed != null) {
    actions.append(el("button", {
      class: "btn", type: "button", onclick: () => reuseSeed(seed),
    }, "复用种子"));
  }
  actions.append(
    el("button", { class: "btn", type: "button", onclick: onGenerate }, "重新生成"),
    el("a", {
      class: "btn", href: fileUrl(task.result_id),
      download: (item && item.filename) || "",
    }, "下载"));

  $("#gen-result").replaceChildren(
    el("div", { class: "card result-card" }, mediaWrap, bar, actions));
}

/** 复用种子：写入设置并立即持久化，同步面板输入框。 */
function reuseSeed(seed) {
  state.settings.comfy_seed = seed;
  const input = $("#param-comfy_seed");
  if (input) input.value = String(seed);
  saveSettingsNow({ comfy_seed: seed });
  toast(`已复用种子 ${seed}`);
}

/* ════════════════════════════════════════════════════════════
   Tab 2：历史
   ════════════════════════════════════════════════════════════ */

async function loadHistory() {
  try {
    const data = await api("/api/history");
    state.history = (data && data.items) || [];
  } catch (e) {
    toast(e.message, "error");
    return;
  }
  renderHistoryGrid();
}

function renderHistoryGrid() {
  const grid = $("#history-grid");
  grid.replaceChildren();
  $("#history-empty").classList.toggle("hidden", state.history.length > 0);
  for (const item of state.history) grid.append(historyCard(item));
}

function historyCard(item) {
  const thumb = el("div", { class: "hist-thumb" });
  if (item.kind === "video") {
    const v = el("video", {
      src: fileUrl(item.id), preload: "metadata", muted: "",
    });
    v.muted = true;
    thumb.append(v, el("span", { class: "hist-play", text: "▶" }));
  } else {
    thumb.append(el("img", {
      src: fileUrl(item.id), loading: "lazy", alt: item.label || "",
    }));
  }
  return el("div", {
    class: "hist-card", dataset: { id: item.id },
    onclick: () => openHistoryDetail(item),
  },
    thumb,
    el("div", { class: "hist-info" },
      el("div", { class: "hist-label", text: item.label || item.wf_key || "" }),
      el("div", { class: "hist-meta" },
        el("span", { class: "mono", text: `seed ${item.seed ?? "—"}` }),
        el("span", { text: fmtRelTime(item.ts || 0) }))));
}

function metaRow(k, v) {
  return el("tr",
    el("td", { text: k }),
    el("td", { class: "meta-value", text: v == null || v === "" ? "—" : String(v) }));
}

/** 详情浮层：大图/播放器 + 完整元数据 + 操作按钮。 */
function openHistoryDetail(item) {
  const root = $("#overlay-root");
  root.replaceChildren();

  const mediaWrap = el("div", { class: "overlay-media" });
  if (item.kind === "video") {
    mediaWrap.append(el("video", { src: fileUrl(item.id), controls: "", loop: "" }));
  } else {
    mediaWrap.append(el("img", { src: fileUrl(item.id), alt: item.label || "" }));
  }

  const meta = el("table", { class: "meta-table" },
    el("tbody",
      metaRow("工作流", item.label || item.wf_key),
      metaRow("Prompt", item.prompt),
      metaRow("翻译后", item.translated),
      item.optimized_prompt ? metaRow("优化后", item.optimized_prompt) : null,
      metaRow("Seed", item.seed),
      metaRow("用时", item.elapsed != null ? `${item.elapsed}秒` : null),
      metaRow("时间", item.ts ? fmtDateTime(item.ts) : null)));

  const close = () => overlay.remove();
  const overlay = el("div", {
    class: "overlay",
    onclick: (e) => { if (e.target === overlay) close(); },
  },
    el("div", { class: "overlay-dialog" },
      el("div", { class: "overlay-head" },
        el("span", { class: "overlay-title", text: item.label || item.wf_key || "详情" }),
        el("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: close }, "关闭")),
      mediaWrap, meta,
      el("div", { class: "overlay-actions" },
        el("button", {
          class: "btn btn-primary", type: "button",
          onclick: () => { close(); loadParamsFromHistory(item); },
        }, "载入参数"),
        el("a", {
          class: "btn", href: fileUrl(item.id), download: item.filename || "",
        }, "下载"),
        el("button", {
          class: "btn btn-danger", type: "button",
          onclick: () => deleteHistoryItem(item, close),
        }, "删除"))));
  root.append(overlay);
}

/** 载入参数：切到生成 Tab，回填 wf_key / prompt / 全部 settings 字段。 */
function loadParamsFromHistory(item) {
  const patch = item.settings || {};
  Object.assign(state.settings, patch);
  saveSettingsNow(patch); // 服务端过滤未知键

  switchTab("generate");
  if (state.workflows.some((w) => w.key === item.wf_key)) {
    const cur = state.currentWf;
    state.currentWf = null; // 强制重渲染
    selectWorkflow(item.wf_key || cur);
    const promptInput = $("#prompt-input");
    if (promptInput) promptInput.value = item.prompt || "";
    toast("已载入参数");
  } else {
    toast(`工作流 ${item.wf_key} 未启用或已归档，仅写入设置`, "error");
  }
}

async function deleteHistoryItem(item, closeOverlay) {
  if (!confirm(`确定删除这条记录（${item.label || item.id}）？`)) return;
  try {
    await api(`/api/history/${encodeURIComponent(item.id)}`, { method: "DELETE" });
    state.history = state.history.filter((i) => i.id !== item.id);
    renderHistoryGrid();
    if (closeOverlay) closeOverlay();
    toast("已删除");
  } catch (e) {
    toast(e.message, "error");
  }
}

/* ════════════════════════════════════════════════════════════
   Tab 3：工作流管理
   ════════════════════════════════════════════════════════════ */

function showEditorView(show) {
  $("#wf-manage-view").classList.toggle("hidden", show);
  $("#wf-editor-view").classList.toggle("hidden", !show);
}

async function loadManage() {
  try {
    const data = await api("/api/manage/workflows");
    renderManageTable((data && data.workflows) || []);
  } catch (e) {
    toast(e.message, "error");
  }
}

function buildSwitch(checked, onChange, disabled) {
  const input = el("input", { type: "checkbox", class: "switch-input" });
  input.checked = !!checked;
  input.disabled = !!disabled;
  input.addEventListener("change", () => onChange(input.checked));
  return el("label", { class: "switch" }, input, el("span", { class: "switch-slider" }));
}

function renderManageTable(items) {
  const wrap = $("#wf-manage-table");
  wrap.replaceChildren();
  if (!items.length) {
    wrap.append(el("div", { class: "empty-state", text: "暂无工作流配置" }));
    return;
  }

  const tbody = el("tbody");
  for (const it of items) {
    const toggle = buildSwitch(it.enabled && !it.error, async (on) => {
      try {
        await api(`/api/workflows/${encodeURIComponent(it.key)}/${on ? "enable" : "disable"}`,
          { method: "POST" });
        it.enabled = on;
        toast(on ? `已启用 ${it.label || it.key}` : `已禁用 ${it.label || it.key}`);
      } catch (e) {
        toast(e.message, "error");
        loadManage(); // 失败回滚
      }
    }, !!it.error); // JSON 解析失败的配置不允许启用

    const nameCell = el("td", {},
      el("div", { text: it.label || it.key }),
      it.error ? el("div", { class: "wf-error-text", text: it.error }) : null);

    tbody.append(el("tr", {},
      el("td", { class: "cell-emoji", text: it.emoji || "" }),
      nameCell,
      el("td", { class: "mono", text: it.key }),
      el("td", {}, toggle),
      el("td", {},
        el("div", { class: "row-actions" },
          el("button", {
            class: "btn btn-sm", type: "button",
            onclick: () => openEditor(it.key),
          }, "编辑"),
          el("button", {
            class: "btn btn-sm btn-danger", type: "button",
            onclick: () => archiveWorkflow(it),
          }, "归档")))));
  }

  wrap.append(el("table", { class: "manage-table" },
    el("thead", {}, el("tr", {},
      el("th", { text: "" }),
      el("th", { text: "名称" }),
      el("th", { text: "key" }),
      el("th", { text: "启用" }),
      el("th", { text: "操作" }))),
    tbody));
}

async function archiveWorkflow(it) {
  if (!confirm(`确定归档工作流「${it.label || it.key}」？配置文件将移入 .trash。`)) return;
  try {
    await api(`/api/workflows/${encodeURIComponent(it.key)}/archive`, { method: "POST" });
    toast(`已归档 ${it.key}`);
    loadManage();
  } catch (e) {
    toast(e.message, "error");
  }
}

async function createWorkflow() {
  const key = (prompt("输入新工作流 key（小写字母、数字、_、-）：") || "").trim();
  if (!key) return;
  try {
    await api("/api/workflows", { method: "POST", json: { key } });
    toast(`已创建 ${key}`);
    openEditor(key);
  } catch (e) {
    toast(e.message, "error");
  }
}

async function uploadComfyJson(file) {
  const fd = new FormData();
  fd.append("file", file, file.name);
  try {
    const data = await api("/api/comfy-upload", { method: "POST", body: fd });
    toast(`已上传: ${data.filename}`);
  } catch (e) {
    toast(e.message, "error");
  }
}

// ── 配置编辑器 ────────────────────────────────────────────────

async function openEditor(key) {
  try {
    const cfg = await api(`/api/workflows/${encodeURIComponent(key)}/config`);
    state.editorKey = key;
    $("#editor-title").textContent = `${key}.json`;
    $("#editor-textarea").value = JSON.stringify(cfg, null, 2);
    $("#editor-report").replaceChildren();
    showEditorView(true);
  } catch (e) {
    toast(e.message, "error");
  }
}

/** 解析编辑器 JSON，失败时尽量给出行号。 */
function parseEditorJson() {
  const text = $("#editor-textarea").value;
  try {
    return { data: JSON.parse(text) };
  } catch (e) {
    let msg = `JSON 语法错误: ${e.message}`;
    const lc = /line (\d+) column (\d+)/i.exec(e.message);
    const pos = /position (\d+)/i.exec(e.message);
    if (lc) {
      msg = `JSON 语法错误（第 ${lc[1]} 行第 ${lc[2]} 列）: ${e.message}`;
    } else if (pos) {
      const upto = text.slice(0, Number(pos[1]));
      const line = upto.split("\n").length;
      const col = Number(pos[1]) - upto.lastIndexOf("\n");
      msg = `JSON 语法错误（第 ${line} 行第 ${col} 列）: ${e.message}`;
    }
    return { error: msg };
  }
}

function showReportError(msg) {
  $("#editor-report").replaceChildren(el("div", { class: "error-card", text: msg }));
}

function renderReport(report) {
  const container = $("#editor-report");
  container.replaceChildren();
  if (!report || !report.length) {
    container.append(el("div", { class: "report-empty", text: "无校验项" }));
    return;
  }
  const list = el("div", { class: "report-list" });
  for (const r of report) {
    const ok = r.status === "ok";
    list.append(el("div", { class: `report-item ${ok ? "report-ok" : "report-error"}` },
      el("span", { class: "dot" }),
      el("span", {
        class: "report-field mono",
        text: r.node ? `${r.field} · 节点${r.node}` : (r.field || ""),
      }),
      el("span", { class: "report-msg", text: r.msg || "" })));
  }
  container.append(list);
}

async function editorValidate() {
  const parsed = parseEditorJson();
  if (parsed.error) { showReportError(parsed.error); return; }
  try {
    const data = await api("/api/validate", { method: "POST", json: parsed.data });
    renderReport(data && data.report);
  } catch (e) {
    showReportError(e.message);
  }
}

async function editorSave() {
  const parsed = parseEditorJson();
  if (parsed.error) { showReportError(parsed.error); return; }
  try {
    const data = await api(
      `/api/workflows/${encodeURIComponent(state.editorKey)}/config`,
      { method: "PUT", json: parsed.data });
    renderReport(data && data.report);
    toast("已保存，Bot 端热重载自动生效");
  } catch (e) {
    showReportError(e.message); // 400 时显示 detail
  }
}

// ── 启动 ─────────────────────────────────────────────────────
function bindEvents() {
  $("#login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    doLogin();
  });
  $("#logout-btn").addEventListener("click", doLogout);
  document.querySelectorAll(".tab").forEach((b) =>
    b.addEventListener("click", () => switchTab(b.dataset.tab)));

  $("#generate-btn").addEventListener("click", onGenerate);
  $("#cancel-poll-btn").addEventListener("click", cancelPolling);

  $("#wf-create-btn").addEventListener("click", createWorkflow);
  $("#wf-upload-btn").addEventListener("click", () => $("#wf-upload-input").click());
  $("#wf-upload-input").addEventListener("change", (e) => {
    const f = e.target.files && e.target.files[0];
    if (f) uploadComfyJson(f);
    e.target.value = "";
  });

  $("#editor-back-btn").addEventListener("click", () => {
    showEditorView(false);
    loadManage();
  });
  $("#editor-validate-btn").addEventListener("click", editorValidate);
  $("#editor-save-btn").addEventListener("click", editorSave);
}

async function init() {
  bindEvents();
  try {
    const me = await api("/api/me");
    state.csrf = me.csrf;
    await enterApp();
  } catch {
    showLogin(); // 未登录或会话过期
  }
}

document.addEventListener("DOMContentLoaded", init);
