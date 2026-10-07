/* Parameter calibration UI: bounds, target-curve cleaning, run, overlay. */

let catalogData = null;
let chart = null;
let pollTimer = null;
let currentCalId = null;
let lastPreviewPoints = null;

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ */
/* Catalog -> form                                                    */
/* ------------------------------------------------------------------ */
function domainInfo() { return catalogData.domains[$("calDomain").value]; }

function fillDomainModel() {
  const order = catalogData.order;
  $("calDomain").innerHTML = order.map((d) =>
    `<option value="${esc(d)}">${esc(catalogData.domains[d].label)}</option>`).join("");
  // default to epidemic since the typical example is infection counts
  $("calDomain").value = "epidemic";
  fillModels();
}

function fillModels() {
  const info = domainInfo();
  $("calModel").innerHTML = Object.entries(info.models).map(([k, m]) =>
    `<option value="${esc(k)}">${esc(m.label)}</option>`).join("");
  fillMetrics();
  fillParams();
}

function fillMetrics() {
  $("calMetric").innerHTML = domainInfo().metrics.map((m) =>
    `<option value="${esc(m.key)}">${esc(m.label)} (${esc(m.key)})</option>`).join("");
  if ([...$("calMetric").options].some((o) => o.value === "infected")) {
    $("calMetric").value = "infected";
  }
}

function fillParams() {
  const model = domainInfo().models[$("calModel").value];
  $("paramList").innerHTML = model.params
    .filter((p) => p.type === "int" || p.type === "float")
    .map((p) => {
      const checked = ["beta", "gamma", "p_slow", "grass_growth", "v0"].includes(p.key);
      return `
      <div class="param-row row gap-6" data-key="${esc(p.key)}" style="margin-bottom:6px">
        <input type="checkbox" class="pcheck" ${checked ? "checked" : ""}
               style="width:auto;flex:0 0 auto">
        <span style="min-width:130px" title="${esc(p.key)}">${esc(p.label)}</span>
        <input class="plow" type="number" value="${p.min}" step="any" style="width:84px"
               title="下界">
        <span class="muted">–</span>
        <input class="phigh" type="number" value="${p.max}" step="any" style="width:84px"
               title="上界">
        <span class="muted small">默认 ${p.default}</span>
      </div>`;
    }).join("");
}

function selectedParams() {
  return [...document.querySelectorAll(".param-row")].map((row) => {
    const key = row.dataset.key;
    if (!row.querySelector(".pcheck").checked) return null;
    return { name: key,
             low: parseFloat(row.querySelector(".plow").value),
             high: parseFloat(row.querySelector(".phigh").value) };
  }).filter(Boolean);
}

/* ------------------------------------------------------------------ */
/* Target preview/cleaning                                            */
/* ------------------------------------------------------------------ */
async function previewTarget() {
  const text = $("calTarget").value.trim();
  if (!text) { showNotice($("previewNote"), "请先粘贴或载入目标数据", "error"); return null; }
  try {
    const res = await post("/api/calibrations/preview", {
      csv_text: text, outlier_mode: $("calOutlier").value,
    });
    lastPreviewPoints = res.points;
    const s = res.summary;
    let html = `清洗结果：<b>${s.n_kept}</b> 个点参与拟合`
      + `（共 ${s.n_total_rows} 行；缺失 ${s.n_dropped_missing}，异常 ${s.n_outliers}，`
      + `合并重复 ${s.n_merged_duplicates}）`;
    const msgs = [...(res.warnings || []), ...(res.errors || [])];
    if (msgs.length) html += "<br>" + msgs.map((m) => `· ${esc(m)}`).join("<br>");
    showNotice($("previewNote"), html, res.errors.length ? "error" : "success");
    return res;
  } catch (e) {
    showNotice($("previewNote"), "预览失败：" + esc(e.message), "error");
    return null;
  }
}

/* ------------------------------------------------------------------ */
/* Run calibration                                                    */
/* ------------------------------------------------------------------ */
function buildPayload() {
  const params = selectedParams();
  return {
    name: $("calName").value.trim() || "参数校准",
    domain: $("calDomain").value,
    model: $("calModel").value,
    metric: $("calMetric").value,
    csv_text: $("calTarget").value,
    params,
    steps: parseInt($("calSteps").value, 10),
    seed: parseInt($("calSeed").value, 10),
    n_replicates: parseInt($("calReps").value, 10),
    confirm_replicates: parseInt($("calConfirm").value, 10),
    loss_kind: $("calLoss").value,
    outlier_mode: $("calOutlier").value,
    time_per_step: parseFloat($("calTps").value),
    time_offset: parseFloat($("calOffset").value),
    extrapolate: $("calExtrap").value,
  };
}

