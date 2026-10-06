/* Comparison experiments: create param groups, run in background, overlay. */

let chart = null;
let currentExp = null;
let labels = {};

function addGroupRow(name = "", cfg = "{}") {
  const div = document.createElement("div");
  div.className = "card group-row";
  div.style.cssText = "padding:10px;margin-bottom:8px";
  div.innerHTML = `
    <div class="row between">
      <input class="gname" placeholder="组名（如 基线 / 封锁70%）" value="${esc(name)}" style="max-width:240px">
      <button class="btn danger small gdel">删除</button>
    </div>
    <textarea class="gcfg" rows="2" placeholder="参数覆盖 JSON，如 {&quot;beta&quot;:0.1}">${esc(cfg)}</textarea>`;
  el("groups").appendChild(div);
  div.querySelector(".gdel").onclick = () => div.remove();
}

async function refreshList() {
  const { experiments } = await get("/api/experiments");
  el("expList").innerHTML = experiments.map((e) => `
    <div class="list-item" data-id="${esc(e.id)}">
      <div style="min-width:0">
        <div class="t">${esc(e.name)}</div>
        <div class="s">${e.groups.length} 组 · ${e.steps} 步 · ${esc(e.scene_id)}</div>
      </div>
      <div class="row gap-6">
        ${statusBadge(e.status)}
        <button class="btn danger small xdel" data-id="${esc(e.id)}">删除</button>
      </div>
    </div>`).join("") || '<p class="muted small">暂无实验。</p>';

  el("expList").querySelectorAll(".list-item").forEach((li) => {
    li.onclick = (ev) => { if (ev.target.closest(".xdel")) return; selectExp(li.dataset.id); };
  });
  el("expList").querySelectorAll(".xdel").forEach((b) => {
    b.onclick = async () => {
      if (!confirm("确认删除该实验？")) return;
      await del(`/api/experiments/${b.dataset.id}`);
      refreshList();
    };
  });
}

async function selectExp(id) {
  currentExp = await get(`/api/experiments/${id}`);
  if (currentExp.status !== "finished") {
    el("chartCard").style.display = "none";
    setTimeout(() => selectExp(id), 800);
    return;
  }
  el("chartCard").style.display = "block";
  el("chartTitle").textContent = currentExp.name;

  const keys = [...new Set(currentExp.runs.flatMap((r) =>
    r.series.length ? Object.keys(r.series[0]).filter((k) => k !== "step") : []))];
  el("metricSel").innerHTML = keys.map((k) => `<option value="${esc(k)}">${esc(labels[k] || k)}</option>`).join("");
  renderChart();
}

function renderChart() {
  if (!currentExp || !currentExp.runs.length) return;
  if (!chart) chart = echarts.init(el("chart"), "dark");
  const metric = el("metricSel").value;
  const ref = currentExp.runs[0];
  const option = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#8b98a5" }, top: 0 },
    grid: { left: 60, right: 24, top: 40, bottom: 40 },
    xAxis: { type: "category", data: ref.series.map((r) => r.step), name: "时间步",
             axisLine: { lineStyle: { color: "#26313f" } } },
    yAxis: { type: "value", axisLine: { lineStyle: { color: "#26313f" } },
             splitLine: { lineStyle: { color: "#1b2430" } } },
    series: currentExp.runs.map((r) => ({
      name: r.name, type: "line", showSymbol: false, smooth: true,
      data: r.series.map((row) => row[metric] != null ? row[metric] : null),
    })),
  };
  chart.setOption(option, true);
  chart.resize();
}

async function createExp() {
  const name = el("expName").value.trim() || "对比实验";
  const scene_id = el("expScene").value;
  if (!scene_id) { alert("请选择基础场景"); return; }
  const steps = parseInt(el("expSteps").value || "200", 10);
  const groups = [];
  document.querySelectorAll(".group-row").forEach((row) => {
    let cfg = {};
    try { cfg = JSON.parse(row.querySelector(".gcfg").value || "{}"); }
    catch (e) { alert("参数组 JSON 解析失败：" + e.message); throw e; }
    groups.push({ name: row.querySelector(".gname").value.trim() || `组${groups.length + 1}`, config: cfg });
  });
  if (!groups.length) { alert("请至少添加一个参数组"); return; }

  const stat = el("expStatus");
  stat.textContent = "正在运行…";
  try {
    const exp = await post("/api/experiments", { name, scene_id, steps, groups });
    stat.textContent = "已提交，正在后台运行各组…";
    const poll = setInterval(async () => {
      const e = await get(`/api/experiments/${exp.id}`);
      if (e.status === "finished") { clearInterval(poll); stat.textContent = "完成 ✓"; await refreshList(); selectExp(exp.id); }
      else if (e.status === "error") { clearInterval(poll); stat.textContent = "出错：" + e.error; }
    }, 600);
  } catch (e) { stat.textContent = "创建失败：" + e.message; }
}

async function init() {
  const { domains } = await get("/api/catalog");
  for (const d of Object.values(domains)) {
    for (const m of d.metrics) labels[m.key] = m.label;
  }
  el("addGroup").onclick = () => addGroupRow();
  el("createExp").onclick = createExp;
  el("metricSel").onchange = renderChart;
  addGroupRow("基线", "{}");
  addGroupRow("干预", "{}");
  await fillSceneSelect(el("expScene"));
  await refreshList();
}

window.addEventListener("resize", () => chart && chart.resize());

init().catch((e) => console.error(e));
