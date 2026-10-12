/* Process invoices: pick or drop files, upload them (with progress), then follow the reading job until it is done.
   The server reads and codes in the background (job "process"); this polls /process/status and draws each invoice
   as it is done, then reloads the page (the job card stays, with the summary). */

import { HEADERS, escapeHtml, follow, messageElement, pollJob, toast } from "./app.js";

const form = document.getElementById("upload-form");
const input = document.getElementById("upload-files");
const zone = document.getElementById("dropzone");
const picked = document.getElementById("picked");
const go = document.getElementById("upload-go");
const label = document.getElementById("upload-label");
const card = document.getElementById("job");

let files = [];

function kb(size) {
  return `${Math.max(1, Math.round(size / 1024)).toLocaleString()} KB`;
}

function showPicked() {
  picked.hidden = files.length === 0;
  picked.innerHTML = files.map((f) => `<li><span>${escapeHtml(f.name)}</span><span>${kb(f.size)}</span></li>`).join("");
  const n = files.length;
  label.textContent = n ? `Process ${n} uploaded invoice${n === 1 ? "" : "s"}` : "Process uploaded invoices";
  go.disabled = !n || go.dataset.ready !== "yes";
}

function take(list) {
  const accept = (input.accept || "").split(",").map((e) => e.trim().toLowerCase()).filter(Boolean);
  for (const f of list) {
    const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
    if (accept.length && !accept.includes(ext)) {
      toast({ text: `${f.name} was not added: AP Coder reads PDFs, scans, photos and saved emails (.eml).`, tone: "warn" });
      continue;
    }
    if (!files.some((g) => g.name === f.name && g.size === f.size)) files.push(f);
  }
  showPicked();
}

/* ---- The job card ---- */

function drawJob(snap) {
  if (!card) return;
  card.classList.remove("hidden");
  card.dataset.state = snap.state;
  const title = card.querySelector("#job-title");
  const bar = card.querySelector("#job-bar");
  const note = card.querySelector("#job-note");
  const lines = card.querySelector("#job-lines");
  const actions = card.querySelector("#job-actions");
  const running = snap.state === "running";
  title.textContent = running
    ? snap.total ? `Processing ${snap.total} invoice${snap.total === 1 ? "" : "s"}…` : "Processing…"
    : snap.state === "error" ? "Processing stopped" : (snap.result && snap.result.label) || "Processed";
  const pct = snap.total ? Math.round((100 * snap.done) / snap.total) : running ? 5 : 100;
  bar.style.width = `${pct}%`;
  bar.parentElement.setAttribute("aria-valuenow", String(snap.done));
  bar.parentElement.setAttribute("aria-valuemax", String(snap.total));
  note.textContent = running ? snap.note || "" : snap.error || "";
  lines.innerHTML = (snap.lines || [])
    .map((l) => `<li class="${escapeHtml(l.tone)}"><span class="ms" aria-hidden="true">${escapeHtml(l.icon || "chevron_right")}</span><span>${escapeHtml(l.text)}</span></li>`)
    .join("");
  lines.scrollTop = lines.scrollHeight;
  actions.hidden = running;
  const box = card.querySelector("#job-messages");
  box.innerHTML = "";
  for (const m of (snap.result && snap.result.messages) || []) box.appendChild(messageElement(m));
}

async function follow_job(statusUrl) {
  card?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  await pollJob(statusUrl, drawJob);
  location.reload();
}

/* ---- Uploading ---- */

function upload() {
  const body = new FormData();
  for (const f of files) body.append("files", f, f.name);
  go.disabled = true;
  go.classList.add("busy");
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", form.action);
    for (const [k, v] of Object.entries(HEADERS)) xhr.setRequestHeader(k, v);
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) label.textContent = `Uploading… ${Math.round((100 * e.loaded) / e.total)}%`;
    };
    xhr.onload = () => resolve(xhr.response || { ok: false, error: `That didn't work (${xhr.status}).` });
    xhr.onerror = () => resolve({ ok: false, error: "AP Coder didn't answer. Is it still running?" });
    xhr.send(body);
  });
}

if (form) {
  input.addEventListener("change", () => {
    take(input.files);
    input.value = "";
  });
  ["dragenter", "dragover"].forEach((t) =>
    zone.addEventListener(t, (e) => {
      e.preventDefault();
      zone.classList.add("over");
    }),
  );
  ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, () => zone.classList.remove("over")));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    take(e.dataTransfer.files);
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!files.length) return;
    const answer = await upload();
    go.classList.remove("busy");
    if (answer.job && answer.status) {
      files = [];
      showPicked();
      await follow_job(answer.status);
      return;
    }
    if (!follow(answer)) showPicked();
  });
}

// The folder's "Process N files" (app.js sends it and tells us a job started), or a job already running.
document.addEventListener("apc:job", (e) => follow_job(e.detail.status));
if (card && card.dataset.state === "running") follow_job(card.dataset.statusUrl);
