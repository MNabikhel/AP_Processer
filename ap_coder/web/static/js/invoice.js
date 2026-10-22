/* The invoice review screen (/review/<id>, pages/invoice.py).

   * Every edit is checked as the reviewer types: the form "inv" (every field, both grids) is posted to
     /review/<id>/check, which answers the sections that follow the edits ([data-live=...]) drawn again, and whether
     it may be approved. The server keeps the edits as a draft, so leaving and coming back keeps them.
   * The grids: add and delete rows here; split a line, the coding rules, the PO's coding and a suggested GL account
     are POSTs answered with the new rows ([data-grid-action]).
   * The page viewer (invoice_viewer.js): focusing a field of the form highlights its box on the page; "Teach a
     field" lets the reviewer click the words that hold it (sent with the approval, the supplier's template learns).
   * OvisOCR2: while it has this invoice in its queue, its progress is looked up again every few seconds.
   * Keys: Ctrl+Enter approves (typed in another form or a dialog, it is theirs), Alt+←/→ previous / next invoice
     and Alt+↑ the queue (not in a text box, where they move the caret). Enter in a text box approves nothing.

   Everything from the server is either HTML it escaped (the sections, the rows) or set as text here.
*/

import { HEADERS, follow, post, toast } from "./app.js";
import { InvoiceViewer } from "./invoice_viewer.js";
import { enhance, fold, registerGroups, setAction, setSource, setValue } from "./picker.js";

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
const checking = document.querySelector("[data-checking]"); // "Checking…" beside the approve bar meanwhile

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
    // A box or choice of this section in use (ticked with the keyboard) keeps the focus once it is drawn again.
    const active = el.contains(document.activeElement) && document.activeElement.name ? document.activeElement : null;
    el.innerHTML = html; // drawn by the server, every text escaped
    el.querySelectorAll("details").forEach((d, i) => {
      if (i < open.length) d.open = open[i];
    });
    if (active) {
      const same = [...el.querySelectorAll("[name]")].find((x) => x.name === active.name && x.value === active.value);
      same?.focus({ preventScroll: true });
    }
  }
  if (approveButton) approveButton.disabled = !answer.allow;
  // the accounts of the vendor now in the vendor field: the account lists' "Used for this vendor" (B-596)
  if (Array.isArray(answer.vendor_accounts)) vendorAccounts = answer.vendor_accounts;
  showMailLanguage();
}

async function check() {
  clearTimeout(timer);
  timer = 0;
  if (!form || form.dataset.sending) return; // being approved: nothing more to check
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
  if (checking) checking.textContent = "Checking…";
  try {
    await running;
  } finally {
    running = null;
    if (checking) checking.textContent = "";
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

/* A line's description wraps in its box (a textarea) but is one line: Enter adds no line break (B-467). */
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.ctrlKey && !e.metaKey && e.target.matches?.("textarea[data-one-line]")) e.preventDefault();
});
document.addEventListener("input", (e) => {
  if (e.target.matches?.("textarea[data-one-line]") && /[\r\n]/.test(e.target.value)) {
    e.target.value = e.target.value.replace(/\s*[\r\n]+\s*/g, " ");
  }
});

if (form) {
  document.addEventListener("input", (e) => {
    if (e.target.form === form && e.target.type !== "checkbox" && e.target.type !== "radio") soon();
  });
  document.addEventListener("change", (e) => {
    if (e.target.form !== form) return;
    if (e.target.name === "mail_lang") return showMailLanguage();
    if (e.target.name === "line_gl") {  // a guessed account the reviewer picked again is no longer a guess
      const cell = e.target.closest(".is-guess");
      if (cell) { cell.classList.remove("is-guess"); cell.querySelector(".inv-guess")?.remove(); }
    }
    soon(e.target.tagName === "SELECT" || e.target.type === "checkbox" ? 0 : 200);
  });
}

