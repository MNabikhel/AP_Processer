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
     and Alt+↑ the queue (not in a text box, where they move the caret). Enter in a text box approves nothing: in an
     invoice detail it goes to the next one (Shift+Enter the one before). P opens Park, X opens Reject (only opens).
   * Right-click (or Shift+F10) a field, a line, a tax line or the page: a menu of what to do with it (ctxmenu.js,
     D-60). Deleting, clearing, splitting and the like can be undone: the message says Undo, and Ctrl+Z (not in a
     text box of another form) does the same. Only the last change is kept to undo, until something else is edited.

   Everything from the server is either HTML it escaped (the sections, the rows) or set as text here.
*/

import { HEADERS, follow, post, toast } from "./app.js";
import { register } from "./ctxmenu.js";
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
  followTicks(); // every edit comes here: a tax line's taxable follows its ticked lines (B-610)
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
    if (e.target.form !== form) return;
    dropUndo(); // an edit since: Undo would put that back too
    headerEdited(e.target);
    if (e.target.type !== "checkbox" && e.target.type !== "radio") soon();
  });
  document.addEventListener("change", (e) => {
    if (e.target.form !== form) return;
    if (e.target.name !== "mail_lang") dropUndo();
    headerEdited(e.target);
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


document.addEventListener("click", (e) => {
  const add = e.target.closest("[data-add-row]");
  if (add) {
    const which = add.dataset.addRow;
    const tpl = document.getElementById(which === "lines" ? "line-template" : "tax-template");
    const body = document.querySelector(`[data-grid="${which}"]`);
    if (!tpl || !body) return;
    const html = tpl.innerHTML.replaceAll("__KEY__", newKey());
    body.insertAdjacentHTML("beforeend", html);
    enhance(body.lastElementChild);
    body.lastElementChild?.querySelector("input:not([type=hidden]), select")?.focus();
    if (which === "lines") fillSplitLines();
    soon(0);
    return;
  }
  const del = e.target.closest("[data-del-row]");
  if (del) {
    const tr = del.closest("tr");
    if (tr) deleteRow(tr);
    return;
  }
  const action = e.target.closest("[data-grid-action]");
  if (action) gridAction(action.dataset.gridAction, { position: action.dataset.position, gl: action.dataset.gl, cc: action.dataset.cc }, action);
  const copy = e.target.closest("[data-copy]");
  if (copy) copyText(copy.parentElement.querySelector("pre")?.textContent || "");
  const copyNumber = e.target.closest("[data-copy-text]");
  if (copyNumber) copyText(copyNumber.dataset.copyText, "Invoice number copied.");
});

function copyText(text, done = "Copied.") {
  if (!navigator.clipboard) {
    toast({ text: "Select the text and copy it.", tone: "info" });
    return;
  }
  navigator.clipboard.writeText(text).then(
    () => toast({ text: done, tone: "ok", icon: "content_copy" }, 2500),
    () => toast({ text: "Select the text and copy it.", tone: "info" }),
  );
}

let keys = 0;
function newKey() {
  return `n${Date.now().toString(36)}${keys++}`;
}

function setLines(html) {
  const body = document.querySelector('[data-grid="lines"]');
  if (body) {
    body.innerHTML = html; // rows drawn by the server, every text escaped
    enhance(body); // their account pickers
  }
  fillSplitLines();
  lastSums = tickedSums(); // the lines drawn again (a split): what the tax lines follow from now on
}

async function gridAction(url, extra, button, undoText = "") {
  await flush();
  const snap = snapshot(); // the lines as they were: the answer's message offers Undo
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
      offerUndo(answer.toast || { text: undoText || "Lines changed.", tone: "ok" }, snap);
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
  if (event.type === "dblclick") return fillFromWord(event);
  if (event.type !== "assign") return;
  const snap = snapshot();
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
  const boxes = taughtBoxes();
  boxes[answer.field] = event.boxes || [];
  setTaught(boxes);
  setAbsent(answer.field, false);
  viewer?.setEdited(answer.field, answer.value);
  offerUndo(answer.toast || { text: "Set.", tone: "ok" }, snap);
  soon(0);
}

/** The words shown on the page for each field ({field: boxes}), from the form's hidden ``taught``. */
function taughtBoxes() {
  try {
    return JSON.parse(document.querySelector("[data-taught]")?.value || "{}") || {};
  } catch {
    return {};
  }
}

function setTaught(boxes) {
  const taught = document.querySelector("[data-taught]");
  if (taught) taught.value = JSON.stringify(boxes);
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
    const snap = snapshot();
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
    offerUndo(answer.toast || { text: "Set.", tone: "ok" }, snap);
    soon(0);
  });
}

