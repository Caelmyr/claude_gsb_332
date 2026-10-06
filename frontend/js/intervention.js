/* Interventions: apply policy/drug/cull actions to a running scene + log. */

let CATALOG = null;
let runId = null;
let runMeta = null;

function paramField(p) {
  if (p.type === "bool") {
    return `<label class="check"><input type="checkbox" data-key="${esc(p.key)}" ${p.default ? "checked" : ""}> ${esc(p.label)}</label>`;
  }
  return `<div class="range-row">
    <label class="muted small" style="min-width:70px">${esc(p.label)}</label>
    <input type="range" data-key="${esc(p.key)}" min="${p.min}" max="${p.max}" step="${p.step || 0.05}" value="${p.default}">
    <span class="range-val">${fmt(p.default)}</span>
  </div>`;
}

function renderForms() {
  const dom = CATALOG[runMeta.domain];
  el("itvForms").innerHTML = dom.interventions.map((itv) => `
    <div class="card" style="padding:12px; margin-bottom:10px" data-type="${esc(itv.type)}">
      <div class="row between" style="margin-bottom:6px">
        <strong>${esc(itv.label)}</strong>
        <button class="btn primary small apply">施加</button>
      </div>
      <div class="itv-params">${itv.params.length ? itv.params.map(paramField).join("") : '<span class="muted small">无参数</span>'}</div>
    </div>`).join("");

  el("itvForms").querySelectorAll(".card").forEach((card) => {
    card.querySelectorAll('input[type="range"]').forEach((r) => {
      const lab = r.parentElement.querySelector(".range-val");
      r.oninput = () => lab.textContent = fmt(parseFloat(r.value));
    });
    card.querySelector(".apply").onclick = async () => {
      const params = {};
      card.querySelectorAll("[data-key]").forEach((inp) => {
        params[inp.dataset.key] = inp.type === "checkbox" ? inp.checked : parseFloat(inp.value);
      });
      try {
        const res = await post(`/api/runs/${runId}/interventions`, { type: card.dataset.type, params });
        alert(res.applied ? ("已施加：" + res.reason) : ("未施加：" + res.reason));
        await loadAll();
      } catch (e) { alert("施加失败：" + e.message); }
    };
  });
}

function renderScheduled() {
  const list = runMeta.interventions || [];
  el("scheduled").innerHTML = list.map((i) => `
    <div class="row between" style="padding:6px 0;border-bottom:1px solid var(--border)">
      <span>${esc(i.label || i.type)} · 触发步 ${i.at_step ?? 0}</span>
      ${i.applied ? '<span class="badge finished">已施加</span>' : '<span class="badge ready">待触发</span>'}
    </div>`).join("") || '<p class="muted small">该场景没有定时干预。</p>';
}

async function loadEvents() {
  const { events } = await get(`/api/runs/${runId}/events`);
  el("events").innerHTML = `<thead><tr><th>步</th><th>类型</th><th>来源</th><th>结果</th></tr></thead><tbody>` +
    events.slice().reverse().map((e) => `
      <tr><td>${e.step}</td><td>${esc(e.type)}</td>
      <td>${e.scheduled ? '<span class="badge">定时</span>' : '<span class="badge ready">手动</span>'}</td>
      <td class="muted small">${esc((e.result && e.result.reason) || "")}</td></tr>`).join("") + "</tbody>";
}

async function loadAll() {
  runMeta = await get(`/api/runs/${runId}`);
  el("runInfo").textContent = `${runMeta.name} · ${DOMAIN_LABEL[runMeta.domain]} · ${MODEL_LABEL[runMeta.model]} · 第 ${runMeta.current_step} 步 · ${runMeta.status}`;
  renderForms();
  renderScheduled();
  await loadEvents();
}

async function init() {
  const { domains } = await get("/api/catalog");
  CATALOG = domains;
  el("runSelect").onchange = (e) => { if (e.target.value) { runId = e.target.value; loadAll(); } };
  el("refreshBtn").onclick = async () => { await fillRunSelect(el("runSelect")); };
  await fillRunSelect(el("runSelect"));
  if (el("runSelect").options.length > 1) {
    el("runSelect").selectedIndex = 1;
    runId = el("runSelect").value;
    await loadAll();
  }
}

init().catch((e) => console.error(e));