async function startCalibration() {
  if (!selectedParams().length) { alert("请至少勾选一个待校准参数并设置上下界"); return; }
  if (!$("calTarget").value.trim()) { alert("请提供目标曲线数据"); return; }
  const payload = buildPayload();
  $("btnRun").disabled = true;
  $("runStatus").textContent = "提交中…";
  try {
    const rec = await post("/api/calibrations", payload);
    currentCalId = rec.id;
    $("btnStop").style.display = "";
    poll(rec.id);
  } catch (e) {
    $("runStatus").textContent = "提交失败：" + e.message;
    $("btnRun").disabled = false;
  }
}

function poll(id) {
  clearInterval(pollTimer);
  const phaseLabel = { init: "初始化", design: "全局采样", refine: "局部寻优",
                       confirm: "独立复核", done: "完成" };
  pollTimer = setInterval(async () => {
    const rec = await get(`/api/calibrations/${id}`);
    const p = rec.progress || {};
    const total = p.total || 0;
    const pct = total ? `${p.done}/${total}` : "";
    $("runStatus").textContent =
      `${phaseLabel[p.phase] || p.phase || ""} ${pct} ${p.note || ""}`;
    if (rec.status === "finished") {
      clearInterval(pollTimer);
      $("btnRun").disabled = false;
      $("btnStop").style.display = "none";
      $("runStatus").textContent = "校准完成 ✓";
      renderResult(rec);
      refreshList();
    } else if (rec.status === "error" || rec.status === "stopped") {
      clearInterval(pollTimer);
      $("btnRun").disabled = false;
      $("btnStop").style.display = "none";
      $("runStatus").textContent = (rec.status === "stopped" ? "已中止" : "出错：")
        + (rec.error || "");
    }
  }, 900);
}

async function stopCalibration() {
  if (!currentCalId) return;
  await post(`/api/calibrations/${currentCalId}/stop`);
}

/* ------------------------------------------------------------------ */
/* Result rendering                                                   */
/* ------------------------------------------------------------------ */
function renderResult(rec) {
  $("chartCard").style.display = "";
  $("resultCard").style.display = "";
  renderOverlay(rec);
  renderBest(rec);
  renderRanking(rec);
  renderSensitivity(rec);
  renderResiduals(rec);
  renderDiagnostics(rec);
}

function renderOverlay(rec) {
  if (!chart) chart = echarts.init($("overlayChart"), "dark");
  const ov = rec.overlay || {};
  const tps = (rec.spec || {}).time_per_step || 1;
  const offset = (rec.spec || {}).time_offset || 0;
  const kept = (rec.target_points || []).filter((p) => p.status !== "excluded");
  const excluded = (rec.target_points || []).filter((p) => p.status === "excluded");
  const suspect = (rec.target_points || []).filter((p) => p.status === "suspect");
  const mean = ov.sim_mean || [];
  const std = ov.sim_std || [];
  const tm = ov.time || mean.map((_, i) => offset + i * tps);
  const upper = mean.map((v, i) => v + (std[i] || 0));
  const lower = mean.map((v, i) => Math.max(0, v - (std[i] || 0)));
  // ECharts area band: transparent base = lower bound; stacked on top of it a
  // segment whose height is upper-lower, filled lightly.
  const base = tm.map((t, i) => [t, lower[i]]);
  const seg = tm.map((t, i) => [t, upper[i] - lower[i]]);

  $("overlayMeta").textContent =
    `复核种子数 ${(ov.seeds || []).length}；阴影 = 多次重复的 ±1 标准差`;

  chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      formatter: (items) => {
        const t = items[0].value[0];
        const lines = [`时间 ${fmt(t, 2)}`];
        for (const it of items) {
          if (["±1σ 下界", "±1σ 带宽"].includes(it.seriesName)) continue;
          lines.push(`${it.marker} ${it.seriesName}: ${fmt(it.value[1], 2)}`);
        }
        return lines.join("<br>");
      },
    },
    legend: { textStyle: { color: "#8b98a5" }, top: 0,
              data: ["目标观测", "仿真最优（均值）", "异常点(已排除)", "存疑点"] },
    grid: { left: 60, right: 24, top: 40, bottom: 42 },
    xAxis: { type: "value", name: "时间",
             axisLine: { lineStyle: { color: "#26313f" } } },
    yAxis: { type: "value", name: metricLabel(rec),
             axisLine: { lineStyle: { color: "#26313f" } },
             splitLine: { lineStyle: { color: "#1b2430" } } },
    series: [
      { name: "±1σ 下界", type: "line", data: base, symbol: "none",
        lineStyle: { opacity: 0 }, areaStyle: { color: "transparent" },
        stack: "band", silent: true, tooltip: { show: false }, z: 1 },
      { name: "±1σ 带宽", type: "line", data: seg, symbol: "none",
        lineStyle: { opacity: 0 }, stack: "band",
        areaStyle: { color: "rgba(52,152,219,0.15)" },
        silent: true, tooltip: { show: false }, z: 1 },
      { name: "仿真最优（均值）", type: "line", smooth: true, showSymbol: false,
        data: tm.map((t, i) => [t, mean[i]]),
        lineStyle: { width: 2.5, color: "#3498db" }, z: 3 },
      { name: "目标观测", type: "scatter", symbolSize: 8,
        data: kept.map((p) => [p.t, p.y]),
        itemStyle: { color: "#e67e22" }, z: 4 },
      { name: "异常点(已排除)", type: "scatter", symbol: "x", symbolSize: 14,
        data: excluded.map((p) => [p.t, p.y]),
        itemStyle: { color: "#e74c3c" }, z: 5 },
      { name: "存疑点", type: "scatter", symbol: "diamond", symbolSize: 11,
        data: suspect.map((p) => [p.t, p.y]),
        itemStyle: { color: "#f1c40f" }, z: 5 },
    ],
  }, true);
  chart.resize();
}

