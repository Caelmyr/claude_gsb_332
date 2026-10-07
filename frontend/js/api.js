/* Shared fetch helpers for the REST API. */

async function api(path, opts = {}) {
  const options = Object.assign({ headers: { "Content-Type": "application/json" } }, opts);
  if (options.body && typeof options.body !== "string") {
    options.body = JSON.stringify(options.body);
  }
  const res = await fetch(path, options);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const j = await res.json(); msg = j.error || j.details || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("Content-Type") || "";
  return ct.includes("json") ? res.json() : res.text();
}

const get = (p) => api(p);
const post = (p, body) => api(p, { method: "POST", body: body || {} });
const put = (p, body) => api(p, { method: "PUT", body: body || {} });
const del = (p) => api(p, { method: "DELETE" });

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmt(v, digits = 2) {
  if (v == null) return "—";
  if (typeof v === "number") {
    if (!Number.isFinite(v)) return "—";
    if (Number.isInteger(v)) return String(v);
    return v.toFixed(digits);
  }
  return String(v);
}

function statusBadge(status) {
  const cls = { ready: "ready", running: "running", paused: "paused",
                finished: "finished", stopped: "stopped", error: "error",
                pending: "ready" }[status] || "ready";
  const label = { ready: "就绪", running: "运行中", paused: "已暂停",
                  finished: "已完成", stopped: "已停止", error: "错误",
                  pending: "等待中" }[status] || status;
  return `<span class="badge ${cls}">${label}</span>`;
}

/* ECharts default dark theme hook (loaded once, shared by all chart pages). */
function echartsTheme() {
  return "dark";
}
