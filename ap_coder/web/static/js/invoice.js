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
   * Right-click a field's name (or Shift+F10 in its box), a line, a tax line or the page: a menu of what to do with it
     (ctxmenu.js, D-60; inside a text box a right-click is the browser's own, with Paste). Deleting, clearing, splitting and the like can be undone: the message says Undo, and Ctrl+Z (not in a
     text box of another form) does the same. Only the last change is kept to undo, until something else is edited.

   Everything from the server is either HTML it escaped (the sections, the rows) or set as text here.
*/

import { HEADERS, follow, post, toast } from "./app.js";
import { openFor, register } from "./ctxmenu.js";
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

function applySections(answer, posted = null) {
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
  showBarState();
  // the lines' tax codes where the invoice is supplied now, and the lines the Tax check points at (D-64)
  if (Array.isArray(answer.tax_codes)) setTaxCodeOptions(answer.tax_codes, answer.tax_code_labels);
  titleTaxCodes();
  if (answer.tax_marks && typeof answer.tax_marks === "object") showTaxMarks(answer.tax_marks);
  // the accounts of the vendor now in the vendor field: the account lists' "Used for this vendor" (B-596)
  if (Array.isArray(answer.vendor_accounts)) vendorAccounts = answer.vendor_accounts;
  if (answer.header) applyHeader(answer.header, posted);
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
      const body = formData();
      const answer = await post(`${base}/check`, body);
      lastCheckOk = Boolean(answer.ok);
      lastDraft = Boolean(answer.draft); // kept as a draft (an edit of the coding), not a tick of "Approve anyway"
      if (answer.ok) applySections(answer, body);
      else if (answer.error) toast({ text: answer.error, tone: "err" }, 9000);
    } while (queued);
  })();
  if (checking) checking.textContent = "Checking…";
  let saved = false;
  try {
    await running;
    saved = lastCheckOk && lastDraft;
  } finally {
    running = null;
    keepFocusInView(); // the sections drawn again may have moved the box being worked in under the approve bar
    // Every edit is kept as a draft on the server as it is checked: say so, so leaving the page is no worry (only
    // when one is kept, B-825).
    if (checking) checking.textContent = saved && edited ? `Draft saved ${clock()}` : "";
    if (checking) checking.title = saved && edited ? "Your edits are kept: leave and come back, they are still here. Approving records them." : "";
    checking?.classList.toggle("saved", saved && edited); // gives way to "Not ready" on the bar (B-820)
  }
}

/** The box with the focus, moved by sections drawn above it, is brought back into view (above the approve bar). */
function keepFocusInView() {
  const el = document.activeElement;
  if (!el || el === document.body || el.tagName === "MAIN" || !el.closest?.(".main")) return;
  const r = el.getBoundingClientRect();
  const bar = document.querySelector(".inv-actionbar")?.getBoundingClientRect().top ?? innerHeight;
  if (r.bottom > bar || r.top < 0) el.scrollIntoView({ block: "nearest" });
}

let lastCheckOk = false; // the last check answered (its edits are kept as a draft)
let lastDraft = false; // the last check kept the edits as a draft
let edited = false; // an edit made on this page (the opening check keeps nothing new)

function clock() {
  const d = new Date();
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function soon(ms = 450) {
  edited = true;
  followTicks(); // every edit comes here: a tax line's taxable follows the lines whose tax code carries it (B-610)
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
  // (Alt+Enter is the line's own key, "insert a line below": left to it, B-822)
  if (e.key === "Enter" && !e.ctrlKey && !e.metaKey && !e.altKey && e.target.matches?.("textarea[data-one-line]")) e.preventDefault();
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
    if (e.target.matches("select[data-tax-code]")) confirmTaxCode(e.target.closest("tr")); // theirs now (B-713)
    if (e.target === vendorInput) vendorChanged(); // the vendor list's tax numbers, with Undo (D-65)
    if (e.target.name === "pay_from_account" && payFromFor) {  // picked: kept while the currency stays (D-66)
      payFromFor.value = (input("currency")?.value || "CAD").trim().toUpperCase() || "CAD";
    }
    if (e.target.name === "line_gl") {  // a guessed account the reviewer picked again is no longer a guess
      const cell = e.target.closest(".is-guess");
      if (cell) { cell.classList.remove("is-guess"); cell.querySelector(".inv-guess")?.remove(); }
    }
    soon(e.target.tagName === "SELECT" || e.target.type === "checkbox" ? 0 : 200);
  });
}

/* Another form or a link while edits wait to be checked: check (and keep) them first. A form already waiting is not
   sent again by a second Enter or click meanwhile (B-625). */
