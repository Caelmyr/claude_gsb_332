/* 参数校准页：目标数据、参数界、确定性优化、结果诊断与曲线叠加。 */

let CATALOG = null;
let scenes = [];
let currentCal = null;
let overlayChart = null;
let historyChart = null;
let pollTimer = null;

function sceneVal() { return el("calScene").value; }
function sceneObj() { return scenes.find((s) => s.id === sceneVal()); }
function domInfo() {
  const s = sceneObj();
  return s ? CATALOG[s.domain] : null;
}

/* ------------------------------------------------------------------ */
/* 参数行：勾选 + 上下界（默认取目录界）                                 */
/* ------------------------------------------------------------------ */
function renderParamRows() {
  const s = sceneObj();
  const box = el("paramRows");
  if (!s) { box.innerHTML = '<p class="muted small">请先选择基础场景。</p>'; return; }
  const params = CATALOG[s.domain].models[s.model].params
    .filter((p) => p.type === "int" || p.type === "float");
  const cfg = s.config || {};
  box.innerHTML = `<table><thead><tr>
      <th>校准</th><th>参数</th><th class="num">场景当前值</th>
      <th class="num">下界</th><th class="num">上界</th></tr></thead><tbody>
    ${params.map((p, idx) => {
      const v = cfg[p.key] != null ? cfg[p.key] : p.default;
      // 默认勾选第一个数值参数，其余由用户选择
      const checked = idx === 0 ? "checked" : "";
      return `<tr data-key="${esc(p.key)}">
        <td><input type="checkbox" class="pcheck" ${checked}></td>
        <td>${esc(p.label)} <span class="muted small mono">${esc(p.key)}</span></td>
        <td class="num">${fmt(v, 4)}</td>
        <td><input class="plo num" type="number" value="${p.min}" step="any" style="width:110px"></td>
        <td><input class="phi num" type="number" value="${p.max}" step="any" style="width:110px"></td>
      </tr>`;
    }).join("")}</tbody></table>`;
}

function collectParamDefs() {
  const defs = [];
  el("paramRows").querySelectorAll("tbody tr").forEach((tr) => {
    if (!tr.querySelector(".pcheck").checked) return;
    const lo = parseFloat(tr.querySelector(".plo").value);
    const hi = parseFloat(tr.querySelector(".phi").value);
    if (!(lo <= hi)) throw new Error(`参数 ${tr.dataset.key} 的下界不能大于上界`);
    defs.push({ key: tr.dataset.key, min: lo, max: hi });
  });
  return defs;
}

/* ------------------------------------------------------------------ */
/* 指标下拉 / 从已有运行导入目标                                        */
/* ------------------------------------------------------------------ */
function renderMetrics() {
  const d = domInfo();
  el("calMetric").innerHTML = d
    ? (() => {
        const keys = d.metrics.map((m) => m.key);
        const preferred = keys.includes("infected") ? "infected" : keys[0];
        return d.metrics.map((m) =>
          `<option value="${esc(m.key)}" ${m.key === preferred ? "selected" : ""}>${esc(m.label)}</option>`).join("");
      })()
    : "";
}

let runsCache = [];

async function fillImportRuns() {
  const { runs } = await get("/api/runs");
  runsCache = runs;
  const sel = el("importRun");
  sel.innerHTML = '<option value="">— 也可从已有运行导入目标 —</option>' +
    runs.map((r) => `<option value="${esc(r.id)}">${esc(r.name)} (${DOMAIN_LABEL[r.domain] || r.domain}) · ${r.current_step}步</option>`).join("");
  return runs;
}