function addLineFrom(value) {
  const tpl = document.getElementById("line-template");
  const body = document.querySelector('[data-grid="lines"]');
  if (!tpl || !body || !value) return;
  body.insertAdjacentHTML("beforeend", tpl.innerHTML.replaceAll("__KEY__", newKey()));
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

/* ---------- Undo: the last change, put back (D-60) ----------
   Before a change made from a menu (or Delete, a grid action, words used from the page), the screen's state is kept:
   every invoice detail, the words taught, the fields marked not on the invoice, both grids row by row. Undo (the
   message's button, or Ctrl+Z outside a text box) puts it back and checks again. One change is kept, until the next
   one or until something is edited by hand. */

let undoState = null; // {snap, note}: the screen before the last change, and the message offering to undo it

function headerElements() {
  return [...document.querySelectorAll('#inv [name], [form="inv"][name]')].filter(
    (el) => !el.closest("[data-grid]") && !["checkbox", "radio", "submit"].includes(el.type) &&
      (el.type !== "hidden" || el.name === "taught" || el.name === "absent"),
  );
}

function rowState(tr) {
  const values = {};
  tr.querySelectorAll("[name]").forEach((el) => {
    if (el.type !== "checkbox") values[el.name] = el.value;
  });
  const gl = tr.querySelector(".c-gl.is-guess");
  return {
    key: tr.dataset.row || "",
    values,
    taxes: [...tr.querySelectorAll("input[type=checkbox]:checked")].map((c) => c.value),
    guess: gl ? gl.querySelector(".inv-guess")?.outerHTML || "" : null,
  };
}

function snapshot() {
  const grid = (which) => [...document.querySelectorAll(`[data-grid="${which}"] > tr`)].map(rowState);
  return {
    header: headerElements().map((el) => [el.name, el.value]),
    lines: grid("lines"),
    taxes: grid("taxes"),
    edits: viewer?.editsNow(),
  };
}

/** A row of a grid from its template, with the values of ``state`` (rowState), put after ``after`` or at the end. */
function makeRow(which, state, after = null) {
  const tpl = document.getElementById(which === "lines" ? "line-template" : "tax-template");
  const body = document.querySelector(`[data-grid="${which}"]`);
  if (!tpl || !body) return null;
  const holder = document.createElement("tbody");
  holder.innerHTML = tpl.innerHTML.replaceAll("__KEY__", state.key || newKey()); // the page's own template
  const tr = holder.firstElementChild;
  if (after) after.after(tr);
  else body.append(tr);
  enhance(tr);
  for (const [name, value] of Object.entries(state.values || {})) {
    // The row's key is the one its template was made with (state.key, or a new one for a copy): its tax boxes are
    // named line_tax_<key>, which the server reads for the row whose line_key it is (B-620).
    if (name === "line_key") continue;
    const el = tr.querySelector(`[name="${CSS.escape(name)}"]`);
    if (!el) continue;
    if (el.matches("select[data-picker]")) setValue(el, value) || setValue(el, "UNASSIGNED");
    else el.value = value;
  }
  tr.querySelectorAll("input[type=checkbox]").forEach((c) => (c.checked = (state.taxes || []).includes(c.value)));
  const cell = tr.querySelector(".c-gl");
  if (cell && state.guess !== null && state.guess !== undefined) {
    cell.classList.add("is-guess");
    if (state.guess) cell.insertAdjacentHTML("beforeend", state.guess); // drawn by the server, every text escaped
  }
  return tr;
}

function restore(snap) {
  for (const which of ["lines", "taxes"]) {
    const body = document.querySelector(`[data-grid="${which}"]`);
    if (!body) continue;
    body.replaceChildren();
    for (const state of snap[which]) makeRow(which, state);
  }
  const els = headerElements();
  for (const [name, value] of snap.header) {
    const el = els.find((x) => x.name === name);
    if (el) el.value = value;
  }
  if (snap.edits && viewer) viewer.restoreEdits(snap.edits);
  showAbsent();
  showVendorMatch();
  fillSplitLines();
  lastSums = tickedSums();
}

function dropUndo() {
  if (!undoState) return;
  undoState.note?.remove();
  undoState = null;
}

/** Say what was done, with Undo: ``msg`` a message ({text, tone}) or its text. */
function offerUndo(msg, snap) {
  dropUndo();
  const message = typeof msg === "string" ? { text: msg, tone: "info", icon: "undo" } : { ...msg };
  undoState = { snap };
  message.action = { label: "Undo", icon: "undo", title: "Put it back as it was (Ctrl+Z)", run: undo };
  undoState.note = toast(message, 10000);
}

function undo() {
  if (!undoState) return false;
  const { snap } = undoState;
  dropUndo();
  restore(snap);
  toast({ text: "Put back as it was.", tone: "ok", icon: "undo" }, 2500);
  check();
  return true;
}

/** Make a change that can be undone: ``change()`` (false when it changed nothing), then the message with Undo. */
async function undoable(text, change) {
  const snap = snapshot();
  if ((await change()) === false) return;
  offerUndo(text, snap);
}

/* ---------- The invoice details: clear one, not on this invoice, Enter to the next ---------- */

const TEACHABLE = new Set([...document.querySelectorAll("[data-teach-field] option")].map((o) => o.value));
let lastField = ""; // the invoice detail focused last: a word double-clicked on the page fills it

function detail(name) {
  return document.querySelector(`.inv-details [data-field="${CSS.escape(name)}"]`);
}

function fieldName(name) {
  const lbl = detail(name)?.querySelector(".lbl");
  const own = lbl ? [...lbl.childNodes].filter((n) => n.nodeType === Node.TEXT_NODE).map((n) => n.textContent).join("").trim() : "";
  return own || name.replace(/_/g, " ");
}

function absentList() {
  try {
    const list = JSON.parse(document.querySelector("[data-absent]")?.value || "[]");
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

function setAbsent(name, on) {
  const box = document.querySelector("input[data-absent]");
  if (!box) return;
  const list = absentList().filter((n) => n !== name);
  if (on) list.push(name);
  box.value = JSON.stringify(list);
  showAbsent();
}

/** Each detail marked not on this invoice says so under its box. */
function showAbsent() {
  const list = absentList();
  document.querySelectorAll(".inv-details [data-absent-ok]").forEach((label) => {
    const on = list.includes(label.dataset.field);
    label.toggleAttribute("data-absent", on);
    const note = label.querySelector(".inv-absent");
    if (note) note.hidden = !on;
  });
}

/** An invoice detail typed in: the Fields list beside the page shows it as edited; a value typed in a field marked
    not on this invoice says it is on it after all. */
function headerEdited(el) {
  const label = el.closest?.(".inv-details [data-field]");
  if (!label || el.name !== label.dataset.field) return;
  if (el.value.trim() && absentList().includes(el.name)) setAbsent(el.name, false);
  viewer?.setEdited(el.name, el.value.trim(), absentList().includes(el.name));
}

function clearField(name, absent = false) {
  const el = input(name);
  if (!el) return false;
  el.value = "";
  const boxes = taughtBoxes();
  if (name in boxes) {
    delete boxes[name];
    setTaught(boxes);
  }
  setAbsent(name, absent);
  if (name === "vendor_name" || name === "gst_hst_registration_number") showVendorMatch();
  viewer?.setEdited(name, "", absent);
  soon(0);
  return true;
}

async function teachHere(name) {
  try {
    viewer?.setWords(await loadWords());
    viewer?.teachField(name);
  } catch (err) {
    toast({ text: err.message || String(err), tone: "err" }, 9000);
  }
}

function showOnPage(name) {
  if (!viewer) return;
  viewer.root.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  viewer.select(name, { from: "menu" });
}

function fieldMenu(name, where) {
  const el = input(name);
  if (!el) return null;
  const value = el.value.trim();
  const label = fieldName(name);
  const absentOk = detail(name)?.hasAttribute("data-absent-ok");
  const absent = absentList().includes(name);
  return [
    { icon: "backspace", label: "Clear this value", id: "clear", disabled: value || absent ? false : "It is blank already",
      run: () => undoable(`${label} cleared.`, () => clearField(name)) },
    { icon: "do_not_disturb_on", label: "Not on this invoice", id: "absent",
      disabled: !absentOk ? "AP Coder needs this one: it can't be left out" : absent ? "Marked already: type a value to unmark it" : false,
      run: () => undoable(`${label}: marked as not on this invoice.`, () => clearField(name, true)) },
    "-",
    TEACHABLE.has(name) && viewer ? { icon: "ads_click", label: "Teach where it is", id: "teach", run: () => teachHere(name) } : null,
    viewer ? { icon: "center_focus_strong", label: "Show on page", id: "show", disabled: viewer.hasBox(name) ? false : "Not found on the page", run: () => showOnPage(name) } : null,
    { icon: "content_copy", label: "Copy value", id: "copy", disabled: value ? false : "Nothing to copy", run: () => copyText(el.value) },
    where !== "form" ? { icon: "edit", label: "Change it on the form", id: "edit", run: () => { showField(name); el.focus(); } } : null,
    name === "vendor_name" && vendorAdd ? "-" : null,
    name === "vendor_name" && vendorAdd ? { icon: "person_add", label: "Add as a new vendor…", id: "vendor", run: () => {
      showVendorMatch();
      vendorAdd.open = true;
      vendorAdd.scrollIntoView({ block: "nearest" });
    } } : null,
  ];
}

document.addEventListener("focusin", (e) => {
  const label = e.target.closest?.(".inv-details [data-field]");
  if (label && TEACHABLE.has(label.dataset.field) && e.target.name === label.dataset.field) lastField = label.dataset.field;
});

/** Enter in an invoice detail goes to the next one, Shift+Enter to the one before (Enter never approves). */
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
  const el = e.target;
  if (!el.matches?.('.inv-details input[form="inv"], .inv-details select[form="inv"]') || el.type === "hidden") return;
  const boxes = [...document.querySelectorAll('.inv-details input[form="inv"]:not([type=hidden]), .inv-details select[form="inv"]')]
    .filter((b) => !b.disabled && b.getClientRects().length);
  const i = boxes.indexOf(el);
  const next = boxes[i + (e.shiftKey ? -1 : 1)];
  e.preventDefault();
  if (next) {
    next.focus();
    next.select?.();
  }
});

/* ---------- A word double-clicked on the page fills the detail focused last ---------- */

async function fillFromWord(event) {
  if (!viewer) return;
  if (!lastField) {
    toast({ text: "Click in an invoice detail first (Invoice #, PO #…), then double-click its word on the page.", tone: "info" }, 6000);
    return;
  }
  try {
    viewer.setWords(await loadWords());
  } catch (err) {
    toast({ text: err.message || String(err), tone: "err" }, 9000);
    return;
  }
  const word = viewer.wordAt(event.x, event.y);
  if (!word) return;
  await onViewerEvent({ type: "assign", field: lastField, text: word.t, boxes: [boxOf(word)] });
}

function boxOf(word) {
  return [word.page, ...word.b.map((v) => Math.round(v * 1e5) / 1e5)];
}

/* ---------- The lines ---------- */

const blank = (select) => !select || !select.value || select.value === "UNASSIGNED";

function lineNo(tr) {
  return tr.querySelector('[name="line_number"]')?.value || "";
}

function lineName(tr) {
  const n = lineNo(tr);
  return n ? `Line ${n}` : "The line";
}

/** The lines numbered 1, 2, 3… stay so when one is added between them; otherwise a new line takes the next number. */
function numberNew(tr) {
  const rows = gridRows();
  const others = rows.filter((r) => r !== tr);
  const numbers = others.map((r) => Number(lineNo(r)));
  const inOrder = numbers.every((n, i) => n === i + 1);
  if (inOrder) rows.forEach((r, i) => (r.querySelector('[name="line_number"]').value = String(i + 1)));
  else tr.querySelector('[name="line_number"]').value = String(Math.max(0, ...numbers.filter(Number.isFinite)) + 1);
}

function deleteRow(tr) {
  const which = tr.closest("[data-grid]")?.dataset.grid;
  const what = which === "taxes" ? "Tax line deleted." : `${lineName(tr)} deleted.`;
  return undoable(what, () => {
    const next = tr.nextElementSibling || tr.previousElementSibling;
    tr.remove();
    if (which === "lines") fillSplitLines();
    next?.querySelector("input:not([type=hidden]), textarea, select")?.focus({ preventScroll: true });
    soon(0);
  });
}

function setGl(tr, gl, cc) {
  const g = tr.querySelector('[name="line_gl"]');
  if (g && gl !== undefined) {
    setValue(g, gl);
    const cell = g.closest(".is-guess");
    if (cell) {
      cell.classList.remove("is-guess");
      cell.querySelector(".inv-guess")?.remove();
    }
  }
  const c = tr.querySelector('[name="line_cc"]');
  if (c && cc !== undefined) setValue(c, cc);
}

function openSplit(tr) {
  if (!split) return;
  fillSplitLines();
  splitLine.value = lineNo(tr);
  split.open = true; // its "toggle" sets the parts from this line
  split.scrollIntoView({ block: "nearest" });
  setTimeout(() => split.querySelector('[name="part_pct"]')?.focus(), 50);
}

function lineMenu(tr) {
  const rows = gridRows();
  const i = rows.indexOf(tr);
  const gl = tr.querySelector('[name="line_gl"]');
  const cc = tr.querySelector('[name="line_cc"]');
  const above = rows[i - 1];
  const others = rows.filter((r) => r !== tr);
  const without = others.filter((r) => blank(r.querySelector('[name="line_gl"]')));
  const n = lineNo(tr);
  const suggested = n ? document.querySelector(`[data-live="suggest"] .inv-sugg[data-line="${CSS.escape(n)}"] [data-gl]`) : null;
  const what = cc ? "GL account and cost center" : "GL account";
  return [
    { icon: "delete", label: "Delete line", id: "delete", hint: "", run: () => deleteRow(tr) },
    { icon: "control_point_duplicate", label: "Duplicate line", id: "duplicate", run: () => undoable(`${lineName(tr)} duplicated.`, () => {
      const copy = makeRow("lines", { ...rowState(tr), key: newKey() }, tr);
      numberNew(copy);
      fillSplitLines();
      soon(0);
    }) },
    { icon: "add_row_below", label: "Insert a line below", id: "insert", run: () => undoable("Line added.", () => {
      const row = makeRow("lines", { key: newKey(), values: {}, taxes: [] }, tr);
      numberNew(row);
      fillSplitLines();
      row.querySelector('[name="line_description"]')?.focus();
      soon(0);
    }) },
    "-",
    { icon: "call_split", label: "Split this line…", id: "split", disabled: n ? false : "Give the line a number first", run: () => openSplit(tr) },
    suggested ? { icon: "add_task", label: `Use the suggested account (${suggested.dataset.gl})`, id: "suggested", run: () => suggested.click() } : null,
    { icon: "vertical_align_top", label: `Copy the ${what} from the line above`, id: "above",
      disabled: !above ? "It is the first line" : blank(above.querySelector('[name="line_gl"]')) ? "The line above has no GL account yet" : false,
      run: () => undoable(`${lineName(tr)}: ${what} copied from the line above.`, () => {
        setGl(tr, above.querySelector('[name="line_gl"]').value, above.querySelector('[name="line_cc"]')?.value);
        soon(0);
      }) },
    { icon: "backspace", label: "Clear GL account", id: "clear-gl", disabled: blank(gl) ? "It has none yet" : false,
      run: () => undoable(`${lineName(tr)}: GL account cleared.`, () => {
        setGl(tr, "UNASSIGNED");
        soon(0);
      }) },
    "-",
    { icon: "playlist_add_check", label: "Use this GL account on the lines without one", id: "fill",
      disabled: blank(gl) ? "Pick this line's GL account first" : !without.length ? "Every other line has a GL account" : false,
      run: () => undoable(`GL ${gl.value} put on ${without.length === 1 ? "1 line" : `${without.length} lines`}.`, () => {
        without.forEach((r) => setGl(r, gl.value));
        soon(0);
      }) },
    { icon: "done_all", label: "…on every line", id: "fill-all", disabled: blank(gl) ? "Pick this line's GL account first" : !others.length ? "It is the only line" : false,
      run: () => {
        if (!confirm(`Put GL account ${gl.value} on every line (${rows.length} lines), replacing the accounts they have?`)) return;
        return undoable(`GL ${gl.value} put on every line.`, () => {
          others.forEach((r) => setGl(r, gl.value));
          soon(0);
        });
      } },
  ];
}

/* ---------- The tax lines ---------- */

function num(el) {
  const text = (el?.value || "").trim();
  const v = Number(text.replace(/[,$\s]/g, ""));
  return text !== "" && Number.isFinite(v) ? v : null;
}

/* A tax line's taxable amount follows the lines ticked with its tax (B-610): ticking or unticking a line's GST box,
   changing a ticked line's amount, deleting or adding one (every edit goes through soon()) updates the GST line's taxable amount (and its amount, at its rate) while it still
   is the sum of the lines it was; a taxable amount the clerk typed is left alone, and the tax line's menu offers
   "Taxable from the lines ticked GST". */

function tickedSums() {
  const sums = {};
  for (const tr of gridRows()) {
    const amount = num(tr.querySelector('[name="line_amount"]'));
    if (amount === null) continue;
    tr.querySelectorAll("input[type=checkbox]:checked").forEach((c) => (sums[c.value] = (sums[c.value] || 0) + amount));
  }
  for (const k of Object.keys(sums)) sums[k] = Math.round(sums[k] * 100) / 100;
  return sums;
}

let lastSums = null; // the sums the tax lines were last made to follow (set at the start)

function same(a, b) {
  return a !== null && b !== null && Math.abs(a - b) < 0.005;
}

/** Put ``taxable`` on a tax line; its amount follows at its rate when it was rate x the old taxable. */
function setTaxable(tr, taxable) {
  const rate = num(tr.querySelector('[name="tax_rate_pct"]'));
  const was = num(tr.querySelector('[name="tax_taxable"]'));
  const amountBox = tr.querySelector('[name="tax_amount"]');
  const amount = num(amountBox);
  tr.querySelector('[name="tax_taxable"]').value = taxable.toFixed(2);
  if (rate !== null && (amount === null || (was !== null && same(amount, Math.round(rate * was) / 100)))) {
    amountBox.value = (Math.round(rate * taxable) / 100).toFixed(2);
  }
}

function followTicks() {
  if (!lastSums) return false; // the page is still starting
  const now = tickedSums();
  let changed = false;
  for (const tr of document.querySelectorAll('[data-grid="taxes"] > tr')) {
    const kind = tr.querySelector('[name="tax_type"]')?.value;
    const taxable = num(tr.querySelector('[name="tax_taxable"]'));
    const before = lastSums[kind] || 0;
    const after = now[kind] || 0;
    if (!kind || same(before, after) || !(taxable === null || same(taxable, before))) continue;
    setTaxable(tr, after);
    changed = true;
  }
  lastSums = now;
  return changed;
}


function taxMenu(tr) {
  const kind = tr.querySelector('[name="tax_type"]')?.value;
  const sum = tickedSums()[kind] || 0;
  const label = tr.querySelector('[name="tax_type"]')?.selectedOptions[0]?.textContent || kind;
  const rate = num(tr.querySelector('[name="tax_rate_pct"]'));
  const taxable = num(tr.querySelector('[name="tax_taxable"]'));
  const amount = rate !== null && taxable !== null ? Math.round(rate * taxable) / 100 : null;
  return [
    { icon: "calculate", label: "Recalculate the amount (rate × taxable)", id: "recalc",
      disabled: amount === null ? "Put in the rate and the taxable amount first" : false,
      run: () => undoable(`Tax amount recalculated: ${amount.toFixed(2)}.`, () => {
        tr.querySelector('[name="tax_amount"]').value = amount.toFixed(2);
        soon(0);
      }) },
    { icon: "sync", label: `Taxable from the lines ticked ${label}: ${sum.toFixed(2)}`, id: "sync",
      disabled: !sum ? `No line is ticked ${label}` : same(num(tr.querySelector('[name="tax_taxable"]')), sum) ? "It is that already" : false,
      run: () => undoable(`Taxable amount set from the lines ticked ${label}: ${sum.toFixed(2)}.`, () => {
        setTaxable(tr, sum);
        soon(0);
      }) },
    "-",
    { icon: "delete", label: "Delete tax line", id: "delete", run: () => deleteRow(tr) },
  ];
}

/* ---------- The page ---------- */

function short(text) {
  return text.length > 24 ? `${text.slice(0, 23)}…` : text;
}

async function addLineFromWords(picked) {
  const snap = snapshot();
  const body = new FormData();
  body.append("field", "line_new");
  body.append("text", picked.map((p) => p.t).join(" "));
  const answer = await post(`${base}/teach`, body);
  if (!answer.ok) {
    toast({ text: answer.error || "That didn't work.", tone: "err" }, 9000);
    return;
  }
  addLineFrom(answer.value);
  offerUndo(answer.toast || "Line added.", snap);
  soon(0);
}

async function pageMenu(_stage, ctx) {
  if (!viewer) return null;
  const at = ctx.keyboard ? null : { x: ctx.x, y: ctx.y };
  let word = null;
  if (at && !viewer.teach) {
    try {
      viewer.setWords(await loadWords());
      word = viewer.wordAt(at.x, at.y);
    } catch {
      word = null; // the words can't be read: the zoom and the turn still work
    }
  }
  const fields = headerTargets();
  const teach = viewer.teach
    ? [{ icon: "close", label: "Stop teaching", hint: "Esc", id: "stop", run: () => viewer.cancelTeach() }]
    : [
        word ? { icon: "input", label: `Use “${short(word.t)}” for…`, id: "use",
                 items: fields.map(([f, l]) => ({ icon: "", label: l, id: `use-${f}`, run: () => onViewerEvent({ type: "assign", field: f, text: word.t, boxes: [boxOf(word)] }) })) } : null,
        { icon: "ads_click", label: "Teach a field here", id: "teach", items: fields.map(([f, l]) => ({ icon: "", label: l, id: `teach-${f}`, run: () => teachHere(f) })) },
        { icon: "playlist_add", label: "Add a line from this row of words", id: "line",
          disabled: word ? false : "Right-click on a row of the invoice's lines", run: () => addLineFromWords(viewer.lineOf(word)) },
      ];
  return [
    { icon: "zoom_in", label: at ? "Zoom in here" : "Zoom in", hint: "+", id: "zoom-in", run: () => viewer.zoomStep(1, at) },
    { icon: "zoom_out", label: "Zoom out", hint: "−", id: "zoom-out", run: () => viewer.zoomStep(-1, at) },
    { icon: "fit_width", label: "Fit width", hint: "0", id: "fit-width", run: () => viewer.setZoom("fit") },
    { icon: "fit_screen", label: "Fit page", hint: "9", id: "fit-page", run: () => viewer.setZoom("page") },
    { icon: "rotate_right", label: "Rotate", hint: "R", id: "rotate", run: () => viewer.rotate() },
    "-",
    ...teach,
  ];
}

/* ---------- Which menu: the first that matches (a box on the page is its field's, not the page's) ---------- */

if (form) {
  register(".iv-row[data-field]", (el) => fieldMenu(el.dataset.field, "list"));
  register(".iv-f[data-field], .iv-tag[data-field]", (el) => fieldMenu(el.dataset.field, "page"));
  register(".iv-stage", pageMenu);
  register(".inv-details [data-field]", (el, ctx) =>
    ctx.target.closest(".inv-vendor-add, .inv-vendor-match, .rvw-hint") ? null : fieldMenu(el.dataset.field, "form"));
  register('[data-grid="lines"] > tr', (tr) => lineMenu(tr));
  register('[data-grid="taxes"] > tr', (tr) => taxMenu(tr));
}

/* ---------- Ctrl+Z, P (Park), X (Reject) ---------- */

document.addEventListener("keydown", (e) => {
  if (e.defaultPrevented || e.altKey || e.target.closest?.("dialog, [role=dialog]")) return;
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && (e.key === "z" || e.key === "Z")) {
    // Not in a text box, where Ctrl+Z is the box's own; but in a box of the review form nothing was typed in since
    // the change (typing there drops the Undo), as after a right-click in it: there the change is what to undo.
    if (typing(e.target) && e.target.form !== form) return;
    if (undo()) e.preventDefault();
    return;
  }
  if (e.ctrlKey || e.metaKey || e.shiftKey || typing(e.target)) return;
  const pop = { p: "[data-park]", P: "[data-park]", x: "[data-reject]", X: "[data-reject]" }[e.key];
  const details = pop && document.querySelector(pop);
  if (!details) return;
  e.preventDefault();
  document.querySelectorAll("details.pop[open]").forEach((d) => d !== details && d.removeAttribute("open"));
  details.open = true; // only opened: the reason is typed and its button pressed, as with a click
  details.querySelector("input[type=text]")?.focus();
});