/* Another form or a link while edits wait to be checked: check (and keep) them first. */
let passing = null;
document.addEventListener(
  "submit",
  (e) => {
    const f = e.target;
    if (f === form) {
      // Approving sends every field as it is now: a check still waiting (the field just left fired "change") would
      // only reach the server after the approval, and be told the invoice is no longer in the queue.
      clearTimeout(timer);
      timer = 0;
      return;
    }
    if (!form || passing === f || !(timer || running)) return;
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
    enhance(body.lastElementChild);
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
  if (body) {
    body.innerHTML = html; // rows drawn by the server, every text escaped
    enhance(body); // their account pickers
  }
  fillSplitLines();
}

async function gridAction(url, extra, button) {
  await flush();
  const data = {};
  for (const [k, v] of Object.entries(extra || {})) if (v !== undefined) data[k] = v;
  if (button) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.classList.add("busy");
  }
  try {
    const answer = await post(url, formData(data));
    if (answer.ok && typeof answer.lines === "string") {
      setLines(answer.lines);
      if (answer.toast) toast(answer.toast);
      await check();
    } else follow(answer);
  } finally {
    if (button) {  // always usable again, whatever went wrong
      button.disabled = false;
      button.removeAttribute("aria-busy");
      button.classList.remove("busy");
    }
  }
}

/* ---------- The account pickers' groups: the accounts this vendor's lines went to, and the ones suggested ----------
   "Used for this vendor" starts as the page's #gl-vendor; each check of the edits answers it again for the vendor in
   the vendor field (B-596). */

let vendorAccounts = [];
try {
  vendorAccounts = JSON.parse(document.getElementById("gl-vendor")?.textContent || "[]") || [];
} catch {
  vendorAccounts = [];
}

registerGroups("gl", (select) => {
  const groups = [{ label: "Used for this vendor", values: vendorAccounts }];
  // the line's suggestions (the Suggested GL accounts card): a line's own, or the line being split
  const number = select.closest("tr")?.querySelector('[name="line_number"]')?.value || (select.closest("[data-split]") ? splitLine?.value : "");
  if (number) {
    const card = document.querySelector(`[data-live="suggest"] .inv-sugg[data-line="${CSS.escape(number)}"]`);
    groups.push({ label: "Suggested", values: [...(card?.querySelectorAll("[data-gl]") || [])].map((b) => b.dataset.gl) });
  }
  return groups;
});

/* ---------- Apply to all lines: one GL account / cost center for the lines without one ---------- */

document.addEventListener("click", (e) => {
  const go = e.target.closest("[data-fill-go]");
  if (!go) return;
  const pop = go.closest("[data-fill]");
  const value = (name) => pop?.querySelector(`[data-fill-${name}]`)?.value || "";
  gridAction(`${base}/fill-lines`, { fill_gl: value("gl"), fill_cc: value("cc") }, go).then(() =>
    pop?.removeAttribute("open"),
  );
});

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
  enhance(tr); // the account pickers (their choices: the page's #gl-options and #cc-options)
  if (gl) setValue(glSelect, gl); // the line's accounts
  if (ccSelect && cc) setValue(ccSelect, cc);
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

/* ---------- The vendor, from the vendor list ---------- */

const vendorInput = document.getElementById("vendor-name");
const vendorMatch = document.querySelector("[data-vendor-match]");
const vendorAdd = document.querySelector("[data-vendor-add]");
let vendors = { enforced: false, list: [] };
try {
  vendors = JSON.parse(document.getElementById("vendor-data")?.textContent || "null") || vendors;
} catch {
  /* no vendor list on this page */
}

/* A vendor name's key, exactly as memory.vendor_key gives it (accents, ligatures, punctuation, "&"/"and", a leading
   "the" and the legal forms at the end aside: "The Northwind IT Solutions Inc." -> "northwind it solutions"). The
   vendor list comes with each vendor's key from the server; this keys the name being typed. Keep it in step with
   memory.py: tests/test_web_invoice.py compares the two on many spellings. */