async function onPickImportRun() {
  const runId = el("importRun").value;
  const sel = el("importMetric");
  sel.innerHTML = "";
  if (!runId) return;
  const { series } = await get(`/api/runs/${runId}/series`);
  if (!series.length) return;
  const keys = Object.keys(series[0]).filter((k) => k !== "step");
  // 用该运行自身的 domain 取指标中文名（运行可能与当前场景不同域）
  const run = runsCache.find((r) => r.id === runId);
  const metrics = (run && CATALOG[run.domain]) ? CATALOG[run.domain].metrics : [];
  const labelOf = Object.fromEntries(metrics.map((m) => [m.key, m.label]));
  sel.innerHTML = keys.map((k) => `<option value="${esc(k)}">${esc(labelOf[k] || k)}</option>`).join("");
  const buildSeries = () => JSON.stringify(
    series.map((r) => [r.step, r[sel.value] == null ? null : r[sel.value]]));
  sel.dataset.series = buildSeries();
  sel.onchange = () => { sel.dataset.series = buildSeries(); };
}

function importTarget() {
  const raw = el("importMetric").dataset.series;
  if (!raw) { alert("请先选择运行与指标"); return; }
  el("calTarget").value = raw;
  el("previewInfo").textContent = `已导入 ${JSON.parse(raw).length} 个点`;
}

/* ------------------------------------------------------------------ */
/* 目标数据预览                                                         */
/* ------------------------------------------------------------------ */
async function previewTarget() {
  const txt = el("calTarget").value.trim();
  if (!txt) { alert("请先粘贴或导入目标数据"); return; }
  const mode = el("calOutlier").value;
  try {
    const r = await post("/api/calibrations/preview", {
      target: parseTargetInput(txt),
      detect_outliers: mode !== "off",
      drop_outliers: mode === "drop",
    });
    const s = r.summary;
    el("previewInfo").innerHTML =
      `共 ${s.n_total} 行 · 有效点 <b>${s.n_usable}</b> · 缺失 ${s.n_missing}` +
      ` · 合并重复 ${s.n_duplicate_rows} · 异常点 <b style="color:var(--orange)">${s.n_outliers}</b>` +
      ` · 时间范围 ${fmt(s.t_min, 2)}–${fmt(s.t_max, 2)}`;
  } catch (e) {
    el("previewInfo").textContent = "检查失败：" + e.message;
  }
}

function parseTargetInput(txt) {
  try { return JSON.parse(txt); } catch (e) { return txt; }  // 非 JSON 当作 CSV
}

/* ------------------------------------------------------------------ */
/* 启动校准                                                             */
/* ------------------------------------------------------------------ */
async function startCalibration() {
  const scene_id = sceneVal();
  if (!scene_id) { alert("请选择基础场景"); return; }
  let params;
  try { params = collectParamDefs(); } catch (e) { alert(e.message); return; }
  if (!params.length) { alert("请至少勾选一个待校准参数"); return; }
  const txt = el("calTarget").value.trim();
  if (!txt) { alert("请提供目标曲线数据"); return; }
  const mode = el("calOutlier").value;
  const body = {
    name: el("calName").value.trim() || "参数校准",
    scene_id,
    metric: el("calMetric").value,
    target: parseTargetInput(txt),
    params,
    steps: parseInt(el("calSteps").value, 10),
    replicates: parseInt(el("calReplicates").value, 10) || 1,
    loss: el("calLoss").value,
    seed: parseInt(el("calSeed").value, 10),
    n_global: parseInt(el("calGlobal").value, 10),
    n_local_starts: parseInt(el("calLocal").value, 10),
    max_evals: parseInt(el("calMaxEvals").value, 10),
    time_offset: parseFloat(el("calT0").value || "0"),
    time_scale: parseFloat(el("calTS").value || "1"),
    detect_outliers: mode !== "off",
    drop_outliers: mode === "drop",
  };
  el("startBtn").disabled = true;
  el("runStatus").textContent = "提交中…";
  try {
    const cal = await post("/api/calibrations", body);
    el("cancelBtn").style.display = "";
    el("cancelBtn").onclick = async () => { await post(`/api/calibrations/${cal.id}/cancel`); };
    pollResult(cal.id);
  } catch (e) {
    el("runStatus").textContent = "创建失败：" + e.message;
    el("startBtn").disabled = false;
  }
}

