/* The invoice review screen (/review/<id>, pages/invoice.py).

   * Every edit is checked as the reviewer types: the form "inv" (every field, both grids) is posted to
     /review/<id>/check, which answers the sections that follow the edits ([data-live=...]) drawn again, and whether
     it may be approved. The server keeps the edits as a draft, so leaving and coming back keeps them.
   * The grids: add and delete rows here; split a line, the coding rules, the PO's coding and a suggested GL account
     are POSTs answered with the new rows ([data-grid-action]).
   * The page viewer (invoice_viewer.js): focusing a field of the form highlights its box on the page; "Teach a
     field" lets the reviewer click the words that hold it (sent with the approval, the supplier's template learns).
   * OvisOCR2: while it has this invoice in its queue, its progress is looked up again every few seconds.
   * Keys: Ctrl+Enter approves, Alt+←/→ previous / next invoice, Alt+↑ the queue.

   Everything from the server is either HTML it escaped (the sections, the rows) or set as text here.
*/

import { follow, post, toast } from "./app.js";
import { InvoiceViewer } from "./invoice_viewer.js";

const form = document.getElementById("inv");
const invoiceId = document.body.querySelector("[data-invoice]")?.dataset.invoice || location.pathname.split("/")[2];
const base = `/review/${invoiceId}`;
const approveButton = document.querySelector("[data-approve]");
let viewer = null;

/* ---------- The page viewer ---------- */

const viewerRoot = document.getElementById("iv");
const viewerData = document.getElementById("viewer-data");
if (viewerRoot && viewerData) {
  try {
    viewer = new InvoiceViewer(viewerRoot, onViewerEvent);
    viewer.update(JSON.parse(viewerData.textContent));
  } catch (err) {
    console.error("invoice viewer:", err); // eslint-disable-line no-console
    viewer = null;
  }
}

/* ---------- Checking the edits ---------- */

let timer = 0;
let running = null; // the check under way (a promise), with any rounds asked for meanwhile
let queued = false; // edits made while a check runs: check again after it

function input(name) {
  return form ? document.querySelector(`[form="inv"][name="${CSS.escape(name)}"]`) : null;
}

function formData(extra) {
  const body = new FormData(form);
  for (const [k, v] of Object.entries(extra || {})) {
    if (Array.isArray(v)) v.forEach((x) => body.append(k, x));
    else body.append(k, v);
  }
  return body;
}

function applySections(answer) {
  for (const [name, html] of Object.entries(answer.sections || {})) {
    const el = document.querySelector(`[data-live="${name}"]`);
    if (!el) continue;
    const open = [...el.querySelectorAll("details")].map((d) => d.open); // what the reviewer opened stays open
    el.innerHTML = html; // drawn by the server, every text escaped
    el.querySelectorAll("details").forEach((d, i) => {
      if (i < open.length) d.open = open[i];
    });
  }
  if (approveButton) approveButton.disabled = !answer.allow;
  showMailLanguage();
}

async function check() {
  clearTimeout(timer);
  timer = 0;
  if (!form) return;
  if (running) {
    queued = true;
    return running;
  }
  running = (async () => {
    do {
      queued = false;
      const answer = await post(`${base}/check`, formData());
      if (answer.ok) applySections(answer);
      else if (answer.error) toast({ text: answer.error, tone: "err" }, 9000);
    } while (queued);
  })();
  try {
    await running;
  } finally {
    running = null;
  }
}

function soon(ms = 450) {
  clearTimeout(timer);
  timer = setTimeout(check, ms);
}

/** Wait until the edits are checked and kept (before leaving the page or another action). */
async function flush() {
  if (timer) await check();
  else if (running) await running;
}

if (form) {
  document.addEventListener("input", (e) => {
    if (e.target.form === form && e.target.type !== "checkbox" && e.target.type !== "radio") soon();
  });
  document.addEventListener("change", (e) => {
    if (e.target.form !== form) return;
    if (e.target.name === "mail_lang") return showMailLanguage();
    soon(e.target.tagName === "SELECT" || e.target.type === "checkbox" ? 0 : 200);
  });
}

/* Another form or a link while edits wait to be checked: check (and keep) them first. */
let passing = null;
document.addEventListener(
  "submit",
  (e) => {
    const f = e.target;
    if (!form || f === form || passing === f || !(timer || running)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    flush().then(() => {
      passing = f;
      f.requestSubmit(e.submitter || undefined);
      passing = null;
    });
  },
  true,
);
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  if (!a || !form || !(timer || running) || a.target || e.ctrlKey || e.metaKey || e.shiftKey) return;
  if (!a.getAttribute("href").startsWith("/")) return;
  e.preventDefault();
  flush().then(() => location.assign(a.href));
});

/* ---------- The grids ---------- */

let newRows = 0;

