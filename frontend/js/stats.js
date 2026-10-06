/* Statistics charts: ECharts line chart of per-step aggregate metrics. */

let chart = null;
let series = [];
let labels = {};
let selected = new Set();

async function init() {
  const { domains } = await get("/api/catalog");
  for (const d of Object.values(domains)) {
    for (const m of d.metrics) labels[m.key] = m.label;
  }
  el("loadBtn").onclick = load;
  el("runSelect").onchange = load;
  await fillRunSelect(el("runSelect"));
  const q = new URLSearchParams(window.location.search).get("run");
  if (q && el("runSelect").querySelector(`option[value="${q}"]`)) {
    el("runSelect").value = q;
    await load();
  } else if (el("runSelect").options.length > 1) { el("runSelect").selectedIndex = 1; await load(); }
}

async function load() {
  const runId = el("runSelect").value;
  if (!runId) return;
  const { series: s } = await get(`/api/runs/${runId}/series`);
  series = s;

  // Metric keys from the first row (minus step).
  const keys = series.length ? Object.keys(series[series.length - 1]).filter((k) => k !== "step") : [];
  selected = new Set(keys);

  el("metricChecks").innerHTML = keys.map((k) => `
    <label class="check"><input type="checkbox" class="mchk" value="${esc(k)}" checked> ${esc(labels[k] || k)}</label>`).join("");
  el("metricChecks").querySelectorAll(".mchk").forEach((c) => {
    c.onchange = () => { c.checked ? selected.add(c.value) : selected.delete(c.value); render(); };
  });

  render();
}

function render() {
  if (!chart) chart = echarts.init(el("chart"), "dark");
  const keys = [...selected];
  const option = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#8b98a5" }, top: 0 },
    grid: { left: 60, right: 24, top: 40, bottom: 40 },
    xAxis: {
      type: "category",
      data: series.map((r) => r.step),
      name: "时间步",
      axisLine: { lineStyle: { color: "#26313f" } },
    },
    yAxis: {
      type: "value",
      axisLine: { lineStyle: { color: "#26313f" } },
      splitLine: { lineStyle: { color: "#1b2430" } },
    },
    series: keys.map((k) => ({
      name: labels[k] || k,
      type: "line",
      showSymbol: false,
      smooth: true,
      data: series.map((r) => r[k] != null ? r[k] : null),
    })),
  };
  chart.setOption(option, true);
  chart.resize();
}

window.addEventListener("resize", () => chart && chart.resize());

init().catch((e) => console.error(e));