const LEGAL_SUFFIXES = new Set(
  ("inc incorporated incorporee ltd ltee limited limitee corp corporation co company cie llc llp lp plc ulc pc pllc " +
    "enr senc sencrl sec srl gmbh ag kg sa sas sarl bv nv pty spa ltda and").split(" "),
);

export function vendorKey(name) {
  const text = fold(name)
    .replace(/&/g, " and ")
    .replace(/(?<![\p{L}\p{N}_])(?:[a-z]\.){2,}/gu, (m) => m.replace(/\./g, "")); // S.E.N.C. -> senc
  const words = fold(text).replace(/[^0-9a-z]+/g, " ").split(" ").filter(Boolean);
  if (words[0] === "the") words.shift();
  while (words.length > 1 && LEGAL_SUFFIXES.has(words[words.length - 1])) words.pop();
  return words.join(" ");
}

function businessNumber(value) {
  const digits = (value || "").replace(/\D/g, "");
  return digits.length >= 9 ? digits.slice(0, 9) : "";
}

function vendorLine(icon, tone, text, action) {
  vendorMatch.className = `inv-vendor-match small ${tone}`;
  const mark = document.createElement("span");
  mark.className = "ms";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = icon;
  const words = document.createElement("span");
  words.textContent = text;
  vendorMatch.replaceChildren(mark, words);
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = action.label;
    button.addEventListener("click", action.run);
    vendorMatch.append(button);
  }
}

/** Under the vendor field: in the vendor list (with its ERP ID), the vendor a printed GST/HST number belongs to, or
    not in the list (add it as a new vendor). */
function showVendorMatch() {
  if (!vendorInput || !vendorMatch) return;
  const typed = vendorKey(vendorInput.value);
  const found = typed ? vendors.list.find((v) => (v.key ?? vendorKey(v.name)) === typed) : null;
  if (found) {
    const id = found.id ? ` · ${found.id}` : "";
    if (found.added) vendorLine("person_add", "warn", `Added here, not in JD Edwards yet${id}`);
    else if (!vendors.enforced) vendorLine("history", "ok", "Approved before"); // no vendor master: a past vendor
    else vendorLine("verified", "ok", `In the vendor list${id}`);
    return;
  }
  const bn = businessNumber(input("gst_hst_registration_number")?.value);
  const byNumber = bn ? vendors.list.find((v) => businessNumber(v.gst) === bn) : null;
  if (byNumber) {
    vendorLine("info", "warn", `The GST/HST number printed is ${byNumber.name}'s.`, {
      label: `Use ${byNumber.name}`,
      run: () => setVendor(byNumber.name),
    });
    return;
  }
  // Without a vendor master the list is only the vendors approved before: a new one is no mistake, so say nothing.
  if (!vendors.list.length || !vendors.enforced) {
    vendorMatch.replaceChildren();
    vendorMatch.className = "inv-vendor-match small";
    return;
  }
  vendorLine("gpp_maybe", "warn", typed ? "Not in the vendor list: pick one, or add it as a new vendor." : "Pick the vendor from the list.");
}

function setVendor(name) {
  vendorInput.value = name;
  showVendorMatch();
  soon(0);
}

vendorInput?.addEventListener("input", showVendorMatch);
// The vendor field's list (picker.js): the vendors of the vendor list, with their ERP ID and GST/HST number.
if (vendorInput) {
  setSource(vendorInput, () =>
    vendors.list.map((v) => ({
      value: v.name,
      extra: [v.id, v.gst ? `GST/HST ${v.gst}` : "", v.added ? "added here" : ""].filter(Boolean).join(" · "),
    })),
  );
}
input("gst_hst_registration_number")?.addEventListener("input", showVendorMatch);
// The vendor list's last row, while it covers the line under the field (B-597): a name not in the list is added.
if (vendorInput && vendorAdd) {
  setAction(vendorInput, (typed) => {
    const key = vendorKey(typed);
    if (!vendors.enforced || !key || vendors.list.some((v) => (v.key ?? vendorKey(v.name)) === key)) return null;
    return {
      label: "Not in the vendor list: add as a new vendor…",
      run: () => {
        showVendorMatch();
        vendorAdd.open = true; // its "toggle" fills the name typed and focuses it
      },
    };
  });
}

