/* Real-time visualization: step loop + canvas rendering. */

let runId = null;
let meta = null;
let labels = {};
let running = false;
let timer = null;

function statusLine(t) { el("statusLine").textContent = t; }

async function refreshRuns() {
  const runs = await fillRunSelect(el("runSelect"));
  if (runId && runs.some((r) => r.id === runId)) {
    el("runSelect").value = runId;
  } else if (runId) {
    runId = null;
  }
}

async function loadRun(id) {
  runId = id;
  meta = await get(`/api/runs/${id}`);
  await loadSnapshot();
}

async function loadSnapshot() {
  if (!runId) return;
  const snap = await get(`/api/runs/${runId}/snapshot`);
  drawSnapshot(snap);
  const cur = await get(`/api/runs/${runId}`);
  statusLine(`第 ${cur.current_step} 步 · ${cur.status}`);
}

function drawSnapshot(snap) {
  renderSnapshot(el("canvas"), snap, meta.domain, meta.model);
  renderLegend(el("legend"), snap.palette);
  renderStats(snap.stats);
}

function renderStats(stats) {
  el("statTiles").innerHTML = Object.entries(stats).map(([k, v]) => `
    <div class="stat"><div class="k">${esc(labels[k] || k)}</div>
    <div class="v">${fmt(v)}</div></div>`).join("");
}

async function tick() {
  if (!running || !runId) return;
  try {
    const n = parseInt(el("stepsPerTick").value, 10) || 1;
    const r = await post(`/api/runs/${runId}/step`, { n });
    drawSnapshot(r.snapshot);
    statusLine(`第 ${r.step} 步 · 运行中`);
  } catch (e) {
    running = false;
    statusLine("已停止：" + e.message);
    return;
  }
  const delay = parseInt(el("speedSel").value, 10) || 160;
  timer = setTimeout(tick, delay);
}

function start() { if (!runId) { alert("请先选择运行"); return; } running = true; tick(); }
function pause() { running = false; if (timer) clearTimeout(timer); }

async function reset() {
  pause();
  if (!runId) return;
  await post(`/api/runs/${runId}/reset`);
  await loadSnapshot();
  statusLine("已重置到第 0 步");
}

async function init() {
  const { domains } = await get("/api/catalog");
  for (const d of Object.values(domains)) {
    for (const m of d.metrics) labels[m.key] = m.label;
  }
  el("runSelect").onchange = (e) => { if (e.target.value) loadRun(e.target.value); };
  el("refreshRuns").onclick = refreshRuns;
  el("btnStart").onclick = start;
  el("btnPause").onclick = pause;
  el("btnStep").onclick = async () => { if (!runId) return; const r = await post(`/api/runs/${runId}/step`, { n: 1 }); drawSnapshot(r.snapshot); statusLine(`第 ${r.step} 步`); };
  el("btnReset").onclick = reset;

  await refreshRuns();
  const q = new URLSearchParams(window.location.search).get("run");
  if (q && el("runSelect").querySelector(`option[value="${q}"]`)) {
    el("runSelect").value = q;
    await loadRun(q);
  }
}

init().catch((e) => statusLine("初始化失败：" + e.message));
