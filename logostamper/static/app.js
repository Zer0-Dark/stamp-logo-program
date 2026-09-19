/* Logo Stamper — click-to-place UI.
   Positions are normalised (0..1) and the logo is sized against the photo's
   geometric mean, matching the server so preview and output agree exactly. */

const $ = (id) => document.getElementById(id);

const S = {
  names: [], positions: {}, rendered: new Set(),
  cursor: 0, globalScale: 0.10,
  logos: [],          // [{name, aspect}]
  activeLogo: 0,      // which logo the next click places
  marks: [],          // marks on the photo currently shown
  lastMarks: null,    // every mark from the previous photo, for Space
  outDir: "", ready: false,
};

const logoAspect = (i) => (S.logos[i] && S.logos[i].aspect) || 1;

const api = async (path, body) => {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
};

/* ============================== setup ============================== */

/* Show the end of a long path — the folder name matters more than the drive.
   Done in JS because the CSS `direction: rtl` trick relocates a leading
   separator ("/photos" renders as "photos/"). */
function shortPath(path, keep = 2) {
  const sep = path.includes("\\") ? "\\" : "/";
  const parts = path.split(sep).filter(Boolean);
  if (parts.length <= keep) return path;
  return "…" + sep + parts.slice(-keep).join(sep);
}

function chosenList(target) {
  try {
    return JSON.parse($(target).value || "[]");
  } catch {
    return [];
  }
}

function setChosen(target, paths) {
  const list = Array.isArray(paths) ? paths.filter(Boolean) : (paths ? [paths] : []);
  writeChosen(target, list);
}

/** Add to what is already chosen, skipping duplicates. */
function addChosen(target, paths) {
  const current = chosenList(target);
  for (const p of (Array.isArray(paths) ? paths : [paths])) {
    if (p && !current.includes(p)) current.push(p);
  }
  writeChosen(target, current);
}

function writeChosen(target, list) {
  $(target).value = JSON.stringify(list);
  const row = document.querySelector(`.chooser[data-target="${target}"]`);
  const out = document.querySelector(`[data-path-for="${target}"]`);
  row.classList.toggle("chosen", list.length > 0);
  if (target === "logoPath") {
    out.textContent = list.length
      ? `${list.length} logo${list.length === 1 ? "" : "s"} chosen — click to add another`
      : "Select one, or several with Ctrl — click again to add more";
    renderLogoList(list);
  } else if (list.length === 1) {
    out.textContent = shortPath(list[0]);
    out.title = list[0];
  }
}

/* Logos are listed so they can be removed one by one. Picking several at once
   relies on Ctrl-click inside the Windows dialog, which does not suit everyone,
   so clicking the button again simply adds more. */
function renderLogoList(list) {
  const ul = $("logoList");
  ul.innerHTML = "";
  list.forEach((path, i) => {
    const li = document.createElement("li");
    const name = path.split(/[\\/]/).pop();
    li.innerHTML =
      `<span class="n">${i + 1}</span>` +
      `<span class="f" title="${path.replace(/"/g, "&quot;")}">${name}</span>`;
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "rm";
    rm.textContent = "\u00d7";
    rm.title = "Remove this logo";
    rm.addEventListener("click", () => {
      writeChosen("logoPath", chosenList("logoPath").filter((p) => p !== path));
    });
    li.appendChild(rm);
    ul.appendChild(li);
  });
}

/* Typed paths are a fallback only; they win if the user filled them in. */
function resolvePaths(target) {
  const typed = $(target + "Typed");
  if (typed && typed.value.trim()) {
    return typed.value.split(/[\n;]+/).map((v) => v.trim()).filter(Boolean);
  }
  return chosenList(target);
}

const resolvePath = (target) => resolvePaths(target)[0] || "";

$("start").addEventListener("click", async () => {
  const err = $("setupError");
  err.hidden = true;
  const payload = {
    photo_dir: resolvePath("photoDir"),
    logo_paths: resolvePaths("logoPath"),
    out_dir: resolvePath("outDir"),
    global_scale: Number($("setScale").value) / 100,
    opacity: Number($("setOpacity").value) / 100,
    batch_size: Number($("setBatch").value),
  };
  if (!payload.photo_dir || !payload.logo_paths.length) {
    err.textContent = "Please choose both a photo folder and at least one logo.";
    err.hidden = false;
    return;
  }
  $("start").disabled = true;
  $("start").textContent = "Reading folder…";
  const res = await api("/api/open", payload);
  $("start").disabled = false;
  $("start").textContent = "Start";
  if (res.error) {
    err.textContent = res.error;
    err.hidden = false;
    return;
  }
  await loadState();
  show("work");
  if (res.resumed) {
    $("resumedText").textContent =
      `Picking up where you left off — ${res.already_done} of ${res.count} photos already done. ` +
      `Size ${(res.global_scale * 100).toFixed(1)}% and opacity ${Math.round(res.opacity * 100)}% ` +
      `were restored from last time.`;
    $("resumed").hidden = false;
  }
});