/* ---------- A note, added without leaving the page (the edits stay as they are) ---------- */

const noteForm = document.querySelector("[data-note-form]");
noteForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  e.stopPropagation(); // not app.js's: it would go to the page again
  const button = noteForm.querySelector("button[type=submit]");
  const body = new FormData(noteForm);
  body.append("inline", "1");
  if (button) button.disabled = true;
  const answer = await post(noteForm.action, body);
  if (button) button.disabled = false;
  if (!answer.ok || typeof answer.html !== "string") {
    follow(answer);
    return;
  }
  const list = document.querySelector("[data-notes]");
  if (list) list.innerHTML = answer.html; // drawn by the server, every text escaped
  const count = document.querySelector("[data-notes-count]");
  if (count) count.textContent = answer.count ? ` · ${answer.count}` : "";
  noteForm.reset();
  if (answer.toast) toast(answer.toast, 3000);
  noteForm.querySelector("input[type=text]")?.focus();
});

/* ---------- Start ---------- */

showMailLanguage();
fillSplitLines();
showVendorMatch();
for (const name of absentList()) viewer?.setEdited(name, "", true); // marked before: the Fields list says so
lastSums = tickedSums(); // what the tax lines follow from now on (B-610)
// The queue shows what this screen shows: check once on opening (today's checks, and any draft kept).
if (form) check();
