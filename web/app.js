/* 拾影 前端 */
(() => {
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const PLAT = {
  youtube: "YouTube", bilibili: "哔哩哔哩", x: "X", douyin: "抖音", wechat: "公众号", other: "其他", local: "本地",
};

/* ---------- 后端调用 ---------- */
let backend = null;
function ready() {
  return new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) return resolve(window.pywebview.api);
    let done = false;
    window.addEventListener("pywebviewready", () => { if (!done) { done = true; resolve(window.pywebview.api); } });
    // 在普通浏览器里打开时使用演示数据，方便调界面
    setTimeout(() => { if (!done) { done = true; resolve(window.ShiYingMock); } }, 700);
  });
}
async function call(name, ...args) {
  const r = await backend[name](...args);
  if (!r || !r.ok) throw new Error((r && r.error) || "操作失败");
  return r.data;
}

/* ---------- 格式化 ---------- */
function fmtBytes(n) {
  if (!n || n < 0) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i >= 2 ? n.toFixed(n >= 100 ? 0 : 1) : Math.round(n)) + " " + u[i];
}
function fmtDur(s) {
  s = Math.round(s || 0);
  if (!s) return "";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  const p = (v) => String(v).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(x)}` : `${m}:${p(x)}`;
}
function fmtEta(s) {
  if (s == null || !isFinite(s)) return "";
  s = Math.round(s);
  if (s < 60) return `剩 ${s} 秒`;
  if (s < 3600) return `剩 ${Math.floor(s / 60)} 分 ${s % 60} 秒`;
  return `剩 ${Math.floor(s / 3600)} 时 ${Math.floor((s % 3600) / 60)} 分`;
}
function fmtTime(t) {
  if (!t) return "";
  const d = new Date(t * 1000), now = new Date();
  const p = (v) => String(v).padStart(2, "0");
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return d.toDateString() === now.toDateString() ? `今天 ${hm}` : `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

/* ---------- 状态 ---------- */
const state = {
  settings: null,
  probe: null,
  quality: null,
  filter: "all",
  tasks: [],
  lastClip: "",
  parsing: false,
  advOpen: false,
};

/* ---------- 解析 ---------- */
const slip = $("#slip");
const input = $("#link");

async function parse(text) {
  text = (text ?? input.value).trim();
  if (!text || state.parsing) return;
  $("#clipHint").hidden = true;
  const links = await call("detect_all", text).catch(() => []);
  if (links.length > 1) { parseBatch(links); return; }
  input.value = text;
  state.batch = null;
  state.parsing = true;
  const hit = await call("detect", text).catch(() => null);
  slip.hidden = false;
  slip.className = "slip loading";
  slip.innerHTML = hit
    ? `<div>正在解析 <span class="tag ${hit.platform}">${PLAT[hit.platform]}</span> 链接…</div>`
    : `<div>正在解析…</div>`;
  $("#parseBtn").classList.add("busy");
  try {
    const p = await call("parse", text);
    state.probe = p;
    state.quality = p.default_quality;
    renderSlip();
  } catch (e) {
    slip.className = "slip error";
    slip.innerHTML = `<p class="err-title">解析失败</p><p>${esc(e.message)}</p>`;
  } finally {
    state.parsing = false;
    $("#parseBtn").classList.remove("busy");
  }
}

function renderSlip() {
  const p = state.probe;
  const tall = p.platform === "douyin" || !!p.vertical;
  const count = p.count > 1 ? (p.kind === "images" ? `图集 ${p.count} 张` : `共 ${p.count} 个视频`) : "";
  const noSub = p.kind === "images" || p.kind === "videos";
  const stamps = p.qualities.map((q) => {
    const sub = [q.sub, q.size ? "约 " + fmtBytes(q.size) : ""].filter(Boolean).join(" ");
    return `<button class="stamp ${q.key.startsWith("audio") ? "audio" : ""}" data-q="${esc(q.key)}"
              aria-pressed="${q.key === state.quality}"><span class="q">${esc(q.label)}</span><span class="s">${esc(sub)}</span></button>`;
  }).join("");
  slip.className = "slip";
  slip.innerHTML = `
    <div class="cover ${tall ? "tall" : ""}">
      ${p.thumbnail ? `<img src="${esc(p.thumbnail)}" referrerpolicy="no-referrer" alt="" onerror="this.remove()">` : ""}
      ${p.duration ? `<span class="dur">${fmtDur(p.duration)}</span>` : ""}
    </div>
    <div class="meta">
      <div class="meta-line"><span class="tag ${p.platform}">${esc(p.platform_name || PLAT[p.platform])}</span>
        ${p.uploader ? `<span>${esc(p.uploader)}</span>` : ""}${count ? `<span>${count}</span>` : ""}</div>
      <h3 title="${esc(p.title)}">${esc(p.title)}</h3>
      ${p.hint ? `<p class="hint-line">${esc(p.hint)}</p>` : ""}
      <div class="stamps" role="group" aria-label="清晰度">${stamps}</div>
      <div class="actions">
        <button class="seal-btn" id="dlBtn">下载</button>
        <button class="ghost" id="dismissBtn">取消</button>
        ${noSub ? "" : subCheck("autoSub")}
        <span class="note">${esc(saveNote(p.platform))}</span>
      </div>
    </div>`;
}

function subCheck(id) {
  return `<label class="check sub-check"><input type="checkbox" id="${id}" ${state.settings?.sub_auto ? "checked" : ""}>下载完生成中文字幕</label>`;
}

function saveNote(platform) {
  const s = state.settings;
  if (!s) return "";
  const folders = { youtube: "YouTube", bilibili: "Bilibili", x: "X", douyin: "Douyin", wechat: "WeChat", other: "Other" };
  const dir = s.download_dir + (s.split_by_platform ? "\\" + folders[platform] : "");
  return "保存到 " + dir;
}

slip.addEventListener("click", async (e) => {
  if (state.batch) return;
  const st = e.target.closest(".stamp");
  if (st) {
    state.quality = st.dataset.q;
    slip.querySelectorAll(".stamp").forEach((b) => b.setAttribute("aria-pressed", b === st));
    return;
  }
  if (e.target.closest("#dismissBtn")) { closeSlip(); return; }
  if (e.target.closest("#dlBtn")) {
    const btn = e.target.closest("#dlBtn");
    btn.disabled = true;
    try {
      await call("add_task", state.probe.probe_id, state.quality, !!$("#autoSub")?.checked && !state.quality.startsWith("audio"));
      toast("已加入下载");
      closeSlip();
      input.value = "";
      setFilter("all");
      poll(true);
    } catch (err) {
      toast(err.message);
      btn.disabled = false;
    }
  }
});

function closeSlip() {
  slip.hidden = true;
  slip.innerHTML = "";
  state.probe = null;
  state.batch = null;
}

$("#intake").addEventListener("submit", (e) => { e.preventDefault(); parse(); });
$("#pasteBtn").addEventListener("click", async () => {
  const t = await call("clipboard").catch(() => "");
  if (t && t.trim()) parse(t);
  else toast("剪贴板是空的");
});

input.addEventListener("paste", (e) => {
  const t = (e.clipboardData && e.clipboardData.getData("text")) || "";
  if (/\n/.test(t.trim()) || (t.match(/https?:\/\//g) || []).length > 1) {
    e.preventDefault();
    parse(t);
  }
});

// 焦点不在输入框时 Ctrl+V 直接解析
document.addEventListener("paste", (e) => {
  if (e.target === input || e.target.closest(".drawer")) return;
  const t = e.clipboardData && e.clipboardData.getData("text");
  if (t) { e.preventDefault(); parse(t); }
});
// 拖拽链接到窗口
document.addEventListener("dragover", (e) => { e.preventDefault(); document.body.classList.add("dragging"); });
document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) document.body.classList.remove("dragging"); });
document.addEventListener("drop", (e) => {
  e.preventDefault();
  document.body.classList.remove("dragging");
  const t = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text");
  if (t) parse(t);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if ($("#app").classList.contains("drawer-open")) closeDrawer();
    else if (!slip.hidden) closeSlip();
  }
});