async function loadState() {
  const st = await api("/api/state");
  if (!st.open) return false;
  S.names = st.names;
  S.positions = st.positions || {};
  S.rendered = new Set(st.rendered || []);
  S.globalScale = st.global_scale;
  S.logos = st.logos || [];
  S.activeLogo = 0;
  S.outDir = st.out_dir;
  S.cursor = st.cursor || 0;

  // Resume where the user stopped: the first photo with no decision on it.
  if (!S.positions[S.names[S.cursor]]) {
    const next = S.names.findIndex((n) => !S.positions[n]);
    if (next >= 0) S.cursor = next;
  }

  $("total").textContent = S.names.length;
  buildLogoBar();
  $("ghost").src = "/api/logo?i=0&t=" + Date.now();
  $("scale").value = (S.globalScale * 100).toFixed(1);
  $("scaleOut").textContent = (S.globalScale * 100).toFixed(1) + "%";
  S.opacity = st.opacity === undefined ? 1 : st.opacity;
  $("opacity").value = Math.round(S.opacity * 100);
  $("opacityOut").textContent = Math.round(S.opacity * 100) + "%";
  applyOpacity();
  S.ready = true;
  render();
  return true;
}

/* One chip per logo. With a single logo the bar stays hidden entirely, so
   the simple case looks exactly as it did before. */
function buildLogoBar() {
  const bar = $("logoBar");
  bar.innerHTML = "";
  bar.hidden = S.logos.length < 2;
  if (bar.hidden) return;
  S.logos.forEach((lg, i) => {
    const chip = document.createElement("button");
    chip.className = "logo-chip";
    chip.type = "button";
    chip.innerHTML =
      `<img src="/api/logo?i=${i}" alt=""><span>${i + 1}</span>`;
    chip.title = `${lg.name} — press ${i + 1} on the top row, or Tab to cycle`;
    chip.addEventListener("click", () => { setActiveLogo(i); chip.blur(); });
    bar.appendChild(chip);
  });
  paintLogoBar();
}

function paintLogoBar() {
  [...$("logoBar").children].forEach((chip, i) => {
    chip.classList.toggle("active", i === S.activeLogo);
    chip.classList.toggle("done", S.marks.some((m) => m.logo === i));
  });
}

function setActiveLogo(i) {
  if (!S.logos.length) return;
  if (S.activeLogo !== (i + S.logos.length) % S.logos.length) S.pendingScale = null;
  S.activeLogo = (i + S.logos.length) % S.logos.length;
  $("ghost").src = `/api/logo?i=${S.activeLogo}`;
  sizeGhost();
  paintLogoBar();
}

/** The next logo on this photo that has not been placed yet, or -1. */
function nextUnplacedLogo() {
  for (let i = 0; i < S.logos.length; i++) {
    if (!S.marks.some((m) => m.logo === i)) return i;
  }
  return -1;
}

function show(which) {
  for (const id of ["setup", "work", "done"]) $(id).hidden = id !== which;
}

/* ============================== stage ============================== */

const photo = $("photo");
const ghost = $("ghost");
const frame = $("frame");

let frameBox = { w: 0, h: 0 };

function fitFrame() {
  if (!photo.naturalWidth) return;
  const stage = $("stage");
  const pad = 36;
  const availW = stage.clientWidth - pad;
  const availH = stage.clientHeight - pad;
  const ratio = photo.naturalWidth / photo.naturalHeight;
  let w = availW;
  let h = w / ratio;
  if (h > availH) { h = availH; w = h * ratio; }
  frameBox = { w: Math.round(w), h: Math.round(h) };
  frame.style.width = frameBox.w + "px";
  frame.style.height = frameBox.h + "px";
  sizeGhost();
  drawPlaced();
}

/** Logo width in screen px — mirrors reference_size() on the server. */
function ghostWidthPx(scale) {
  return Math.sqrt(frameBox.w * frameBox.h) * scale;
}

function currentScale() {
  const m = S.marks.find((k) => k.logo === S.activeLogo);
  // pendingScale is a Shift+scroll made before this logo has been placed.
  return (m && m.scale) || S.pendingScale || S.globalScale;
}

