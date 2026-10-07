/* Landing hub: KPIs, quick start, feature cards. */

const CARDS = [
  { file: "config.html", icon: "🗺", title: "场景配置", desc: "选择领域与模型，配置地图与参数，安排定时干预措施。" },
  { file: "individuals.html", icon: "🚗", title: "个体管理", desc: "查看、筛选与检索车辆 / 动物 / 人等个体状态。" },
  { file: "visualize.html", icon: "🎨", title: "实时可视化", desc: "Canvas 实时渲染仿真过程，逐帧步进、暂停、调速。" },
  { file: "stats.html", icon: "📈", title: "统计图表", desc: "ECharts 绘制各统计量随时间的演化曲线。" },
  { file: "intervention.html", icon: "💉", title: "干预措施", desc: "在运行中施加政策 / 药物 / 捕杀等干预并查看日志。" },
  { file: "replay.html", icon: "⏪", title: "回放与时间轴", desc: "拖动时间轴快速回放任意时间步的个体快照。" },
  { file: "compare.html", icon: "⚖️", title: "对比实验", desc: "多组参数并行运行，叠加对比不同策略的效果。" },
  { file: "calibration.html", icon: "🎯", title: "参数校准", desc: "给定真实观测曲线，自动搜索最优参数并叠加对比、给出误差与诊断。" },
  { file: "report.html", icon: "📋", title: "报告生成", desc: "自动汇总峰值、终态与领域小结，生成运行报告。" },
  { file: "export.html", icon: "💾", title: "数据导出", desc: "导出统计 CSV、完整运行 JSON 或个体状态 JSON。" },
  { file: "history.html", icon: "🗂", title: "历史场景", desc: "浏览历史场景、运行与实验，一键重新打开或回放。" },
];

async function load() {
  const hist = await get("/api/history");
  const finished = hist.runs.filter((r) => r.status === "finished").length;
  el("stats").innerHTML = `
    <div class="stat"><div class="k">场景</div><div class="v">${hist.scenes.length}</div><div class="d">已保存的场景定义</div></div>
    <div class="stat"><div class="k">运行</div><div class="v">${hist.runs.length}</div><div class="d">历史运行总数</div></div>
    <div class="stat"><div class="k">已完成</div><div class="v">${finished}</div><div class="d">批量运行至完成</div></div>
    <div class="stat"><div class="k">对比实验</div><div class="v">${hist.experiments.length}</div><div class="d">多组参数实验</div></div>`;

  el("cards").innerHTML = CARDS.map((c) => `
    <a class="card" href="/${c.file}" style="display:block;text-decoration:none;color:var(--text)">
      <div style="font-size:22px">${c.icon}</div>
      <h2 style="margin:8px 0 6px">${c.title}</h2>
      <p class="muted small" style="margin:0">${c.desc}</p>
    </a>`).join("");

  await fillSceneSelect(el("quickScene"));
}

el("quickGo").addEventListener("click", async () => {
  const sceneId = el("quickScene").value;
  if (!sceneId) { alert("请先选择一个场景"); return; }
  try {
    const run = await post("/api/runs", { scene_id: sceneId });
    window.location.href = `/visualize.html?run=${run.id}`;
  } catch (e) { alert("创建运行失败：" + e.message); }
});

load().catch((e) => showNotice(el("stats"), "加载失败：" + e.message, "error"));
