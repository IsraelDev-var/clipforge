"use strict";

const STAGE_LABELS = {
  ingest: "Descarga", audio: "Audio", transcribe: "Transcripción", select: "Selección IA", silences: "Silencios", virality: "Viralidad",
  reframe: "Encuadre", captions: "Subtítulos", render: "Render", metadata: "Metadatos",
};
const STATE_LABELS = { new: "nuevo", queued: "en cola", running: "procesando", done: "listo", error: "error" };
const LLM_LABELS = { hook: "Gancho", standalone: "Se entiende solo", value: "Valor", emotion: "Emoción", shareability: "Compartible" };
const SIGNAL_LABELS = { hook_delay: "Arranque", pace: "Ritmo", dead_air: "Silencios", ending: "Cierre", duration: "Duración", energy: "Energía" };
const LAYOUT_LABELS = { auto: "Automático", face: "Seguir la cara", split: "Dividido: cámara + pantalla", screen: "Solo pantalla" };
const LAYOUT_SHORT = { face: "👤 cara", center: "▣ centrado", split: "🖥️ dividido", screen: "🖥️ pantalla" };
const app = document.getElementById("app");
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};
let pollTimer = null;
let config = null;

// ---------- utilidades ----------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers = { "Content-Type": "application/json" };
  }
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorText(data) || `Error ${res.status}`);
  return data;
}

function errorText(data) {
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg).join("; ");
  return "";
}

function toast(msg, isErr = false) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "show" + (isErr ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = ""), 3500);
}