vendorAdd?.addEventListener("toggle", () => {
  if (!vendorAdd.open) return;
  const fill = { name: vendorInput?.value || "", gst: input("gst_hst_registration_number")?.value || "",
    qst: input("qst_registration_number")?.value || "", pst: input("pst_registration_number")?.value || "",
    province: input("supplier_province")?.value || "" };
  for (const [key, value] of Object.entries(fill)) {
    const el = vendorAdd.querySelector(`[data-new="${key}"]`);
    if (el && !el.value) el.value = value;
  }
  vendorAdd.querySelector('[data-new="name"]')?.focus();
});

vendorAdd?.querySelector("[data-new-go]")?.addEventListener("click", async (e) => {
  const button = e.currentTarget;
  const body = new FormData();
  vendorAdd.querySelectorAll("[data-new]").forEach((el) => body.append(el.dataset.new, el.value.trim()));
  button.disabled = true;
  const answer = await post(`${base}/vendor`, body);
  button.disabled = false;
  if (!answer.ok) {
    toast({ text: answer.error || "That didn't work.", tone: "err" }, 9000);
    return;
  }
  const added = { name: answer.vendor, key: answer.key ?? vendorKey(answer.vendor), id: "", gst: body.get("gst"), qst: body.get("qst"), pst: body.get("pst"), added: true };
  vendors.list = vendors.list.filter((v) => v.key !== added.key).concat([added]); // the vendor field offers it now
  vendorAdd.removeAttribute("open");
  if (answer.toast) toast(answer.toast);
  setVendor(added.name);
});

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
  if (event.type === "line_hover") return markRow(event.index, event.on);
  if (event.type === "selection") return chooseFor(event);
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
  if (answer.field === "vendor_name") showVendorMatch();
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

/* ---------- Line items: a row of the grid and its outline on the page ---------- */

const viewerLines = (() => {
  try {
    return JSON.parse(viewerData?.textContent || "{}").line_items || [];
  } catch {
    return [];
  }
})();

function gridRows() {
  return [...document.querySelectorAll('[data-grid="lines"] tr')];
}

function rowAmount(tr) {
  const v = Number(tr.querySelector('[name="line_amount"]')?.value);
  return Number.isFinite(v) ? Math.round(v * 100) / 100 : null;
}

/** Which outline on the page each grid row is: the same amount (in order, each used once), else the same place in the
    list when the page has as many lines as the grid. [rowIndex] -> viewer line index or -1. */
function rowToLine() {
  const rows = gridRows();
  const used = new Set();
  const out = rows.map((tr) => {
    const amount = rowAmount(tr);
    const i = amount === null ? -1 : viewerLines.findIndex((l, j) => !used.has(j) && l.amount !== null && Math.abs(l.amount - amount) < 0.005);
    if (i >= 0) used.add(i);
    return i;
  });
  if (rows.length === viewerLines.length) out.forEach((v, r) => (out[r] = v >= 0 ? v : used.has(r) ? -1 : r));
  return out;
}

function markRow(index, on) {
  const rows = gridRows();
  rows.forEach((tr) => tr.classList.remove("inv-row-hot"));
  if (!on) return;
  const r = rowToLine().indexOf(index);
  if (r >= 0) rows[r].classList.add("inv-row-hot");
}

const linesGrid = document.querySelector('[data-grid="lines"]');
linesGrid?.addEventListener("mouseover", (e) => {
  const tr = e.target.closest("tr");
  if (!tr || !viewer) return;
  viewer.markLine(rowToLine()[gridRows().indexOf(tr)], true);
});
linesGrid?.addEventListener("mouseleave", () => viewer?.markLine(-1, false));
linesGrid?.addEventListener("focusin", (e) => {
  const tr = e.target.closest("tr");
  const i = tr ? rowToLine()[gridRows().indexOf(tr)] : -1;
  if (viewer && i >= 0) viewer.revealLine(i);
});