document.addEventListener("click", (e) => {
  const add = e.target.closest("[data-add-row]");
  if (add) {
    const which = add.dataset.addRow;
    const tpl = document.getElementById(which === "lines" ? "line-template" : "tax-template");
    const body = document.querySelector(`[data-grid="${which}"]`);
    if (!tpl || !body) return;
    const html = tpl.innerHTML.replaceAll("__KEY__", `n${Date.now().toString(36)}${newRows++}`);
    body.insertAdjacentHTML("beforeend", html);
    body.lastElementChild?.querySelector("input:not([type=hidden]), select")?.focus();
    if (which === "lines") fillSplitLines();
    soon(0);
    return;
  }
  const del = e.target.closest("[data-del-row]");
  if (del) {
    del.closest("tr")?.remove();
    fillSplitLines();
    soon(0);
    return;
  }
  const action = e.target.closest("[data-grid-action]");
  if (action) gridAction(action.dataset.gridAction, { position: action.dataset.position, gl: action.dataset.gl, cc: action.dataset.cc }, action);
  const copy = e.target.closest("[data-copy]");
  if (copy) {
    const text = copy.parentElement.querySelector("pre")?.textContent || "";
    navigator.clipboard?.writeText(text).then(
      () => toast({ text: "Copied.", tone: "ok" }, 2500),
      () => toast({ text: "Select the text and copy it.", tone: "info" }),
    );
  }
});

function setLines(html) {
  const body = document.querySelector('[data-grid="lines"]');
  if (body) body.innerHTML = html; // rows drawn by the server, every text escaped
  fillSplitLines();
}

async function gridAction(url, extra, button) {
  await flush();
  const data = {};
  for (const [k, v] of Object.entries(extra || {})) if (v !== undefined) data[k] = v;
  if (button) button.disabled = true;
  const answer = await post(url, formData(data));
  if (button) button.disabled = false;
  if (answer.ok && typeof answer.lines === "string") {
    setLines(answer.lines);
    if (answer.toast) toast(answer.toast);
    await check();
  } else follow(answer);
}

/* ---------- Split a line ---------- */

const split = document.querySelector("[data-split]");
const splitLine = split?.querySelector("[data-split-line]");
const parts = split?.querySelector("[data-parts]");
const partTemplate = document.getElementById("part-template");

function lineRows() {
  return [...document.querySelectorAll('[data-grid="lines"] tr')].map((tr) => ({
    number: tr.querySelector('[name="line_number"]')?.value || "",
    description: tr.querySelector('[name="line_description"]')?.value || "",
    gl: tr.querySelector('[name="line_gl"]')?.value || "",
    cc: tr.querySelector('[name="line_cc"]')?.value || "",
  }));
}

function fillSplitLines() {
  if (!splitLine) return;
  const before = splitLine.value;
  const seen = new Set();
  splitLine.replaceChildren();
  for (const row of lineRows()) {
    if (!row.number || seen.has(row.number)) continue;
    seen.add(row.number);
    const option = document.createElement("option");
    option.value = row.number;
    option.textContent = `${row.number} · ${row.description.slice(0, 50)}`;
    splitLine.append(option);
  }
  if ([...splitLine.options].some((o) => o.value === before)) splitLine.value = before;
}

function addPart(gl, cc, pct) {
  if (!partTemplate || !parts) return;
  parts.append(partTemplate.content.cloneNode(true));
  const tr = parts.lastElementChild;
  const glSelect = tr.querySelector('[name="part_gl"]');
  const ccSelect = tr.querySelector('[name="part_cc"]');
  if (gl && [...glSelect.options].some((o) => o.value === gl)) glSelect.value = gl;
  if (ccSelect && cc && [...ccSelect.options].some((o) => o.value === cc)) ccSelect.value = cc;
  tr.querySelector('[name="part_pct"]').value = pct;
}

function resetParts() {
  if (!parts) return;
  parts.replaceChildren();
  const row = lineRows().find((r) => r.number === splitLine.value) || {};
  addPart(row.gl, row.cc, 50);
  addPart(row.gl, row.cc, 50);
  splitTotal();
}

function splitTotal() {
  const total = [...parts.querySelectorAll('[name="part_pct"]')].reduce((s, i) => s + (Number(i.value) || 0), 0);
  const label = split.querySelector("[data-split-total]");
  label.textContent = `Total ${Math.round(total * 100) / 100}% (must be 100%)`;
  split.querySelector("[data-split-go]").disabled = Math.abs(total - 100) > 0.01;
}

