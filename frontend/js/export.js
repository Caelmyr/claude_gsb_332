/* Data export: pick a run + format, trigger a file download. */

let formats = [];

async function init() {
  const f = await get("/api/export/formats");
  formats = f.formats;
  el("formats").innerHTML = formats.map((x, i) => `
    <label class="check" style="margin:6px 0">
      <input type="radio" name="fmt" value="${esc(x.key)}" ${i === 0 ? "checked" : ""}>
      <span><strong>${esc(x.label)}</strong> — <span class="muted small">${esc(x.desc)}</span></span>
    </label>`).join("");

  el("formats").querySelectorAll("input[name=fmt]").forEach((r) => {
    r.onchange = () => { el("stepField").style.display = r.value === "individuals" ? "block" : "none"; };
  });

  el("runSelect").onchange = async (e) => {
    if (!e.target.value) return;
    const meta = await get(`/api/runs/${e.target.value}`);
    el("stepInput").value = meta.current_step;
  };
  await fillRunSelect(el("runSelect"));
  if (el("runSelect").options.length > 1) {
    el("runSelect").selectedIndex = 1;
    const meta = await get(`/api/runs/${el("runSelect").value}`);
    el("stepInput").value = meta.current_step;
  }

  el("dlBtn").onclick = () => {
    const runId = el("runSelect").value;
    if (!runId) { alert("请选择运行"); return; }
    const fmt = document.querySelector("input[name=fmt]:checked").value;
    let url = `/api/export/${runId}?format=${fmt}`;
    if (fmt === "individuals") url += `&step=${el("stepInput").value || 0}`;
    el("dlStatus").textContent = "正在生成文件…";
    window.location.href = url;
    setTimeout(() => el("dlStatus").textContent = "已开始下载 ✓", 800);
  };
}

init().catch((e) => console.error(e));
