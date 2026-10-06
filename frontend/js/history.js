/* History: browse scenes / runs / experiments with cross-page links. */

let hist = { scenes: [], runs: [], experiments: [] };
let tab = "scenes";

function renderScenes() {
  return hist.scenes.map((s) => `
    <div class="list-item">
      <div style="min-width:0">
        <div class="t">${esc(s.name)}</div>
        <div class="s">${DOMAIN_LABEL[s.domain] || s.domain} · ${MODEL_LABEL[s.model] || s.model} · ${esc(s.updated_at)}</div>
      </div>
      <div class="row gap-6">
        <a class="btn small" href="/config.html?scene=${esc(s.id)}">编辑</a>
        <button class="btn danger small del" data-kind="scenes" data-id="${esc(s.id)}">删除</button>
      </div>
    </div>`).join("") || '<p class="muted">暂无场景。</p>';
}

function renderRuns() {
  return hist.runs.map((r) => `
    <div class="list-item">
      <div style="min-width:0">
        <div class="t">${esc(r.name)} ${statusBadge(r.status)}</div>
        <div class="s">${DOMAIN_LABEL[r.domain] || r.domain} · ${MODEL_LABEL[r.model] || r.model} · 第 ${r.current_step} 步 · ${esc(r.updated_at)}</div>
      </div>
      <div class="row gap-6">
        <a class="btn small" href="/visualize.html?run=${esc(r.id)}">可视化</a>
        <a class="btn small" href="/replay.html?run=${esc(r.id)}">回放</a>
        <a class="btn small" href="/stats.html?run=${esc(r.id)}">统计</a>
        <a class="btn small" href="/report.html?run=${esc(r.id)}">报告</a>
        <button class="btn danger small del" data-kind="runs" data-id="${esc(r.id)}">删除</button>
      </div>
    </div>`).join("") || '<p class="muted">暂无运行。</p>';
}

function renderExperiments() {
  return hist.experiments.map((e) => `
    <div class="list-item">
      <div style="min-width:0">
        <div class="t">${esc(e.name)} ${statusBadge(e.status)}</div>
        <div class="s">${e.groups.length} 组 × ${e.steps} 步 · ${esc(e.created_at)}</div>
      </div>
      <div class="row gap-6">
        <a class="btn small" href="/compare.html">查看对比</a>
        <button class="btn danger small del" data-kind="experiments" data-id="${esc(e.id)}">删除</button>
      </div>
    </div>`).join("") || '<p class="muted">暂无实验。</p>';
}

function render() {
  const body = { scenes: renderScenes, runs: renderRuns, experiments: renderExperiments }[tab];
  el("list").innerHTML = body();
  el("list").querySelectorAll(".del").forEach((b) => {
    b.onclick = async () => {
      if (!confirm("确认删除？")) return;
      await del(`/api/${b.dataset.kind}/${b.dataset.id}`);
      await load();
    };
  });
}

async function load() {
  hist = await get("/api/history");
  render();
}

function init() {
  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
      tab = t.dataset.t;
      render();
    };
  });
  load().catch((e) => console.error(e));
}

init();