function metricLabel(rec) {
  const metrics = (catalogData.domains[rec.domain] || {}).metrics || [];
  const m = metrics.find((x) => x.key === rec.metric);
  return m ? m.label : rec.metric;
}

function renderBest(rec) {
  const ce = rec.confirm_error || {};
  const te = rec.train_error || {};
  const rows = (metrics) => Object.entries(metrics).map(([k, a]) =>
    `<tr><td>${esc(k.toUpperCase())}</td><td>${fmt(a.mean, 5)}</td>
      <td class="muted">±${fmt(a.std, 3)}</td></tr>`).join("");
  const paramChips = Object.entries(rec.best_params || {}).map(([k, v]) =>
    `<span class="tag" style="margin-right:8px">${esc(k)} = <b>${fmt(v, 5)}</b></span>`
    + boundaryTag(rec, k)).join("");
  $("bestBox").innerHTML = `
    <div style="margin-bottom:10px">${paramChips}</div>
    <div class="grid grid-2">
      <div>
        <div class="muted small" style="margin-bottom:4px">拟合误差（独立复核种子）</div>
        <table><thead><tr><th>指标</th><th>均值</th><th>标准差</th></tr></thead>
        <tbody>${rows(ce)}</tbody></table>
      </div>
      <div>
        <div class="muted small" style="margin-bottom:4px">寻优阶段误差（同一组种子）</div>
        <table><thead><tr><th>指标</th><th>均值</th><th>标准差</th></tr></thead>
        <tbody>${rows(te)}</tbody></table>
      </div>
    </div>
    <div class="row gap-6" style="margin-top:12px">
      <button class="btn small success" id="btnApply">用最优参数创建运行</button>
      <button class="btn small" id="btnDownload">下载完整结果 JSON</button>
    </div>`;
  $("btnApply").onclick = () => applyBest(rec);
  $("btnDownload").onclick = () => {
    const blob = new Blob([JSON.stringify(rec, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `calibration_${rec.id}.json`;
    a.click();
  };
}

function boundaryTag(rec, key) {
  const hit = (rec.warnings || []).some((w) => w.includes(`参数 ${key}`));
  return hit ? ' <span class="badge stopped" title="该参数贴到了上下界">贴界</span>' : "";
}

function renderRanking(rec) {
  const rows = (rec.ranking || []).map((f, i) => `
    <tr class="${f.winner ? "row-best" : ""}">
      <td>${i + 1}${f.winner ? " ★" : ""}</td>
      <td class="mono small">${esc(Object.entries(f.params)
        .map(([k, v]) => `${k}=${fmt(v, 4)}`).join(" "))}</td>
      <td>${fmt(f.train_loss, 5)}</td>
      <td><b>${fmt(f.confirm_mean, 5)}</b></td>
      <td class="muted">±${fmt(f.confirm_se, 4)}</td>
    </tr>`).join("");
  $("rankTable").innerHTML =
    `<thead><tr><th>#</th><th>参数组合</th><th>寻优误差</th><th>复核均值</th><th>标准误</th></tr></thead>
     <tbody>${rows}</tbody>`;
}

function renderSensitivity(rec) {
  const rows = (rec.sensitivity || []).map((s) => `
    <tr><td>${esc(s.param)}</td>
      <td>${fmt(s.sensitivity, 5)}</td>
      <td><div class="bar"><i style="width:${Math.round(100 * (s.relative || 0))}%"></i></div></td>
      <td class="muted small">${s.relative < 0.05 ? "近乎不敏感 → 难以识别" : ""}</td>
    </tr>`).join("");
  $("sensTable").innerHTML =
    `<thead><tr><th>参数</th><th>±5% 扰动的误差增量</th><th>相对灵敏度</th><th></th></tr></thead>
     <tbody>${rows}</tbody>`;
}

function renderResiduals(rec) {
  const rows = (rec.residuals || []).map((r) => `
    <tr class="${r.status === "excluded" ? "row-excl" : ""}">
      <td>${fmt(r.t, 2)}</td><td>${fmt(r.target, 3)}</td>
      <td>${r.sim == null ? "—" : fmt(r.sim, 3)}</td>
      <td>${r.residual == null ? "—" : fmt(r.residual, 3)}</td>
      <td class="muted small">${esc(r.status)}</td>
    </tr>`).join("");
  $("residTable").innerHTML =
    `<thead><tr><th>时间</th><th>目标</th><th>仿真(插值)</th><th>残差</th><th>状态</th></tr></thead>
     <tbody>${rows}</tbody>`;
}

function renderDiagnostics(rec) {
  const ws = rec.warnings || [];
  $("diagBox").innerHTML = ws.length
    ? `<div class="notice ${ws.some((w) => w.includes("不可区分")) ? "warning" : "success"}"
            style="margin-top:10px">
         <b>诊断（${ws.length}）</b><br>${ws.map((w) => `· ${esc(w)}`).join("<br>")}
       </div>`
    : `<div class="notice success" style="margin-top:10px">所有参数均在区间内部收敛，
        候选间差异显著，目标数据质量良好。</div>`;
}

async function applyBest(rec) {
  try {
    const meta = await post(`/api/calibrations/${rec.id}/apply`, { seed: rec.seed });
    if (confirm("已用最优参数创建运行，是否前往实时可视化？")) {
      window.location.href = `/visualize.html?run=${meta.id}`;
    }
  } catch (e) { alert("创建运行失败：" + e.message); }
}

/* ------------------------------------------------------------------ */
/* History list                                                       */
/* ------------------------------------------------------------------ */
async function refreshList() {
  const { calibrations } = await get("/api/calibrations");
  $("calList").innerHTML = calibrations.map((c) => `
    <div class="list-item" data-id="${esc(c.id)}">
      <div style="min-width:0">
        <div class="t">${esc(c.name)}
          <span class="muted small">${esc(c.domain)}/${esc(c.model)} · ${esc(c.metric)}</span>
        </div>
        <div class="s mono small">
          ${c.best_params ? Object.entries(c.best_params)
              .map(([k, v]) => `${esc(k)}=${fmt(v, 4)}`).join("  ") : "—"}
        </div>
      </div>
      <div class="row gap-6">
        ${statusBadge(c.status)}
        <button class="btn small xview" data-id="${esc(c.id)}">查看</button>
        <button class="btn danger small xdel" data-id="${esc(c.id)}">删除</button>
      </div>
    </div>`).join("") || '<p class="muted small">暂无校准记录。</p>';

  $("calList").querySelectorAll(".xview").forEach((b) => {
    b.onclick = async () => {
      const rec = await get(`/api/calibrations/${b.dataset.id}`);
      if (rec.status === "running") { currentCalId = rec.id; poll(rec.id); }
      else if (rec.status === "finished") renderResult(rec);
      else alert("该记录状态：" + rec.status + (rec.error ? "（" + rec.error + "）" : ""));
    };
  });
  $("calList").querySelectorAll(".xdel").forEach((b) => {
    b.onclick = async () => {
      if (!confirm("确认删除该校准记录？")) return;
      await del(`/api/calibrations/${b.dataset.id}`);
      refreshList();
    };
  });
}

async function loadDemo() {
  // Built-in sample: weekly-ish infected counts with a reporting gap (32/36
  // absent) and a clerical x5 spike at t=60 — mirrors data/targets/*.csv.
  $("calTarget").value =
    "day,infected\n0,5\n4,98\n8,372\n12,432\n16,626\n20,679\n24,673\n28,679\n"
    + "40,203\n44,128\n48,65\n52,44\n56,31\n60,75\n64,7\n68,4\n72,1\n76,0\n80,0";
  previewTarget();
}

/* ------------------------------------------------------------------ */
async function init() {
  catalogData = await get("/api/catalog");
  fillDomainModel();
  $("calDomain").onchange = fillModels;
  $("calModel").onchange = () => { fillMetrics(); fillParams(); };
  $("btnPreview").onclick = previewTarget;
  $("btnRun").onclick = startCalibration;
  $("btnStop").onclick = stopCalibration;
  $("btnDemo").onclick = loadDemo;
  await refreshList();
}

window.addEventListener("resize", () => chart && chart.resize());
init().catch((e) => console.error(e));