function pollResult(id) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const cal = await get(`/api/calibrations/${id}`);
    currentCal = cal;
    if (cal.status === "running") {
      const p = cal.progress || {};
      el("runStatus").textContent =
        `运行中：已评估 ${p.n_evals || 0} 次（${phaseLabel(p.phase)}）` +
        (p.best_loss != null ? `，当前最优误差 ${fmt(p.best_loss, 5)}` : "");
      return;
    }
    clearInterval(pollTimer);
    el("startBtn").disabled = false;
    el("cancelBtn").style.display = "none";
    refreshList();
    if (cal.status === "error") {
      el("runStatus").textContent = "校准失败：" + cal.error;
      return;
    }
    if (cal.status === "cancelled") {
      el("runStatus").textContent = "已取消";
      return;
    }
    el("runStatus").textContent = "完成 ✓";
    renderResult(cal);
  }, 700);
}

function phaseLabel(p) {
  return { init: "初始化", seed: "起点评估", lhs: "全局搜索", nm: "局部细化",
           pattern: "模式搜索细化", bound_check: "边界诊断" }[p] || p || "";
}

/* ------------------------------------------------------------------ */
/* 结果渲染                                                             */
/* ------------------------------------------------------------------ */
function renderResult(cal) {
  el("resultCard").style.display = "block";
  el("resultTitle").textContent = cal.name;
  const f = cal.fit || {};
  el("resultBadge").innerHTML = statusBadge(cal.status);
  el("kpis").innerHTML = [
    ["NRMSE", fmt(f.nrmse, 4), "归一化均方根误差（越小越好）"],
    ["RMSE", fmt(f.rmse, 3), "原始量纲均方根误差"],
    ["R²", f.r2 == null ? "—" : fmt(f.r2, 4), "决定系数（≤1，越接近 1 越好）"],
    ["最大残差", fmt(f.max_abs_residual, 2), `${f.n_aligned ?? 0} 个对齐点 · ${f.n_out_of_range ?? 0} 个越界`],
  ].map(([k, v, sub]) => `<div class="kpi"><div class="kpi-label">${k}</div>
      <div class="kpi-value">${v}</div><div class="kpi-sub">${sub}</div></div>`).join("");

  // 告警
  el("warnings").innerHTML = (cal.warnings || []).length
    ? `<div class="notice warn" style="margin:10px 0"><b>诊断与告警（${cal.warnings.length}）</b><ul style="margin:6px 0 0 18px">
       ${cal.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`
    : '<div class="notice ok" style="margin:10px 0">未发现数据或边界方面的明显问题。</div>';

  // 最优参数表
  const boundMap = Object.fromEntries((cal.bounds || []).map((b) => [b.key, b]));
  el("bestParams").innerHTML = (cal.params_spec || []).map((p) => {
    const v = cal.best_params[p.key];
    const b = boundMap[p.key];
    let badge = "";
    if (b) {
      const side = b.at === "lower" ? "下界" : "上界";
      badge = b.active
        ? `<span class="badge error">贴${side}·主动约束</span>`
        : `<span class="badge paused">贴${side}·平台</span>`;
    }
    return `<tr><td>${esc(p.label)} <span class="muted small mono">${esc(p.key)}</span></td>
      <td class="num"><b>${fmt(v, 5)}</b></td><td class="num">${fmt(p.min, 4)}</td>
      <td class="num">${fmt(p.max, 4)}</td><td>${badge}</td></tr>`;
  }).join("");

  const plateauN = cal.plateau_size || (cal.plateau || []).length;
  el("plateauInfo").innerHTML = plateauN > 1
    ? `⚠ 有 ${plateauN} 组参数的误差在 0.1% 以内（同一误差平台），数据不足以唯一区分；已按确定性规则（优先靠近可行域中心）选出上表这一组。主种子 ${cal.settings?.seed}，同输入重跑必得同一结果。`
    : `主种子 ${cal.settings?.seed} · ${cal.n_evals} 次仿真评估 · 耗时 ${cal.elapsed_seconds ?? "—"}s · 停止原因：${esc(stopLabel(cal.stopped_reason))}`;

  drawOverlay(cal);
  drawHistory(cal);

  // 下载完整 JSON
  el("downloadResult").href = "data:application/json;charset=utf-8," +
    encodeURIComponent(JSON.stringify(cal, null, 2));
  el("applyParams").onclick = () => applyParams(cal);
}