function sizeGhost() {
  const w = ghostWidthPx(currentScale());
  ghost.style.width = w + "px";
  ghost.style.height = w * logoAspect(S.activeLogo) + "px";
}

const currentName = () => S.names[S.cursor];

/** Keep the logo fully inside the photo. */
function clampToFrame(x, y) {
  const halfW = ghostWidthPx(currentScale()) / 2 / frameBox.w;
  const halfH = (ghostWidthPx(currentScale()) * logoAspect(S.activeLogo)) / 2 / frameBox.h;
  return [
    Math.min(1 - halfW, Math.max(halfW, x)),
    Math.min(1 - halfH, Math.max(halfH, y)),
  ];
}

function moveGhost(x, y) {
  ghost.style.left = x * 100 + "%";
  ghost.style.top = y * 100 + "%";
}

/* Marks already placed on this photo are drawn as their own <img> elements;
   the ghost only ever represents the logo about to be placed. */
function drawPlaced() {
  const layer = $("placedLayer");
  layer.innerHTML = "";
  for (const m of S.marks) {
    const img = document.createElement("img");
    img.src = `/api/logo?i=${m.logo}`;
    img.className = "mark";
    const w = ghostWidthPx(m.scale || S.globalScale);
    const h = w * logoAspect(m.logo);
    img.style.width = w + "px";
    img.style.height = h + "px";
    // Mirror the renderer: a mark enlarged after placement stays on the photo.
    const halfW = w / 2 / frameBox.w;
    const halfH = h / 2 / frameBox.h;
    img.style.left = Math.min(1 - halfW, Math.max(halfW, m.x)) * 100 + "%";
    img.style.top = Math.min(1 - halfH, Math.max(halfH, m.y)) * 100 + "%";
    layer.appendChild(img);
  }
  const done = S.marks.length;
  const total = S.logos.length;
  $("placedMark").hidden = done === 0;
  $("placedMark").textContent = total > 1
    ? `✓ ${done} of ${total} logos placed`
    : "✓ placed — click again to move";
  ghost.classList.toggle("hidden", true);
  paintLogoBar();
}