if (split) {
  split.addEventListener("toggle", () => {
    if (!split.open) return;
    fillSplitLines();
    resetParts();
  });
  splitLine.addEventListener("change", resetParts);
  split.addEventListener("input", (e) => {
    if (e.target.name === "part_pct") splitTotal();
  });
  split.addEventListener("click", (e) => {
    if (e.target.closest("[data-add-part]")) {
      addPart(undefined, undefined, 0);
      splitTotal();
    } else if (e.target.closest("[data-del-part]")) {
      e.target.closest("tr").remove();
      splitTotal();
    } else if (e.target.closest("[data-split-go]")) {
      const rows = [...parts.querySelectorAll("tr")];
      gridAction(
        `${base}/split`,
        {
          split_line: splitLine.value,
          part_gl: rows.map((r) => r.querySelector('[name="part_gl"]').value),
          part_cc: rows.map((r) => r.querySelector('[name="part_cc"]')?.value || ""),
          part_pct: rows.map((r) => r.querySelector('[name="part_pct"]').value),
        },
        e.target.closest("[data-split-go]"),
      ).then(() => split.removeAttribute("open"));
    }
  });
}

/* ---------- The vendor email's language ---------- */

function showMailLanguage() {
  const chosen = document.querySelector('[name="mail_lang"]:checked')?.value;
  document.querySelectorAll(".inv-mail-draft").forEach((d) => (d.hidden = d.dataset.lang !== chosen));
}

/* ---------- Fields and their boxes on the page ---------- */

document.addEventListener("focusin", (e) => {
  const label = e.target.closest?.("[data-field]");
  if (label && viewer) viewer.show(label.dataset.field);
});

/** A field clicked on the page: its input in the form is brought into view and marked for a moment. */
function showField(name) {
  const label = document.querySelector(`.inv-details [data-field="${CSS.escape(name)}"]`);
  if (!label) return;
  label.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  label.classList.remove("inv-flash");
  void label.offsetWidth; // restart the animation
  label.classList.add("inv-flash");
}

let words = null;

async function loadWords() {
  if (words) return words;
  const response = await fetch(`${base}/words`, { headers: { Accept: "application/json" } });
  const answer = await response.json().catch(() => ({ ok: false, error: "The words on the page can't be read." }));
  if (!answer.ok) throw new Error(answer.error);
  words = answer.words;
  return words;
}

const teach = document.querySelector("[data-teach]");
teach?.querySelector("[data-teach-go]")?.addEventListener("click", async (e) => {
  const field = teach.querySelector("[data-teach-field]").value;
  const button = e.currentTarget;
  button.disabled = true;
  try {
    viewer?.setWords(await loadWords());
    teach.removeAttribute("open");
    viewer?.teachField(field);
  } catch (err) {
    toast({ text: err.message || String(err), tone: "err" }, 9000);
  } finally {
    button.disabled = false;
  }
});

async function onViewerEvent(event) {
  if (event.type === "select") return showField(event.field);
  if (event.type !== "assign") return;
  const body = new FormData();
  body.append("field", event.field);
  body.append("text", event.text || "");
  const answer = await post(`${base}/teach`, body);
  if (!answer.ok) {
    toast({ text: answer.error || "That didn't work.", tone: "err" }, 9000);
    return;
  }
  const el = input(answer.field);
  if (el) el.value = answer.value;
  const taught = document.querySelector("[data-taught]");
  let boxes = {};
  try {
    boxes = JSON.parse(taught.value || "{}") || {};
  } catch {
    boxes = {};
  }
  boxes[answer.field] = event.boxes || [];
  taught.value = JSON.stringify(boxes);
  if (answer.toast) toast(answer.toast);
  soon(0);
}

/* ---------- OvisOCR2 ---------- */

const reader = document.querySelector("[data-reader][data-poll]");
if (reader) {
  const every = Math.max(5, Number(reader.dataset.poll) || 20) * 1000;
  const look = async () => {
    try {
      const response = await fetch(`${base}/reader`, { headers: { Accept: "application/json" }, cache: "no-store" });
      const answer = await response.json();
      if (!answer.poll) {
        await flush(); // the edits are kept: the page then shows the reading, or offers to start over from it
        location.reload();
        return;
      }
      reader.innerHTML = answer.html; // drawn by the server, every text escaped
    } catch {
      /* not answering now: look again later */
    }
    setTimeout(look, every);
  };
  setTimeout(look, every);
}

/* ---------- Keys ---------- */

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && approveButton && form) {
    e.preventDefault();
    flush().then(() => {
      if (!approveButton.disabled) form.requestSubmit(approveButton);
      else toast({ text: "Not ready to approve yet: see the bar at the bottom.", tone: "warn" });
    });
    return;
  }
  if (!e.altKey || e.ctrlKey || e.metaKey) return;
  const where = { ArrowLeft: "prev", ArrowRight: "next", ArrowUp: "queue" }[e.key];
  const link = where && document.querySelector(`[data-nav="${where}"]`);
  if (link) {
    e.preventDefault();
    flush().then(() => location.assign(link.href));
  }
});

/* ---------- Start ---------- */

showMailLanguage();
fillSplitLines();
// The queue shows what this screen shows: check once on opening (today's checks, and any draft kept).
if (form) check();