/* ---------- 批量 ---------- */
const UNIFY = [["best", "最高"], ["2160", "4K"], ["1440", "2K"], ["1080", "1080P"], ["720", "720P"], ["audio", "仅音频"]];

function pickQuality(qs, want) {
  const keys = qs.map((q) => q.key);
  const video = keys.filter((k) => !k.startsWith("audio"));
  if (want === "audio") return keys.find((k) => k === "audio_m4a") || keys.find((k) => k.startsWith("audio")) || keys[0];
  if (want === "best" || !/^\d+$/.test(want)) return video[0] || keys[0];
  const nums = video.filter((k) => /^\d+$/.test(k)).map(Number).sort((a, b) => b - a);
  if (!nums.length) return video[0] || keys[0];   // 抖音等非数字档位：取第一个（最高）
  const hit = nums.find((n) => n <= Number(want));
  return String(hit ?? nums[nums.length - 1]);
}

function defaultUnify() {
  const d = String(state.settings?.default_quality || "1080");
  if (d.startsWith("audio")) return "audio";
  return UNIFY.some(([k]) => k === d) ? d : "1080";
}

function parseBatch(links) {
  input.value = links.map((l) => l.url).join("   ");
  state.probe = null;
  state.batch = {
    unify: (state.batch && state.batch.unify) || defaultUnify(),
    items: links.map((l, i) => ({ key: "b" + Date.now() + i, url: l.url, platform: l.platform, status: "waiting" })),
  };
  renderBatch();
  runBatchParse(state.batch.items.slice());
}

async function runBatchParse(items) {
  const pool = 3;   // 同时解析 3 个
  const batch = state.batch;
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const it = items[next++];
      it.status = "parsing";
      updateBatchItem(it);
      try {
        const p = await call("parse", it.url);
        it.probe = p;
        it.quality = pickQuality(p.qualities, batch.unify);
        it.status = "ok";
      } catch (e) {
        it.status = "error";
        it.error = e.message;
      }
      if (state.batch !== batch) return;
      updateBatchItem(it);
      updateBatchHead();
    }
  }
  await Promise.all(Array.from({ length: Math.min(pool, items.length) }, worker));
}

function renderBatch() {
  const b = state.batch;
  slip.hidden = false;
  slip.className = "slip batch";
  slip.innerHTML = `
    <div class="batch-head">
      <div class="batch-count"></div>
      <div class="batch-unify"><span>统一清晰度</span>
        <div class="seg" id="unifySeg">${UNIFY.map(([k, l]) => `<button data-v="${k}" aria-pressed="${k === b.unify}">${l}</button>`).join("")}</div>
      </div>
    </div>
    <ol class="batch-list" id="batchList"></ol>
    <div class="actions">
      <button class="seal-btn" id="dlAllBtn" disabled>全部下载</button>
      <button class="ghost" id="dismissBtn">取消</button>
      ${subCheck("autoSubBatch")}
      <span class="note">没有对应清晰度时自动选最接近的；每一行也可以单独改</span>
    </div>`;
  const list = $("#batchList");
  for (const it of b.items) {
    const li = document.createElement("li");
    li.className = "b-item";
    li.dataset.key = it.key;
    list.appendChild(li);
    updateBatchItem(it);
  }
  updateBatchHead();
}

function updateBatchItem(it) {
  const li = slip.querySelector(`.b-item[data-key="${it.key}"]`);
  if (!li) return;
  const p = it.probe;
  li.className = "b-item " + it.status;
  if (it.status === "parsing" || it.status === "waiting") {
    li.innerHTML = `<div class="thumb ph" data-l="${esc(PLAT[it.platform])}"></div>
      <div class="b-main"><div class="b-title"><span class="tag ${it.platform}"></span><span class="b-url">${esc(it.url)}</span></div>
      <div class="b-sub">${it.status === "parsing" ? '<span class="spinner"></span>解析中…' : "等待解析"}</div></div><div></div>
      <button class="b-x" data-act="drop" title="移除">×</button>`;
    return;
  }
  if (it.status === "error") {
    li.innerHTML = `<div class="thumb ph" data-l="${esc(PLAT[it.platform])}"></div>
      <div class="b-main"><div class="b-title"><span class="tag ${it.platform}"></span><span class="b-url">${esc(it.url)}</span></div>
      <div class="b-sub err">${esc(it.error)}</div></div>
      <button class="mini" data-act="reparse">重新解析</button>
      <button class="b-x" data-act="drop" title="移除">×</button>`;
    return;
  }
  const opts = p.qualities.map((q) => {
    const label = q.label + (q.sub ? " " + q.sub : "") + (q.size ? "  约 " + fmtBytes(q.size) : "");
    return `<option value="${esc(q.key)}" ${q.key === it.quality ? "selected" : ""}>${esc(label)}</option>`;
  }).join("");
  const extra = [p.uploader, p.duration ? fmtDur(p.duration) : "", p.count > 1 ? (p.kind === "images" ? `图集 ${p.count} 张` : `${p.count} 个视频`) : ""].filter(Boolean);
  li.innerHTML = `<div class="thumb ph" data-l="${esc(PLAT[it.platform])}">${p.thumbnail ? `<img src="${esc(p.thumbnail)}" referrerpolicy="no-referrer" alt="" onerror="this.remove()">` : ""}</div>
    <div class="b-main"><div class="b-title" title="${esc(p.title)}"><span class="tag ${it.platform}"></span>${esc(p.title)}</div>
    <div class="b-sub">${extra.map((x) => `<span>${esc(x)}</span>`).join("")}${p.hint ? `<span class="warn" title="${esc(p.hint)}">${esc(p.hint)}</span>` : ""}</div></div>
    <select data-act="quality" aria-label="清晰度">${opts}</select>
    <button class="b-x" data-act="drop" title="移除">×</button>`;
}

function updateBatchHead() {
  const b = state.batch;
  if (!b) return;
  const n = b.items.length;
  const ok = b.items.filter((i) => i.status === "ok").length;
  const bad = b.items.filter((i) => i.status === "error").length;
  const busy = n - ok - bad;
  $(".batch-count", slip).innerHTML = `<b>${n} 个链接</b>
    <span>${busy ? `还剩 ${busy} 个在解析　` : ""}可下载 ${ok}${bad ? `　<em>失败 ${bad}</em>` : ""}</span>`;
  const btn = $("#dlAllBtn");
  btn.disabled = ok === 0;
  btn.textContent = ok ? `全部下载（${ok}）` : "全部下载";
}

slip.addEventListener("click", async (e) => {
  const b = state.batch;
  if (!b) return;
  if (e.target.closest("#dismissBtn")) { closeSlip(); return; }
  const seg = e.target.closest("#unifySeg button");
  if (seg) {
    b.unify = seg.dataset.v;
    $("#unifySeg").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === seg));
    b.items.filter((i) => i.status === "ok").forEach((i) => { i.quality = pickQuality(i.probe.qualities, b.unify); updateBatchItem(i); });
    return;
  }
  const act = e.target.closest("[data-act]");
  if (act && act.tagName === "BUTTON") {
    const key = act.closest(".b-item").dataset.key;
    const it = b.items.find((i) => i.key === key);
    if (act.dataset.act === "drop") {
      b.items = b.items.filter((i) => i !== it);
      act.closest(".b-item").remove();
      if (!b.items.length) { closeSlip(); return; }
      updateBatchHead();
    } else if (act.dataset.act === "reparse") {
      it.status = "waiting";
      runBatchParse([it]);
      updateBatchHead();
    }
    return;
  }
  if (e.target.closest("#dlAllBtn")) {
    const btn = $("#dlAllBtn");
    btn.disabled = true;
    const ready = b.items.filter((i) => i.status === "ok");
    let added = 0;
    for (const it of ready) {
      const sub = !!$("#autoSubBatch")?.checked && !it.quality.startsWith("audio") && it.probe.kind !== "images";
      try { await call("add_task", it.probe.probe_id, it.quality, sub); added++; b.items = b.items.filter((x) => x !== it); }
      catch (err) { it.status = "error"; it.error = err.message; }
    }
    toast(`已加入 ${added} 个下载`);
    setFilter("all");
    poll(true);
    if (!b.items.length) { closeSlip(); input.value = ""; }
    else renderBatch();
  }
});
slip.addEventListener("change", (e) => {
  const sel = e.target.closest('select[data-act="quality"]');
  if (!sel || !state.batch) return;
  const it = state.batch.items.find((i) => i.key === sel.closest(".b-item").dataset.key);
  if (it) it.quality = sel.value;
});