/* ---------- Words picked on the page by dragging a box: what are they? ---------- */

const LINE_COLUMNS = [
  ["line_description", "description", "description"],
  ["line_amount", "amount", "amount"],
  ["line_quantity", "quantity", "quantity"],
  ["line_unit_price", "unit_price", "unit price"],
];
let chooser = null;

function closeChooser() {
  chooser?.remove();
  chooser = null;
  viewer?.markSelection(0, null);
}

function headerTargets() {
  const options = [...document.querySelectorAll("[data-teach-field] option")];
  return options.map((o) => [o.value, o.textContent]);
}

async function chooseFor(event) {
  closeChooser();
  let picked = [];
  try {
    viewer.setWords(await loadWords());
    picked = viewer.wordsIn(event.page, event.rect);
  } catch (err) {
    toast({ text: err.message || String(err), tone: "err" }, 9000);
    return;
  }
  if (!picked.length) {
    toast({ text: "No words in that box: drag across the words you want.", tone: "info" }, 4000);
    return;
  }
  viewer.markSelection(event.page, event.rect);
  const text = picked.map((p) => p.t).join(" ");
  const select = document.createElement("select");
  select.setAttribute("aria-label", "What these words are");
  const group = (label, items) => {
    const g = document.createElement("optgroup");
    g.label = label;
    for (const [value, words] of items) {
      const o = document.createElement("option");
      o.value = value;
      o.textContent = words;
      g.append(o);
    }
    if (items.length) select.append(g);
  };
  group("Invoice field", headerTargets());
  const rows = gridRows();
  group(
    "Line items",
    rows.flatMap((tr, r) => {
      const n = tr.querySelector('[name="line_number"]')?.value || String(r + 1);
      return LINE_COLUMNS.map(([field, , words]) => [`${field}:${r}`, `Line ${n}: ${words}`]);
    }),
  );
  group("New line", [["line_new", "A new line from these words"]]);
  // Words with a description and an amount look like a row of the line table: a new line is the likely choice.
  if (picked.length > 3 && /\d[\d,]*\.\d{2}\b/.test(text)) select.value = "line_new";
  chooser = document.createElement("div");
  chooser.className = "inv-chooser card";
  chooser.setAttribute("role", "dialog");
  chooser.setAttribute("aria-label", "Use the words picked on the page");
  const quote = document.createElement("q");
  quote.textContent = text.length > 120 ? `${text.slice(0, 117)}…` : text;
  const use = document.createElement("button");
  use.type = "button";
  use.className = "btn primary sm";
  use.textContent = "Use";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "btn sm ghost";
  cancel.textContent = "Cancel";
  const head = document.createElement("div");
  head.className = "small muted";
  head.textContent = `${picked.length === 1 ? "1 word" : `${picked.length} words`} picked:`;
  const actions = document.createElement("div");
  actions.className = "row";
  actions.append(cancel, use);
  chooser.append(head, quote, select, actions);
  document.body.append(chooser);
  const w = chooser.offsetWidth;
  const hgt = chooser.offsetHeight;
  chooser.style.left = `${Math.max(8, Math.min(event.x + 8, innerWidth - w - 8))}px`;
  chooser.style.top = `${Math.max(8, Math.min(event.y + 8, innerHeight - hgt - 8))}px`;
  select.focus();
  cancel.addEventListener("click", closeChooser);
  chooser.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeChooser();
    // Enter in the list uses the words; on a button, Enter presses that button (Cancel must not use them).
    if (e.key === "Enter" && e.target === select) {
      e.preventDefault();
      use.click();
    }
  });
  use.addEventListener("click", async () => {
    use.disabled = true;
    const [field, row] = select.value.split(":");
    const boxes = picked.map((p) => [p.page, ...p.b.map((v) => Math.round(v * 1e5) / 1e5)]);
    if (!field.startsWith("line_")) {
      closeChooser();
      return onViewerEvent({ type: "assign", field, text, boxes });
    }
    const body = new FormData();
    body.append("field", field);
    body.append("text", text);
    const answer = await post(`${base}/teach`, body);
    use.disabled = false;
    if (!answer.ok) {
      toast({ text: answer.error || "That didn't work.", tone: "err" }, 9000);
      return;
    }
    closeChooser();
    if (field === "line_new") addLineFrom(answer.value);
    else {
      const tr = gridRows()[Number(row)];
      const cell = tr?.querySelector(`[name="${field}"]`);
      if (cell) cell.value = answer.value;
    }
    if (answer.toast) toast(answer.toast);
    soon(0);
  });
}