function stopLabel(r) {
  return { converged: "已收敛", max_evals: "达到评估预算",
           budget_exhausted_global: "预算在全局阶段用尽",
           budget_exhausted_local: "预算在局部阶段用尽" }[r] || r || "";
}

function drawOverlay(cal) {
  if (!overlayChart) overlayChart = echarts.init(el("overlayChart"), "dark");
  const aligned = cal.aligned || [];
  const simCurve = cal.sim_curve || [];
  const rep = (cal.settings || {}).replicates || 1;

  const targetOK = aligned.filter((a) => !a.outlier);
  const targetOut = aligned.filter((a) => a.outlier);
  const missing = (cal.target_summary || {}).missing || [];

  const series = [
    {
      name: "仿真（最优参数）", type: "line", showSymbol: false, smooth: false,
      data: simCurve.map((p) => [p.t, p.sim]),
      lineStyle: { width: 2.5, color: "#4f8cff" },
      itemStyle: { color: "#4f8cff" }, z: 3,
    },
    {
      name: "目标观测", type: "line", showSymbol: false,
      data: targetOK.map((a) => [a.t, a.target]),
      lineStyle: { width: 2, color: "#f39c12", type: "dashed" },
      itemStyle: { color: "#f39c12" }, z: 2,
    },
    {
      name: "目标异常点（未参与拟合）", type: "scatter", symbolSize: 11,
      data: targetOut.map((a) => [a.t, a.target]),
      itemStyle: { color: "#e74c3c" }, z: 4,
    },
  ];
  if (rep > 1) {
    // ECharts 置信带标准写法：基线（下界，透明）+ 带宽（2σ，透明线条+面积）
    series.push({
      name: `重复轨迹 ±1σ（n=${rep}）`, type: "line", showSymbol: false,
      lineStyle: { opacity: 0 }, stack: "band-ci", symbol: "none",
      data: simCurve.map((p) => [p.t, Math.max(0, p.sim - (p.sim_std || 0))]),
      z: 1, tooltip: { show: false }, silent: true,
    });
    series.push({
      name: `重复轨迹 ±1σ（n=${rep}）`, type: "line", showSymbol: false,
      lineStyle: { opacity: 0 }, stack: "band-ci", symbol: "none",
      areaStyle: { color: "rgba(79,140,255,0.18)" },
      data: simCurve.map((p) => [p.t, 2 * (p.sim_std || 0)]),
      z: 1, silent: true,
    });
  }

  overlayChart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#8b98a5" }, top: 0,
              data: series.map((s) => s.name).filter((v, i, a) => a.indexOf(v) === i) },
    grid: { left: 64, right: 24, top: 44, bottom: 44 },
    xAxis: { type: "value", name: "时间",
             axisLine: { lineStyle: { color: "#26313f" } },
             splitLine: { lineStyle: { color: "#1b2430" } } },
    yAxis: { type: "value", name: cal.metric_label || cal.metric,
             axisLine: { lineStyle: { color: "#26313f" } },
             splitLine: { lineStyle: { color: "#1b2430" } } },
    series,
  }, true);
  overlayChart.resize();

  el("chartLegend").textContent =
    `蓝线：最优参数仿真${rep > 1 ? `（${rep} 条重复轨迹均值，阴影为 ±1σ）` : ""}；` +
    `橙虚线：目标；红点：被判为异常、未参与拟合的观测点。` +
    (missing.length ? ` 另有 ${missing.length} 个缺测点。` : "");
}