/* ---------- 剪贴板识别 ---------- */
async function checkClipboard() {
  if (!state.settings || !state.settings.watch_clipboard) return;
  const t = (await call("clipboard").catch(() => "")).trim();
  if (!t || t === state.lastClip || t === input.value.trim()) return;
  state.lastClip = t;
  const h = $("#clipHint");
  const all = await call("detect_all", t).catch(() => []);
  if (all.length > 1) {
    const plats = [...new Set(all.map((x) => x.platform))].map((p) => `<span class="tag ${p}">${PLAT[p]}</span>`).join(" ");
    h.innerHTML = `<span>剪贴板里有 ${all.length} 个链接</span>${plats}<button class="text" id="clipGo">全部解析</button>`;
    h.hidden = false;
    $("#clipGo").onclick = () => parse(t);
    return;
  }
  const hit = all[0];
  if (!hit || hit.platform === "other") return;
  h.innerHTML = `<span>剪贴板里有 <span class="tag ${hit.platform}">${PLAT[hit.platform]}</span> 链接</span>
    <b title="${esc(hit.url)}">${esc(hit.url)}</b><button class="text" id="clipGo">解析</button>`;
  h.hidden = false;
  $("#clipGo").onclick = () => parse(t);
}
window.addEventListener("focus", checkClipboard);

/* ---------- 队列 ---------- */
const rowsEl = $("#rows");
const rowCache = new Map();
const STATUS_GROUP = { queued: "active", downloading: "active", merging: "active", done: "done", error: "failed", canceled: "failed" };

function setFilter(f) {
  state.filter = f;
  document.querySelectorAll("#filters button").forEach((b) => b.classList.toggle("on", b.dataset.f === f));
  renderRows();
}
$("#filters").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) setFilter(b.dataset.f);
});

function rowSub(t) {
  switch (t.status) {
    case "queued":
      return `<span class="muted">排队中</span><span>${esc(t.quality_label)}</span>`;
    case "downloading": {
      const pct = Math.floor((t.progress || 0) * 100);
      const size = t.total ? `${fmtBytes(t.downloaded)} / ${fmtBytes(t.total)}` : (t.downloaded ? fmtBytes(t.downloaded) : "");
      const conns = t.conns > 1 ? `<span class="conns" title="同时在下载的连接数">${t.conns} 线程</span>` : t.conns === 1 ? `<span class="conns one">单线程</span>` : "";
      return `<span class="pct">${pct}%</span>${t.speed ? `<span>${fmtBytes(t.speed)}/s</span>` : ""}${conns}
        ${size ? `<span>${size}</span>` : ""}${t.eta != null ? `<span>${fmtEta(t.eta)}</span>` : ""}
        <span class="muted">${esc(t.quality_label)}</span>`;
    }
    case "merging":
      return `<span class="pct">100%</span><span>${(t.quality || "").startsWith("audio") ? "正在转换音频…" : "正在处理文件…"}</span>`;
    case "done": {
      const head = t.platform === "local"
        ? `<span>本地文件</span>${t.total ? `<span>${fmtBytes(t.total)}</span>` : ""}`
        : `<span class="ok">已完成</span><span>${esc(t.quality_label)}</span>${t.total ? `<span>${fmtBytes(t.total)}</span>` : ""}`;
      return head + subLine(t, `<span class="muted">${fmtTime(t.finished)}</span>`);
    }
    case "canceled":
      return `<span class="muted">已取消</span>`;
    case "error":
      return `<span class="err" title="${esc(t.error)}">${esc(t.error)}</span>`;
  }
  return "";
}

const SUB_RUNNING = ["queued", "fetching", "extracting", "recognizing", "translating", "writing"];

// 专门做过优化的平台，按使用人数从多到少排（国内按 QuestMobile 2026 年 6 月月活）
const SUPPORTED = [
  ["国内", [["wechat", "微信公众号"], ["douyin", "抖音"], ["bilibili", "哔哩哔哩"]]],
  ["海外", [["youtube", "YouTube"], ["x", "X"]]],
];
function platformChips() {
  return `<div class="plats">${SUPPORTED.map(([k, ps]) =>
    `<div class="plat-row"><span class="plat-k">${k}</span>${ps.map(([id, name]) => `<span class="tag ${id}">${name}</span>`).join("")}</div>`).join("")}
    <p class="plat-more">还可以试小红书、微博、Vimeo、Twitch 等上千个网站</p></div>`;
}

function subLine(t, fallback) {
  const st = t.sub_status;
  if (SUB_RUNNING.includes(st)) return `<span class="sub-run">字幕：${esc(t.sub_msg || "处理中")}</span>`;
  if (st === "done") return `<span class="sub-ok" title="${esc((t.sub_files || []).join("\n"))}">${esc(t.sub_msg || "字幕已生成")}</span>`;
  if (st === "error") return `<span class="err" title="${esc(t.sub_error)}">字幕失败：${esc(t.sub_error)}</span>`;
  return fallback;
}

function rowActions(t) {
  if (["queued", "downloading", "merging"].includes(t.status))
    return `<button data-a="cancel">取消</button>`;
  if (t.status === "done") {
    const play = `<button class="primary" data-a="open">播放</button>`;
    const rm = `<button data-a="remove">移除</button>`;
    const folder = `<button data-a="reveal">文件夹</button>`;
    if (t.quality === "images") return play + folder + rm;
    const st = t.sub_status;
    if (SUB_RUNNING.includes(st)) return play + `<button data-a="sub-cancel">停止字幕</button>` + rm;
    if (st === "done") return play + `<button data-a="sub-reveal">字幕文件</button><button data-a="sub">重做字幕</button>` + rm;
    if (st === "error") return play + `<button data-a="sub">重试字幕</button>` + folder + rm;
    return play + `<button data-a="sub">生成字幕</button>` + folder + rm;
  }
  if (t.status === "error")
    return `<button class="primary" data-a="retry">重试</button><button data-a="feedback">反馈</button><button data-a="remove">移除</button>`;
  return `<button class="primary" data-a="retry">重试</button><button data-a="remove">移除</button>`;
}

function buildRow(t) {
  const li = document.createElement("li");
  li.className = "row";
  li.dataset.id = t.id;
  li.innerHTML = `
    <div class="thumb ph" data-l="${esc((t.platform === "other" && t.site) || PLAT[t.platform])}">
      ${t.thumbnail ? `<img src="${esc(t.thumbnail)}" referrerpolicy="no-referrer" alt="" loading="lazy" onerror="this.remove()">` : ""}
    </div>
    <div class="row-main">
      <div class="row-title" title="${esc(t.title)}"></div>
      <div class="row-sub"></div>
    </div>
    <div class="row-actions"></div>
    <i class="bar"></i>`;
  li.querySelector(".row-title").innerHTML = `<span class="tag ${t.platform}" style="margin-right:8px"></span>${esc(t.title)}`;
  return li;
}

function updateRow(li, t) {
  const sig = [t.status, Math.floor((t.progress || 0) * 1000), Math.round(t.speed / 1024), Math.round(t.eta || 0), t.error, t.conns,
    t.sub_status, t.sub_msg, t.sub_error].join("|");
  if (li._sig === sig) return;
  const actKey = t.status + "|" + (t.sub_status || "");
  const statusChanged = li._status !== actKey;
  li._sig = sig;
  li._status = actKey;
  const subbing = t.status === "done" && SUB_RUNNING.includes(t.sub_status);
  li.className = "row " + t.status + (subbing ? " subbing" : "");
  li.style.setProperty("--p", t.status === "merging" ? 1 : subbing ? (t.sub_progress || 0) : (t.progress || 0));
  li.querySelector(".row-sub").innerHTML = rowSub(t);
  if (statusChanged) li.querySelector(".row-actions").innerHTML = rowActions(t);
}