frame.addEventListener("mousemove", (e) => {
  if (!S.ready || !frameBox.w) return;
  const r = frame.getBoundingClientRect();
  const [x, y] = clampToFrame((e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
  ghost.classList.remove("hidden", "set");
  moveGhost(x, y);
  $("placedMark").hidden = true;
});

frame.addEventListener("mouseleave", drawPlaced);

frame.addEventListener("click", (e) => {
  const r = frame.getBoundingClientRect();
  const [x, y] = clampToFrame((e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
  place(x, y);
});

/* Shift + wheel resizes this photo only. */
frame.addEventListener("wheel", (e) => {
  if (!e.shiftKey) return;
  e.preventDefault();
  const name = currentName();
  const step = Math.min(0.6, Math.max(0.02, currentScale() * (e.deltaY < 0 ? 1.06 : 0.94)));
  const mark = S.marks.find((m) => m.logo === S.activeLogo);
  if (mark) {
    mark.scale = step;
    commit(name);
  } else {
    S.pendingScale = step;   // applied when this logo is placed
  }
  sizeGhost();
  drawPlaced();
}, { passive: false });

/* ============================ actions ============================ */

/** Place the active logo, then move on: to the next unplaced logo on this
    photo if there is one, otherwise to the next photo. */
async function place(x, y) {
  const name = currentName();
  const existing = S.marks.find((m) => m.logo === S.activeLogo);
  const scale = (existing && existing.scale) || S.pendingScale || null;
  S.pendingScale = null;
  S.marks = S.marks.filter((m) => m.logo !== S.activeLogo);
  S.marks.push({ logo: S.activeLogo, x, y, ...(scale ? { scale } : {}) });
  S.positions[name] = { marks: S.marks };
  S.lastMarks = S.marks.map((m) => ({ ...m }));
  commit(name);

  const pending = nextUnplacedLogo();
  if (pending >= 0) {
    setActiveLogo(pending);
    drawPlaced();
  } else {
    next();
  }
}

function commit(name) {
  api("/api/place", { name, marks: S.marks }).then(applyStats);
}

function skip() {
  const name = currentName();
  S.marks = [];
  S.positions[name] = { skip: true };
  api("/api/skip", { name }).then(applyStats);
  next();
}

/** Space: reuse every mark from the previous photo, all logos at once. */
function repeatLast() {
  if (!S.lastMarks || !S.lastMarks.length) return;
  const name = currentName();
  S.marks = S.lastMarks.map((m) => {
    const saved = S.activeLogo;
    S.activeLogo = m.logo;
    const [x, y] = clampToFrame(m.x, m.y);
    S.activeLogo = saved;
    return { ...m, x, y };
  });
  S.positions[name] = { marks: S.marks };
  commit(name);
  next();
}

/** Remove the active logo's mark from this photo. */
function clearActive() {
  const name = currentName();
  const before = S.marks.length;
  S.marks = S.marks.filter((m) => m.logo !== S.activeLogo);
  if (S.marks.length === before) return;
  if (S.marks.length) S.positions[name] = { marks: S.marks };
  else delete S.positions[name];
  commit(name);
  drawPlaced();
}

/** Nine fixed spots, arranged like a numpad: 7 8 9 top, 4 5 6 middle, 1 2 3 bottom. */
function preset(key) {
  const grid = { 7: [0, 0], 8: [1, 0], 9: [2, 0], 4: [0, 1], 5: [1, 1], 6: [2, 1], 1: [0, 2], 2: [1, 2], 3: [2, 2] };
  const [cx, cy] = grid[key];
  const margin = 0.035;
  const halfW = ghostWidthPx(currentScale()) / 2 / frameBox.w;
  const halfH = (ghostWidthPx(currentScale()) * logoAspect(S.activeLogo)) / 2 / frameBox.h;
  const xs = [margin + halfW, 0.5, 1 - margin - halfW];
  const ys = [margin + halfH, 0.5, 1 - margin - halfH];
  place(xs[cx], ys[cy]);
}

function next() {
  if (S.cursor >= S.names.length - 1) return finish();
  go(S.cursor + 1);
}

/** Load the marks belonging to whatever photo is now on screen. */
function syncMarks() {
  S.pendingScale = null;
  const pos = S.positions[currentName()];
  S.marks = pos && pos.marks ? pos.marks.map((m) => ({ ...m })) : [];
  const pending = nextUnplacedLogo();
  setActiveLogo(pending >= 0 ? pending : 0);
}

function go(i) {
  S.cursor = Math.min(S.names.length - 1, Math.max(0, i));
  api("/api/cursor", { cursor: S.cursor });
  render();
}

function setGlobalScale(v) {
  S.globalScale = Math.min(0.6, Math.max(0.02, v));
  $("scale").value = (S.globalScale * 100).toFixed(1);
  $("scaleOut").textContent = (S.globalScale * 100).toFixed(1) + "%";
  sizeGhost();
  drawPlaced();
  clearTimeout(setGlobalScale.t);
  setGlobalScale.t = setTimeout(() => {
    api("/api/settings", { global_scale: S.globalScale, rerender: true }).then(applyStats);
  }, 400);
}

$("scale").addEventListener("input", (e) => setGlobalScale(Number(e.target.value) / 100));

/* Opacity applies to every logo. The preview mirrors it live via CSS so the
   user sees the real result before anything is written. */
function applyOpacity() {
  $("stage").style.setProperty("--logo-opacity", S.opacity);
}

function setOpacity(v) {
  S.opacity = Math.min(1, Math.max(0.05, v));
  $("opacity").value = Math.round(S.opacity * 100);
  $("opacityOut").textContent = Math.round(S.opacity * 100) + "%";
  applyOpacity();
  clearTimeout(setOpacity.t);
  setOpacity.t = setTimeout(() => {
    api("/api/settings", { opacity: S.opacity, rerender: true }).then(applyStats);
  }, 400);
}

$("opacity").addEventListener("input", (e) => setOpacity(Number(e.target.value) / 100));

/* Controls must not keep focus: otherwise number keys and arrows would go to
   the slider or the last button pressed instead of placing the logo. */
for (const el of [$("scale"), $("opacity"), $("help"), $("finish")]) {
  el.addEventListener("pointerup", () => setTimeout(() => el.blur(), 0));
  el.addEventListener("keyup", (e) => { if (e.key === "Enter") el.blur(); });
}

/* ============================= render ============================= */

function render() {
  const name = currentName();
  syncMarks();
  $("idx").textContent = S.cursor + 1;
  $("filename").textContent = name;
  $("filename").title = name;
  $("progressFill").style.width = ((S.cursor + 1) / S.names.length * 100) + "%";

  $("loading").hidden = false;
  photo.onload = () => { $("loading").hidden = true; fitFrame(); };
  photo.src = "/api/preview?name=" + encodeURIComponent(name);
  prefetch();
}

/* Keep the next few previews warm so advancing feels instant. */
function prefetch() {
  for (let i = 1; i <= 5; i++) {
    const n = S.names[S.cursor + i];
    if (n) new Image().src = "/api/preview?name=" + encodeURIComponent(n);
  }
}

function applyStats(res) {
  const s = (res && res.stats) || res;
  if (!s || s.rendered === undefined) return;
  $("doneCount").textContent = s.rendered;
  const busy = s.rendering > 0;
  $("busyDot").hidden = !busy;
  $("busyCount").hidden = !busy;
  $("busyCount").textContent = busy ? `${s.rendering} saving…` : "";
}

setInterval(() => { if (S.ready) api("/api/progress").then(applyStats); }, 1500);
window.addEventListener("resize", fitFrame);

/* ========================== keyboard ========================== */

document.addEventListener("keydown", (e) => {
  if ($("helpModal").hidden === false) {
    if (e.key === "Escape" || e.key === "Enter") closeHelp();
    return;
  }
  const typing = e.target.tagName === "INPUT" && e.target.type === "text";
  if ($("work").hidden || typing) return;
  if (e.target.tagName === "INPUT") e.target.blur();

  if (e.key >= "1" && e.key <= "9") { e.preventDefault(); return preset(Number(e.key)); }
  switch (e.key) {
    case "Tab": e.preventDefault(); return setActiveLogo(S.activeLogo + 1);
    case "Delete": e.preventDefault(); return clearActive();
    case " ": e.preventDefault(); return repeatLast();
    case "s": case "S": return skip();
    case "Backspace": e.preventDefault(); return go(S.cursor - 1);
    case "ArrowLeft": e.preventDefault(); return go(S.cursor - 1);
    case "ArrowRight": e.preventDefault(); return go(S.cursor + 1);
    case "+": case "=": return setGlobalScale(S.globalScale * 1.08);
    case "-": case "_": return setGlobalScale(S.globalScale * 0.93);
    case "?": return openHelp();
    case "Enter": return finish();
  }
});

/* ============================ finish ============================ */

async function finish() {
  await api("/api/render");
  const s = await api("/api/progress");
  const placed = s.placed || 0;
  $("doneSummary").textContent =
    `${placed} photo${placed === 1 ? "" : "s"} stamped` +
    (s.skipped ? `, ${s.skipped} skipped` : "") +
    (s.remaining ? `, ${s.remaining} not done yet` : "") + ".";
  $("donePath").textContent = S.outDir;
  const failed = Object.keys(s.failed || {});
  $("doneFailed").hidden = !failed.length;
  if (failed.length) {
    $("doneFailed").textContent =
      `${failed.length} photo(s) could not be saved: ${failed.slice(0, 5).join(", ")}` +
      (failed.length > 5 ? "…" : "");
  }
  show("done");
  pollDone();
}

function pollDone() {
  const tick = setInterval(async () => {
    if ($("done").hidden) return clearInterval(tick);
    const s = await api("/api/progress");
    $("doneBusy").hidden = !s.rendering;
    if (!s.rendering) {
      clearInterval(tick);
      const placed = s.placed || 0;
      $("doneSummary").textContent =
        `${s.rendered} of ${placed} photo${placed === 1 ? "" : "s"} saved` +
        (s.skipped ? `, ${s.skipped} skipped` : "") + ".";
    }
  }, 1200);
}

$("finish").addEventListener("click", finish);
$("backToWork").addEventListener("click", () => { show("work"); fitFrame(); });
$("quit").addEventListener("click", async () => {
  await api("/api/quit", {});
  document.body.innerHTML =
    '<div class="screen" style="align-items:center;justify-content:center">' +
    '<div class="card" style="text-align:center"><h1>Closed</h1>' +
    '<p class="lede">Everything is saved. You can close this tab.</p></div></div>';
});

const openHelp = () => { $("helpModal").hidden = false; };
const closeHelp = () => { $("helpModal").hidden = true; };
$("help").addEventListener("click", openHelp);
$("helpClose").addEventListener("click", closeHelp);
$("helpModal").addEventListener("click", (e) => { if (e.target === $("helpModal")) closeHelp(); });

/* Surface errors instead of letting them break a feature quietly. */
function reportCrash(message) {
  const box = $("crash");
  $("crashText").textContent = message;
  box.hidden = false;
}

window.addEventListener("error", (e) => reportCrash(e.message || String(e.error)));
window.addEventListener("unhandledrejection", (e) =>
  reportCrash((e.reason && e.reason.message) || String(e.reason)));
$("crashClose").addEventListener("click", () => { $("crash").hidden = true; });
$("resumedClose").addEventListener("click", () => {
  $("resumed").hidden = true;
  fitFrame();
});

/* Resume automatically if a session is already open (e.g. after a refresh). */
loadState().then((open) => { if (open) show("work"); });
