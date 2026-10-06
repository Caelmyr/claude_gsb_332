/* Canvas renderer shared by the real-time visualization and replay pages.

 * `renderSnapshot(canvas, snap, domain, model)` draws a simulation snapshot
 * (bounds + palette + individuals + optional substrate) onto a 2D canvas.  It
 * is domain-agnostic: colors come from the snapshot palette, and the drawing
 * mode (road strip / lattice cells / continuous points) is chosen from the
 * domain/model plus the coordinate type of the individuals.
 */

function colorOf(palette, state, type) {
  const c = (palette[state] || palette[type] || {}).color;
  return c || "#cccccc";
}

function isCellLike(inds) {
  if (!inds.length) return false;
  const sample = inds.slice(0, 10);
  return sample.every((a) => Number.isInteger(a.x) && Number.isInteger(a.y));
}

function drawPoints(ctx, inds, cw, ch, W, H, palette) {
  const r = Math.max(1.5, Math.min(4, cw / 320));
  for (const a of inds) {
    ctx.fillStyle = colorOf(palette, a.state, a.type);
    ctx.beginPath();
    ctx.arc(a.x / W * cw, a.y / H * ch, r, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawCells(ctx, inds, cw, ch, W, H, palette) {
  const cw1 = cw / W, ch1 = ch / H;
  for (const a of inds) {
    ctx.fillStyle = colorOf(palette, a.state, a.type);
    ctx.fillRect(a.x * cw1, a.y * ch1, Math.max(1, cw1), Math.max(1, ch1));
  }
}

function drawLanes(ctx, cw, ch, lanes) {
  ctx.strokeStyle = "#1f2a37";
  ctx.lineWidth = 1;
  for (let i = 1; i < lanes; i++) {
    const y = i / lanes * ch;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(cw, y); ctx.stroke();
  }
}

function drawRing(ctx, inds, cw, ch, L, palette) {
  const mid = ch / 2;
  ctx.strokeStyle = "#26313f";
  ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(cw, mid); ctx.stroke();
  for (const a of inds) {
    ctx.fillStyle = colorOf(palette, a.state, a.type);
    ctx.fillRect(a.x / L * cw - 2, mid - 5, 4, 10);
  }
}

function drawSubstrate(ctx, substrate, cw, ch) {
  const gh = substrate.length, gw = substrate[0].length || 1;
  const cw1 = cw / gw, ch1 = ch / gh;
  for (let y = 0; y < gh; y++) {
    for (let x = 0; x < gw; x++) {
      ctx.fillStyle = substrate[y][x] ? "#1e3b2a" : "#0c1116";
      ctx.fillRect(x * cw1, y * ch1, cw1 + 0.5, ch1 + 0.5);
    }
  }
}

function renderSnapshot(canvas, snap, domain, model) {
  const ctx = canvas.getContext("2d");
  const bounds = snap.bounds || { width: 100, height: 100 };
  const W = Math.max(1, bounds.width), H = Math.max(1, bounds.height);
  const inds = snap.individuals || [];
  const palette = snap.palette || {};
  const substrate = snap.substrate || null;
  const wrap = canvas.parentElement;
  const maxW = Math.max(320, wrap.clientWidth || 900);
  const dpr = window.devicePixelRatio || 1;

  let cw, ch;
  const isRing = domain === "traffic" && model === "abm";
  const isTrafficCA = domain === "traffic" && model === "ca";

  if (isRing) {
    cw = maxW; ch = 70;
  } else if (isTrafficCA) {
    const laneH = 26;
    cw = maxW; ch = Math.max(3, H) * laneH;
  } else if (substrate) {
    const gh = substrate.length, gw = substrate[0].length || 1;
    cw = maxW; ch = Math.round(cw * gh / gw);
  } else {
    cw = maxW; ch = Math.round(cw * H / W);
  }

  canvas.width = Math.round(cw * dpr);
  canvas.height = Math.round(ch * dpr);
  canvas.style.height = ch + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#0a0e12";
  ctx.fillRect(0, 0, cw, ch);

  if (isRing) {
    drawRing(ctx, inds, cw, ch, W, palette);
  } else if (isTrafficCA) {
    drawLanes(ctx, cw, ch, H);
    drawCells(ctx, inds, cw, ch, W, H, palette);
  } else if (substrate) {
    drawSubstrate(ctx, substrate, cw, ch);
    drawCells(ctx, inds, cw, ch, W, H, palette);
  } else if (isCellLike(inds)) {
    drawCells(ctx, inds, cw, ch, W, H, palette);
  } else {
    drawPoints(ctx, inds, cw, ch, W, H, palette);
  }

  return { cw, ch };
}

function renderLegend(container, palette) {
  if (!container) return;
  container.innerHTML = Object.entries(palette)
    .map(([key, v]) => `<span class="item"><span class="swatch" style="background:${v.color}"></span>${esc(v.label || key)}</span>`)
    .join("");
}