function fmt(t) {
  if (t == null) return "—";
  const h_ = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = (t % 60).toFixed(1).padStart(4, "0");
  return h_ ? `${h_}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

function badge(status) {
  return h("span", { class: `badge ${status.state}` }, STATE_LABELS[status.state] || status.state);
}

function stopPolling() {
  clearTimeout(pollTimer);
  pollTimer = null;
}

// ---------- inicio ----------

async function renderHome() {
  config = config || (await api("/api/config"));
  let mode = "url";
  let file = null;

  const urlInput = h("input", { type: "url", placeholder: "https://www.youtube.com/watch?v=…", required: true });
  const fileInput = h("input", { type: "file", accept: "video/mp4,video/x-matroska,video/quicktime,video/webm,.mkv,.mov", hidden: true });
  const dropText = h("div", {}, "Arrastra aquí tu video o haz clic para elegirlo", h("div", { class: "small" }, ".mp4 · .mkv · .mov · .webm"));
  const drop = h("div", { class: "drop", hidden: true, onclick: () => fileInput.click() }, dropText);
  const setFile = (f) => {
    file = f;
    dropText.replaceChildren(f ? `${f.name} (${(f.size / 1e6).toFixed(0)} MB)` : "Arrastra aquí tu video o haz clic para elegirlo");
  };
  fileInput.onchange = () => setFile(fileInput.files[0] || null);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); setFile(e.dataTransfer.files[0] || null); };

  const tabUrl = h("button", { type: "button", class: "on" }, "Enlace de YouTube");
  const tabFile = h("button", { type: "button" }, "Subir archivo");
  const setMode = (m) => {
    mode = m;
    tabUrl.classList.toggle("on", m === "url");
    tabFile.classList.toggle("on", m === "file");
    urlInput.hidden = m !== "url";
    urlInput.required = m === "url";
    drop.hidden = m !== "file";
  };
  tabUrl.onclick = () => setMode("url");
  tabFile.onclick = () => setMode("file");

  const nClips = h("input", { type: "number", min: 1, max: 30, value: config.clips });
  const minS = h("input", { type: "number", min: 5, max: 180, step: 1, value: config.min });
  const maxS = h("input", { type: "number", min: 5, max: 180, step: 1, value: config.max });
  const faces = h("input", { type: "checkbox", checked: true });
  const silences = h("input", { type: "checkbox", checked: config.silences !== false });
  const layout = h("select", {}, Object.entries(LAYOUT_LABELS).map(([v, l]) => h("option", { value: v }, l)));
  layout.value = config.layout || "auto";
  const bar = h("div");
  const progress = h("div", { class: "progress", hidden: true }, bar);
  const submit = h("button", { class: "primary", type: "submit" }, "Crear clips");

  const form = h("form", { class: "card stack" },
    h("div", { class: "tabs" }, tabUrl, tabFile),
    urlInput, drop, fileInput,
    h("div", { class: "row" },
      h("label", {}, "Clips", nClips),
      h("label", {}, "Mínimo (s)", minS),
      h("label", {}, "Máximo (s)", maxS),
      h("label", {}, "Diseño", layout),
      h("label", { class: "check" }, faces, "Seguir la cara"),
      h("label", { class: "check" }, silences, "Quitar silencios"),
    ),
    progress,
    h("div", {}, submit),
  );

  form.onsubmit = async (e) => {
    e.preventDefault();
    if (mode === "file" && !file) return toast("Elige un archivo de video.", true);
    const fd = new FormData();
    fd.append("clips", nClips.value);
    fd.append("min_s", minS.value);
    fd.append("max_s", maxS.value);
    fd.append("faces", faces.checked);
    fd.append("silences", silences.checked);
    fd.append("layout", layout.value);
    if (mode === "url") fd.append("url", urlInput.value.trim());
    else fd.append("file", file);
    submit.disabled = true;
    progress.hidden = mode !== "file";
    try {
      const { id } = await upload(fd, (p) => (bar.style.width = `${p * 100}%`));
      location.hash = `#/job/${encodeURIComponent(id)}`;
    } catch (err) {
      toast(err.message, true);
      submit.disabled = false;
      progress.hidden = true;
    }
  };

  const list = h("ul", { class: "jobs" });
  const listCard = h("section", { class: "card" }, h("h2", {}, "Trabajos"), list);

  app.replaceChildren(
    h("div", { class: "stack" },
      h("div", {}, h("h1", {}, "Nuevo trabajo"),
        h("p", { class: "muted" }, "Pega el enlace de un directo ya terminado o sube el archivo. ClipForge transcribe, elige los mejores momentos y exporta clips 9:16 con subtítulos.")),
      form, listCard),
  );

  const refresh = async () => {
    if (!location.hash.match(/^#?\/?$/)) return;
    const jobs = await api("/api/jobs").catch(() => []);
    list.replaceChildren(...(jobs.length ? jobs.map(jobRow) : [h("li", { class: "muted" }, "Aún no hay trabajos.")]));
    if (jobs.some((j) => j.busy)) pollTimer = setTimeout(refresh, 4000);
  };
  refresh();
}

function jobRow(j) {
  const src = j.title || (j.source && j.source.startsWith("http") ? j.source : (j.source || "").split(/[\\/]/).pop());
  return h("li", {}, h("a", { href: `#/job/${encodeURIComponent(j.id)}` },
    h("span", { class: "jid" }, j.id, h("div", { class: "small muted" }, src)),
    j.clips ? h("span", { class: "small muted" }, `${j.clips} clips`) : null,
    badge(j.status)));
}

function upload(fd, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch {}
      xhr.status < 300 ? resolve(data) : reject(new Error(errorText(data) || `Error ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("No se pudo conectar con el servidor."));
    xhr.send(fd);
  });
}

// ---------- trabajo ----------

async function renderJob(id) {
  let job;
  try {
    job = await api(`/api/jobs/${encodeURIComponent(id)}`);
  } catch (err) {
    app.replaceChildren(h("div", { class: "alert" }, err.message), h("p", {}, h("a", { href: "#/" }, "← Volver")));
    return;
  }
  if (location.hash !== `#/job/${encodeURIComponent(id)}`) return;
  const st = job.status;
  const active = job.busy || st.state === "running" || st.state === "queued";
  const base = `/api/jobs/${encodeURIComponent(job.id)}`;

  const source = job.title || job.source || "";
  const head = h("div", { class: "jobhead" },
    h("div", {},
      h("p", { class: "small" }, h("a", { href: "#/" }, "← Trabajos")),
      h("h1", {}, job.id),
      h("div", { class: "muted small" }, source,
        job.video ? ` · ${job.video.width}×${job.video.height} · ${fmt(job.video.duration)}` : "")),
    h("div", { class: "actions" },
      badge(st),
      job.clips.length > 1 ? sortSelect(job.id) : null,
      job.clips.some((c) => c.file) ? h("a", { class: "btn", href: `${base}/zip` }, "⬇ Descargar todo (.zip)") : null,
      !active && st.state !== "done" ? h("button", { class: "ghost", onclick: () => resume(job.id) }, "↻ Reanudar") : null,
      !active && job.clips.length ? h("button", { class: "ghost", onclick: () => reselect(job.id) }, "✦ Volver a elegir clips") : null,
      !active ? h("button", { class: "ghost danger", onclick: () => removeJob(job.id) }, "Eliminar") : null,
    ));

  const stages = config?.stages || Object.keys(STAGE_LABELS);
  const idx = stages.indexOf(st.stage);
  const steps = h("div", { class: "steps", style: `--n:${stages.length}` }, stages.map((s, i) => {
    let cls = "";
    if (st.state === "done") cls = "done";
    else if (idx >= 0 && i < idx) cls = "done";
    else if (i === idx) cls = st.state === "error" ? "error" : st.state === "running" ? "running" : "";
    return h("div", { class: `step ${cls}` }, STAGE_LABELS[s] || s);
  }));

  const info = [];
  if (st.state === "error") info.push(h("div", { class: "alert" }, st.message || "Error"));
  else if (active) info.push(h("p", { class: "muted small" },
    st.state === "queued" ? "En cola: empezará cuando termine el trabajo anterior." :
    st.message || `${STAGE_LABELS[st.stage] || "Procesando"}… (la transcripción de un directo largo puede tardar un rato)`));

  // Solo se reconstruyen las tarjetas si cambian los clips: así el sondeo no reinicia
  // los videos que estás viendo ni borra lo que estás escribiendo.
  const order = store.get("cf-order") || "time";
  const sorted = [...job.clips].sort((a, b) =>
    order === "virality" ? (b.virality?.score ?? 0) - (a.virality?.score ?? 0) : a.start - b.start);
  const sig = job.id + "|" + order + "|" + sorted.map((c) => `${c.n}:${c.version}:${c.start}:${c.end}:${c.virality?.score}`).join(",");
  let clips = app.querySelector(".clips");
  if (!clips || clips.dataset.sig !== sig) {
    clips = job.clips.length ? h("div", { class: "clips", "data-sig": sig }, sorted.map((c) => clipCard(job, c))) : null;
  }
  if (clips) clips.querySelectorAll("[data-lock]").forEach((b) => (b.disabled = active));

  app.replaceChildren(h("div", { class: "stack" }, head, h("section", { class: "card stack" }, steps, ...info), clips));

  stopPolling();
  if (active) pollTimer = setTimeout(() => renderJob(id), 2000);
}

function clipCard(job, c) {
  const base = `/api/jobs/${encodeURIComponent(job.id)}`;
  const media = c.file
    ? h("video", { src: `${base}/files/${c.file}?v=${c.version}`, controls: true, preload: "metadata", playsinline: true })
    : h("div", { class: "ph" }, "Renderizando…");

  const title = h("input", { type: "text", value: c.title || "", maxlength: 60, placeholder: "Título" });
  const desc = h("textarea", { rows: 3, placeholder: "Descripción" }, c.description || "");
  const tags = h("input", { type: "text", value: (c.hashtags || []).join(" "), placeholder: "#hashtags" });

  const save = async () => {
    try {
      await api(`${base}/clips/${c.n}`, { method: "PATCH", json: {
        title: title.value, description: desc.value, hashtags: tags.value.split(/[\s,]+/).filter(Boolean) } });
      toast("Texto guardado.");
    } catch (err) { toast(err.message, true); }
  };
  const copy = async () => {
    const txt = `${title.value}\n\n${desc.value}\n\n${tags.value}`;
    try { await navigator.clipboard.writeText(txt); toast("Copiado al portapapeles."); }
    catch { toast("No se pudo copiar.", true); }
  };

  return h("article", { class: "card clip" },
    media,
    h("div", { class: "fields" },
      h("div", { class: "clip-meta" },
        viralBadge(c.virality),
        h("span", {}, `#${c.n}`),
        h("span", {}, `${fmt(c.start)} → ${fmt(c.end)}`),
        h("span", {}, durationText(c)),
        c.layout_used ? h("span", { class: "chip", title: "Diseño del clip" }, LAYOUT_SHORT[c.layout_used] || c.layout_used) : null),
      title, desc, tags,
      c.hook ? h("div", { class: "small muted" }, h("b", {}, "Gancho: "), c.hook) : null,
      c.text ? h("div", { class: "text", title: "Transcripción del clip" }, c.text) : null),
    c.virality ? viralDetails(c.virality) : null,
    h("div", { class: "btns" },
      h("button", { class: "ghost", onclick: save, "data-lock": true }, "Guardar texto"),
      h("button", { class: "ghost", onclick: copy }, "Copiar texto"),
      job.has_source ? h("button", { class: "ghost", onclick: () => openAdjust(job, c), "data-lock": true }, "✂ Ajustar inicio/fin") : null,
      c.file ? h("a", { class: "btn", href: `${base}/files/${c.file}?download=1` }, "⬇ MP4") : null),
  );
}

function durationText(c) {
  const src = c.end - c.start, out = c.edited_duration ?? src;
  const cut = src - out;
  return cut >= 0.1 ? `${out.toFixed(1)} s (−${cut.toFixed(1)} s de silencios)` : `${src.toFixed(1)} s`;
}

function sortSelect(id) {
  const sel = h("select", { "aria-label": "Ordenar clips" },
    h("option", { value: "time" }, "Orden cronológico"),
    h("option", { value: "virality" }, "Más virales primero"));
  sel.value = store.get("cf-order") || "time";
  sel.onchange = () => { store.set("cf-order", sel.value); renderJob(id); };
  return sel;
}

function viralBadge(v) {
  if (!v) return h("span", { class: "viral none", title: "Sin puntuación de viralidad" }, "–");
  return h("span", { class: `viral ${v.grade}`, title: `Viralidad ${v.score}/100 (${v.grade})` }, "🔥 ", String(v.score));
}

function bar(label, frac, valueText) {
  const pct = Math.round(Math.max(0, Math.min(1, frac)) * 100);
  return h("div", { class: "bar" },
    h("span", {}, label),
    h("div", { class: "track" }, h("div", { class: pct >= 70 ? "hi" : pct >= 45 ? "mid" : "lo", style: `width:${pct}%` })),
    h("span", { class: "val" }, valueText));
}

function signalText(k, s) {
  switch (k) {
    case "hook_delay": return `${s.hook_delay.toFixed(1)} s`;
    case "pace": return `${s.pace.toFixed(1)} p/s`;
    case "dead_air": return `${Math.round(s.dead_air * 100)} %`;
    case "ending": return s.ends_sentence ? "sí" : "no";
    case "energy": return s.energy.toFixed(2);
    default: return "";
  }
}

function viralDetails(v) {
  const llm = v.llm
    ? Object.entries(LLM_LABELS).map(([k, l]) => bar(l, v.llm[k] / 10, `${v.llm[k]}/10`))
    : [h("p", { class: "small muted" }, "Sin criterios de la IA (trabajo anterior): se usa su puntuación general.")];
  const sig = Object.entries(SIGNAL_LABELS).map(([k, l]) =>
    bar(l, v.subscores[k], k === "duration" ? "" : signalText(k, v.signals)));
  return h("details", { class: "viral-details" },
    h("summary", {}, `Viralidad ${v.score}/100 · ${v.grade}`, v.tips.length ? ` · ${v.tips.length} consejo${v.tips.length > 1 ? "s" : ""}` : ""),
    h("div", { class: "small muted" }, `IA ${v.llm_score} × 70 % + señales ${v.signals_score} × 30 %`,
      v.signals.question_hook ? " + 3 por pregunta en el gancho" : ""),
    h("h4", {}, "Criterios de la IA"), ...llm,
    h("h4", {}, "Señales medidas"), ...sig,
    v.tips.length ? h("ul", { class: "tips" }, v.tips.map((t) => h("li", {}, t))) : h("p", { class: "small ok" }, "Sin puntos débiles claros."));
}

async function resume(id, fromStage = null) {
  try {
    await api(`/api/jobs/${encodeURIComponent(id)}/resume`, { method: "POST", json: { from_stage: fromStage } });
    renderJob(id);
  } catch (err) { toast(err.message, true); }
}

function reselect(id) {
  if (confirm("La IA volverá a elegir los clips y se perderán los ajustes manuales. ¿Continuar?")) resume(id, "select");
}

async function removeJob(id) {
  if (!confirm(`¿Eliminar el trabajo «${id}» y todos sus archivos?`)) return;
  try {
    await api(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
    location.hash = "#/";
  } catch (err) { toast(err.message, true); }
}

// ---------- diálogo de ajuste ----------

const dlg = document.getElementById("adjust");
const adjVideo = document.getElementById("adj-video");
const adjStart = document.getElementById("adj-start");
const adjEnd = document.getElementById("adj-end");
const adjInfo = document.getElementById("adj-info");
const adjCut = document.getElementById("adj-cut");
const adjLayout = document.getElementById("adj-layout");
adjLayout.append(...Object.entries(LAYOUT_LABELS).map(([v, l]) => h("option", { value: v }, l)));
let adjCtx = null;
let previewEnd = null;

function updateAdjInfo() {
  const s = +adjStart.value, e = +adjEnd.value;
  const { min, max } = adjCtx.job.options;
  const d = e - s;
  adjInfo.textContent = `${fmt(s)} → ${fmt(e)} · duración ${d.toFixed(1)} s` +
    (adjCut.checked ? " antes de quitar silencios" : "") +
    (d < min || d > max ? ` (fuera del rango configurado ${min}–${max} s)` : "");
}

function openAdjust(job, c) {
  adjCtx = { job, clip: c };
  document.getElementById("adj-title").textContent = `Ajustar clip #${c.n}`;
  adjLayout.value = c.layout || "auto";
  adjCut.checked = c.cut_silences ?? (c.segments ? true : job.options.silences ?? true);
  adjStart.value = c.start.toFixed(1);
  adjEnd.value = c.end.toFixed(1);
  adjVideo.src = `/api/jobs/${encodeURIComponent(job.id)}/files/source.mp4#t=${c.start}`;
  updateAdjInfo();
  dlg.showModal();
}

dlg.addEventListener("close", () => { adjVideo.pause(); adjVideo.removeAttribute("src"); adjVideo.load(); previewEnd = null; });
adjStart.oninput = adjEnd.oninput = adjCut.onchange = updateAdjInfo;
dlg.querySelectorAll(".nudge button").forEach((b) => (b.onclick = () => {
  const input = b.dataset.k === "start" ? adjStart : adjEnd;
  input.value = Math.max(0, +input.value + +b.dataset.d).toFixed(1);
  updateAdjInfo();
  adjVideo.currentTime = +input.value;
}));
document.getElementById("adj-set-start").onclick = () => { adjStart.value = adjVideo.currentTime.toFixed(1); updateAdjInfo(); };
document.getElementById("adj-set-end").onclick = () => { adjEnd.value = adjVideo.currentTime.toFixed(1); updateAdjInfo(); };
document.getElementById("adj-preview").onclick = () => {
  adjVideo.currentTime = +adjStart.value;
  previewEnd = +adjEnd.value;
  adjVideo.play();
};
adjVideo.addEventListener("timeupdate", () => {
  if (previewEnd != null && adjVideo.currentTime >= previewEnd) { adjVideo.pause(); previewEnd = null; }
});
document.getElementById("adj-save").onclick = async () => {
  const start = +adjStart.value, end = +adjEnd.value;
  if (!(end > start)) return toast("El final debe ser posterior al inicio.", true);
  const { job, clip } = adjCtx;
  try {
    await api(`/api/jobs/${encodeURIComponent(job.id)}/clips/${clip.n}/render`,
      { method: "POST", json: { start, end, cut_silences: adjCut.checked, layout: adjLayout.value } });
    dlg.close();
    toast(`Re-exportando clip #${clip.n}…`);
    renderJob(job.id);
  } catch (err) { toast(err.message, true); }
};

// ---------- rutas ----------

async function route() {
  stopPolling();
  const m = location.hash.match(/^#\/job\/(.+)$/);
  config = config || (await api("/api/config").catch(() => null));
  if (m) renderJob(decodeURIComponent(m[1]));
  else renderHome();
}
window.addEventListener("hashchange", route);
route();