function addLineFrom(value) {
  const tpl = document.getElementById("line-template");
  const body = document.querySelector('[data-grid="lines"]');
  if (!tpl || !body || !value) return;
  body.insertAdjacentHTML("beforeend", tpl.innerHTML.replaceAll("__KEY__", `n${Date.now().toString(36)}${newRows++}`));
  const tr = body.lastElementChild;
  enhance(tr);
  tr.querySelector('[name="line_description"]').value = value.description || "";
  tr.querySelector('[name="line_amount"]').value = Number(value.amount).toFixed(2);
  fillSplitLines();
  tr.querySelector('[name="line_gl"]')?.focus();
}

document.addEventListener("pointerdown", (e) => {
  if (chooser && !chooser.contains(e.target)) closeChooser();
});

/* ---------- "Why is this the right value?" (the same correction again and again) ---------- */

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  node.append(...children.filter(Boolean));
  return node;
}

document.addEventListener("apc:ask-why", (event) => {
  const { form: asked, submitter, ask } = event.detail;
  if (asked !== form) return;
  document.querySelector("dialog.inv-why")?.remove();
  const items = (ask.fields || []).map((f, i) => {
    const id = `why-${i}`;
    const was = f.proposed ? `AP Coder read “${f.proposed}”; you chose “${f.value}”.` : `You chose “${f.value}”.`;
    const area = el("textarea", { id, rows: "2", "data-why": f.key, placeholder: "e.g. On TELUS statements the invoice number is the account number and the statement date" });
    if (f.explanation) area.value = f.explanation;
    return el(
      "div",
      { class: "inv-why-item" },
      el("label", { for: id }, el("b", { text: `${f.label[0].toUpperCase()}${f.label.slice(1)}` }), el("span", { class: "muted small", text: ` corrected ${f.times} times` })),
      el("p", { class: "small", text: was }),
      f.explanation ? el("p", { class: "small muted", text: "Your last reason is below: change it if it wasn't enough." }) : null,
      area,
    );
  });
  const skip = el("button", { type: "button", class: "btn ghost", text: "Skip for now" });
  const save = el("button", { type: "button", class: "btn primary", text: "Keep the reason and approve" });
  const dialog = el(
    "dialog",
    { class: "inv-why card", "aria-labelledby": "why-title" },
    el("h3", { id: "why-title", text: "One question before saving" }),
    el("p", { class: "muted", text: "You keep making the same correction for this vendor. In a sentence: why is your value the right one? AP Coder keeps your reason and the AI reads it with this vendor's next invoices, so it gets it right without you." }),
    ...items,
    el("div", { class: "row inv-why-actions" }, skip, save),
  );
  document.body.append(dialog);
  const send = (withAnswers) => {
    form.querySelectorAll("input[data-why-sent]").forEach((x) => x.remove());
    const add = (name, value) => form.append(el("input", { type: "hidden", name, value, "data-why-sent": "1" }));
    add("why_done", "1");
    if (withAnswers) dialog.querySelectorAll("textarea[data-why]").forEach((t) => t.value.trim() && add(`why::${t.dataset.why}`, t.value.trim()));
    dialog.close();
    dialog.remove();
    form.requestSubmit(submitter || approveButton || undefined);
  };
  skip.addEventListener("click", () => send(false));
  save.addEventListener("click", () => send(true));
  dialog.addEventListener("cancel", () => dialog.remove());
  dialog.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      save.click(); // Ctrl+Enter here is "Keep the reason and approve", with the reasons typed
    }
  });
  dialog.showModal();
  dialog.querySelector("textarea")?.focus();
});

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