let passing = null;
const waiting = new WeakSet();
document.addEventListener(
  "submit",
  (e) => {
    const f = e.target;
    if (waiting.has(f)) {
      e.preventDefault();
      e.stopImmediatePropagation();
      return;
    }
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
    waiting.add(f);
    flush().then(() => {
      waiting.delete(f);
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
    // a new line: its description, where it starts (B-742); a tax line: its tax
    const fresh = body.lastElementChild;
    (fresh?.querySelector('[name="line_description"]') || fresh?.querySelector("input:not([type=hidden]), select"))?.focus();
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
  showTaxMarks(taxMarks);
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

/** "Red River Office Interiors’" and "Pacific Office Supply Ltd.’s": never "Interiors's" (B-740). */
export function possessive(name) {
  const text = String(name || "").trim();
  return /s$/i.test(text) ? `${text}’` : `${text}’s`;
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
  const sure = vendorInput.closest("[data-field]")?.querySelector(".lbl .inv-sure");
  const flagged = vendorInput.closest("[data-field]")?.classList.contains("is-flagged");
  if (sure) sure.hidden = sure.dataset.dropped === "1" || flagged || (!found && vendors.enforced && vendors.list.length > 0);
  if (found) {
    const an8 = vendorMatch.dataset.an8;
    // JD Edwards in use: the supplier number it gets (AN8), said as such
    const valid = /^\d{1,8}$/.test(an8 || "");
    const id = an8 !== undefined ? (valid ? ` · AN8 ${an8}` : an8 ? ` · ${an8}, not an AN8` : " · no AN8")
      : found.id ? ` · ${found.id}` : "";
    if (found.added) vendorLine("person_add", "warn", `Added here, not in JD Edwards yet${id}`);
    else if (!vendors.enforced) vendorLine("history", "ok", "Approved before"); // no vendor master: a past vendor
    // in the list, but its number is no AN8 (JD Edwards not set up for it): plain, the error under it says why (B-827)
    else if (an8 !== undefined && !valid) vendorLine("info", "", `In the vendor list${id}`);
    else vendorLine("verified", "ok", `In the vendor list${id}`);
    return;
  }
  // (a number filled from the vendor list is that vendor's: it says nothing about who sent the invoice)
  const filled = "gst_hst_registration_number" in (fromVendor().fields || {});
  const bn = filled ? "" : businessNumber(input("gst_hst_registration_number")?.value);
  const byNumber = bn ? vendors.list.find((v) => businessNumber(v.gst) === bn) : null;
  if (byNumber) {
    // good to know (as the checks say it), not a warning (B-827)
    vendorLine("info", "info", `The GST/HST number printed is ${possessive(byNumber.name)}.`, {
      label: `Use ${byNumber.name}`,
      run: () => setVendor(byNumber.name),
    });
    return;
  }
  // Without a vendor master the list is only the vendors approved before: a new one is no mistake, so say nothing;
  // and the check's own note under the field says it already (B-796): said once.
  const note = vendorInput.closest("[data-field]")?.querySelector(":scope > [data-flag]:not([hidden]) [data-flag-text]");
  if (!vendors.list.length || !vendors.enforced || /vendor list/i.test(note?.textContent || "")) {
    vendorMatch.replaceChildren();
    vendorMatch.className = "inv-vendor-match small";
    return;
  }
  vendorLine("gpp_maybe", "warn", typed ? "Not in the vendor list: pick one, or add it as a new vendor." : "Pick the vendor from the list.");
}

function setVendor(name) {
  vendorInput.value = name;
  showVendorMatch();
  vendorChanged();
  soon(0);
}

/* ---------- The vendor list's tax numbers (D-65) ----------
   A vendor of the vendor list fills each blank GST/HST, QST and PST number with its own, and says so under the field
   ("From the vendor list — not printed on the invoice"); a number filled from the vendor follows it when the vendor
   changes. A number read from the invoice or typed is never touched. The server applies the same rule on every check
   (invoice_view.fill_from_vendor), so a vendor typed in is filled too; here it is at once, with Undo. */

const TAX_FIELDS = { gst_hst_registration_number: "gst", qst_registration_number: "qst", pst_registration_number: "pst" };
const fromVendorBox = document.querySelector("input[data-from-vendor]");
const payFromFor = document.querySelector("input[data-pay-from-for]");

function fromVendor() {
  try {
    const state = JSON.parse(fromVendorBox?.value || "{}");
    return state && typeof state === "object" ? state : {};
  } catch {
    return {};
  }
}

/** The fields still holding the number filled from the vendor (one changed or cleared since is the reviewer's). */
function stillFilled(state) {
  const fields = {};
  for (const [name, value] of Object.entries(state.fields || {})) {
    if (name in TAX_FIELDS && value && (input(name)?.value || "").trim() === value) fields[name] = value;
  }
  return fields;
}

function setFromVendor(state) {
  if (fromVendorBox) fromVendorBox.value = JSON.stringify(state);
  showFromVendor();
}

function showFromVendor() {
  const fields = fromVendor().fields || {};
  document.querySelectorAll(".inv-details [data-from-vendor-note]").forEach((note) => {
    const field = note.closest("[data-field]");
    note.hidden = !(field?.dataset.field in fields);
    // how sure the reading was says nothing of a number that came from the vendor list
    const sure = field?.querySelector(".lbl > .inv-sure");
    if (sure) sure.hidden = !note.hidden;
  });
}

let vendorBefore = vendorInput?.value ?? ""; // the vendor as it was before the change being made (Undo's)

/** The vendor field changed (picked, or left after typing): fill from the vendor list, with Undo. */
function vendorChanged() {
  if (!vendorInput || !fromVendorBox) return;
  const key = vendorKey(vendorInput.value);
  const state = fromVendor();
  const fields = stillFilled(state);
  const before = vendorBefore;
  vendorBefore = vendorInput.value;
  if ("vendor" in state && state.vendor === key) return setFromVendor({ vendor: key, fields });
  // Undo puts back the vendor with its numbers, together (B-682): the screen as it was before the vendor changed.
  const snap = snapshot();
  snap.header = snap.header.map(([n, v]) => [n, n === "vendor_name" ? before : v]);
  const found = key ? vendors.list.find((v) => (v.key ?? vendorKey(v.name)) === key) : null;
  const absent = absentList();
  // only a number the reading left blank (not printed): one the clerk cleared is the clerk's to type (B-688)
  let unread = null;
  try {
    unread = JSON.parse(fromVendorBox.dataset.unread || "null");
  } catch {
    unread = null;
  }
  const changed = [];
  for (const [name, column] of Object.entries(TAX_FIELDS)) {
    const el = input(name);
    if (!el) continue;
    const number = String(found?.[column] || "").trim();
    const current = el.value.trim();
    if (name in fields) {
      if (number !== current) {
        el.value = number;
        changed.push(name);
      }
      if (number) fields[name] = number;
      else delete fields[name];
    } else if (!current && number && !absent.includes(name) && (!Array.isArray(unread) || unread.includes(name))) {
      el.value = fields[name] = number;
      changed.push(name);
    }
  }
  setFromVendor({ vendor: key, fields });
  if (!changed.length) return;
  showVendorMatch();
  for (const name of changed) viewer?.setEdited(name, input(name).value.trim(), false);
  offerUndo({ text: `${changed.length === 1 ? "Tax number" : "Tax numbers"} filled from the vendor list.`, tone: "info", icon: "menu_book" }, snap);
}


/** What the server filled in (the vendor list's numbers, the default Pay from account) and the details its checks are
    about (amber, with a note), after a check. A value is put in only where the box still holds what was sent. */
function applyHeader(header, posted) {
  const sent = (name) => (posted ? posted.get(name) ?? "" : null);
  for (const [name, value] of Object.entries(header.values || {})) {
    const el = input(name);
    if (!el || (sent(name) !== null && el.value !== sent(name))) continue;
    el.value = value;
    if (name in TAX_FIELDS) viewer?.setEdited(name, value.trim(), false);
  }
  if (fromVendorBox && header.from_vendor && (sent("from_vendor") === null || fromVendorBox.value === sent("from_vendor"))) {
    fromVendorBox.value = JSON.stringify(header.from_vendor);
  }
  if (payFromFor && typeof header.pay_from_for === "string" && (sent("pay_from_for") === null || payFromFor.value === sent("pay_from_for"))) {
    payFromFor.value = header.pay_from_for;
  }
  showFromVendor();
  if (header.flags) showFlags(header.flags, header.flag_errors || []);
  if (header.help) showHelp(header.help);
  if (vendorMatch && "an8" in header) {
    if (header.an8 === null || header.an8 === undefined) delete vendorMatch.dataset.an8;
    else vendorMatch.dataset.an8 = header.an8;
    showVendorMatch();
  }
}

/** A note with a one-click fix under a field (which of two dates, an open PO, a value boxed on the page), from the
    server's ``help``: {field: {text, actions: [{label, value}]}}. */
function showHelp(help) {
  document.querySelectorAll(".inv-details [data-field]").forEach((field) => {
    const box = field.querySelector(":scope > [data-help]");
    if (!box) return;
    const h = help[field.dataset.field];
    box.hidden = !h;
    box.querySelectorAll("[data-help-use]").forEach((b) => b.remove());
    const text = box.querySelector("[data-help-text]");
    if (text) text.textContent = h ? h.text : "";
    for (const a of (h && h.actions) || []) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn sm ghost";
      b.dataset.helpUse = a.value;
      b.textContent = a.label;
      box.append(b);
    }
  });
}

/* A help note's button puts its value in the field (with Undo), and the focus stays in that field. */
document.addEventListener("click", (e) => {
  const use = e.target.closest?.("[data-help-use]");
  if (!use) return;
  e.preventDefault(); // a button inside the field's <label>: not a click on the box
  const field = use.closest("[data-field]");
  const name = field?.dataset.field;
  const el = name && input(name);
  if (!el) return;
  const value = use.dataset.helpUse;
  undoable(`${fieldName(name)}: ${value} put in.`, () => {
    el.value = value;
    setAbsent(name, false);
    headerEdited(el);
    viewer?.setEdited(name, value, false);
    use.closest("[data-help]").hidden = true;
    if (name === "vendor_name") showVendorMatch();
    soon(0);
  });
  el.focus();
});

/** The invoice details a check is about: amber, with its note under it (the checks list them too); red when the
    check is an error, as in "Must be fixed" (``errors``: those details, B-796). */
function showFlags(flags, errors = []) {
  document.querySelectorAll(".inv-details [data-field]").forEach((field) => {
    const note = flags[field.dataset.field] || "";
    field.classList.toggle("is-flagged", Boolean(note));
    field.classList.toggle("is-error", Boolean(note) && errors.includes(field.dataset.field));
    // "Verified" never sits beside what a check says is wrong with the field (B-740)
    const sure = field.querySelector(".lbl .inv-sure");
    if (sure && note) sure.hidden = true;
    const box = field.querySelector(":scope > [data-flag]");
    if (!box) return;
    box.hidden = !note;
    const text = box.querySelector("[data-flag-text]");
    if (text) text.textContent = note;
  });
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

/* ---------- A quantity-1 line: its unit price follows a changed amount when it was blank, 0 or the amount (B-671) ---------- */

linesGrid?.addEventListener("focusin", (e) => {
  if (e.target.name === "line_amount") e.target.dataset.before = e.target.value;
});
linesGrid?.addEventListener("change", (e) => {
  if (e.target.name !== "line_amount") return;
  const tr = e.target.closest("tr");
  const qty = tr?.querySelector("[name=line_quantity]");
  const price = tr?.querySelector("[name=line_unit_price]");
  const before = e.target.dataset.before ?? "";
  e.target.dataset.before = e.target.value;
  if (!qty || !price || Number(qty.value) !== 1) return;
  const was = Number(price.value);
  if (price.value === "" || was === 0 || (before !== "" && Math.abs(was - Number(before)) < 0.005)) {
    price.value = e.target.value;
    price.dispatchEvent(new Event("input", { bubbles: true }));
  }
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
      else {
        toast({ text: `Not ready to approve: ${notReady()}`, tone: "warn" }); // the reason, not "see the bar" (B-743)
        focusNotReady();
      }
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
      (el.type !== "hidden" || ["taught", "absent", "from_vendor", "pay_from_for"].includes(el.name)),
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
    focus: focusKey(document.activeElement),
  };
}

/** Where the focus is, so Undo can put it back there: a box of the form by name (and its row). */
function focusKey(el) {
  if (!el || el === document.body || el.tagName === "MAIN") return null;
  const tr = el.closest?.("[data-grid] > tr");
  const box = el.closest?.(".pk")?.querySelector("select") || el;
  const name = box.name || (tr ? "line_description" : "");
  if (!name) return null;
  return { row: tr?.dataset.row || "", grid: tr?.closest("[data-grid]")?.dataset.grid || "", name };
}

function focusBack(key) {
  if (!key) return false;
  let scope = document;
  if (key.grid) {
    scope = [...document.querySelectorAll(`[data-grid="${key.grid}"] > tr`)].find((tr) => tr.dataset.row === key.row);
    if (!scope) return false;
  }
  const el = key.grid ? scope.querySelector(`[name="${CSS.escape(key.name)}"]`) : input(key.name);
  const target = el?.classList.contains("pk-native") ? el.closest(".pk")?.querySelector(".pk-input") : el;
  if (!target || !target.getClientRects().length) return false;
  target.focus({ preventScroll: true });
  target.scrollIntoView({ block: "nearest" });
  return document.activeElement === target;
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
    else if (el.matches("select[data-tax-code]")) setTaxCode(el, value);
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
  showFromVendor();
  if (vendorInput) vendorBefore = vendorInput.value;
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
  focusBack(snap.focus) || focusFirstToCheck(true); // the focus where it was before the change (B-742)
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
// The invoice detail a word double-clicked on the page fills (B-621): the one being typed in, or the one the clerk last
// clicked or typed in within ARMED_MS (nothing outside the page clicked since). The time is that of the clerk's own
// use of the box (focus, a key, an edit), never of leaving it: the double-click's first click takes the focus from
// it, and that moment is not "just used" (B-627). window.AP_ARMED_MS shortens it for the tests.
const ARMED_MS = 5000;
let lastField = "";
let lastFieldAt = 0;

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
  // The reading's "Verified" / "Likely 97%" vouched for what was read, not for this: it goes (B-740).
  const sure = label.querySelector(".lbl .inv-sure");
  if (sure) {
    sure.hidden = true;
    sure.dataset.dropped = "1";
  }
  if (el.name in TAX_FIELDS) setFromVendor({ ...fromVendor(), fields: stillFilled(fromVendor()) }); // typed over: theirs
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
  if (name in TAX_FIELDS) setFromVendor({ ...fromVendor(), fields: stillFilled(fromVendor()) });
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

function headerBox(el) {
  const label = el?.closest?.(".inv-details [data-field]");
  return label && TEACHABLE.has(label.dataset.field) && el.name === label.dataset.field ? label.dataset.field : "";
}

for (const type of ["focusin", "keydown", "input"]) {
  document.addEventListener(type, (e) => {
    const name = headerBox(e.target);
    if (!name) return;
    lastField = name;
    lastFieldAt = Date.now();
  });
}
document.addEventListener(
  "pointerdown",
  (e) => {
    if (!e.target.closest?.(".iv-sheet") && !headerBox(e.target)) lastField = ""; // something else chosen since
  },
  true,
);

function armedField() {
  // Focused long ago and nothing typed since (still focused or not): not the detail meant.
  return lastField && Date.now() - lastFieldAt < (window.AP_ARMED_MS || ARMED_MS) ? lastField : "";
}

/** Enter in an invoice detail goes to the next one, Shift+Enter to the one before (Enter never approves). */
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
  const el = e.target;
  if (!el.matches?.('.inv-details input[form="inv"], .inv-details select[form="inv"]') || el.type === "hidden") return;
  // Only boxes that can take the focus: not disabled, shown, not folded away in a closed <details> (Place of supply,
  // B-623); and if one still refuses it, the one after.
  const boxes = [...document.querySelectorAll('.inv-details input[form="inv"]:not([type=hidden]), .inv-details select[form="inv"]')]
    .filter((b) => b === el || (!b.disabled && b.getClientRects().length && !b.closest("details:not([open])")));
  const step = e.shiftKey ? -1 : 1;
  e.preventDefault();
  for (let i = boxes.indexOf(el) + step; i >= 0 && i < boxes.length; i += step) {
    boxes[i].focus();
    if (document.activeElement === boxes[i]) {
      boxes[i].select?.();
      break;
    }
  }
});

/* ---------- A word double-clicked on the page fills the detail focused last ---------- */

async function fillFromWord(event) {
  if (!viewer || event.field) return; // a field's box: its click showed that field; it fills nothing
  const field = armedField();
  if (!field) {
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
  const name = fieldName(field);
  if (event.line && !confirm(`“${word.t}” is in a line item. Use it for ${name}?`)) return;
  const el = input(field);
  if (el?.value.trim() && detail(field)?.querySelector(".inv-sure.ok") &&
      !confirm(`${name} is verified (“${el.value.trim()}”). Replace it with “${word.t}”?`)) return;
  await onViewerEvent({ type: "assign", field, text: word.t, boxes: [boxOf(word)] });
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
    // the focus to the line that took its place (its description), else the button that adds one (B-742)
    const to = next?.querySelector('[name="line_description"]') || next?.querySelector("input:not([type=hidden]), textarea, select") ||
      document.querySelector(`[data-add-row="${which}"]`);
    to?.focus({ preventScroll: true });
    to?.scrollIntoView({ block: "nearest" });
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

function copyAbove(tr) {
  const rows = gridRows();
  const above = rows[rows.indexOf(tr) - 1];
  if (!above || blank(above.querySelector('[name="line_gl"]'))) {
    toast({ text: above ? "The line above has no GL account yet." : "It is the first line: there is none above.", tone: "info" }, 3000);
    return;
  }
  const what = tr.querySelector('[name="line_cc"]') ? "GL account and cost center" : "GL account";
  undoable(`${lineName(tr)}: ${what} copied from the line above.`, () => {
    setGl(tr, above.querySelector('[name="line_gl"]').value, above.querySelector('[name="line_cc"]')?.value);
    soon(0);
  });
}

function insertBelow(tr) {
  return undoable("Line added.", () => {
    const row = makeRow("lines", { key: newKey(), values: {}, taxes: [] }, tr);
    numberNew(row);
    fillSplitLines();
    row.querySelector('[name="line_description"]')?.focus();
    soon(0);
  });
}

/** A line's menu (B-741): grouped (its coding, its tax code, the line itself), what can't be done left out rather than
    greyed, and Delete last, apart, with its key. */
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
  const what = cc ? "coding" : "GL account";
  const taxItems = taxCodeItems(tr, above, others).filter((it) => it && !it.disabled);
  const confirm = taxItems.find((it) => it.id === "tax-confirm");
  const moreTax = taxItems.filter((it) => it !== confirm);
  return [
    suggested ? { icon: "add_task", label: `Use the suggested account (${suggested.dataset.gl})`, id: "suggested", run: () => suggested.click() } : null,
    above && !blank(above.querySelector('[name="line_gl"]')) ? { icon: "vertical_align_top", label: `Copy the ${what} from above`, hint: "Ctrl+D", id: "above", run: () => copyAbove(tr) } : null,
    !blank(gl) && without.length ? { icon: "playlist_add_check", label: `Use GL ${gl.value} on the ${without.length === 1 ? "line" : `${without.length} lines`} without one`, id: "fill",
      run: () => undoable(`GL ${gl.value} put on ${without.length === 1 ? "1 line" : `${without.length} lines`}.`, () => {
        without.forEach((r) => setGl(r, gl.value));
        soon(0);
      }) } : null,
    !blank(gl) ? { icon: "backspace", label: "Clear GL account", id: "clear-gl",
      run: () => undoable(`${lineName(tr)}: GL account cleared.`, () => {
        setGl(tr, "UNASSIGNED");
        soon(0);
      }) } : null,
    "-",
    confirm || null,
    moreTax.length ? { icon: "percent", label: "Tax code", id: "tax", items: moreTax } : null,
    "-",
    { icon: "add_row_below", label: "Insert a line below", hint: "Alt+Enter", id: "insert", run: () => insertBelow(tr) },
    { icon: "control_point_duplicate", label: "Duplicate line", id: "duplicate", run: () => undoable(`${lineName(tr)} duplicated.`, () => {
      const copy = makeRow("lines", { ...rowState(tr), key: newKey() }, tr);
      numberNew(copy);
      fillSplitLines();
      copy.querySelector('[name="line_description"]')?.focus();
      soon(0);
    }) },
    n ? { icon: "call_split", label: "Split this line…", id: "split", run: () => openSplit(tr) } : null,
    "-",
    { icon: "delete", label: "Delete line", hint: "Alt+Del", id: "delete", danger: true, run: () => deleteRow(tr) },
  ];
}

/* A line's keys, in any of its boxes: Ctrl+D the coding of the line above (as Excel fills down), Alt+Enter a line
   below, Alt+Delete deletes it (Ctrl+Z puts it back). */
document.addEventListener("keydown", (e) => {
  const tr = e.target.closest?.('[data-grid="lines"] > tr');
  if (!tr || e.defaultPrevented || e.metaKey) return;
  if (e.ctrlKey && !e.altKey && !e.shiftKey && (e.key === "d" || e.key === "D")) {
    e.preventDefault(); // never the browser's bookmark
    copyAbove(tr);
  } else if (e.altKey && !e.ctrlKey && !e.shiftKey && e.key === "Enter") {
    e.preventDefault();
    insertBelow(tr);
  } else if (e.altKey && !e.ctrlKey && !e.shiftKey && e.key === "Delete") {
    e.preventDefault();
    deleteRow(tr);
  }
});

/* ---------- The tax lines ---------- */

function num(el) {
  const text = (el?.value || "").trim();
  const v = Number(text.replace(/[,$\s]/g, ""));
  return text !== "" && Number.isFinite(v) ? v : null;
}

/* A tax line's taxable amount follows the lines whose tax code carries its tax (B-610, D-64): changing a line's tax
   code ("GST + PST" to "GST only"), changing a marked line's amount, deleting or adding one (every edit goes through
   soon()) updates the PST line's taxable amount (and its amount, at its rate) while it still is the sum of the lines
   it was; a taxable amount the clerk typed, or left blank, is left alone (B-622), and the tax line's menu offers
   "Taxable from the lines marked PST". */

const TAX_TYPES = ["GST", "HST", "PST", "QST", "OTHER"];

/** The taxes a line's tax code charges: "GST+PST" is GST and PST, "GST~PST" GST alone; "EXEMPT", "ZERO", "OUT" none. */
function lineTaxes(tr) {
  const code = tr.querySelector("select[data-tax-code]")?.value || "";
  const ticked = [...tr.querySelectorAll("input[type=checkbox]:checked")].map((c) => c.value); // a page of before
  // "GST~PST": GST charged, PST exempt (D-64): only the part before "~" is charged
  return [...code.split("~")[0].split("+").filter((t) => TAX_TYPES.includes(t)), ...ticked.filter((t) => TAX_TYPES.includes(t))];
}

function tickedSums() {
  const sums = {};
  for (const tr of gridRows()) {
    const amount = num(tr.querySelector('[name="line_amount"]'));
    if (amount === null) continue;
    for (const t of new Set(lineTaxes(tr))) sums[t] = (sums[t] || 0) + amount;
  }
  for (const k of Object.keys(sums)) sums[k] = Math.round(sums[k] * 100) / 100;
  return sums;
}

/* ---------- The lines' tax codes (D-64) ---------- */

let taxLabels = {};
try {
  taxLabels = JSON.parse(document.getElementById("tax-code-labels")?.textContent || "{}");
} catch {
  taxLabels = {};
}
let taxMarks = {}; // {line number: why}: the lines the Tax check points at, drawn yellow

/** Put ``code`` in a line's tax-code dropdown, adding it when the place of supply doesn't list it (kept as it is). */
function setTaxCode(select, code) {
  if (!select || !code) return;
  if (![...select.options].some((o) => o.value === code)) {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = taxLabels[code] || code;
    select.append(option);
  }
  select.value = code;
}

/** The dropdowns' choices where the invoice is supplied now: each line keeps its own code, listed or not. */
function setTaxCodeOptions(options, labels) {
  if (labels && typeof labels === "object") taxLabels = labels;
  const fill = (select) => {
    const keep = select.value;
    const now = [...select.options].map((o) => [o.value, o.textContent]);
    const want = options.map(([code, label]) => [String(code), String(label)]);
    if (keep && !want.some(([code]) => code === keep)) want.push([keep, taxLabels[keep] || keep]);
    if (JSON.stringify(now) === JSON.stringify(want)) return;
    select.replaceChildren(...want.map(([code, label]) => {
      const option = document.createElement("option");
      option.value = code;
      option.textContent = label;
      return option;
    }));
    select.value = keep;
  };
  document.querySelectorAll('[data-grid="lines"] select[data-tax-code]').forEach(fill);
  const tpl = document.getElementById("line-template");
  tpl?.content.querySelectorAll("select[data-tax-code]").forEach((select) => {
    const keep = select.value;
    fill(select);
    if (!keep) select.selectedIndex = 0;
  });
}

/** Draw yellow the tax-code cells of the lines the Tax check points at (``marks``: {line number: why}). */
function showTaxMarks(marks) {
  taxMarks = marks || {};
  for (const tr of gridRows()) {
    const cell = tr.querySelector(".c-tax");
    if (!cell) continue;
    const why = taxMarks[lineNo(tr)] || (cell.dataset.taxGuess ? cell.dataset.taxGuess : "");
    cell.classList.toggle("is-taxcheck", Boolean(why));
    if (why) cell.title = why;
    else cell.removeAttribute("title");
    const note = cell.querySelector("[data-tax-note]");
    if (note) {
      note.hidden = !why;
      const text = note.querySelector("[data-tax-note-text]");
      if (text) text.textContent = why;
    }
  }
}

const GUESS_NOTE = "Tax code worked out from the tax charged: check it."; // line_tax.GUESS_NOTE
const LEARNED_NOTE = "Tax code learned from past approvals of this vendor."; // line_tax.LEARNED_NOTE
const INFERRED = /\b(PST|RST) exemption inferred \(charged on the other lines, not this one\): confirm it\./; // line_tax._INFERRED
const CONFIRMED = /\b(PST|RST) exemption confirmed on review\./; // line_tax._CONFIRMED_RE

/** The clerk picked or confirmed a line's tax code: no longer marked to check, and its note goes from the line's
    reasoning (posted with it), so the approved coding does not keep it (B-713). */
function confirmTaxCode(tr) {
  const cell = tr?.querySelector(".c-tax");
  if (!cell) return;
  delete cell.dataset.taxGuess;
  cell.querySelector("[data-tax-learned]")?.remove(); // the clerk's own pick now, not the history's
  const reason = tr.querySelector('[name="line_reason"]');
  if (reason) {
    // line_tax.confirm_line: the "check it" note goes; a PST exemption inferred and still in the code is recorded as
    // confirmed on review (B-715), and goes when the code no longer exempts it.
    // The inference is kept on the row, so picking the exemption again after another code is still confirmed (B-723).
    let text = reason.value.replace(GUESS_NOTE, " ").replace(LEARNED_NOTE, " ");
    const code = tr.querySelector("select[data-tax-code]")?.value || "";
    const still = (code.split("~")[1] || "").split("+").includes("PST");
    const found = text.match(INFERRED) || text.match(CONFIRMED);
    if (text.match(INFERRED)) tr.dataset.taxInferred = text.match(INFERRED)[1];
    if (found) text = text.replace(found[0], still ? `${found[1]} exemption confirmed on review.` : " ");
    else if (still && tr.dataset.taxInferred) text = `${text} ${tr.dataset.taxInferred} exemption confirmed on review.`;
    reason.value = text.replace(/\s+/g, " ").trim();
  }
  showTaxMarks(taxMarks);
}

/** The tax-code items of a line's menu: confirm a worked-out code, copy the line above's, put this one on every line,
    mark it exempt. */
function taxCodeItems(tr, above, others) {
  const select = tr.querySelector("select[data-tax-code]");
  if (!select) return [];
  const code = select.value;
  const label = select.selectedOptions[0]?.textContent || code;
  const aboveCode = above?.querySelector("select[data-tax-code]")?.value || "";
  const set = (row, value) => {
    setTaxCode(row.querySelector("select[data-tax-code]"), value);
    confirmTaxCode(row); // the clerk's choice now
  };
  const guessed = Boolean(tr.querySelector(".c-tax")?.dataset.taxGuess) ||
    INFERRED.test(tr.querySelector('[name="line_reason"]')?.value || "");
  return [
    guessed ? { icon: "task_alt", label: `Confirm the tax code (${label})`, id: "tax-confirm",
      run: () => undoable(`${lineName(tr)}: tax code confirmed.`, () => {
        confirmTaxCode(tr);
        soon(0);
      }) } : null,
    { icon: "vertical_align_top", label: "Copy the tax code from the line above", id: "tax-above",
      disabled: !above ? "It is the first line" : aboveCode === code ? "It has that code already" : false,
      run: () => undoable(`${lineName(tr)}: tax code copied from the line above.`, () => {
        set(tr, aboveCode);
        soon(0);
      }) },
    { icon: "block", label: "Mark this line exempt", id: "tax-exempt", disabled: code === "EXEMPT" ? "It is exempt already" : false,
      run: () => undoable(`${lineName(tr)}: marked exempt.`, () => {
        set(tr, "EXEMPT");
        soon(0);
      }) },
    { icon: "done_all", label: `Use “${label}” on every line`, id: "tax-all",
      disabled: !others.length ? "It is the only line" : others.every((r) => r.querySelector("select[data-tax-code]")?.value === code) ? "Every line has it already" : false,
      run: () => undoable(`Tax code “${label}” put on every line.`, () => {
        others.forEach((r) => set(r, code));
        soon(0);
      }) },
  ];
}

let lastSums = null; // the sums the tax lines were last made to follow (set at the start)

function same(a, b) {
  return a !== null && b !== null && Math.abs(a - b) < 0.005;
}

/** Put ``taxable`` on a tax line. Its amount is what the invoice charges, never changed by the lines' tax codes
    (B-700): the Tax check then says when the lines marked and the tax charged disagree. */
function setTaxable(tr, taxable) {
  tr.querySelector('[name="tax_taxable"]').value = taxable.toFixed(2);
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
    // Only a tax line that was the sum follows it: a blank one (just added) stays blank, never a second copy (B-622).
    if (!kind || same(before, after) || !same(taxable, before)) continue;
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
    { icon: "sync", label: `Taxable from the lines marked ${label}: ${sum.toFixed(2)}`, id: "sync",
      disabled: !sum ? `No line is marked ${label}` : same(num(tr.querySelector('[name="tax_taxable"]')), sum) ? "It is that already" : false,
      run: () => undoable(`Taxable amount set from the lines marked ${label}: ${sum.toFixed(2)}.`, () => {
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
  if (at && !viewer.teach && form) {
    try {
      viewer.setWords(await loadWords());
      word = viewer.wordAt(at.x, at.y);
    } catch {
      word = null; // the words can't be read: the zoom and the turn still work
    }
  }
  const fields = headerTargets();
  // an invoice not being reviewed (approved, rejected…): the page's own items only, nothing to teach (B-829)
  const teach = !form
    ? []
    : viewer.teach
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

/* ---------- The posting, the Tax check and the checks: a menu each (B-798) ---------- */

/** "9,200.00" as it pastes into another program: 9200.00 (a credit -2050.00). */
function plainAmount(text) {
  return String(text || "").replace(/,/g, "").trim();
}

/** Focus a line's box (its account picker's text box when it has one), in view. */
function focusLineBox(tr, name) {
  const box = tr?.querySelector(`[form="inv"][name="${CSS.escape(name)}"]`);
  if (!box) return false;
  const target = box.closest(".pk")?.querySelector(".pk-input") || box;
  target.scrollIntoView({ block: "center", behavior: "smooth" });
  target.focus({ preventScroll: true });
  return true;
}

function lineRowByNumber(number) {
  return gridRows().find((tr) => lineNo(tr) === String(number));
}

/** An invoice detail's box, focused in view. */
function goToField(name) {
  const box = input(name);
  if (!box || !box.getClientRects().length) return false;
  const target = box.closest(".pk")?.querySelector(".pk-input") || box;
  target.scrollIntoView({ block: "center", behavior: "smooth" });
  target.focus({ preventScroll: true });
  return true;
}

function postingMenu(tr) {
  const line = tr.dataset.postLine;
  const amount = plainAmount(tr.dataset.amount);
  const gl = tr.dataset.gl;
  const firstTax = document.querySelector('[data-grid="taxes"] [name="tax_amount"]');
  return [
    line ? { icon: "arrow_downward", label: `Go to line ${line}`, id: "goto", run: () => focusLineBox(lineRowByNumber(line), "line_gl") }
      : firstTax ? { icon: "arrow_downward", label: "Go to the tax lines", id: "goto", run: () => {
        firstTax.scrollIntoView({ block: "center", behavior: "smooth" });
        firstTax.focus({ preventScroll: true });
      } } : null,
    "-",
    amount ? { icon: "content_copy", label: `Copy the amount (${amount})`, id: "copy-amount", run: () => copyText(amount) } : null,
    gl && gl !== "UNASSIGNED" ? { icon: "content_copy", label: `Copy the GL account (${gl})`, id: "copy-gl", run: () => copyText(gl) } : null,
    tr.dataset.ani ? { icon: "content_copy", label: `Copy the JDE account (${tr.dataset.ani})`, id: "copy-ani", run: () => copyText(tr.dataset.ani) } : null,
  ];
}

/** The lines whose tax code carries ``type`` (GST, PST…). */
function linesMarked(type) {
  return gridRows().filter((tr) => lineTaxes(tr).includes(type));
}

function taxCheckMenu(tr) {
  const row = tr.dataset.taxRow || "";
  const type = tr.dataset.taxType || "";
  const diff = plainAmount(tr.dataset.diff);
  const expected = plainAmount(tr.dataset.expected);
  const marked = type ? linesMarked(type) : [];
  const taxLine = type ? [...document.querySelectorAll('[data-grid="taxes"] > tr')]
    .find((t) => t.querySelector('[name="tax_type"]')?.value === type) : null;
  const field = row === "lines" ? "subtotal" : row === "total" ? "grand_total" : "";
  return [
    marked.length ? { icon: "arrow_downward", label: `Go to the first line marked ${type}`, id: "goto", run: () => focusLineBox(marked[0], "line_tax_code") } : null,
    taxLine ? { icon: "arrow_downward", label: `Go to the ${type} tax line`, id: "goto-tax", run: () => {
      const box = taxLine.querySelector('[name="tax_amount"]');
      box.scrollIntoView({ block: "center", behavior: "smooth" });
      box.focus({ preventScroll: true });
    } } : null,
    field ? { icon: "arrow_upward", label: `Go to ${fieldName(field)}`, id: "goto-field", run: () => goToField(field) } : null,
    "-",
    diff ? { icon: "content_copy", label: `Copy the difference (${diff})`, id: "copy-diff", run: () => copyText(diff) } : null,
    expected ? { icon: "content_copy", label: `Copy the expected amount (${expected})`, id: "copy-expected", run: () => copyText(expected) } : null,
  ];
}

function checkMenu(li) {
  const code = li.dataset.code || "";
  const field = li.dataset.fieldOf || "";
  const line = li.querySelector("[data-goto-line]");
  const text = (li.querySelector(".t")?.textContent || li.title || "").trim();
  return [
    line ? { icon: "arrow_downward", label: `Go to line ${line.dataset.line}`, id: "goto-line", run: () => goToLine(line.dataset.gotoLine, line.dataset.line) } : null,
    field && input(field) ? { icon: "arrow_downward", label: `Go to ${fieldName(field)}`, id: "goto", run: () => goToField(field) } : null,
    "-",
    text ? { icon: "content_copy", label: "Copy the message", id: "copy-text", run: () => copyText(text) } : null,
    code ? { icon: "help", label: "What this check means", hint: "Help", id: "help",
      run: () => window.open(`/help?q=${encodeURIComponent(code)}#check-${encodeURIComponent(code)}`, "_blank", "noopener") } : null,
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
  register('[data-live="posting"] tbody tr', (tr) => postingMenu(tr), { keepFocus: true });
  register("[data-tax-check] tr[data-tax-row]", (tr) => taxCheckMenu(tr), { keepFocus: true });
  register("[data-live=checks] .rvw-status li", (li) => checkMenu(li), { keepFocus: true });
} else {
  register(".iv-stage", pageMenu); // the page's zoom and turn on a read-only invoice too
}

/* ---------- Ctrl+Z, P (Park), X (Reject) ---------- */

document.addEventListener("keydown", (e) => {
  if (e.defaultPrevented || e.altKey || e.target.closest?.("dialog, [role=dialog]")) return;
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && (e.key === "z" || e.key === "Z")) {
    // Not in a text box, where Ctrl+Z is the box's own; but in a box of the review form nothing was typed in since
    // the change (typing there drops the Undo), as after a right-click in it: there the change is what to undo.
    if (typing(e.target) && e.target.form !== form) return;
    if (undo()) {
      e.preventDefault();
      return;
    }
    // Outside a text box the browser's own undo would take a character off the last box typed in, maybe off the
    // screen, and that would be kept (B-739): never. In a box of the form, its own typing is the browser's to undo.
    if (!(typing(e.target) && e.target.tagName !== "SELECT")) {
      e.preventDefault();
      // nothing edited here since: the approval just made, as the banner's Undo (B-823)
      const banner = document.querySelector("#flashes form.flash-undo");
      if (banner) banner.requestSubmit();
      else toast({ text: "Nothing to undo.", tone: "info", icon: "undo" }, 2500);
    }
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
let noteSending = false; // one note at a time: Enter twice or a double click sends it once (B-625)
noteForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  e.stopPropagation(); // not app.js's: it would go to the page again
  if (noteSending) return;
  noteSending = true;
  try {
    await sendNote();
  } finally {
    noteSending = false;
  }
});

async function sendNote() {
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
}

/* ---------- The approve bar: why it is closed, "Approve anyway" with a reason, a credit note ---------- */

const overrideWhy = document.querySelector("[data-override-reason]");

/** Why Approve is closed, in a few words (the bar's own). */
function notReady() {
  const why = document.querySelector("[data-live=bar] [data-not-ready]")?.dataset.notReady || "";
  if (why) return `${why.replace(/\.$/, "")}.`;
  // "Approve anyway" as it stands now, ticked or not (B-790): never "tick it" when it is ticked
  const box = document.querySelector("[data-live=bar] [name=override]");
  if (box && !box.checked) return "Tick “Approve anyway” and say why.";
  if (box && overrideWhy && !overrideWhy.value.trim()) return "Say why you approve anyway.";
  return "see the bar at the bottom.";
}

/** The box that answers why Approve is closed, when it is the reason for "Approve anyway". */
function focusNotReady() {
  const box = document.querySelector("[data-live=bar] [name=override]");
  if (document.querySelector("[data-live=bar] [data-not-ready]")?.dataset.notReady || !box) return;
  if (box.checked && overrideWhy && !overrideWhy.value.trim()) {
    overrideWhy.hidden = false;
    overrideWhy.disabled = false;
    overrideWhy.focus();
  } else if (!box.checked) box.focus();
}

/** Nothing focused hides under the approve bar, however tall it is (B-742): the page's scroll padding is its height. */
function padForBar() {
  const bar = document.querySelector(".inv-actionbar");
  if (bar) document.documentElement.style.scrollPaddingBottom = `${Math.round(bar.getBoundingClientRect().height) + 16}px`;
}
window.addEventListener("resize", padForBar);

function showBarState() {
  padForBar();
  const box = document.querySelector("[data-live=bar] [name=override]");
  if (overrideWhy) {
    const was = overrideWhy.hidden;
    overrideWhy.hidden = !(box && box.checked);
    overrideWhy.disabled = overrideWhy.hidden; // not sent while hidden
    if (was && !overrideWhy.hidden && document.activeElement === box) overrideWhy.focus();
  }
  const credit = document.querySelector("[data-live=bar] [data-credit]")?.dataset.credit === "1";
  const label = approveButton?.querySelector("[data-approve-label]");
  if (label) label.textContent = credit ? "Approve credit note" : "Approve & teach";
}

document.addEventListener("change", (e) => {
  if (e.target.name === "override" && e.target.checked && overrideWhy) {
    overrideWhy.hidden = false;
    overrideWhy.disabled = false;
    overrideWhy.focus();
  }
});

/* ---------- The ⋮ beside a name with a menu, a line's remark, Qty and price ---------- */

document.addEventListener("click", (e) => {
  const hint = e.target.closest?.("[data-menu-hint]");
  if (!hint) return;
  e.preventDefault();
  const field = hint.closest("[data-field]");
  const box = field && input(field.dataset.field);
  box?.focus({ preventScroll: true }); // Esc gives the focus back to the box
  openFor(field, hint);
});

const remarkLimit = Number(document.getElementById("lines")?.dataset.remarkLimit || 0);

/** JD Edwards keeps 30 characters of a line's description (VNEXA): the count while typing, a warning past it. */
function showRemark(textarea, typingNow) {
  const note = textarea?.closest("td")?.querySelector("[data-remark]");
  if (!note || !remarkLimit) return;
  const n = textarea.value.trim().length;
  const over = n > remarkLimit;
  note.hidden = !(over || typingNow);
  note.classList.toggle("over", over);
  note.classList.toggle("typing", Boolean(typingNow));
  // past the limit: the count, and while typing what JD Edwards keeps (its title says it too)
  note.textContent = over && typingNow
    ? `${n}/${remarkLimit}: JD Edwards keeps “${textarea.value.trim().slice(0, remarkLimit)}”`
    : `${n}/${remarkLimit}`;
  note.title = over ? `JD Edwards keeps the first ${remarkLimit} characters: “${textarea.value.trim().slice(0, remarkLimit)}”` : "";
}

document.addEventListener("focusin", (e) => {
  if (e.target.name === "line_description") showRemark(e.target, true);
});
document.addEventListener("focusout", (e) => {
  if (e.target.name === "line_description") showRemark(e.target, false);
});
document.addEventListener("input", (e) => {
  if (e.target.name === "line_description") showRemark(e.target, true);
});

/** A line's tax code says itself in full on hover, as "GST + QST" can be cut off in its box (B-800). */
function titleTaxCodes() {
  document.querySelectorAll("select[data-tax-code]").forEach((sel) => {
    sel.title = sel.selectedOptions[0]?.textContent.trim() || "";
  });
}
document.addEventListener("change", (e) => {
  if (e.target.matches?.("select[data-tax-code]")) titleTaxCodes();
});

document.querySelector("[data-show-qty]")?.addEventListener("change", (e) => {
  document.getElementById("lines")?.classList.toggle("show-qty", e.target.checked);
});

/* ---------- A popover opened: its first text box has the focus (Reopen for correction's reason, B-804) ---------- */

document.addEventListener(
  "toggle",
  (e) => {
    const pop = e.target;
    if (!pop.matches?.("details.pop") || !pop.open) return;
    const box = pop.querySelector(":scope > .pop-body input[type=text], :scope > .pop-body textarea");
    if (box && !pop.querySelector(":scope > .pop-body").contains(document.activeElement)) box.focus();
  },
  true,
);

/* ---------- Alt+PgDn / Alt+PgUp: the invoice's next or previous page from anywhere ---------- */

document.addEventListener("keydown", (e) => {
  if (!viewer || !e.altKey || e.ctrlKey || e.metaKey || (e.key !== "PageDown" && e.key !== "PageUp")) return;
  if (!viewer.pages || viewer.pages.length < 2) return;
  e.preventDefault();
  viewer.showPage(viewer.page + (e.key === "PageDown" ? 1 : -1));
});

/* ---------- The focus on opening: the first thing to check (B-742) ---------- */

/** The first invoice detail a check is about (flagged, a note to act on, a reading to check), else the first line
    still to code, else the vendor, focused without scrolling. ``always``: even when something else has the focus. */
let acted = false; // the clerk pressed a key or clicked since the page opened: the focus is theirs to place
for (const type of ["pointerdown", "keydown"]) document.addEventListener(type, () => (acted = true), { capture: true, once: true });

function focusFirstToCheck(always = false) {
  const active = document.activeElement;
  if (!always && (acted || (active && active !== document.body && active.tagName !== "MAIN"))) return false;
  const fields = [...document.querySelectorAll(".inv-details [data-field]")].filter((f) => f.getClientRects().length);
  const wanted = fields.find((f) => f.classList.contains("is-flagged")) ||
    fields.find((f) => f.querySelector(":scope > [data-help]:not([hidden])")) ||
    fields.find((f) => f.querySelector(".lbl .inv-sure.warn:not([hidden]), .lbl .inv-sure.err:not([hidden])"));
  let box = wanted ? input(wanted.dataset.field) : null;
  if (!box) {
    const pick = [...document.querySelectorAll('select[form="inv"][name="line_gl"], select[form="inv"][name="line_cc"]')]
      .find((b) => blank(b));
    box = pick ? pick.closest(".pk")?.querySelector(".pk-input") || pick : null;
  }
  box = box || vendorInput;
  if (!box || !box.getClientRects().length) return false;
  // Focused for the clerk, not by them: Space with the pointer on the page still holds the Hand (B-591) until they
  // type in it or click it.
  if (!always) {
    box.dataset.autofocused = "1";
    const mine = (e) => {
      if (e.type === "keydown" && e.key === " ") return;
      delete box.dataset.autofocused;
      for (const t of ["keydown", "pointerdown", "blur"]) box.removeEventListener(t, mine);
    };
    for (const t of ["keydown", "pointerdown", "blur"]) box.addEventListener(t, mine);
  }
  // The page stays at its top (B-792): the invoice's header, what must be fixed and the message of the approval just
  // made are what the clerk reads first. The box has the focus all the same: typing in it, Tab or N brings it into
  // view (above the approve bar).
  box.focus({ preventScroll: true });
  if (always) box.scrollIntoView({ block: "nearest" }); // after an Undo: where the work is
  return true;
}

/* ---------- Start ---------- */

showMailLanguage();
titleTaxCodes();
fillSplitLines();
showVendorMatch();
for (const name of absentList()) viewer?.setEdited(name, "", true); // marked before: the Fields list says so
lastSums = tickedSums(); // what the tax lines follow from now on (B-610)
showBarState();
document.querySelectorAll('[data-grid="lines"] textarea[name="line_description"]').forEach((t) => showRemark(t, false));
// The queue shows what this screen shows: check once on opening (today's checks, and any draft kept); then the
// focus goes to the first thing to check, unless the clerk is somewhere already.
if (form) check().then(() => focusFirstToCheck());