function renderRows() {
  const list = state.tasks.filter((t) => state.filter === "all" || STATUS_GROUP[t.status] === state.filter);
  const keep = new Set(list.map((t) => t.id));
  for (const [id, li] of rowCache) if (!keep.has(id)) { li.remove(); if (!state.tasks.find((t) => t.id === id)) rowCache.delete(id); }
  let prev = null;
  for (const t of list) {
    let li = rowCache.get(t.id);
    if (!li) { li = buildRow(t); rowCache.set(t.id, li); }
    updateRow(li, t);
    const want = prev ? prev.nextSibling : rowsEl.firstChild;
    if (want !== li) rowsEl.insertBefore(li, want);
    prev = li;
  }
  const active = state.tasks.filter((t) => STATUS_GROUP[t.status] === "active").length;
  $("#cntActive").textContent = active || "";
  const empty = $("#empty");
  empty.hidden = list.length > 0;
  if (!list.length && state.tasks.length) {
    empty.innerHTML = `<p>这里还没有${{ active: "进行中的", done: "已完成的", failed: "失败的" }[state.filter] || ""}任务</p>`;
  } else if (!list.length) {
    empty.innerHTML = `<p class="empty-title">复制一个视频链接，回到这里</p>
      <p>窗口会自动认出剪贴板里的链接，也可以直接粘贴到上面。</p>
      ${platformChips()}`;
  }
}

rowsEl.addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-a]");
  if (!b) return;
  const id = b.closest(".row").dataset.id;
  const a = b.dataset.a;
  try {
    if (a === "cancel") await call("cancel", id);
    if (a === "open") await call("open_task", id);
    if (a === "reveal") await call("reveal_task", id);
    if (a === "remove") { await call("remove", id); rowCache.get(id)?.remove(); rowCache.delete(id); }
    if (a === "sub") await call("subtitle", id);
    if (a === "sub-cancel") await call("cancel_subtitle", id);
    if (a === "sub-reveal") await call("reveal_subtitle", id);
    if (a === "feedback") {
      const t = state.tasks.find((x) => x.id === id) || {};
      openFeedback({ site: (t.platform === "other" && t.site) || PLAT[t.platform] || "", title: t.title, error: t.error, url: t.url });
      return;
    }
    if (a === "retry") {
      try { await call("retry", id); }
      catch (err) {
        if (err.message === "NEED_REPARSE") {
          const t = state.tasks.find((x) => x.id === id);
          if (t) parse(t.url);
          return;
        }
        throw err;
      }
    }
    poll(true);
  } catch (err) { toast(err.message); }
});
rowsEl.addEventListener("dblclick", (e) => {
  const row = e.target.closest(".row.done");
  if (row) call("open_task", row.dataset.id).catch((err) => toast(err.message));
});

$("#openDirBtn").onclick = () => call("open_download_dir").catch((e) => toast(e.message));
$("#localSubBtn").onclick = async () => {
  try {
    const added = await call("add_local_videos");
    if (added && added.length) { toast(`已加入 ${added.length} 个字幕任务`); setFilter("all"); poll(true); }
  } catch (e) { toast(e.message); }
};
$("#clearBtn").onclick = async () => { await call("clear_finished"); poll(true); };

let pollTimer;
async function poll(now) {
  clearTimeout(pollTimer);
  try {
    state.tasks = await call("tasks");
    renderRows();
  } catch (_) { /* 忽略一次失败 */ }
  const busy = state.tasks.some((t) => STATUS_GROUP[t.status] === "active" || SUB_RUNNING.includes(t.sub_status));
  pollTimer = setTimeout(poll, busy ? 500 : 2000);
}

/* ---------- 组件提示 ---------- */
let bannerTimer;
async function refreshBanner() {
  clearTimeout(bannerTimer);
  const info = await call("app_info").catch(() => null);
  const b = $("#setupBanner");
  if (!info) { b.hidden = true; return; }
  const job = info.setup || {};
  if (job.status === "working") {
    const pct = job.total ? Math.floor((job.done / job.total) * 100) : 0;
    const cur = (job.items || []).find((it) => it.key === job.current);
    b.className = "banner busy-line";
    b.innerHTML = `<span>正在后台安装组件 ${pct}%${cur ? "（" + esc(cur.label) + "）" : ""}</span><button class="text" id="bannerGo">查看</button>`;
    b.hidden = false;
    $("#bannerGo").onclick = () => openSetup(false);
    bannerTimer = setTimeout(refreshBanner, 1500);
    return;
  }
  if (!info.missing.length) { b.hidden = true; return; }
  b.className = "banner";
  b.innerHTML = `<span>还缺 ${esc(info.missing.join("、"))}，YouTube、哔哩哔哩、X 暂时下不了（抖音、公众号不受影响）</span>
    <button class="text" id="bannerGo">一键安装</button>`;
  b.hidden = false;
  $("#bannerGo").onclick = () => openSetup(false);
}

/* ---------- 设置 ---------- */
const QUALITY_OPTS = [["best", "最高画质"], ["2160", "4K"], ["1440", "2K"], ["1080", "1080P"], ["720", "720P"], ["480", "480P"], ["audio_m4a", "仅音频 M4A"]];
const COOKIE_PLATS = ["youtube", "bilibili", "x", "douyin", "other"];

const ADVANCED = ["download-more", "format", "network", "cookies", "comp-net"];
function openDrawer(focus) {
  if (focus && ADVANCED.includes(focus)) state.advOpen = true;
  $("#app").classList.add("drawer-open");
  $("#drawer").setAttribute("aria-hidden", "false");
  renderSettings();
  loadComponents();
  loadSubAssets();
  if (focus) setTimeout(() => $("#g-" + focus)?.scrollIntoView({ behavior: "smooth" }), 280);
}
function closeDrawer() {
  $("#app").classList.remove("drawer-open");
  $("#drawer").setAttribute("aria-hidden", "true");
  clearTimeout(compTimer);
  refreshBanner();
}
$("#settingsBtn").onclick = () => openDrawer();
$("#closeDrawer").onclick = closeDrawer;
$("#scrim").onclick = closeDrawer;

function seg(name, value, opts) {
  return `<div class="seg" data-seg="${name}">${opts.map(([v, l]) =>
    `<button type="button" data-v="${v}" aria-pressed="${String(v) === String(value)}">${l}</button>`).join("")}</div>`;
}