/** Where the key was typed belongs to something else than the review form: another form (Park, Reject, a note) or a
    dialog. Ctrl+Enter is then that one's, never an approval. */
function elsewhere(target) {
  const owner = target.form || target.closest?.("form, dialog, [role=dialog]");
  return Boolean(owner) && owner !== form;
}

/** A box being typed in, where Alt+←/→ moves the caret word by word (Option+arrows on a Mac). */
function typing(target) {
  if (target.isContentEditable || target.tagName === "TEXTAREA" || target.tagName === "SELECT") return true;
  return target.tagName === "INPUT" && !["checkbox", "radio", "button", "submit", "reset"].includes(target.type);
}

/* ---------- The line that needs an account ---------- */

/** The first line's GL account or cost center still to pick (the approve bar's hint, and G): scrolled to and focused,
    where it was a label and the line far down the page (B-434). */
function goToPick() {
  const boxes = [...document.querySelectorAll('select[form="inv"][name="line_gl"], select[form="inv"][name="line_cc"]')];
  const box = boxes.find((b) => !b.value || b.value === "UNASSIGNED");
  if (!box) return false;
  box.scrollIntoView({ block: "center", behavior: "smooth" });
  box.focus({ preventScroll: true });
  return true;
}

/** The box of one line (a finding in the checks: "Line 2: no GL account yet"), by its line number (B-443). */
function goToLine(name, number) {
  const rows = [...document.querySelectorAll('input[form="inv"][name="line_number"]')];
  const row = rows.find((r) => r.value === number)?.closest("tr");
  const box = row?.querySelector(`[form="inv"][name="${CSS.escape(name)}"]`);
  if (!box) return goToPick();
  box.scrollIntoView({ block: "center", behavior: "smooth" });
  box.focus({ preventScroll: true });
  return true;
}

document.addEventListener("click", (e) => {
  if (e.target.closest?.("[data-goto-pick]")) goToPick();
  const line = e.target.closest?.("[data-goto-line]");
  if (line) goToLine(line.dataset.gotoLine, line.dataset.line);
});

document.addEventListener("keydown", (e) => {
  if ((e.key === "g" || e.key === "G") && !e.ctrlKey && !e.metaKey && !e.altKey && !typing(e.target) &&
      !e.target.closest?.("dialog, [role=dialog]")) {
    if (goToPick()) e.preventDefault();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && approveButton && form) {
    if (elsewhere(e.target)) return;
    e.preventDefault();
    flush().then(() => {
      if (!approveButton.disabled) form.requestSubmit(approveButton);
      else toast({ text: "Not ready to approve yet: see the bar at the bottom.", tone: "warn" });
    });
    return;
  }
  if (!e.altKey || e.ctrlKey || e.metaKey || typing(e.target)) return;
  const where = { ArrowLeft: "prev", ArrowRight: "next", ArrowUp: "queue" }[e.key];
  const link = where && document.querySelector(`[data-nav="${where}"]`);
  if (link) {
    e.preventDefault();
    flush().then(() => location.assign(link.href));
  }
});

/* The page left another way (the browser's Back, closing the tab) while an edit waits to be checked: it is sent
   anyway, so the server keeps it as a draft (keepalive: the request outlives the page). */
window.addEventListener("pagehide", () => {
  if (!form || !timer || form.dataset.sending) return;
  clearTimeout(timer);
  timer = 0;
  fetch(`${base}/check`, { method: "POST", body: formData(), headers: HEADERS, credentials: "same-origin", keepalive: true }).catch(() => {});
});

/* ---------- Start ---------- */

showMailLanguage();
fillSplitLines();
showVendorMatch();
// The queue shows what this screen shows: check once on opening (today's checks, and any draft kept).
if (form) check();
