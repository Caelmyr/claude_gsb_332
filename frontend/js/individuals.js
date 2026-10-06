/* Individual management: list / filter / search per-run individuals. */

const PAGE_SIZE = 100;
let all = [];
let filtered = [];
let page = 0;
let columns = ["id", "type", "state", "x", "y"];

async function refreshRuns() {
  const runs = await fillRunSelect(el("runSelect"));
  if (runs.length) {
    const r = runs[0];
    el("runSelect").value = r.id;
    el("stepInput").value = r.current_step;
  }
}

function nearestStep(steps, v) {
  if (!steps.length) return 0;
  return steps.reduce((p, c) => (Math.abs(c - v) < Math.abs(p - v) ? c : p), steps[0]);
}

async function load() {
  const runId = el("runSelect").value;
  if (!runId) { showNotice(el("summary"), "请先创建或选择一个运行。", "warn"); return; }
  let step = parseInt(el("stepInput").value || 0, 10);
  // Snap to a persisted snapshot (snapshots may be sharded at an interval > 1).
  const { steps } = await get(`/api/runs/${runId}/steps`);
  step = nearestStep(steps, step);
  el("stepInput").value = step;
  const snap = await get(`/api/runs/${runId}/snapshot?step=${step}`);
  all = snap.individuals || [];

  // Build columns from extra keys on the first individual.
  const extra = [];
  if (all.length) {
    const first = all[0];
    for (const k of Object.keys(first)) {
      if (!["id", "type", "state", "x", "y"].includes(k)) extra.push(k);
    }
  }
  columns = ["id", "type", "state", "x", "y"].concat(extra.slice(0, 5));

  // Populate filter options.
  const types = [...new Set(all.map((a) => a.type))];
  const states = [...new Set(all.map((a) => a.state))];
  el("typeFilter").innerHTML = '<option value="">全部类型</option>' + types.map((t) => `<option>${esc(t)}</option>`).join("");
  el("stateFilter").innerHTML = '<option value="">全部状态</option>' + states.map((s) => `<option>${esc(s)}</option>`).join("");

  applyFilter();
}

function applyFilter() {
  const q = el("searchInput").value.trim().toLowerCase();
  const tf = el("typeFilter").value;
  const sf = el("stateFilter").value;
  filtered = all.filter((a) =>
    (!q || String(a.id).toLowerCase().includes(q)) &&
    (!tf || a.type === tf) &&
    (!sf || a.state === sf));
  page = 0;
  render();
}

function render() {
  const start = page * PAGE_SIZE;
  const rows = filtered.slice(start, start + PAGE_SIZE);
  const head = columns.map((c) => `<th class="${c === "x" || c === "y" ? "num" : ""}">${esc(c)}</th>`).join("");
  const body = rows.map((a) => `<tr>${columns.map((c) => {
    const v = a[c];
    const cls = (c === "x" || c === "y" || typeof v === "number") ? "num" : "";
    return `<td class="${cls}">${fmt(v)}</td>`;
  }).join("")}</tr>`).join("");
  el("itable").innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body || '<tr><td colspan="' + columns.length + '" class="muted">无数据</td></tr>'}</tbody>`;
  el("summary").textContent = `共 ${all.length} 个个体，筛选后 ${filtered.length} 个；类型列 = 车辆/动物/人，状态列 = 各自的状态标签。`;
  el("pageInfo").textContent = `第 ${page + 1} / ${Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))} 页`;
  el("prevBtn").disabled = page === 0;
  el("nextBtn").disabled = (page + 1) * PAGE_SIZE >= filtered.length;
}

function init() {
  el("loadBtn").onclick = load;
  el("prevBtn").onclick = () => { if (page > 0) { page--; render(); } };
  el("nextBtn").onclick = () => { if ((page + 1) * PAGE_SIZE < filtered.length) { page++; render(); } };
  el("searchInput").oninput = debounce(applyFilter, 200);
  el("typeFilter").onchange = applyFilter;
  el("stateFilter").onchange = applyFilter;
  el("runSelect").onchange = async (e) => {
    if (!e.target.value) return;
    const meta = await get(`/api/runs/${e.target.value}`);
    el("stepInput").value = meta.current_step;
    load();
  };
  refreshRuns().then(load).catch((e) => showNotice(el("summary"), "加载失败：" + e.message, "error"));
}

init();
