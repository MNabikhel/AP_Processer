/* Process invoices: pick or drop files, upload them (with progress), then follow the reading job until it is done.
   The server reads and codes the import queue in the background (D-168); this polls /process/status (the queue's
   latest run) and draws each invoice as it is done, then reloads the page (the job card stays, with the summary). */

import { HEADERS, LOOK_MS, escapeHtml, follow, messageElement, noAnswer, pollJob, sessionEnded, toast } from "./app.js";

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

/* Several files that are one voucher (D-216): with two files or more chosen, the person may say they are the pages of
   one invoice or the receipts of one claim; they are then joined in the order listed, which the arrows change. */
const voucher = document.getElementById("upload-voucher");
const voucherRow = document.getElementById("voucher-row");

function arrows(name, i) {
  const one = (by, word, icon) =>
    `<button type="button" class="btn sm ghost" data-pick="${i}" data-by="${by}" aria-label="Move ${escapeHtml(name)} ${word}"><span class="ms" aria-hidden="true">${icon}</span></button>`;
  return one(-1, "up", "arrow_upward") + one(1, "down", "arrow_downward");
}

function showPicked() {
  const n = files.length;
  if (voucherRow) {
    voucherRow.hidden = n < 2;
    if (n < 2) voucher.value = "";
    document.getElementById("voucher-hint").hidden = !voucher.value;
  }
  const one = voucher && voucher.value;
  picked.hidden = n === 0;
  picked.innerHTML = files
    .map((f, i) => `<li><span>${escapeHtml(f.name)}</span><span>${one ? arrows(f.name, i) : ""}${kb(f.size)}</span></li>`)
    .join("");
  label.textContent = one
    ? `Process ${n} files as one voucher`
    : n ? `Process ${n} uploaded invoice${n === 1 ? "" : "s"}` : "Process uploaded invoices";
  go.disabled = !n || go.dataset.ready !== "yes";
}

function take(list) {
  const accept = (input.accept || "").split(",").map((e) => e.trim().toLowerCase()).filter(Boolean);
  for (const f of list) {
    const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
    if (accept.length && !accept.includes(ext)) {
      toast({ text: `${f.name} was not added: AP Coder reads PDFs, scans, photos, saved emails (.eml, .msg) and invoices saved as text (.txt).`, tone: "warn" });
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
  if (running) card.querySelector("#job-when").textContent = ""; // (when the run before ended: not this one's)
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
  // The import queue's run: every file in line, a colleague's too (D-168), then the page is drawn again (not when the
  // session ended: the page says so and stays, D-185).
  if ((await pollJob(statusUrl, drawJob)) !== null) location.reload();
}

/* ---- Uploading ---- */

function upload() {
  const body = new FormData();
  for (const f of files) body.append("files", f, f.name);
  if (voucher && voucher.value && files.length > 1) body.append("voucher", voucher.value);
  go.disabled = true;
  go.classList.add("busy");
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", form.action);
    for (const [k, v] of Object.entries(HEADERS)) xhr.setRequestHeader(k, v);
    xhr.responseType = "json";
    // A deadline, as every request has (D-161): LOOK_MS without the files moving on, or without the answer once they
    // are all sent (the server answers within a few seconds, D-172), is no answer. However long the upload takes while
    // it moves, it goes on.
    let quiet = 0;
    const still = () => {
      clearTimeout(quiet);
      quiet = setTimeout(() => xhr.abort(), LOOK_MS);
    };
    xhr.upload.onprogress = (e) => {
      still();
      if (e.lengthComputable) label.textContent = `Uploading… ${Math.round((100 * e.loaded) / e.total)}%`;
    };
    xhr.upload.onload = still;
    xhr.onloadend = () => clearTimeout(quiet);
    xhr.onload = () => {
      if (xhr.status === 401 && xhr.response?.session_ended) {
        sessionEnded(); // the files stay picked: sent again once logged in (D-185)
        return resolve({ ok: false });
      }
      resolve(xhr.response || { ok: false, error: `That didn't work (${xhr.status}).` });
    };
    xhr.onerror = xhr.onabort = () => resolve({ ok: false, error: noAnswer() });
    still();
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
  voucher?.addEventListener("change", showPicked);
  picked.addEventListener("click", (e) => {
    const button = e.target.closest("[data-pick]");
    if (!button) return;
    const from = Number(button.dataset.pick);
    const to = from + Number(button.dataset.by);
    if (to < 0 || to >= files.length) return;
    [files[from], files[to]] = [files[to], files[from]];
    showPicked();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!files.length) return;
    const answer = await upload();
    go.classList.remove("busy");
    if (answer.job && answer.status) {
      if (answer.toast) toast(answer.toast); // queued behind the import in progress: it says so
      files = [];
      showPicked();
      await follow_job(answer.status);
      return;
    }
    if (!follow(answer)) showPicked();
  });
}

// The invoices folder's list: the files ticked as one voucher are sent in the order shown (D-216); an arrow moves a
// row, and the button opens once two are ticked.
const listed = document.getElementById("folder-files");
const voucherGo = document.getElementById("voucher-go");
if (listed && voucherGo) {
  listed.addEventListener("click", (e) => {
    const button = e.target.closest("[data-move]");
    const row = button?.closest("tr");
    if (!row) return;
    if (button.dataset.move === "-1") row.previousElementSibling?.before(row);
    else row.nextElementSibling?.after(row);
    button.focus();
  });
  listed.addEventListener("change", () => {
    voucherGo.disabled = listed.querySelectorAll("input[name=names]:checked").length < 2 || voucherGo.dataset.ready !== "yes";
  });
}

// The folder's "Process N files" (app.js sends it and tells us a job started), or a job already running.
document.addEventListener("apc:job", (e) => follow_job(e.detail.status));
if (card && card.dataset.state === "running") follow_job(card.dataset.statusUrl);