function renderSettings() {
  const s = state.settings;
  const body = $("#settingsBody");
  const cookieRows = COOKIE_PLATS.map((p) => {
    const c = s.cookies[p];
    const right = c.mode === "file"
      ? `<div class="with-btn"><input class="input" data-k="cookies.${p}.file" value="${esc(c.file)}" placeholder="cookies.txt 路径"><button class="mini" data-pick-cookie="${p}">选择</button></div>`
      : c.mode === "browser"
        ? `<select data-k="cookies.${p}.browser">${["firefox", "edge", "chrome", "brave"].map((b) => `<option ${b === c.browser ? "selected" : ""}>${b}</option>`).join("")}</select>`
        : `<span style="color:var(--ink-3)">不登录</span>`;
    return `<div class="cookie-row"><span class="tag ${p}">${p === "other" ? "其他网站" : PLAT[p]}</span>
      <select data-k="cookies.${p}.mode">
        <option value="none" ${c.mode === "none" ? "selected" : ""}>不使用</option>
        <option value="file" ${c.mode === "file" ? "selected" : ""}>cookies.txt</option>
        <option value="browser" ${c.mode === "browser" ? "selected" : ""}>从浏览器读</option>
      </select>${right}</div>`;
  }).join("");

  body.innerHTML = `
  <section class="group" id="g-download">
    <h3>下载</h3>
    <div class="field"><label>保存位置</label>
      <div class="with-btn"><input class="input" data-k="download_dir" value="${esc(s.download_dir)}"><button class="mini" id="pickDir">选择</button></div></div>
    <div class="field"><span>默认清晰度</span>
      <select data-k="default_quality">${QUALITY_OPTS.map(([v, l]) => `<option value="${v}" ${v === s.default_quality ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    <div class="field"><span>同时下载</span>${seg("max_concurrent", s.max_concurrent, [[1, "1 个"], [2, "2 个"], [3, "3 个"], [4, "4 个"]])}</div>
  </section>

  <section class="group" id="g-subtitle">
    <h3>字幕（识别 + 翻译）</h3>
    <p class="about" style="margin:0 0 4px">字幕文件和视频同名、放在同一个文件夹，PotPlayer 打开视频会自动加载；右键 → 字幕，可以在中文和中英双语之间切换。</p>
    <div id="subAssets"><p class="about">读取中…</p></div>
    <div class="field" style="margin-top:14px"><span>翻译方式</span>${seg("sub_translator", s.sub_translator, [["deepl", "DeepL"], ["llm", "大模型"]])}</div>
    ${s.sub_translator === "llm" ? `
      <div class="field"><label>接口地址</label><input class="input" data-k="llm_base_url" value="${esc(s.llm_base_url)}" placeholder="https://api.deepseek.com"></div>
      <div class="field"><label>密钥</label><input class="input" data-k="llm_key" value="${esc(s.llm_key)}" placeholder="sk-..."></div>
      <div class="field"><label>模型</label><div class="with-btn"><input class="input" data-k="llm_model" value="${esc(s.llm_model)}" placeholder="deepseek-chat"><button class="mini" id="checkTr">检测</button></div></div>
      <div class="field"><span></span><small style="margin:0">DeepSeek、通义千问、Kimi 等兼容 OpenAI 的接口都能用。会结合上下文成段翻译，口语比 DeepL 更自然，费用很低</small></div>`
    : `
      <div class="field"><label>DeepL 密钥</label><div class="with-btn"><input class="input" data-k="deepl_key" value="${esc(s.deepl_key)}" placeholder="xxxxxxxx-xxxx-…:fx"><button class="mini" id="checkTr">检测</button></div></div>
      <div class="field"><span></span><small style="margin:0">免费版每月 50 万字符，一个 20 分钟的英文视频大约用 1.5～2 万字符</small></div>`}
    <div class="field"><span></span><small id="trResult" style="margin:0"></small></div>
    <div class="field top-align"><span>生成文件</span><div>
      <label class="check"><input type="checkbox" data-k="sub_outputs.zh" ${s.sub_outputs.zh ? "checked" : ""}>中文</label>
      <label class="check"><input type="checkbox" data-k="sub_outputs.bilingual" ${s.sub_outputs.bilingual ? "checked" : ""}>中英双语</label>
      <label class="check"><input type="checkbox" data-k="sub_outputs.en" ${s.sub_outputs.en ? "checked" : ""}>英文原文</label></div></div>
    <div class="switch-row"><div>默认勾选“下载完生成中文字幕”<small>解析页和批量页的勾选框默认状态，每次下载前还能再改</small></div>
      <input type="checkbox" class="switch" data-k="sub_auto" ${s.sub_auto ? "checked" : ""}></div>
    <div class="switch-row"><div>YouTube 有人工英文字幕时直接用<small>比语音识别更准，而且不花识别时间</small></div>
      <input type="checkbox" class="switch" data-k="sub_prefer_platform" ${s.sub_prefer_platform ? "checked" : ""}></div>
    <div class="switch-row"><div>跳过静音和纯音乐<small>减少片头音乐、无人说话时乱出字幕</small></div>
      <input type="checkbox" class="switch" data-k="sub_vad" ${s.sub_vad ? "checked" : ""}></div>
  </section>

  <section class="group" id="g-components">
    <h3>组件</h3>
    <div id="compList"><p class="about">读取中…</p></div>
    <div class="comp-tools"><button class="mini" id="openSetupBtn">打开安装向导</button>
      <small>缺什么一键装好；组件装在固定位置，换新版本不用重装</small></div>
  </section>

  <section class="group" id="g-look">
    <h3>外观</h3>
    <div class="field"><span>颜色</span>${seg("theme", s.theme, [["system", "跟随系统"], ["light", "浅色"], ["dark", "深色"]])}</div>
  </section>

  <section class="group" id="g-about">
    <h3>反馈与关于</h3>
    <div class="switch-row"><div>下载不了、报错，或者想支持新网站<small>反馈页会自动带上系统和组件版本，一键复制</small></div>
      <button class="mini" id="openFeedbackBtn">反馈问题</button></div>
    <p class="about" id="aboutLine"></p>
  </section>

  <details class="adv" id="g-advanced" ${state.advOpen ? "open" : ""}>
    <summary>高级设置<small>文件名、多线程、格式、代理、登录信息、组件下载</small></summary>

  <section class="group" id="g-download-more">
    <h3>下载细节</h3>
    <div class="field"><label>文件名</label><input class="input" data-k="filename_template" value="${esc(s.filename_template)}">
      <small>yt-dlp 模板写法。默认：标题前 60 字 + [视频ID]</small></div>
    <div class="switch-row"><div>多线程加速<small>像 IDM 一样把文件切块、多个连接同时下。遇到不支持的视频会自动换回普通方式</small></div>
      <input type="checkbox" class="switch" data-k="accelerate" ${s.accelerate ? "checked" : ""}></div>
    ${s.accelerate ? `<div class="field"><span>每个文件连接数</span>${seg("connections", s.connections, [[4, "4"], [8, "8"], [16, "16"], [32, "32"]])}</div>
    <div class="field"><span></span><small style="margin:0">一般 16 就够；个别网站连接太多会被限速或报错，可以调小</small></div>` : ""}
    <div class="switch-row"><div>按平台分文件夹<small>YouTube、Bilibili、X、Douyin、WeChat 各放一个子文件夹</small></div>
      <input type="checkbox" class="switch" data-k="split_by_platform" ${s.split_by_platform ? "checked" : ""}></div>
    <div class="switch-row"><div>识别剪贴板<small>切回窗口时，如果剪贴板里有支持的链接就提示一键解析</small></div>
      <input type="checkbox" class="switch" data-k="watch_clipboard" ${s.watch_clipboard ? "checked" : ""}></div>
  </section>

  <section class="group" id="g-format">
    <h3>格式</h3>
    <div class="switch-row"><div>兼容优先<small>同等清晰度下优先 H.264 + AAC，Windows 自带播放器、手机、剪辑软件都能直接用。关掉则优先 AV1/VP9，体积更小但老电脑可能放不了</small></div>
      <input type="checkbox" class="switch" data-k="compat_mode" ${s.compat_mode ? "checked" : ""}></div>
    <div class="field"><span>封装格式</span>${seg("container", s.container, [["mp4", "MP4"], ["mkv", "MKV"]])}</div>
  </section>

  <section class="group" id="g-network">
    <h3>网络与代理</h3>
    <div class="field"><span>代理</span>${seg("proxy_mode", s.proxy_mode, [["system", "跟随系统"], ["manual", "手动"], ["none", "不使用"]])}</div>
    ${s.proxy_mode === "system" ? `<div class="field"><span></span><small style="margin:0">${s._system_proxy ? "检测到系统代理 " + esc(s._system_proxy) : "未检测到系统代理（Clash 等需打开“系统代理”）"}</small></div>` : ""}
    ${s.proxy_mode === "manual" ? `<div class="field"><label>代理地址</label><input class="input" data-k="proxy_url" value="${esc(s.proxy_url)}" placeholder="http://127.0.0.1:7890"></div>` : ""}
    <div class="field top-align"><span>走代理的平台</span><div>${["youtube", "x", "bilibili", "douyin", "wechat", "other", "translate"].map((p) =>
      `<label class="check"><input type="checkbox" data-k="proxy_platforms.${p}" ${s.proxy_platforms[p] ? "checked" : ""}>${p === "translate" ? "字幕翻译接口" : PLAT[p]}</label>`).join("")}</div></div>
  </section>

  <section class="group" id="g-cookies">
    <h3>登录信息（Cookie）</h3>
    <p class="about" style="margin:0 0 6px">B站 1080P 以上、X 敏感内容、YouTube 人机验证时需要。Windows 上 Chrome / Edge 的 Cookie 被系统加密，读不出来，建议用浏览器扩展 “Get cookies.txt LOCALLY” 导出 cookies.txt，或用 Firefox。</p>
    ${cookieRows}
    <div class="field top-align" style="margin-top:14px"><label>抖音 Cookie</label>
      <textarea data-k="douyin_cookie" placeholder="一般不需要。解析失败时，在浏览器登录 douyin.com，F12 → 网络 → 任意请求的 Cookie 整串粘贴到这里">${esc(s.douyin_cookie)}</textarea></div>
  </section>

  <section class="group" id="g-comp-net">
    <h3>组件下载</h3>
    <div class="field"><label>GitHub 加速</label>
      <input class="input" data-k="github_mirror" value="${esc(s.github_mirror)}" placeholder="可选，例如 https://ghfast.top/"></div>
    <div class="field"><span></span><small style="margin:0">组件从 GitHub 下载。没开代理又下不动时填一个加速前缀；镜像是第三方提供的，下完会和官方校验值核对。也可以手动把 exe 放进 <button class="text" id="openBin">bin 文件夹</button></small></div>
    <div class="switch-row"><div>自动更新 yt-dlp<small>网站改版后旧版会下不了。打开拾影时悄悄检查，两天最多一次</small></div>
      <input type="checkbox" class="switch" data-k="auto_update_ytdlp" ${s.auto_update_ytdlp !== false ? "checked" : ""}></div>
  </section>
  </details>`;

  call("app_info").then((i) => {
    $("#aboutLine").innerHTML = `拾影 ${esc(i.version)}${i.build ? " " + esc(i.build) : ""}　配置目录 ${esc(i.data_dir)}<br>仅供个人学习备份使用，请尊重创作者版权。`;
  }).catch(() => {});
}

function setPath(obj, path, val) {
  const ks = path.split(".");
  let o = obj;
  ks.slice(0, -1).forEach((k) => (o = o[k] = o[k] || {}));
  o[ks[ks.length - 1]] = val;
}
function patchFor(path, val) {
  const p = {};
  setPath(p, path, val);
  return p;
}

let saveTimer;
async function save(path, val, rerender) {
  setPath(state.settings, path, val);
  if (path === "theme") applyTheme();
  clearTimeout(saveTimer);
  const patch = patchFor(path, val);
  saveTimer = setTimeout(async () => {
    try {
      const sys = state.settings._system_proxy;
      state.settings = await call("save_settings", patch);
      state.settings._system_proxy = sys;
    } catch (e) { toast(e.message); }
  }, rerender ? 0 : 350);
  if (rerender) { const y = $("#settingsBody").scrollTop; renderSettings(); loadComponents(); loadSubAssets(); $("#settingsBody").scrollTop = y; }
}

$("#settingsBody").addEventListener("toggle", (e) => {
  if (e.target.id === "g-advanced") state.advOpen = e.target.open;
}, true);
$("#settingsBody").addEventListener("input", (e) => {
  const el = e.target;
  if (!el.dataset.k || el.type === "checkbox" || el.tagName === "SELECT") return;
  save(el.dataset.k, el.value, false);
});
$("#settingsBody").addEventListener("change", (e) => {
  const el = e.target;
  if (!el.dataset.k) return;
  if (el.type === "checkbox") save(el.dataset.k, el.checked, el.dataset.k === "accelerate");
  else if (el.type === "radio") { save(el.dataset.k, el.value, false); loadSubAssets(); }
  else if (el.tagName === "SELECT") save(el.dataset.k, el.value, el.dataset.k.endsWith(".mode"));
});
$("#settingsBody").addEventListener("click", async (e) => {
  const sb = e.target.closest(".seg button");
  if (sb) {
    const name = sb.parentElement.dataset.seg;
    const v = ["max_concurrent", "connections"].includes(name) ? Number(sb.dataset.v) : sb.dataset.v;
    sb.parentElement.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b === sb));
    save(name, v, name === "proxy_mode" || name === "sub_translator");
    return;
  }
  if (e.target.closest("#pickDir")) {
    const d = await call("choose_folder").catch((err) => toast(err.message));
    if (d) { save("download_dir", d, true); }
    return;
  }
  const pc = e.target.closest("[data-pick-cookie]");
  if (pc) {
    const f = await call("choose_cookie_file").catch((err) => toast(err.message));
    if (f) save(`cookies.${pc.dataset.pickCookie}.file`, f, true);
    return;
  }
  if (e.target.closest("#openBin")) { call("open_bin_dir").catch(() => {}); return; }
  if (e.target.closest("#openSetupBtn")) { closeDrawer(); openSetup(false); return; }
  if (e.target.closest("#openFeedbackBtn")) { closeDrawer(); openFeedback(); return; }
  if (e.target.closest("#openModels")) { call("open_models_dir").catch(() => {}); return; }
  const wb = e.target.closest("[data-whisper]");
  if (wb) { await call("install_whisper", wb.dataset.whisper === "gpu").catch((err) => toast(err.message)); loadSubAssets(); return; }
  const mb = e.target.closest("[data-model]");
  if (mb) { await call("install_model", mb.dataset.model).catch((err) => toast(err.message)); loadSubAssets(); return; }
  if (e.target.closest("#checkTr")) {
    const out = $("#trResult");
    out.textContent = "检测中…";
    out.style.color = "";
    clearTimeout(saveTimer);
    try {
      const sys = state.settings._system_proxy;
      const patch = {};
      ["deepl_key", "llm_base_url", "llm_key", "llm_model"].forEach((k) => { if (k in state.settings) patch[k] = state.settings[k]; });
      state.settings = await call("save_settings", patch);
      state.settings._system_proxy = sys;
      out.textContent = await call("check_translator");
      out.style.color = "var(--celadon)";
    } catch (err) { out.textContent = err.message; out.style.color = "var(--danger)"; }
    return;
  }
  const inst = e.target.closest("[data-install]");
  if (inst) {
    await call("install_component", inst.dataset.install).catch((err) => toast(err.message));
    loadComponents();
  }
});

let compTimer;
async function loadComponents() {
  clearTimeout(compTimer);
  const list = await call("components").catch(() => null);
  const el = $("#compList");
  if (!el || !list) return;
  el.innerHTML = list.map((c) => {
    let ver, cls = "ver";
    if (c.status === "working") ver = c.message || "处理中…";
    else if (c.status === "error") { ver = c.message; cls += " err"; }
    else if (c.version) ver = (c.status === "done" && c.message ? c.message : c.version) + (c.local ? "" : "（系统中已安装）");
    else { ver = "未安装"; cls += " missing"; }
    const btn = c.status === "working" ? "" :
      `<button class="mini" data-install="${c.name}">${c.version ? (c.name === "yt-dlp" ? "检查更新" : "重新下载") : "安装"}</button>`;
    return `<div class="comp"><div><b>${c.name}</b> <span class="desc">${esc(c.desc)}</span></div>${btn}
      <div class="${cls}">${esc(ver)}</div>
      ${c.status === "working" ? `<div class="prog" style="--p:${c.progress || 0}"><i></i></div>` : ""}</div>`;
  }).join("");
  if (list.some((c) => c.status === "working") && $("#app").classList.contains("drawer-open")) {
    compTimer = setTimeout(loadComponents, 600);
  }
}

let subTimer;
async function loadSubAssets() {
  clearTimeout(subTimer);
  const a = await call("sub_assets").catch(() => null);
  const el = $("#subAssets");
  if (!el || !a) return;
  const e = a.engine;
  const working = e.status === "working";
  let engineState;
  if (working) engineState = esc(e.message || "安装中…");
  else if (e.status === "error") engineState = `<span class="err-text">${esc(e.message)}</span>`;
  else if (e.installed) engineState = (e.gpu ? "GPU 版（NVIDIA 显卡加速）" : "CPU 版") + (e.build ? `　${esc(e.build)}` : "");
  else engineState = `<span class="missing">未安装</span>　${a.nvidia ? "检测到 NVIDIA 显卡，推荐装 GPU 版" : "没检测到 NVIDIA 显卡，装 CPU 版即可"}`;
  const btns = working ? "" : e.installed
    ? `<button class="mini" data-whisper="${e.gpu ? "cpu" : "gpu"}">换成${e.gpu ? " CPU " : " GPU "}版</button>`
    : `<button class="mini" data-whisper="cpu">装 CPU 版 约 20MB</button><button class="mini" data-whisper="gpu">装 GPU 版 约 650MB</button>`;
  const models = a.models.map((m) => {
    const sel = state.settings.sub_model === m.key;
    let right;
    if (m.status === "working") right = `<span class="m-prog">${esc(m.message || "下载中")}</span>`;
    else if (m.installed) right = `<span class="m-ok">已下载</span>`;
    else right = `<button class="mini" data-model="${m.key}">下载 ${Math.round(m.size / 1e6)}MB</button>`;
    return `<label class="model ${sel ? "on" : ""}">
      <input type="radio" name="sub_model" data-k="sub_model" value="${m.key}" ${sel ? "checked" : ""}>
      <span class="m-name">${esc(m.label)}<em>${esc(m.key)}</em></span>
      <span class="m-desc">${esc(m.desc)}${m.status === "error" ? `　<span class="err-text">${esc(m.message)}</span>` : ""}</span>
      <span class="m-right">${right}</span>
      ${m.status === "working" ? `<span class="prog" style="--p:${m.progress || 0}"><i></i></span>` : ""}
    </label>`;
  }).join("");
  el.innerHTML = `
    <div class="comp"><div><b>识别引擎</b> <span class="desc">whisper.cpp，在本机运行，不上传视频</span></div><div class="btns">${btns}</div>
      <div class="ver">${engineState}</div>
      ${working ? `<div class="prog" style="--p:${e.progress || 0}"><i></i></div>` : ""}</div>
    <div class="models-head"><span>识别模型</span><button class="text" id="openModels">模型文件夹</button></div>
    <div class="models">${models}</div>`;
  const busy = working || a.models.some((m) => m.status === "working");
  if (busy && $("#app").classList.contains("drawer-open")) subTimer = setTimeout(loadSubAssets, 700);
}

function applyTheme() {
  const t = state.settings?.theme || "system";
  if (t === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}

/* ---------- 弹窗 ---------- */
function openModal(id) { $("#" + id).hidden = false; document.body.classList.add("modal-open"); }
function closeModal(id) {
  $("#" + id).hidden = true;
  if (!document.querySelector(".modal:not([hidden])")) document.body.classList.remove("modal-open");
}
document.addEventListener("click", (e) => {
  const c = e.target.closest("[data-close]");
  if (c) { if (c.dataset.close === "setup") closeSetup(); else closeModal(c.dataset.close + "Modal"); return; }
  if (e.target.classList.contains("modal")) {
    if (e.target.id === "setupModal") closeSetup(); else closeModal(e.target.id);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("#feedbackModal").hidden) closeModal("feedbackModal");
  else if (!$("#setupModal").hidden) closeSetup();
});

/* ---------- 安装向导 ---------- */
const setupUI = { plan: null, sel: new Set(), subs: false, model: null, timer: null, auto: false };
const GROUP_TITLES = { download: ["下载视频", "必装"], subtitle: ["AI 中文字幕", "可选"] };

async function openSetup(auto) {
  setupUI.auto = !!auto;
  openModal("setupModal");
  $("#setupTitle").textContent = "装好组件就能开始下载";
  $("#setupBody").innerHTML = `<p class="about">读取中…</p>`;
  $("#setupFoot").innerHTML = "";
  let p;
  try { p = await call("setup_plan"); } catch (e) { toast(e.message); closeSetup(); return; }
  setupUI.plan = p;
  if (p.job && ["working", "error", "stopped"].includes(p.job.status)) { showSetupJob(p.job); return; }
  setupUI.sel = new Set(p.items.filter((it) => it.group === "download" && !it.installed).map((it) => it.key));
  const installedModel = p.items.find((it) => it.group === "model" && it.installed);
  setupUI.model = installedModel ? installedModel.key : p.recommended_model;
  setupUI.subs = false;
  renderSetupChoose();
}

function closeSetup() {
  clearTimeout(setupUI.timer);
  closeModal("setupModal");
  refreshBanner();
}

function setupKeys() {
  const p = setupUI.plan;
  const keys = [...setupUI.sel];
  if (setupUI.subs) {
    const eng = p.items.find((it) => it.key === "engine");
    if (eng && !eng.installed) keys.push("engine");
    const m = p.items.find((it) => it.key === setupUI.model);
    if (m && !m.installed) keys.push(m.key);
  }
  return keys;
}

function setupItem(it, input, right) {
  return `<label class="setup-item ${it.installed ? "is-done" : ""}">${input}
    <span class="si-name">${esc(it.label)}${it.recommended ? `<em class="rec">推荐</em>` : ""}</span>
    <span class="si-desc">${esc(it.desc)}</span>
    <span class="si-right">${right}</span></label>`;
}

function renderSetupChoose() {
  const p = setupUI.plan;
  const dl = p.items.filter((it) => it.group === "download");
  const eng = p.items.find((it) => it.key === "engine");
  const models = p.items.filter((it) => it.group === "model");
  const sizeOf = (it) => (it.installed ? `<span class="ok">已装好</span>` : `约 ${fmtBytes(it.size)}`);
  const dlRows = dl.map((it) => setupItem(it,
    `<input type="checkbox" data-sk="${it.key}" ${it.installed ? "checked disabled" : setupUI.sel.has(it.key) ? "checked" : ""}>`, sizeOf(it))).join("");
  const modelRows = models.map((it) => setupItem(it,
    `<input type="radio" name="setupModel" data-sm="${it.key}" ${setupUI.model === it.key ? "checked" : ""}>`, sizeOf(it))).join("");
  const keys = setupKeys();
  const total = keys.reduce((n, k) => n + ((p.items.find((it) => it.key === k) || {}).size || 0), 0);
  const allDone = dl.every((it) => it.installed);
  $("#setupTitle").textContent = allDone ? "下载组件都装好了" : "装好组件就能开始下载";
  $("#setupBody").innerHTML = `
    <p class="setup-lead">抖音、微信公众号不用装组件也能下。YouTube、哔哩哔哩、X 等网站要先装下面的下载组件。</p>
    <section class="setup-group"><h3>${GROUP_TITLES.download[0]}<em>${GROUP_TITLES.download[1]}</em></h3>${dlRows}</section>
    <section class="setup-group"><h3>${GROUP_TITLES.subtitle[0]}<em>${GROUP_TITLES.subtitle[1]}</em></h3>
      <label class="setup-item"><input type="checkbox" id="setupSubs" ${setupUI.subs ? "checked" : ""}>
        <span class="si-name">给英文视频生成中文字幕</span>
        <span class="si-desc">在你电脑上识别语音，不上传视频；翻译要在设置里填 DeepL 或大模型密钥</span>
        <span class="si-right"></span></label>
      ${setupUI.subs ? `<div class="setup-sub">${setupItem(eng, `<input type="checkbox" checked disabled>`, sizeOf(eng))}
        <p class="setup-k">识别模型（选一个）</p>${modelRows}</div>` : ""}
    </section>
    <p class="setup-sum">${keys.length ? `一共要下载约 <b>${fmtBytes(total)}</b>。` : allDone ? "下载需要的组件都已经装好了。" : "勾选要装的组件。"}
      组件装在 <code>${esc(p.location)}</code>，以后换新版本不用重装。</p>`;
  $("#setupFoot").innerHTML = `
    <button class="ghost" id="setupLater">${setupUI.auto ? "以后再说" : "关闭"}</button>
    <button class="seal-btn" id="setupGo" ${keys.length ? "" : "disabled"}>${keys.length ? "一键安装" : "没有要装的"}</button>`;
}

$("#setupModal").addEventListener("change", (e) => {
  const el = e.target;
  if (el.dataset.sk) { if (el.checked) setupUI.sel.add(el.dataset.sk); else setupUI.sel.delete(el.dataset.sk); renderSetupChoose(); }
  else if (el.id === "setupSubs") { setupUI.subs = el.checked; renderSetupChoose(); }
  else if (el.dataset.sm) { setupUI.model = el.dataset.sm; renderSetupChoose(); }
});

$("#setupModal").addEventListener("click", async (e) => {
  const id = (e.target.closest("button") || {}).id;
  if (!id) return;
  try {
    if (id === "setupLater") {
      if (setupUI.auto) await call("setup_later");
      closeSetup();
    } else if (id === "setupGo") {
      showSetupJob(await call("setup_start", setupKeys()));
    } else if (id === "setupStop") {
      showSetupJob(await call("setup_stop"));
    } else if (id === "setupRetry") {
      showSetupJob(await call("setup_retry"));
    } else if (id === "setupMirror") {
      const st = await call("setup_use_mirror", null);
      toast("已换成加速镜像，正在重新下载");
      showSetupJob(st.status === "working" ? st : await call("setup_retry"));
    } else if (id === "setupBg") {
      closeSetup();
      toast("在后台继续安装，顶上会显示进度");
    } else if (id === "setupDone") {
      closeSetup();
      state.settings = await call("get_settings");
    }
  } catch (err) { toast(err.message); }
});

function showSetupJob(job) {
  clearTimeout(setupUI.timer);
  const items = job.items || [];
  const cur = items.find((it) => it.key === job.current);
  const pct = job.total ? Math.min(1, job.done / job.total) : 0;
  const failed = items.filter((it) => it.status === "error");
  const title = { working: "正在安装组件", done: "全部装好了", error: `有 ${failed.length} 项没装上`, stopped: "已停止安装" }[job.status] || "安装组件";
  $("#setupTitle").textContent = title;
  const meta = [];
  if (job.total) meta.push(`${fmtBytes(job.done)} / ${fmtBytes(job.total)}`);
  if (job.status === "working" && job.speed) meta.push(`${fmtBytes(job.speed)}/s`);
  if (job.status === "working" && job.eta != null) meta.push(fmtEta(job.eta));
  const line = job.status === "working" && cur ? `正在装 <b>${esc(cur.label)}</b>（第 ${job.index} 项，共 ${job.count} 项）`
    : job.status === "done" ? "可以开始下载了：复制一个视频链接，回到拾影就会自动认出来。"
    : job.status === "stopped" ? "装到一半停下了，点「继续安装」会接着装没装完的。"
    : "下面写了没装上的原因。常见的是连不上 GitHub：换加速镜像，或者开代理后再试。";
  const rows = items.map((it) => {
    const p = it.total ? it.done / it.total : it.progress || 0;
    const right = it.status === "done" ? `<span class="ok">已装好</span>`
      : it.status === "working" ? esc(it.message || "下载中…")
      : it.status === "error" ? `<span class="err-text">${esc(it.message || "没装上")}</span>`
      : it.status === "stopped" ? "已停止" : "等待";
    return `<li class="${it.status}"><span class="sl-name">${esc(it.label)}</span><span class="sl-right">${right}</span>
      ${it.status === "working" ? `<i class="sl-bar" style="--p:${p}"></i>` : ""}</li>`;
  }).join("");
  const canMirror = !job.mirror;
  const slow = job.status === "working" && job.slow && canMirror
    ? `<div class="setup-slow">下载有点慢？<button class="text" id="setupMirror">换加速镜像</button><small>镜像是第三方提供的，下完会和官方校验值核对</small></div>` : "";
  $("#setupBody").innerHTML = `
    <div class="setup-total ${job.status}">
      <p class="st-line">${line}</p>
      <div class="bigbar" style="--p:${job.status === "done" ? 1 : pct}"><i></i></div>
      <p class="st-meta">${meta.join("　·　")}</p>
    </div>
    ${slow}
    <ul class="setup-list">${rows}</ul>`;
  const foot = {
    working: `<button class="ghost" id="setupStop">停止</button><button class="ink" id="setupBg">后台安装</button>`,
    done: `<button class="seal-btn" id="setupDone">开始使用</button>`,
    error: `<button class="ghost" data-close="setup">关闭</button>${canMirror ? `<button class="ghost" id="setupMirror">换加速镜像重试</button>` : ""}<button class="ink" id="setupRetry">重试没装上的</button>`,
    stopped: `<button class="ghost" data-close="setup">关闭</button><button class="ink" id="setupRetry">继续安装</button>`,
  }[job.status] || `<button class="ghost" data-close="setup">关闭</button>`;
  $("#setupFoot").innerHTML = foot;
  if (job.status === "working" && !$("#setupModal").hidden) {
    setupUI.timer = setTimeout(async () => {
      const st = await call("setup_status").catch(() => null);
      if (st && !$("#setupModal").hidden) showSetupJob(st);
    }, 500);
  }
  if (job.status !== "working") refreshBanner();
}

/* ---------- 反馈 ---------- */
let feedbackText = "";
async function openFeedback(extra) {
  openModal("feedbackModal");
  const body = $("#feedbackBody");
  body.innerHTML = `<p class="about">读取中…</p>`;
  let f;
  try { f = await call("feedback_info", extra || null); } catch (e) { body.innerHTML = `<p class="err-text">${esc(e.message)}</p>`; return; }
  feedbackText = f.text;
  const rows = (list) => list.map((r) => `<div class="fb-row"><span>${esc(r.label)}</span><b>${esc(r.value)}</b></div>`).join("");
  body.innerHTML = `
    <p class="fb-lead">下载不了、报错，或者想支持新网站，都可以告诉我们。把下面这些信息一起发过来，查问题会快很多。</p>
    ${f.task.length ? `<div class="fb-card task"><h4>这次出错的任务</h4>${rows(f.task)}</div>` : ""}
    <div class="fb-card"><h4>你的电脑和拾影</h4>${rows(f.items)}</div>
    <div class="fb-actions">
      <button class="ink" id="fbCopy">复制这些信息</button>
      <button class="ghost" id="fbIssue" data-url="${esc(f.issue_url)}">去 GitHub 反馈</button>
    </div>
    ${f.survey_url ? `<div class="fb-qr"><img src="feedback-qr.png" alt="反馈问卷二维码" width="132" height="132">
      <div><b>没有 GitHub 账号？</b><p>微信扫码填问卷，不用登录，国内网络直接能打开。</p>
      <button class="text" id="fbSurvey" data-url="${esc(f.survey_url)}">在浏览器打开问卷</button></div></div>` : ""}
    <p class="fb-note">这些信息里没有你的 Cookie、密钥和下载记录。视频链接可能带个人信息，介意的话发之前把那一行删掉。</p>`;
}
$("#feedbackBtn").onclick = () => openFeedback();
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* 退回旧办法 */ }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  return ok;
}
$("#feedbackModal").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.id === "fbCopy") toast((await copyText(feedbackText)) ? "已复制，粘贴到反馈里就行" : "复制失败，可以手动选中文字复制");
  if (b.id === "fbIssue" || b.id === "fbSurvey") call("open_url", b.dataset.url).catch((err) => toast(err.message));
});

/* ---------- 启动 ---------- */
(async function boot() {
  backend = await ready();
  try { state.settings = await call("get_settings"); } catch (e) { toast(e.message); }
  applyTheme();
  call("app_info").then((i) => {
    if (!i.build) return;
    const b = $("#buildBadge");
    b.textContent = i.build;
    b.hidden = false;
    document.title = "拾影 " + i.build;
  }).catch(() => {});
  poll(true);
  refreshBanner();
  input.focus();
  checkClipboard();
  // 第一次打开、还缺下载组件时，直接弹出安装向导
  call("app_info").then((i) => {
    if ((i.missing.length && !i.setup_skip) || (i.setup && i.setup.status === "working")) openSetup(!(i.setup && i.setup.status === "working"));
  }).catch(() => {});
})();

})();