function drawHistory(cal) {
  if (!historyChart) historyChart = echarts.init(el("historyChart"), "dark");
  const hist = (cal.history || []).filter((h) => !h.failed);
  // 最优误差随评估次数下降的包络
  let best = Infinity;
  const envelope = hist.map((h, i) => {
    best = Math.min(best, h.loss);
    return [i + 1, best];
  });
  const phaseColor = { seed: "#95a5a6", lhs: "#f39c12", nm: "#2ecc71",
                       pattern: "#16a085" };
  historyChart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#8b98a5" }, top: 0,
              data: ["每次评估误差", "当前最优"] },
    grid: { left: 64, right: 16, top: 36, bottom: 36 },
    xAxis: { type: "value", name: "评估次数", minInterval: 1,
             axisLine: { lineStyle: { color: "#26313f" } } },
    yAxis: { type: "value", name: "NRMSE 损失",
             axisLine: { lineStyle: { color: "#26313f" } },
             splitLine: { lineStyle: { color: "#1b2430" } } },
    series: [
      { name: "每次评估误差", type: "scatter", symbolSize: 5,
        data: hist.map((h, i) => ({
          value: [i + 1, h.loss],
          itemStyle: { color: phaseColor[h.phase] || "#8b98a5" },
        })) },
      { name: "当前最优", type: "line", showSymbol: false, smooth: false,
        data: envelope, lineStyle: { color: "#4f8cff", width: 2 } },
    ],
  }, true);
  historyChart.resize();
}

/* 将最优参数写回场景（PUT），不自动跳转 */
async function applyParams(cal) {
  const s = sceneObj();
  const useThis = (s && s.id === cal.scene_id) ? s
    : (await get(`/api/scenes/${cal.scene_id}`));
  if (!useThis || !useThis.id) { alert("找不到原始场景，无法写回"); return; }
  const cfg = { ...(useThis.config || {}), ...cal.best_params };
  try {
    await put(`/api/scenes/${cal.scene_id}`, { ...useThis, config: cfg });
    alert("已将最优参数写回场景配置。");
    scenes = await fillSceneSelect(el("calScene"));
    el("calScene").value = cal.scene_id;
    renderParamRows();
  } catch (e) { alert("写回失败：" + e.message); }
}

/* ------------------------------------------------------------------ */
/* 任务列表                                                             */
/* ------------------------------------------------------------------ */
async function refreshList() {
  const { calibrations } = await get("/api/calibrations");
  el("calList").innerHTML = calibrations.map((c) => `
    <div class="list-item" data-id="${esc(c.id)}">
      <div style="min-width:0">
        <div class="t">${esc(c.name)}</div>
        <div class="s">${esc(c.scene_name)} · 指标 ${esc(c.metric)} ·
          ${(c.params_spec || []).map((p) => esc(p.key)).join("、")}
          ${c.best_params ? ` · NRMSE ${fmt(c.fit?.nrmse, 4)}` : ""}</div>
      </div>
      <div class="row gap-6">
        ${statusBadge(c.status)}
        <button class="btn small xview" data-id="${esc(c.id)}">查看</button>
        <button class="btn danger small xdel" data-id="${esc(c.id)}">删除</button>
      </div>
    </div>`).join("") || '<p class="muted small">暂无校准任务。</p>';

  el("calList").querySelectorAll(".xview").forEach((b) => {
    b.onclick = async () => {
      const cal = await get(`/api/calibrations/${b.dataset.id}`);
      currentCal = cal;
      renderResult(cal);
      el("resultCard").scrollIntoView({ behavior: "smooth" });
    };
  });
  el("calList").querySelectorAll(".xdel").forEach((b) => {
    b.onclick = async () => {
      if (!confirm("确认删除该校准任务？")) return;
      await del(`/api/calibrations/${b.dataset.id}`);
      refreshList();
    };
  });
}

/* ------------------------------------------------------------------ */
async function init() {
  const { domains } = await get("/api/catalog");
  CATALOG = domains;
  scenes = await fillSceneSelect(el("calScene"));
  renderMetrics();
  renderParamRows();
  el("calScene").onchange = () => { renderMetrics(); renderParamRows(); };
  el("calMetric").onchange = onPickImportRun;
  el("importRun").onchange = onPickImportRun;
  el("importBtn").onclick = importTarget;
  el("previewBtn").onclick = previewTarget;
  el("startBtn").onclick = startCalibration;
  await fillImportRuns();
  await refreshList();
}

window.addEventListener("resize", () => {
  if (overlayChart) overlayChart.resize();
  if (historyChart) historyChart.resize();
});

init().catch((e) => console.error(e));
