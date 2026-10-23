/* AP Coder invoice viewer: the invoice page with a box over every field read from it (the review screen), given its
   data directly by the page.

     import { InvoiceViewer } from "./invoice_viewer.js";
     const viewer = new InvoiceViewer(root, (event) => ...);
     viewer.update({pages, fields, line_items, height});   // pages: [{src, width, height}] (points)
     viewer.show("invoice_number");                         // highlight a field's box and bring it into view
     viewer.setWords(words); viewer.teachField("po_number"); // teach mode: click the words that hold the field

   Looking at the page: zoom from 50% to 400% ("-", "+", the % (back to Fit width), Fit width, Fit page; Ctrl or Cmd +
   the wheel, or a trackpad pinch, zooms where the pointer is); the Hand (H, or Space held, or the middle button) drags
   the page about; Rotate turns the page a quarter turn to read it, on screen only (nothing is saved or sent). A page
   zoomed in is drawn again sharper (?scale=2 or 3: 240 or 360 dpi), swapped in once it has loaded.

   Events: {type: "select", field}, {type: "assign", field, text, boxes: [[page, x0, y0, x1, y1], ...]} (the words
   picked, in reading order), {type: "teach_cancel", field}, {type: "selection", page, rect: [x0, y0, x1, y1], x, y}
   (a box dragged on the page with the mouse, outside teach mode: x, y where the mouse was let go, in the window) and
   {type: "line_hover", index, on} (a line item's outline on the page, pointed at or left) and {type: "dblclick", page, x,
   y, field, line} (a double click on the page outside teach mode, x and y in the window; field: the field whose box it
   was on, line: on a line item's outline. invoice.js fills the detail being typed in with a plain word).
   Coordinates are fractions of the page (0..1, origin top-left), pages numbered from 1. Text from the invoice is only
   ever set as text (never as HTML).

   The field list follows the form: viewer.setEdited(name, value, absent) shows a field changed there as "Edited" with
   its new value (or "Not on this invoice"); setEdited(name, null) shows the reading again (D-60).
*/

const SVG_NS = "http://www.w3.org/2000/svg";
const CSS_PX_PER_PT = 96 / 72; // 100% = the page's printed size on a 96 dpi screen
const FIT_WIDTH = "fit"; // the zoom: "fit" (the page's width fills the viewer), "page" (all of it shows) or a %
const FIT_PAGE = "page";
const ZOOM_STEPS = [50, 67, 75, 90, 100, 125, 150, 200, 300, 400]; // "+" and "-" go from step to step
const MIN_ZOOM = 50;
const MAX_ZOOM = 400;
const WHEEL_RATE = 0.01; // Ctrl + wheel or a pinch: the zoom is multiplied by e^(-deltaY x this)...
const WHEEL_STEP = 20; // ... with deltaY held to +/- this per event, so one notch of a mouse wheel is about 22%
// The picture at scale 1 is drawn at 120 dpi, never more than 2,400 px long (web/document.py DPI, MAX_SIDE). Past
// 1.25 screen pixels per picture pixel it is asked for at scale 2 (240 dpi), past 2.5 at scale 3 (360 dpi).
const PICTURE_DPI = 120;
const PICTURE_SIDE = 2400;
const SHARPER = [[2.5, 3], [1.25, 2]];
const ECHO_MS = 20000; // a `selected` from Python equal to a field clicked here this recently is its echo
const SMALL_SHEET = 560; // px: a page drawn narrower than this shows a label tag only on hover or selection
const ZOOM_KEY = "apc-invoice-viewer-zoom";
const FIELDS_KEY = "apc-invoice-viewer-fields"; // the field list shown or folded, when it sits above the page
const FOLD_BELOW = 640; // px: a viewer shorter than this starts with the field list folded (above the page only)
const VIEWPORT_MARGIN = 190; // px of the browser window kept for the card around the viewer and the action bar
const MIN_FIT_HEIGHT = 360;
// The field list, grouped as an AP clerk reads an invoice; fields not found go last, folded away.
const GROUPS = [
  ["Supplier", ["vendor_name", "gst_hst_registration_number", "qst_registration_number", "pst_registration_number"]],
  ["Invoice", ["invoice_number", "invoice_date", "due_date", "po_number", "payment_terms", "currency"]],
  ["Amounts & tax", ["subtotal", "other_charges", "gst_amount", "hst_amount", "pst_amount", "qst_amount", "tax_total", "grand_total"]],
];
// A shape for each status as well as a colour, so the status never depends on colour alone.
const GLYPH = { verified: "\u2713", likely: "\u2713", check: "!", failed: "\u2715", missing: "", edited: "\u270E" };
const STATUS_TEXT = { verified: "Verified", likely: "Likely", check: "Check", missing: "Not found", failed: "Failed check", edited: "Edited" };
const STATUS_HELP = {
  verified: "Readers agree and the checks pass",
  likely: "One strong reading, checks pass: a quick look",
  check: "Readers disagree or are unsure: look at it",
  missing: "Not found on the invoice",
  failed: "A check failed: look at it",
  edited: "Changed on the form",
};
const READERS = {
  text: "PDF text",
  ocr: "OCR",
  ocr2: "OCR, second engine",
  vlm: "Page reader",
  rules: "Rule reader",
  rule: "Rule reader",
  template: "Supplier template",
  di: "Document Intelligence",
  ai: "AI coder",
  vendor: "Vendor master",
  vendor_master: "Vendor master",
};
const reducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- Small DOM helpers ---------- */

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function replace(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

function s(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, String(v));
  }
  return el;
}

const ICONS = {
  left: "M15 18l-6-6 6-6",
  right: "M9 18l6-6-6-6",
  next: "M12 5v14M5 12l7 7 7-7",
  check: "M5 12.5l4.5 4.5L19 7.5",
  pointer: "M5 3l6.5 17 2.3-7.2L21 10.5z",
  minus: "M6 12h12",
  plus: "M12 6v12M6 12h12",
  fitWidth: "M4 5v14M20 5v14M8 12h8M10.5 9.5L8 12l2.5 2.5M13.5 9.5L16 12l-2.5 2.5",
  fitPage: "M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5",
  hand: "M12 3v18M3 12h18M9.5 5.5L12 3l2.5 2.5M9.5 18.5L12 21l2.5-2.5M5.5 9.5L3 12l2.5 2.5M18.5 9.5L21 12l-2.5 2.5",
  rotate: "M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5",
};

function icon(name, size = 16) {
  const el = s("svg", { viewBox: "0 0 24 24", width: size, height: size, class: "iv-ic", "aria-hidden": "true" });
  el.append(s("path", { d: ICONS[name], fill: "none", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" }));
  return el;
}

function isTyping(target) {
  return target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) && target.type !== "checkbox";
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** "Invoice number" -> "invoice number", but "GST/HST number" and "PO number" stay as they are. */
function inSentence(label) {
  return label.length > 1 && label[1] === label[1].toLowerCase() ? label[0].toLowerCase() + label.slice(1) : label;
}

function pct(confidence) {
  const v = Math.max(0, Math.min(1, Number(confidence) || 0));
  return `${Math.floor(v * 100 + 1e-9)}%`; // never rounds 99.6% up to a certainty it does not have
}

function plainValue(v) {
  if (v === null || v === undefined || v === "") return "";
  if (typeof v === "object") {
    if ("value" in v) return plainValue(v.value);
    return JSON.stringify(v);
  }
  return String(v);
}

function norm(v) {
  const text = plainValue(v).toLowerCase().replace(/[\s$€£,]/g, "");
  const n = Number(text);
  return text !== "" && Number.isFinite(n) ? String(n) : text;
}

/** Items in reading order: page, then line by line down the page, left to right along a line. */
function readingOrder(items) {
  const sorted = [...items].sort((a, b) => a.page - b.page || a.cy - b.cy);
  const lines = [];
  for (const item of sorted) {
    const line = lines[lines.length - 1];
    const first = line && line[0];
    if (first && first.page === item.page && Math.abs(first.cy - item.cy) <= 0.6 * Math.max(first.h, item.h)) line.push(item);
    else lines.push([item]);
  }
  return lines.flatMap((line) => line.sort((a, b) => a.x - b.x));
}

const toneOf = (f) => (f.failed && f.status !== "missing" ? "failed" : f.status);

/* ---------- The viewer ---------- */

export class InvoiceViewer {
  constructor(root, onEvent) {
    this.root = root;
    this.onEvent = onEvent || (() => {});
    this.args = null;
    this.pages = [];
    this.fields = [];
    this.byName = new Map();
    this.words = [];
    this.lineItems = [];
    this.page = 1;
    this.zoom = this.savedZoom();
    this.rotations = new Map(); // page -> quarter turns shown (0, 90, 180, 270), for this invoice only, never saved
    this.hand = false; // the Hand: a drag moves the page instead of picking words
    this.spaceHand = false; // the Hand while Space is held
    this.spaceTaken = false; // the Space down was the viewer's: its keyup is too
    this.overStage = false; // the pointer is over the page
    this.overCard = false; // the pointer is over the Document card
    this.picScale = 1; // the scale of the picture showing (1, 2 or 3)
    this.imgFor = ""; // the page the picture showing is of (its address at scale 1)
    this.fieldsOpen = this.savedFields(); // null until chosen: then it follows the viewer's height
    this.onlyCheck = false;
    this.selected = null;
    this.hovered = null;
    this.teach = null;
    this.picks = new Map(); // "page:index" -> word, in the order picked
    this.argSelected = undefined;
    this.sentSelects = new Map(); // field -> when it was selected here, to tell Python's echo from its own choice
    this.argTeach = undefined;
    this.lastSeq = 0;
    this.stepAt = -1;
    this.fieldsKey = "";
    this.flashTimer = 0;
    this.tipTimer = 0;
    this.tipFor = null;
    this.lastHeight = -1;
    this.edits = new Map(); // field -> {value, absent}: changed on the form since the page was read (D-60)
    this.build();
  }

  /* ----- Building the frame (once) ----- */

  build() {
    this.summary = h("span", { class: "iv-summary" });
    // Only shown when the list sits above the page (a narrow viewer): it folds away to give the page the room.
    this.foldBtn = h("button", { type: "button", class: "iv-fold", "aria-expanded": "true", onclick: () => this.setFieldsOpen(!this.root.classList.contains("fields-open"), true) }, "Hide");
    this.meter = h("div", { class: "iv-meter", "aria-hidden": "true" });
    this.list = h("div", { class: "iv-list", "aria-label": "Fields read from the invoice" });
    this.onlyInput = h("input", { type: "checkbox", role: "switch", onchange: () => this.setOnlyCheck(this.onlyInput.checked) });
    const only = h("label", { class: "iv-switch" }, this.onlyInput, h("span", { class: "iv-track", "aria-hidden": "true" }), h("span", null, "To check only"));
    only.title = "Show only the fields that need checking";
    // The key to the boxes' colours and "To check only" sit on the Fields line, so the tools above the page take one
    // row and the page gets the room (D-57); beside the page (a wide viewer), on a line of their own under it.
    this.legend = h("div", { class: "iv-legend", role: "note", "aria-label": "Colours of the boxes" });
    this.panel = h(
      "aside",
      { class: "iv-panel", "aria-label": "Fields" },
      h("div", { class: "iv-panel-head" }, h("h2", null, "Fields"), this.summary, h("div", { class: "iv-panel-tools" }, this.legend, only), this.foldBtn),
      this.meter,
      this.list
    );

    this.outBtn = h("button", { type: "button", class: "iv-zbtn", title: "Zoom out (-)", "aria-label": "Zoom out", "aria-keyshortcuts": "-", onclick: () => this.zoomStep(-1) }, icon("minus", 15));
    this.zoomText = h("button", { type: "button", class: "iv-zpct", "aria-live": "polite", onclick: () => this.setZoom(this.zoom === FIT_WIDTH ? 100 : FIT_WIDTH) }, "100%");
    this.inBtn = h("button", { type: "button", class: "iv-zbtn", title: "Zoom in (+)", "aria-label": "Zoom in", "aria-keyshortcuts": "+", onclick: () => this.zoomStep(1) }, icon("plus", 15));
    this.fitWidthBtn = h("button", { type: "button", class: "iv-zbtn", title: "Fit the width (0)", "aria-label": "Fit width", "aria-keyshortcuts": "0", "aria-pressed": "false", onclick: () => this.setZoom(FIT_WIDTH) }, icon("fitWidth", 16));
    this.fitPageBtn = h("button", { type: "button", class: "iv-zbtn", title: "Fit the whole page (9)", "aria-label": "Fit page", "aria-keyshortcuts": "9", "aria-pressed": "false", onclick: () => this.setZoom(FIT_PAGE) }, icon("fitPage", 16));
    this.handBtn = h("button", { type: "button", class: "iv-icon-btn iv-tool", title: "Hand: drag to move the page (H, or hold Space)", "aria-label": "Hand", "aria-keyshortcuts": "h", "aria-pressed": "false", onclick: () => this.setHand(!this.hand) }, icon("hand", 16));
    this.rotateBtn = h("button", { type: "button", class: "iv-icon-btn iv-tool iv-rotate", title: "Turn the page a quarter turn, to read it (R). Only on screen: nothing is saved.", "aria-label": "Rotate", "aria-keyshortcuts": "r", onclick: () => this.rotate() }, icon("rotate", 16));
    this.prevBtn = h("button", { type: "button", class: "iv-icon-btn", title: "Previous page", "aria-label": "Previous page", onclick: () => this.showPage(this.page - 1) }, icon("left"));
    this.nextBtn = h("button", { type: "button", class: "iv-icon-btn", title: "Next page", "aria-label": "Next page", onclick: () => this.showPage(this.page + 1) }, icon("right"));
    this.where = h("span", { class: "iv-where", "aria-live": "polite" });
    this.pager = h("div", { class: "iv-pager", role: "group", "aria-label": "Pages" }, this.prevBtn, this.where, this.nextBtn);
    this.stepBtn = h(
      "button",
      { type: "button", class: "iv-btn iv-step", title: "Go to the next field that is not verified (n)", "aria-keyshortcuts": "n", onclick: () => this.nextToCheck() },
      icon("next", 15),
      h("span", { class: "iv-step-text" }, "Next to check"),
      h("kbd", null, "n")
    );
    this.count = h("span", { class: "iv-count", "aria-live": "polite" });
    // One row of tools above the page: zoom, fit, the Hand, Rotate, Next to check, then the pages (D-57).
    this.toolbar = h(
      "div",
      { class: "iv-toolbar", role: "toolbar", "aria-label": "Page tools" },
      h(
        "div",
        { class: "iv-view" },
        h("div", { class: "iv-segs iv-zoom", role: "group", "aria-label": "Zoom" }, this.outBtn, this.zoomText, this.inBtn),
        h("div", { class: "iv-segs", role: "group", "aria-label": "Fit" }, this.fitWidthBtn, this.fitPageBtn),
        this.handBtn,
        this.rotateBtn,
        this.pager
      ),
      h("div", { class: "iv-check" }, this.stepBtn, this.count)
    );

    this.teachText = h("div", { class: "iv-teach-text" });
    this.useBtn = h("button", { type: "button", class: "iv-btn primary", disabled: true, onclick: () => this.assign() }, icon("check", 15), "Use these words");
    this.teachBar = h(
      "div",
      { class: "iv-teach", role: "region", "aria-label": "Teach", hidden: true },
      h("span", { class: "iv-teach-ic" }, icon("pointer", 16)),
      this.teachText,
      h("div", { class: "iv-teach-actions" }, h("button", { type: "button", class: "iv-btn ghost", title: "Esc", onclick: () => this.cancelTeach() }, "Cancel"), this.useBtn)
    );

    this.img = h("img", { class: "iv-img", alt: "", draggable: "false" });
    this.img.addEventListener("load", () => {
      this.sheet.classList.remove("loading");
      this.layoutTags();
      this.reportHeight();
    });
    this.svg = s("svg", { class: "iv-svg", viewBox: "0 0 1 1", preserveAspectRatio: "none", "aria-hidden": "true" });
    this.gLines = s("g", { class: "iv-lines" });
    this.gFields = s("g", { class: "iv-fields" });
    this.gWords = s("g", { class: "iv-words" });
    this.gSel = s("g", { class: "iv-sel" });
    this.svg.append(this.gLines, this.gFields, this.gWords, this.gSel);
    this.tags = h("div", { class: "iv-tags" });
    this.sheet = h("div", { class: "iv-sheet loading" }, this.img, this.svg, this.tags);
    // The frame holds the room the page takes on screen; the sheet in it is turned when the page is rotated.
    this.frame = h("div", { class: "iv-frame" }, this.sheet);
    this.empty = h("p", { class: "iv-empty", hidden: true }, "No page to show.");
    this.stage = h("div", { class: "iv-stage", tabindex: "-1" }, this.frame, this.empty);
    // A tag in the page's corner while the Hand is on or the page is shown turned: what a drag does now, and that
    // the turn is on screen only (D-57).
    this.modeTag = h("div", { class: "iv-mode", "aria-live": "polite" });
    this.stageWrap = h("div", { class: "iv-stage-wrap" }, this.stage, this.modeTag);
    this.main = h("section", { class: "iv-main", "aria-label": "Invoice page" }, this.toolbar, this.teachBar, this.stageWrap);
    this.tip = h("div", { class: "iv-tip", role: "tooltip", id: "iv-tip", hidden: true });
    replace(this.root, this.panel, this.main);
    document.body.append(this.tip); // on <body>: the card around the viewer would hold a fixed card in

    // Page interactions, delegated: one listener each, whatever is drawn.
    this.gFields.addEventListener("pointerover", (e) => {
      const g = e.target.closest(".iv-f");
      if (g) this.setHover(g.dataset.field, g);
    });
    this.gFields.addEventListener("pointerout", (e) => {
      const g = e.target.closest(".iv-f");
      if (g && !g.contains(e.relatedTarget)) this.setHover(null);
    });
    this.gFields.addEventListener("click", (e) => {
      if (this.dragged) return; // the end of a box dragged across the page, not a click on this field
      const g = e.target.closest(".iv-f");
      if (g) this.select(g.dataset.field, { from: "page" });
    });
    this.sheet.addEventListener("pointerdown", (e) => this.dragStart(e));
    this.sheet.addEventListener("pointermove", (e) => this.dragMove(e));
    this.sheet.addEventListener("pointerup", (e) => this.dragEnd(e));
    this.sheet.addEventListener("pointercancel", () => this.dragCancel());
    this.sheet.addEventListener("dblclick", (e) => {
      if (this.teach || this.hand || this.spaceHand || !this.pageInfo()) return;
      e.preventDefault();
      // What was double-clicked: a field's box (its click selected it already), a line item, or plain words (B-621).
      const box = e.target.closest && e.target.closest(".iv-f, .iv-tag");
      const line = Boolean(e.target.closest && e.target.closest(".iv-lg"));
      this.emit({ type: "dblclick", page: this.page, x: e.clientX, y: e.clientY, field: box ? box.dataset.field : null, line });
    });
    this.tags.addEventListener("click", (e) => {
      const tag = e.target.closest(".iv-tag");
      if (tag) this.select(tag.dataset.field, { from: "page" });
    });
    this.tags.addEventListener("pointerover", (e) => {
      const tag = e.target.closest(".iv-tag");
      if (tag) this.setHover(tag.dataset.field, tag);
    });
    this.tags.addEventListener("pointerout", (e) => {
      const tag = e.target.closest(".iv-tag");
      if (tag && !tag.contains(e.relatedTarget)) this.setHover(null);
    });
    this.gLines.addEventListener("pointerover", (e) => {
      const r = e.target.closest(".iv-line");
      if (!r) return;
      this.showTip(r, [h("div", { class: "iv-tip-head" }, h("b", null, "Line item")), h("p", { class: "iv-tip-value" }, r.dataset.label)], 0);
      const g = r.closest(".iv-lg");
      if (g) this.emit({ type: "line_hover", index: Number(g.dataset.idx), on: true });
    });
    this.gLines.addEventListener("pointerout", (e) => {
      this.hideTip();
      const g = e.target.closest(".iv-lg");
      if (g && !g.contains(e.relatedTarget)) this.emit({ type: "line_hover", index: Number(g.dataset.idx), on: false });
    });
    this.gWords.addEventListener("click", (e) => {
      if (this.dragged) return;
      const r = e.target.closest(".iv-w");
      if (r) this.toggleWord(Number(r.dataset.i));
    });

    // The Hand (or the middle button): a drag moves the page. Ctrl + wheel, or a pinch: zoom where the pointer is.
    this.stage.addEventListener("pointerdown", (e) => this.panStart(e));
    this.stage.addEventListener("pointermove", (e) => this.panMove(e));
    this.stage.addEventListener("pointerup", (e) => this.panEnd(e));
    this.stage.addEventListener("pointercancel", (e) => this.panEnd(e));
    this.stage.addEventListener("mousedown", (e) => {
      if (e.button === 1) e.preventDefault(); // no autoscroll: the middle button moves the page
    });
    this.stage.addEventListener("pointerenter", () => (this.overStage = true));
    this.stage.addEventListener("pointerleave", () => (this.overStage = false));
    const card = this.card();
    card.addEventListener("pointerenter", () => (this.overCard = true));
    card.addEventListener("pointerleave", () => (this.overCard = false));
    this.stage.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
    new ResizeObserver(() => {
      const size = `${this.stage.clientWidth}x${this.stage.clientHeight}`;
      if (size === this.stageSize) return;
      this.stageSize = size;
      if (this.zoom === FIT_WIDTH || this.zoom === FIT_PAGE) this.sizeSheet(); // the fit follows the window
    }).observe(this.stage);

    this.stage.addEventListener("scroll", () => this.hideTip(), { passive: true });
    window.addEventListener("keydown", (e) => this.onKey(e));
    window.addEventListener("keyup", (e) => {
      // only a Space the viewer took when it went down (a button pressed with Space is pressed as it comes up, B-591)
      if (e.key !== " " || !this.spaceTaken) return;
      e.preventDefault();
      this.spaceTaken = false;
      this.spaceHand = false;
      this.renderHand();
    });
    window.addEventListener("blur", () => {
      this.hideTip();
      this.spaceTaken = false;
      if (this.spaceHand) {
        this.spaceHand = false;
        this.renderHand();
      }
    });
    new ResizeObserver(() => {
      this.layoutTags();
      this.reportHeight();
    }).observe(this.root);
    new ResizeObserver(() => this.layoutTags()).observe(this.sheet);
    window.addEventListener("resize", () => this.applyHeight());
    if (this.root.closest(".inv-doc")) {  // the review screen: the card rises to its sticky place as the page scrolls
      let queued = false;
      window.addEventListener("scroll", () => {
        if (queued) return;
        queued = true;
        requestAnimationFrame(() => { queued = false; this.applyHeight(); });
      }, { passive: true });
    }
  }

  /* ----- New arguments from Python ----- */

  update(args) {
    const prevPages = this.pages;
    this.args = args;
    this.pages = Array.isArray(args.pages) ? args.pages : [];
    this.words = Array.isArray(args.words) ? args.words : [];
    this.lineItems = Array.isArray(args.line_items) ? args.line_items : [];
    const fields = Array.isArray(args.fields) ? args.fields : [];
    const fieldsKey = JSON.stringify(fields);
    const fieldsChanged = fieldsKey !== this.fieldsKey;
    this.fieldsKey = fieldsKey;
    this.fields = fields;
    this.byName = new Map(fields.map((f) => [f.field, f]));

    const fixed = Number.isFinite(args.height) && args.height > 0;
    this.root.classList.toggle("fixed", fixed);
    this.maxHeight = fixed ? args.height : null;
    this.applyHeight();
    if (this.fieldsShown === undefined) this.setFieldsOpen(this.fieldsOpen !== null ? this.fieldsOpen : !fixed || this.root.offsetHeight >= FOLD_BELOW, false);

    const samePages = prevPages.length === this.pages.length && prevPages.every((p, i) => p.src === this.pages[i].src);
    if (!samePages) {
      this.page = 1;
      this.rotations = new Map();
    }
    this.page = Math.min(Math.max(1, this.page), Math.max(1, this.pages.length));
    if (this.selected && !this.byName.has(this.selected)) this.selected = null;

    if (fieldsChanged) {
      this.stepAt = -1;
      this.renderPanel();
    }
    this.renderLegend();
    this.renderStep();

    // Python's selected/teach take effect when they change, so a click here is not undone by the rerun it causes.
    let reveal = null;
    // Python echoing back a field clicked here (often a rerun or two late, as a script reads the event after
    // drawing the viewer) changes nothing; a field it chose itself is shown.
    if (args.selected !== this.argSelected) {
      this.argSelected = args.selected;
      const sentAt = this.sentSelects.get(args.selected);
      const echo = sentAt !== undefined && Date.now() - sentAt < ECHO_MS;
      this.sentSelects.delete(args.selected);
      if (args.selected && !echo && args.selected !== this.selected && this.byName.has(args.selected)) {
        this.selected = args.selected;
        reveal = args.selected;
      }
    }
    if (args.teach !== this.argTeach) {
      this.argTeach = args.teach;
      if (args.teach) this.startTeach(args.teach);
      else this.endTeach();
    }
    this.renderPage(!samePages);
    this.renderSelection();
    if (reveal) requestAnimationFrame(() => this.reveal(reveal, { flash: true, scrollPanel: true }));
    this.reportHeight();
  }

  /** A fixed height never taller than the browser window, so the whole viewer fits on screen beside the form. */
  applyHeight() {
    if (!this.maxHeight) {
      this.root.style.height = "";
      return;
    }
    const vh = window.innerHeight;
    // On the review screen the Document card sticks 8 px from the top over a bar fixed at the window's foot: the
    // viewer ends above that bar wherever the card is (it grows as the card rises to its sticky place, B-478), so
    // the whole page view shows. Elsewhere a fixed margin.
    const bar = document.querySelector(".inv-actionbar");
    const card = this.root.closest(".inv-doc");
    let margin = VIEWPORT_MARGIN;
    let settledMargin = margin;
    if (bar && card) {
      const top = this.root.getBoundingClientRect().top;
      const stuck = 8 + (top - card.getBoundingClientRect().top);
      margin = bar.offsetHeight + Math.max(top, stuck) + 10;
      settledMargin = bar.offsetHeight + stuck + 10;
    }
    const fit = (m) => (vh ? Math.min(this.maxHeight, Math.max(MIN_FIT_HEIGHT, vh - m)) : this.maxHeight);
    const height = fit(margin);
    // The height once the card has risen to its sticky place: Fit page fits that, so its % does not drift as the
    // dashboard page scrolls.
    this.settledHeight = fit(settledMargin);
    this.root.style.height = `${height}px`;
  }

  setFieldsOpen(open, remember) {
    this.fieldsShown = open;
    this.root.classList.toggle("fields-open", open);
    this.foldBtn.setAttribute("aria-expanded", String(open));
    replace(this.foldBtn, open ? "Hide" : "Show");
    this.foldBtn.title = open ? "Fold the field list away, to see more of the page" : "Show the field list";
    if (remember) {
      try {
        window.localStorage.setItem(FIELDS_KEY, open ? "open" : "closed");
      } catch (_e) {
        /* then it is remembered until the page is left */
      }
    }
    this.reportHeight();
  }

  savedFields() {
    try {
      const v = window.localStorage.getItem(FIELDS_KEY);
      return v === "open" ? true : v === "closed" ? false : null;
    } catch (_e) {
      return null;
    }
  }

  /* ----- Field panel ----- */

  renderPanel() {
    const counts = { verified: 0, likely: 0, check: 0, failed: 0, missing: 0 };
    for (const f of this.fields) counts[toneOf(f)] += 1;
    const toCheck = counts.likely + counts.check + counts.failed;
    const parts = [];
    if (counts.verified) parts.push(`${counts.verified} verified`);
    if (toCheck) parts.push(`${toCheck} to check`);
    if (counts.missing) parts.push(`${counts.missing} not found`);
    replace(this.summary, parts.join(" · ") || "Nothing read yet");
    const total = this.fields.length || 1;
    replace(
      this.meter,
      ["verified", "likely", "check", "failed", "missing"]
        .filter((t) => counts[t])
        .map((t) => h("i", { class: `t-${t}`, style: `flex-grow:${counts[t] / total}`, title: `${STATUS_TEXT[t]}: ${counts[t]}` }))
    );
    this.rows = new Map();
    const rowFor = (f) => {
      const edit = this.edits.get(f.field);
      const tone = edit ? "edited" : toneOf(f);
      const missing = !edit && f.status === "missing";
      const shown = edit ? (edit.absent ? "" : edit.value) : f.display;
      const blank = edit ? (edit.absent ? "Not on this invoice" : "—") : missing ? "Not found" : "—";
      const value = missing || !shown ? h("span", { class: "iv-row-value none" }, blank) : h("span", { class: "iv-row-value" }, shown);
      const row = h(
        "button",
        {
          type: "button",
          role: "listitem",
          class: `iv-row t-${tone}`,
          dataset: { field: f.field },
          "aria-describedby": "iv-tip",
          "aria-label": `${f.label}: ${missing ? "not found" : shown || (edit && edit.absent ? "not on this invoice" : "blank")}. ${STATUS_TEXT[tone]}${missing || edit ? "" : `, ${pct(f.confidence)} confidence`}.`,
          onclick: () => {
            // The card would cover the box just brought into view: it stays shut until the pointer leaves.
            this.tipMuted = row;
            this.hideTip();
            this.select(f.field, { from: "panel" });
          },
        },
        h("span", { class: "iv-dot", "aria-hidden": "true" }, GLYPH[tone]),
        h("span", { class: "iv-row-text" }, h("span", { class: "iv-row-label" }, f.label), value),
        h("span", { class: "iv-badge", "aria-hidden": "true", title: STATUS_TEXT[tone] }, edit ? "edited" : missing ? "—" : pct(f.confidence))
      );
      row.addEventListener("pointerenter", () => {
        this.setHover(f.field, null);
        if (this.tipMuted !== row) this.showTip(row, this.tipContent(f), 260, "side");
      });
      row.addEventListener("pointerleave", () => {
        if (this.tipMuted === row) this.tipMuted = null;
        this.setHover(null);
      });
      row.addEventListener("focus", () => {
        if (row.matches(":focus-visible") && this.tipMuted !== row) this.showTip(row, this.tipContent(f), 0, "side");
      });
      row.addEventListener("blur", () => {
        if (this.tipMuted === row) this.tipMuted = null;
        this.hideTip();
      });
      this.rows.set(f.field, row);
      return row;
    };
    const found = this.fields.filter((f) => f.status !== "missing");
    const missing = this.fields.filter((f) => f.status === "missing");
    const grouped = new Set(GROUPS.flatMap(([, names]) => names));
    const groups = GROUPS.map(([title, names]) => [title, found.filter((f) => names.includes(f.field))]);
    groups.push(["Other", found.filter((f) => !grouped.has(f.field))]);
    const section = (title, rows, extra) =>
      h(
        "div",
        { class: `iv-group${extra ? ` ${extra}` : ""}`, role: "group", "aria-label": title },
        h("div", { class: "iv-group-head" }, h("span", null, title), h("span", { class: "iv-group-n" }, rows.length)),
        h("div", { class: "iv-group-rows", role: "list" }, rows)
      );
    const lineRows = this.lineItems.map((item, i) =>
      h(
        "button",
        { type: "button", role: "listitem", class: "iv-row iv-lrow", dataset: { line: i }, title: "Show this line on the page", onclick: () => this.revealLine(i) },
        h("span", { class: "iv-dot line", "aria-hidden": "true" }),
        h("span", { class: "iv-row-text" }, h("span", { class: "iv-row-label" }, `Line ${i + 1}`), h("span", { class: "iv-row-value thin" }, item.label))
      )
    );
    replace(
      this.list,
      this.fields.length ? null : h("p", { class: "iv-none" }, "No fields to show."),
      groups.filter(([, fs]) => fs.length).map(([title, fs]) => section(title, fs.map(rowFor))),
      lineRows.length ? section("Lines", lineRows, "iv-group-lines") : null,
      missing.length
        ? h(
            "details",
            { class: "iv-group iv-group-missing" },
            h("summary", { class: "iv-group-head" }, h("span", null, "Not found on the invoice"), h("span", { class: "iv-group-n" }, missing.length)),
            h("div", { class: "iv-group-rows", role: "list" }, missing.map(rowFor))
          )
        : null
    );
  }

  /** Bring a line item into view on the page and mark it for a moment. */
  revealLine(i) {
    const item = this.lineItems[i];
    const b = item && (item.boxes || [])[0];
    if (!b) return;
    if (b[0] !== this.page) {
      this.page = b[0];
      this.stage.scrollTop = 0;
      this.renderPage(true);
      this.reportHeight();
    }
    const g = this.gLines.querySelector(`.iv-lg[data-idx="${i}"]`);
    if (!g) return;
    this.scrollToBox(b, g);
    for (const other of this.gLines.querySelectorAll(".iv-lg.on")) other.classList.remove("on");
    g.classList.add("on");
    clearTimeout(this.lineTimer);
    this.lineTimer = setTimeout(() => g.classList.remove("on"), 2200);
  }

  tipContent(f) {
    const tone = toneOf(f);
    const out = [
      h(
        "div",
        { class: "iv-tip-head" },
        h("b", null, f.label),
        h("span", { class: `iv-pill t-${tone}` }, STATUS_TEXT[tone], f.status === "missing" ? null : ` · ${pct(f.confidence)}`)
      ),
    ];
    if (f.status !== "missing" && f.display) out.push(h("p", { class: "iv-tip-value" }, f.display));
    const edit = this.edits.get(f.field);
    if (edit) out.push(h("p", { class: "iv-tip-help" }, edit.absent ? "You marked it as not on this invoice." : `You changed it on the form: ${edit.value || "blank"}.`));
    out.push(h("p", { class: "iv-tip-help" }, STATUS_HELP[tone]));
    if (f.reasons && f.reasons.length) out.push(h("ul", { class: "iv-tip-reasons" }, f.reasons.map((r) => h("li", null, r))));
    const sources = Object.entries(f.sources || {});
    if (sources.length) {
      const want = norm(f.value);
      const shown = norm(f.display);
      out.push(
        h("div", { class: "iv-tip-sub" }, "What each reader read"),
        h(
          "table",
          { class: "iv-tip-sources" },
          h(
            "tbody",
            null,
            sources.map(([reader, value]) => {
              // "other:<value>": the readers named in ``value`` read <value>, not the field's value.
              const other = reader.startsWith("other:");
              const names = other
                ? String(value).split(",").map((r) => READERS[r.trim()] || r.trim().replace(/_/g, " ")).join(", ")
                : READERS[reader] || reader.replace(/_/g, " ");
              const said = other ? reader.slice(6) : plainValue(value);
              // The server says which readers agree (a date printed "Oct 1, 2026" agrees with 2026-10-01); older
              // pages without that list compare the text.
              const agrees = !other && said !== "" &&
                (Array.isArray(f.agreeing) ? f.agreeing.includes(reader) : norm(value) === want || norm(value) === shown);
              return h(
                "tr",
                null,
                h("th", { scope: "row" }, names),
                h("td", { class: said ? "" : "none" }, said || "nothing"),
                h("td", { class: `iv-agree ${agrees ? "yes" : said ? "no" : ""}` }, agrees ? "agrees" : said ? "differs" : "")
              );
            })
          )
        )
      );
    }
    const pagesOn = [...new Set((f.boxes || []).map((b) => b[0]))];
    if (pagesOn.length && this.pages.length > 1) out.push(h("p", { class: "iv-tip-foot" }, `On page ${pagesOn.join(", ")}`));
    return out;
  }

  renderLegend() {
    const counts = { verified: 0, likely: 0, check: 0, failed: 0 };
    for (const f of this.fields) if (f.boxes && f.boxes.length && toneOf(f) in counts) counts[toneOf(f)] += 1;
    const key = (tone, words, n) => h("span", { class: "iv-key", title: STATUS_HELP[tone] }, h("i", { class: `iv-swatch t-${tone}` }), words, n ? h("b", null, n) : null);
    replace(
      this.legend,
      key("verified", "Verified", counts.verified),
      key("likely", "Likely", counts.likely),
      key("check", "Check", counts.check),
      counts.failed ? key("failed", "Failed check", counts.failed) : null,
      this.lineItems.length ? h("span", { class: "iv-key" }, h("i", { class: "iv-swatch line" }), "Line item") : null
    );
  }

  /* ----- The page ----- */

  pageInfo() {
    return this.pages[this.page - 1] || null;
  }

  renderPage(newPicture) {
    const info = this.pageInfo();
    this.empty.hidden = Boolean(info);
    this.frame.hidden = !info;
    const many = this.pages.length > 1;
    this.pager.hidden = !many;
    // "Page" folds away in a narrow viewer (still read out): "1 of 2" keeps the tools on one row
    replace(this.where, info ? [h("span", { class: "iv-where-word" }, "Page "), `${this.page} of ${this.pages.length}`] : "");
    this.prevBtn.disabled = this.page <= 1;
    this.nextBtn.disabled = this.page >= this.pages.length;
    if (!info) return;
    this.sizeSheet();
    if (newPicture || this.imgFor !== info.src) {
      // A new page: the shimmer until its picture has loaded, asked for as sharp as the zoom needs straight away.
      this.imgFor = info.src;
      this.pending = null;
      this.picScale = this.wantedScale();
      this.sheet.classList.add("loading");
      this.img.src = this.srcAt(info, this.picScale);
      this.img.alt = `Invoice page ${this.page} of ${this.pages.length}`;
    }

    const aspect = info.width / info.height; // y-units per x-unit, for even padding and round corners
    const padX = 0.0032;
    const padY = padX * aspect;
    const rx = 0.0035;
    const ry = rx * aspect;
    const rect = (b, attrs) =>
      s("rect", Object.assign({ x: b[1] - padX, y: b[2] - padY, width: b[3] - b[1] + 2 * padX, height: b[4] - b[2] + 2 * padY, rx, ry }, attrs));

    this.gLines.replaceChildren();
    this.lineItems.forEach((item, i) => {
      const here = item.boxes.filter((b) => b[0] === this.page);
      if (!here.length) return;
      const g = s("g", { class: "iv-lg", dataset: { idx: i } });
      for (const b of here) g.append(rect(b, { class: "iv-line", dataset: { label: item.label } }));
      this.gLines.append(g);
    });

    this.gFields.replaceChildren();
    this.tags.replaceChildren();
    this.groups = new Map();
    this.tagEls = new Map();
    for (const f of this.fields) {
      const boxes = (f.boxes || []).filter((b) => b[0] === this.page);
      if (f.status === "missing" || !boxes.length) continue;
      const tone = toneOf(f);
      const g = s("g", { class: `iv-f t-${tone}`, dataset: { field: f.field } });
      for (const b of boxes) {
        g.append(rect(b, { class: "iv-halo" }), rect(b, { class: "iv-box" }));
      }
      this.gFields.append(g);
      this.groups.set(f.field, g);
      // No name tag beside the box (it covered the page): the field's name and readings show on hover.
    }
    this.renderWords();
    this.renderSelection();
    this.layoutTags();
  }

  /** The quarter turns the page is shown at (0, 90, 180 or 270). */
  get rot() {
    return this.rotations.get(this.page) || 0;
  }

  /** The room for the page in the stage (CSS px): its width, and its height (the window's when the viewer grows). */
  room() {
    const st = this.stage;
    const cs = window.getComputedStyle(st);
    const w = st.clientWidth - parseFloat(cs.paddingLeft || 0) - parseFloat(cs.paddingRight || 0);
    let ht = st.clientHeight - parseFloat(cs.paddingTop || 0) - parseFloat(cs.paddingBottom || 0);
    if (!this.root.classList.contains("fixed")) ht = Math.max(MIN_FIT_HEIGHT, window.innerHeight - VIEWPORT_MARGIN);
    else if (this.settledHeight) ht += this.settledHeight - this.root.offsetHeight; // as it is once stuck
    return [Math.max(0, w), Math.max(0, ht)];
  }

  /** The zoom (%) a fit gives: the page's width (as turned) fills the room, or for Fit page all of it shows. */
  fitPct(mode) {
    const info = this.pageInfo();
    if (!info) return 100;
    const turned = this.rot % 180 !== 0;
    const w = (turned ? info.height : info.width) * CSS_PX_PER_PT;
    const ht = (turned ? info.width : info.height) * CSS_PX_PER_PT;
    const [rw, rh] = this.room();
    if (!rw) return this.shownPct || 100; // not laid out yet (hidden): keep what was shown
    const byWidth = (rw / w) * 100;
    const pct = mode === FIT_PAGE && rh ? Math.min(byWidth, (rh / ht) * 100) : byWidth;
    return clamp(pct, 5, MAX_ZOOM);
  }

  /** The zoom shown (%), whatever the mode. */
  zoomPct() {
    return typeof this.zoom === "number" ? this.zoom : this.fitPct(this.zoom);
  }

  /** Size the page for the zoom and the rotation: the sheet (upright) is turned inside a frame of the room it takes. */
  sizeSheet() {
    const info = this.pageInfo();
    if (!info) return;
    const pct = this.zoomPct();
    this.shownPct = pct;
    const k = (CSS_PX_PER_PT * pct) / 100;
    const W = Math.max(1, Math.floor(info.width * k));
    const H = Math.max(1, Math.floor(info.height * k));
    const rot = this.rot;
    const turned = rot % 180 !== 0;
    this.frame.style.width = `${turned ? H : W}px`;
    this.frame.style.height = `${turned ? W : H}px`;
    this.sheet.style.width = `${W}px`;
    this.sheet.style.height = `${H}px`;
    // Turned about its top-left corner, then moved back into the frame.
    const shift = { 90: [H, 0], 180: [W, H], 270: [0, W] }[rot];
    this.sheet.style.transform = shift ? `translate(${shift[0]}px, ${shift[1]}px) rotate(${rot}deg)` : "";
    this.root.classList.toggle("turned", Boolean(rot));
    this.renderMode();
    this.renderZoom();
    this.sharpen();
  }

  renderZoom() {
    const pct = this.shownPct || this.zoomPct();
    const words = `${Math.round(pct)}%`;
    replace(this.zoomText, words);
    const fit = this.zoom === FIT_WIDTH;
    this.zoomText.title = fit ? `Zoom ${words} (the width fits): click for 100%` : `Zoom ${words}: click to fit the width (0)`;
    this.zoomText.setAttribute("aria-label", fit ? `Zoom ${words}, fit width. Show at 100%` : `Zoom ${words}. Fit the width`);
    this.outBtn.disabled = pct <= MIN_ZOOM + 0.5;
    this.inBtn.disabled = pct >= MAX_ZOOM - 0.5;
    this.fitWidthBtn.setAttribute("aria-pressed", String(fit));
    this.fitPageBtn.setAttribute("aria-pressed", String(this.zoom === FIT_PAGE));
    this.root.dataset.zoom = typeof this.zoom === "number" ? String(Math.round(this.zoom)) : this.zoom;
  }

  /** "+" or "-": the next step of ZOOM_STEPS up or down from the zoom shown. */
  zoomStep(dir, at = null) {
    const now = this.zoomPct();
    const next = dir > 0 ? ZOOM_STEPS.find((z) => z > now * 1.01) : [...ZOOM_STEPS].reverse().find((z) => z < now / 1.01);
    if (next !== undefined) this.setZoom(next, at);
  }

  /** The page fractions (x, y; 0..1 on the page, not clamped) under a point of the window, the rotation undone. */
  pageAt(clientX, clientY) {
    const r = this.frame.getBoundingClientRect();
    const a = (clientX - r.left) / (r.width || 1);
    const b = (clientY - r.top) / (r.height || 1);
    switch (this.rot) {
      case 90:
        return [b, 1 - a];
      case 180:
        return [1 - a, 1 - b];
      case 270:
        return [1 - b, a];
      default:
        return [a, b];
    }
  }

  /** Where a point of the page (fractions) is in the stage's scrolled content (px), the rotation applied. */
  stagePoint(x, y) {
    const [a, b] = { 90: [1 - y, x], 180: [1 - x, 1 - y], 270: [y, 1 - x] }[this.rot] || [x, y];
    return [this.frame.offsetLeft + a * this.frame.offsetWidth, this.frame.offsetTop + b * this.frame.offsetHeight];
  }

  /** Make a change to the page's size (zoom, rotation) keeping one point of it where it was on screen: the point
   *  under the pointer (`at`, the window's x and y), else the selected box on this page, else the middle. */
  keepInPlace(change, at = null, useSelected = true) {
    const st = this.stage;
    const sr = st.getBoundingClientRect();
    let ax = st.clientWidth / 2;
    let ay = st.clientHeight / 2;
    let point = null;
    if (this.pageInfo()) {
      if (at) {
        ax = at.x - sr.left - st.clientLeft;
        ay = at.y - sr.top - st.clientTop;
        point = this.pageAt(at.x, at.y);
      } else {
        const f = useSelected && this.byName.get(this.selected);
        const b = f && (f.boxes || []).find((x) => x[0] === this.page);
        point = b ? [(b[1] + b[3]) / 2, (b[2] + b[4]) / 2] : this.pageAt(sr.left + st.clientLeft + ax, sr.top + st.clientTop + ay);
      }
      point = [clamp(point[0], 0, 1), clamp(point[1], 0, 1)];
    }
    change();
    if (point) {
      const [x, y] = this.stagePoint(point[0], point[1]);
      st.scrollLeft = x - ax;
      st.scrollTop = y - ay;
    }
  }

  /** Turn the page shown a quarter turn clockwise: on screen only, for this invoice. */
  rotate() {
    if (!this.pageInfo()) return;
    this.hideTip();
    this.keepInPlace(() => {
      this.rotations.set(this.page, (this.rot + 90) % 360);
      this.sizeSheet();
      this.layoutTags();
    }, null, false);
    this.rotateBtn.setAttribute("aria-label", this.rot ? `Rotate (turned ${this.rot} degrees)` : "Rotate");
    this.reportHeight();
  }

  /* ----- The Hand: dragging the page about ----- */

  setHand(on) {
    this.hand = Boolean(on);
    this.renderHand();
  }

  renderHand() {
    this.handBtn.setAttribute("aria-pressed", String(this.hand));
    this.root.classList.toggle("hand", this.hand || this.spaceHand);
    this.renderMode();
  }

  /** The tag in the page's corner: the Hand on (how to turn it off), the page turned (on screen only). */
  renderMode() {
    const tags = [];
    if (this.hand) tags.push(["hand", "Hand: drag to move · H to turn off"]);
    else if (this.spaceHand) tags.push(["hand", "Hand: drag to move"]);
    if (this.rot && this.pageInfo()) tags.push(["turned", `Turned ${this.rot}°: view only, nothing is saved`]);
    const text = tags.map((t) => t[1]).join("\n");
    if (text === this.modeText) return;
    this.modeText = text;
    replace(this.modeTag, tags.map(([kind, words]) => h("span", { class: `iv-mode-tag ${kind}` }, words)));
  }

  panWanted(e) {
    return e.pointerType !== "touch" && (e.button === 1 || (e.button === 0 && (this.hand || this.spaceHand)));
  }

  panStart(e) {
    if (!this.panWanted(e) || !this.pageInfo() || this.pan) return;
    e.preventDefault();
    const st = this.stage;
    this.pan = { id: e.pointerId, x: e.clientX, y: e.clientY, left: st.scrollLeft, top: st.scrollTop, winY: window.scrollY, moved: false };
    st.setPointerCapture?.(e.pointerId);
    this.root.classList.add("panning");
    this.hideTip();
  }

  panMove(e) {
    const p = this.pan;
    if (!p || e.pointerId !== p.id) return;
    e.preventDefault();
    const dx = e.clientX - p.x;
    const dy = e.clientY - p.y;
    if (Math.hypot(dx, dy) > 3) p.moved = true;
    const st = this.stage;
    st.scrollLeft = p.left - dx;
    st.scrollTop = p.top - dy;
    // A viewer that grows with its page does not scroll up and down: the dashboard page does.
    if (st.scrollHeight <= st.clientHeight + 1) window.scrollTo(window.scrollX, p.winY - dy);
  }

  panEnd(e) {
    const p = this.pan;
    if (!p || e.pointerId !== p.id) return;
    this.pan = null;
    this.stage.releasePointerCapture?.(e.pointerId);
    this.root.classList.remove("panning");
    if (p.moved) {
      this.dragged = true; // the click that ends the move is not a click on what is under it
      setTimeout(() => (this.dragged = false), 0);
    }
  }

  /** Ctrl (or Cmd) + wheel, and a trackpad pinch (Chrome sends it as a wheel with ctrlKey): zoom where the pointer
   *  is, smoothly, and never the browser's own zoom of the whole dashboard. */
  onWheel(e) {
    if (!(e.ctrlKey || e.metaKey) || !this.pageInfo()) return;
    e.preventDefault();
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
    const dy = clamp(e.deltaY * unit, -WHEEL_STEP, WHEEL_STEP);
    this.wheelFactor = (this.wheelFactor || 1) * Math.exp(-dy * WHEEL_RATE);
    this.wheelAt = { x: e.clientX, y: e.clientY };
    if (this.wheelQueued) return;
    this.wheelQueued = true;
    requestAnimationFrame(() => {
      this.wheelQueued = false;
      const factor = this.wheelFactor;
      this.wheelFactor = 1;
      this.setZoom(clamp(this.zoomPct() * factor, MIN_ZOOM, MAX_ZOOM), this.wheelAt);
    });
  }

  /* ----- A sharper picture for a page zoomed in ----- */

  /** The page's picture at a scale: its address with ?scale= (scale 1 is the address as given). */
  srcAt(info, scale) {
    return scale > 1 ? `${info.src}${info.src.includes("?") ? "&" : "?"}scale=${scale}` : info.src;
  }

  /** The scale the page needs: screen pixels across the page shown over the picture's pixels at scale 1. */
  wantedScale() {
    const info = this.pageInfo();
    if (!info) return 1;
    const picture = Math.min(PICTURE_DPI / 72, PICTURE_SIDE / Math.max(info.width, info.height, 1)) * info.width;
    const shown = info.width * CSS_PX_PER_PT * ((this.shownPct || this.zoomPct()) / 100) * (window.devicePixelRatio || 1);
    const ratio = shown / (picture || 1);
    const step = SHARPER.find(([over]) => ratio > over);
    return step ? step[1] : 1;
  }

  sharpen() {
    clearTimeout(this.sharpTimer);
    this.sharpTimer = setTimeout(() => this.loadSharper(), 150); // once the wheel or pinch has stopped
  }

  /** Load the sharper picture aside, then swap it in: the page never goes blank. Zooming out keeps the sharp one. */
  loadSharper() {
    const info = this.pageInfo();
    if (!info || this.imgFor !== info.src) return;
    const want = this.wantedScale();
    if (want <= this.picScale) return;
    const src = this.srcAt(info, want);
    if (this.pending === src) return;
    this.pending = src;
    const pre = new Image();
    const swap = () => {
      if (this.pending !== src) return;
      this.pending = null;
      if (this.imgFor !== info.src || want <= this.picScale) return; // another page by now
      this.picScale = want;
      this.img.src = src;
    };
    pre.onload = () => (pre.decode ? pre.decode().then(swap, swap) : swap());
    pre.onerror = () => {
      if (this.pending === src) this.pending = null;
    };
    pre.src = src;
  }

  /** Each label tag beside its box: to the right when there is room, else to the left, else above. */
  layoutTags() {
    if (!this.tagEls || !this.tagEls.size) return;
    const W = this.sheet.clientWidth;
    const H = this.sheet.clientHeight;
    if (!W || !H) return;
    // On a small page the tags would cover the words they label: then only the hovered or selected one shows.
    this.root.classList.toggle("small-sheet", W < SMALL_SHEET);
    const placed = []; // tags already placed, so two never cover each other
    const hits = (r) => placed.find((p) => r.left < p.left + p.w + 2 && p.left < r.left + r.w + 2 && r.top < p.top + p.h && p.top < r.top + r.h);
    const tags = [...this.tagEls.values()].map((tag) => ({ tag, b: JSON.parse(tag.dataset.box), w: tag.offsetWidth, h: tag.offsetHeight }));
    tags.sort((a, b) => a.b[1] - b.b[1] || a.b[0] - b.b[0]);
    for (const { tag, b, w, h: th } of tags) {
      const [x0, y0, x1, y1] = b;
      const middle = ((y0 + y1) / 2) * H - th / 2;
      const tries = [
        { left: x1 * W + 4, top: middle, side: "right" },
        { left: x0 * W - 4 - w, top: middle, side: "left" },
        { left: Math.min(Math.max(2, x0 * W), W - w - 2), top: y0 * H - th - 3, side: "above" },
      ];
      let at = null;
      for (const t of tries) {
        const r = { left: t.left, top: Math.max(0, t.top), w, h: th, side: t.side };
        // Slide along past any tag in the way, as long as it stays on the page.
        for (let guard = 0; guard < 6; guard++) {
          const other = hits(r);
          if (!other) break;
          r.left = t.side === "left" ? other.left - 4 - w : other.left + other.w + 4;
        }
        if (r.left >= 2 && r.left + w <= W - 2 && !hits(r)) {
          at = r;
          break;
        }
      }
      at = at || { left: Math.min(Math.max(2, x1 * W + 4), W - w - 2), top: Math.max(0, middle), w, h: th, side: "right" };
      placed.push(at);
      tag.style.left = `${(at.left / W) * 100}%`;
      tag.style.top = `${(at.top / H) * 100}%`;
      tag.dataset.side = at.side;
    }
  }

  /** Zoom to "fit", "page" or a % (50 to 400). After zooming, the point under the pointer (`at`: wheel, pinch)
   *  stays there; else the selected box on this page is in the middle; else whatever was in the middle stays. */
  setZoom(value, at = null) {
    if (value !== FIT_WIDTH && value !== FIT_PAGE) {
      value = Number(value);
      if (!Number.isFinite(value)) return;
      value = clamp(value, MIN_ZOOM, MAX_ZOOM);
    }
    this.hideTip();
    this.keepInPlace(() => {
      this.zoom = value;
      this.saveZoom(value);
      this.sizeSheet();
      this.layoutTags();
    }, at);
    this.reportHeight();
  }

  /** The zoom kept from last time: "fit", "page" or a % (as saved by older versions too: "100", "150"). */
  savedZoom() {
    try {
      const v = window.localStorage.getItem(ZOOM_KEY);
      if (v === FIT_PAGE) return FIT_PAGE;
      const n = Number(v);
      return v && Number.isFinite(n) && n >= MIN_ZOOM && n <= MAX_ZOOM ? n : FIT_WIDTH;
    } catch (_e) {
      return FIT_WIDTH; // storage can be refused (private windows, sandboxed frames)
    }
  }

  saveZoom(value) {
    try {
      if (value === FIT_WIDTH) window.localStorage.removeItem(ZOOM_KEY);
      else window.localStorage.setItem(ZOOM_KEY, typeof value === "number" ? String(Math.round(value)) : value);
    } catch (_e) {
      /* the zoom then lasts until the page is left */
    }
  }

  showPage(number) {
    if (number < 1 || number > this.pages.length || number === this.page) return;
    this.page = number;
    this.hideTip();
    this.stage.scrollTop = 0;
    this.renderPage(true);
    this.reportHeight();
  }

  setOnlyCheck(on) {
    this.onlyCheck = on;
    this.onlyInput.checked = on;
    this.root.classList.toggle("only-check", on);
    this.hideTip();
  }

  /* ----- Selecting, hovering, revealing ----- */

  setHover(name, anchor) {
    if (this.hovered === name) return;
    this.hovered = name;
    for (const [field, row] of this.rows || []) row.classList.toggle("hover", field === name);
    for (const [field, g] of this.groups || []) g.classList.toggle("hover", field === name);
    for (const [field, tag] of this.tagEls || []) tag.classList.toggle("hover", field === name);
    if (!name) {
      this.hideTip();
      return;
    }
    const f = this.byName.get(name);
    if (anchor && f) this.showTip(anchor, this.tipContent(f), 120);
  }

  renderSelection() {
    for (const [field, row] of this.rows || []) {
      const on = field === this.selected;
      row.classList.toggle("selected", on);
      if (on) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
      row.classList.toggle("teaching", field === this.teach);
    }
    for (const [field, g] of this.groups || []) g.classList.toggle("selected", field === this.selected);
    for (const [field, tag] of this.tagEls || []) tag.classList.toggle("selected", field === this.selected);
    // The selected box is drawn last, so it is on top of any box it overlaps.
    const g = this.groups && this.groups.get(this.selected);
    if (g && g !== this.gFields.lastChild) this.gFields.append(g);
  }

  select(name, { from = "panel", send = true } = {}) {
    const f = this.byName.get(name);
    if (!f) return;
    this.selected = name;
    this.renderSelection();
    this.reveal(name, { flash: true, scrollPage: from !== "page", scrollPanel: from !== "panel" });
    const toCheck = this.toCheck();
    const at = toCheck.findIndex((x) => x.field === name);
    if (at >= 0) this.stepAt = at;
    this.renderStep();
    if (send) {
      this.sentSelects.set(name, Date.now());
      this.emit({ type: "select", field: name });
    }
  }

  reveal(name, { flash = false, scrollPage = true, scrollPanel = false } = {}) {
    const f = this.byName.get(name);
    if (!f) return;
    const row = this.rows && this.rows.get(name);
    const folded = row && row.closest("details");
    if (folded && !folded.open) folded.open = true;
    if (row && scrollPanel) this.scrollWithin(this.list, row);
    const boxes = (f.boxes || []).filter(Boolean);
    if (f.status === "missing" || !boxes.length) return;
    const target = boxes.find((b) => b[0] === this.page) || boxes[0];
    if (target[0] !== this.page) {
      this.page = target[0];
      this.stage.scrollTop = 0;
      this.renderPage(true);
      this.reportHeight();
    }
    const g = this.groups && this.groups.get(name);
    if (!g) return;
    if (scrollPage) this.scrollToBox(target, g);
    if (flash) this.flash(g);
  }

  /** Bring a box into view: inside the page's own scroll area, and the dashboard page around it if needed. */
  scrollToBox(b, g) {
    const behavior = reducedMotion() ? "auto" : "smooth";
    const st = this.stage;
    const scrollsX = st.scrollWidth > st.clientWidth + 1;
    const scrollsY = st.scrollHeight > st.clientHeight + 1;
    if (scrollsX || scrollsY) {
      const [cx, cy] = this.stagePoint((b[1] + b[3]) / 2, (b[2] + b[4]) / 2); // at any zoom, turned or not
      st.scrollTo({ left: scrollsX ? cx - st.clientWidth / 2 : st.scrollLeft, top: scrollsY ? cy - st.clientHeight / 2 : st.scrollTop, behavior });
    }
    if (!this.root.classList.contains("fixed")) {
      // The viewer grows to fit the page, so the dashboard page is what scrolls: only as far as needed.
      const box = g.querySelector(".iv-box") || g.querySelector("rect");
      if (box && box.scrollIntoView) {
        try {
          box.scrollIntoView({ block: "nearest", inline: "nearest", behavior });
        } catch (_e) {
          /* an old browser without the options */
        }
      }
    }
  }

  scrollWithin(container, el) {
    const c = container.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    const behavior = reducedMotion() ? "auto" : "smooth";
    if (container.scrollHeight > container.clientHeight + 1) {
      if (r.top < c.top) container.scrollBy({ top: r.top - c.top - 8, behavior });
      else if (r.bottom > c.bottom) container.scrollBy({ top: r.bottom - c.bottom + 8, behavior });
    }
    if (container.scrollWidth > container.clientWidth + 1) {
      if (r.left < c.left) container.scrollBy({ left: r.left - c.left - 8, behavior });
      else if (r.right > c.right) container.scrollBy({ left: r.right - c.right + 8, behavior });
    }
  }

  flash(g) {
    if (this.flashing && this.flashing !== g) this.flashing.classList.remove("flash");
    clearTimeout(this.flashTimer);
    this.flashing = g;
    g.classList.remove("flash");
    void g.getBoundingClientRect(); // restart the animation when it is already running
    g.classList.add("flash");
    this.flashTimer = setTimeout(() => g.classList.remove("flash"), 1700);
  }

  /* ----- Next to check ----- */

  toCheck() {
    const placed = [];
    const unplaced = [];
    for (const f of this.fields) {
      if (toneOf(f) === "verified") continue;
      const b = (f.boxes || [])[0];
      if (f.status === "missing" || !b) unplaced.push({ field: f.field });
      else placed.push({ field: f.field, page: b[0], x: b[1], cy: (b[2] + b[4]) / 2, h: b[4] - b[2] });
    }
    return [...readingOrder(placed), ...unplaced];
  }

  renderStep() {
    const list = this.toCheck();
    this.stepBtn.disabled = !list.length || Boolean(this.teach);
    replace(this.stepBtn.querySelector(".iv-step-text"), list.length ? "Next to check" : "All verified");
    const at = list.findIndex((x) => x.field === this.selected);
    replace(this.count, !list.length ? "" : at >= 0 ? `${at + 1} of ${list.length}` : `${list.length} to check`);
  }

  nextToCheck() {
    const list = this.toCheck();
    if (!list.length || this.teach) return;
    const at = list.findIndex((x) => x.field === this.selected);
    const next = list[(at + 1) % list.length];
    this.select(next.field, { from: "step" });
    const row = this.rows && this.rows.get(next.field);
    if (row && this.root.contains(document.activeElement) && document.activeElement !== this.stepBtn) {
      this.tipMuted = row; // the box is what to look at, not the card
      row.focus({ preventScroll: true });
    }
  }

  /* ----- A box dragged across the page (the mouse) ----- */

  /** Where the pointer is on the page (fractions, clamped to it), the rotation undone. */
  pointAt(e) {
    const [x, y] = this.pageAt(e.clientX, e.clientY);
    return [clamp(x, 0, 1), clamp(y, 0, 1)];
  }

  dragStart(e) {
    if (e.button !== 0 || e.pointerType === "touch" || !this.pageInfo()) return; // a finger scrolls the page
    if (this.panWanted(e)) return; // the Hand: the stage moves the page instead
    this.drag = { start: this.pointAt(e), x: e.clientX, y: e.clientY, id: e.pointerId, moved: false };
  }

  dragMove(e) {
    const d = this.drag;
    if (!d || e.pointerId !== d.id) return;
    if (!d.moved && Math.hypot(e.clientX - d.x, e.clientY - d.y) < 6) return; // a click, not a drag
    if (!d.moved) {
      d.moved = true;
      this.sheet.setPointerCapture?.(e.pointerId);
      this.root.classList.add("dragging");
      this.hideTip();
    }
    e.preventDefault();
    d.end = this.pointAt(e);
    const [x0, x1] = [d.start[0], d.end[0]].sort((a, b) => a - b);
    const [y0, y1] = [d.start[1], d.end[1]].sort((a, b) => a - b);
    d.rect = [x0, y0, x1, y1];
    this.gSel.replaceChildren(s("rect", { class: "iv-drag", x: x0, y: y0, width: x1 - x0, height: y1 - y0 }));
  }

  dragEnd(e) {
    const d = this.drag;
    this.drag = null;
    if (!d || e.pointerId !== d.id || !d.moved || !d.rect) return;
    this.root.classList.remove("dragging");
    this.dragged = true; // the click that follows the drag is not a click on what is under it
    setTimeout(() => (this.dragged = false), 0);
    if (this.teach) {
      // Teach mode: every word in the box is picked (dragging again adds more).
      this.gSel.replaceChildren();
      const pageWords = this.words[this.page - 1] || [];
      for (const w of this.wordsIn(this.page, d.rect)) {
        const i = pageWords.findIndex((x) => x.b === w.b);
        if (i >= 0) this.picks.set(`${this.page}:${i}`, { page: this.page, t: w.t, b: w.b });
      }
      this.renderWords();
      this.renderTeach();
      return;
    }
    this.emit({ type: "selection", page: this.page, rect: d.rect.map((v) => Math.round(v * 1e5) / 1e5), x: e.clientX, y: e.clientY });
  }

  dragCancel() {
    this.drag = null;
    this.root.classList.remove("dragging");
    this.gSel.replaceChildren();
  }

  /* ----- Teach mode ----- */

  startTeach(name) {
    const f = this.byName.get(name);
    this.teach = name;
    this.picks = new Map();
    this.root.classList.add("teaching");
    this.teachBar.hidden = false;
    this.hideTip();
    if (f) this.selected = name;
    const box = f && (f.boxes || [])[0];
    if (box && box[0] !== this.page && box[0] <= this.pages.length) {
      this.page = box[0];
      this.stage.scrollTop = 0;
    }
    this.renderTeach();
    this.renderStep();
  }

  endTeach() {
    if (!this.teach) return;
    this.teach = null;
    this.picks = new Map();
    this.root.classList.remove("teaching");
    this.teachBar.hidden = true;
    this.renderWords();
    this.renderSelection();
    this.renderStep();
    this.reportHeight();
  }

  renderTeach() {
    if (!this.teach) return;
    const f = this.byName.get(this.teach);
    const label = f ? f.label : this.teach.replace(/_/g, " ");
    const picked = this.pickedInOrder();
    const pageWords = this.words[this.page - 1] || [];
    const lines = [
      h("b", null, `Teach: ${label}`),
      h("span", null, "Click the words that hold the ", inSentence(label), ", then ", h("em", null, "Use these words"), "."),
    ];
    if (!pageWords.length) lines.push(h("span", { class: "iv-teach-warn" }, "This page has no words to pick from."));
    if (picked.length) lines.push(h("span", { class: "iv-teach-picked" }, `${picked.length === 1 ? "1 word" : `${picked.length} words`}: `, h("q", null, picked.map((p) => p.t).join(" "))));
    replace(this.teachText, lines);
    this.useBtn.disabled = !picked.length;
  }

  renderWords() {
    this.gWords.replaceChildren();
    if (!this.teach) return;
    const info = this.pageInfo();
    if (!info) return;
    const aspect = info.width / info.height;
    const padX = 0.0015;
    const padY = padX * aspect;
    (this.words[this.page - 1] || []).forEach((w, i) => {
      const b = w.b;
      const on = this.picks.has(`${this.page}:${i}`);
      const r = s("rect", { class: `iv-w${on ? " on" : ""}`, x: b[0] - padX, y: b[1] - padY, width: b[2] - b[0] + 2 * padX, height: b[3] - b[1] + 2 * padY, dataset: { i } });
      const title = s("title");
      title.textContent = w.t;
      r.append(title);
      this.gWords.append(r);
    });
  }

  toggleWord(i) {
    if (!this.teach) return;
    const w = (this.words[this.page - 1] || [])[i];
    if (!w) return;
    const k = `${this.page}:${i}`;
    if (this.picks.has(k)) this.picks.delete(k);
    else this.picks.set(k, { page: this.page, t: w.t, b: w.b });
    const r = this.gWords.querySelector(`.iv-w[data-i="${i}"]`);
    if (r) r.classList.toggle("on", this.picks.has(k));
    this.renderTeach();
    this.reportHeight();
  }

  pickedInOrder() {
    const items = [...this.picks.values()].map((p) => Object.assign({ x: p.b[0], cy: (p.b[1] + p.b[3]) / 2, h: p.b[3] - p.b[1] }, p));
    return readingOrder(items);
  }

  assign() {
    const picked = this.pickedInOrder();
    if (!this.teach || !picked.length) return;
    const field = this.teach;
    this.emit({
      type: "assign",
      field,
      text: picked.map((p) => p.t).join(" "),
      boxes: picked.map((p) => [p.page, ...p.b.map((v) => Math.round(v * 1e5) / 1e5)]),
    });
    this.endTeach();
    this.selected = field;
    this.renderSelection();
  }

  cancelTeach() {
    if (!this.teach) return;
    const field = this.teach;
    this.endTeach();
    this.emit({ type: "teach_cancel", field });
  }

  /* ----- Hover card ----- */

  showTip(anchor, children, delay = 0, placement = "below") {
    clearTimeout(this.tipTimer);
    const open = () => {
      if (!anchor.isConnected) return;
      replace(this.tip, children);
      this.tip.hidden = false;
      this.tipFor = anchor;
      this.placeTip(anchor, placement);
    };
    if (delay && this.tip.hidden) this.tipTimer = setTimeout(open, delay);
    else open();
  }

  placeTip(anchor, placement) {
    const at = anchor.getBoundingClientRect();
    const tip = this.tip;
    const w = tip.offsetWidth;
    const tall = tip.offsetHeight;
    const gap = 8;
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    let left;
    let top;
    if (placement === "side" && at.right + gap + w <= vw - gap) {
      left = at.right + gap;
      top = at.top;
    } else {
      left = at.left + at.width / 2 - w / 2;
      top = at.bottom + gap;
      if (top + tall > vh - gap && at.top - tall - gap >= gap) top = at.top - tall - gap;
    }
    tip.style.left = `${Math.max(gap, Math.min(left, vw - w - gap))}px`;
    tip.style.top = `${Math.max(gap, Math.min(top, vh - tall - gap))}px`;
  }

  hideTip() {
    clearTimeout(this.tipTimer);
    this.tip.hidden = true;
    this.tipFor = null;
  }

  /* ----- Keys ----- */

  onKey(e) {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "Escape") {
      if (this.teach) {
        e.preventDefault();
        this.cancelTeach();
      } else this.hideTip();
      return;
    }
    // A box the review screen focused on opening, not yet used, keeps no Space from the Hand over the page (D-76).
    const unused = e.key === " " && e.target.dataset?.autofocused === "1" && this.overStage;
    if (isTyping(e.target) && !unused) return;
    if ((e.key === "n" || e.key === "N") && !e.shiftKey) {
      e.preventDefault();
      this.nextToCheck();
      return;
    }
    if (this.viewKey(e)) return;
    const row = e.target.closest && e.target.closest(".iv-row");
    if (row && ["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
      const rows = [...this.list.querySelectorAll(".iv-row")].filter((r) => r.getClientRects().length);
      const i = rows.indexOf(row);
      const j = e.key === "Home" ? 0 : e.key === "End" ? rows.length - 1 : e.key === "ArrowDown" || e.key === "ArrowRight" ? Math.min(rows.length - 1, i + 1) : Math.max(0, i - 1);
      if (j !== i) {
        e.preventDefault();
        rows[j].focus();
        this.scrollWithin(this.list, rows[j]);
      }
    }
  }

  /** The card the viewer is in (the review screen's Document card), else the viewer. */
  card() {
    return this.root.closest(".inv-doc") || this.root;
  }

  /** The page's keys: + = - zoom, 0 fit width, 9 (or Shift+0) fit page, H the Hand, Space held the Hand for a
   *  moment, R rotate, Page Up / Page Down the page. True when the key was one of them. */
  viewKey(e) {
    if (!this.pageInfo() || (e.target.closest && e.target.closest("dialog[open], [role=dialog]"))) return false;
    const k = e.key;
    if (k === " ") {
      // Space holds the Hand with the focus on the page itself, or with the focus nowhere (the body) and the pointer
      // on the page. A box, a button or a link anywhere else keeps Space for itself, the pointer wherever it rests
      // (B-591); with neither, it scrolls the dashboard page as usual.
      const onPage = this.stage.contains(e.target);
      // (a box the review screen focused on opening, not yet used by the clerk, counts as nowhere: D-76)
      const nowhere = !e.target || e.target === document.body || e.target === document.documentElement ||
        e.target.dataset?.autofocused === "1";
      if (!onPage && !(nowhere && this.overStage)) return false;
      e.preventDefault();
      this.spaceTaken = true;
      if (!this.spaceHand) {
        this.spaceHand = true;
        this.renderHand();
      }
      return true;
    }
    let act = null;
    if (k === "+" || k === "=") act = () => this.zoomStep(1);
    else if (k === "-" || k === "_" || k === "\u2212") act = () => this.zoomStep(-1);
    else if (k === "9" || (e.code === "Digit0" && e.shiftKey)) act = () => this.setZoom(FIT_PAGE);
    else if (k === "0") act = () => this.setZoom(FIT_WIDTH);
    else if (k === "h" || k === "H") act = () => this.setHand(!this.hand);
    else if (k === "r" || k === "R") act = () => this.rotate();
    // Page Up / Page Down turn the invoice's pages with the pointer over the Document card or the focus in it; else
    // they scroll the screen as usual (B-592).
    else if ((k === "PageDown" || k === "PageUp") && this.pages.length > 1 && (this.overCard || this.card().contains(e.target))) {
      act = () => this.showPage(this.page + (k === "PageDown" ? 1 : -1));
    }
    if (!act) return false;
    e.preventDefault();
    act();
    return true;
  }

  /* ----- Talking to the page ----- */

  emit(value) {
    this.lastSeq = Math.max(this.lastSeq + 1, Date.now());
    try {
      this.onEvent(Object.assign({}, value, { seq: this.lastSeq }));
    } catch (err) {
      console.error("invoice viewer:", err); // eslint-disable-line no-console
    }
  }

  /** Called whenever the viewer's height may change: nothing to do, the page sizes itself. */
  reportHeight() {}

  /* ----- Called by the page ----- */

  /** The words on each page ([[{t, b: [x0, y0, x1, y1]}]]), for teach mode. */
  setWords(words) {
    this.words = Array.isArray(words) ? words : [];
    if (this.teach) {
      this.renderWords();
      this.renderTeach();
    }
  }

  /** Start teach mode for a field: the reviewer clicks the words that hold it, then "Use these words". */
  teachField(name) {
    this.argTeach = name;
    this.startTeach(name);
    this.renderPage(false);
    this.renderSelection();
    this.root.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
  }

  /** Light a line item's outline up (its row in the grid is pointed at), or not; on another page, nothing. */
  markLine(i, on) {
    for (const g of this.gLines.querySelectorAll(".iv-lg.hot")) g.classList.remove("hot");
    if (!on) return;
    this.gLines.querySelector(`.iv-lg[data-idx="${Number(i)}"]`)?.classList.add("hot");
  }

  /** The words on a page inside a box (fractions of the page), in reading order: [{t, b, page}]. */
  wordsIn(page, rect) {
    const [x0, y0, x1, y1] = rect;
    const inside = (this.words[page - 1] || [])
      .filter((w) => {
        const cx = (w.b[0] + w.b[2]) / 2;
        const cy = (w.b[1] + w.b[3]) / 2;
        return cx >= x0 && cx <= x1 && cy >= y0 && cy <= y1;
      })
      .map((w) => ({ t: w.t, b: w.b, page, x: w.b[0], cy: (w.b[1] + w.b[3]) / 2, h: w.b[3] - w.b[1] }));
    return readingOrder(inside);
  }

  /** Show (or with null, clear) the box dragged on the page while the page asks what it is. */
  markSelection(page, rect) {
    this.gSel.replaceChildren();
    if (!rect || page !== this.page) return;
    const [x0, y0, x1, y1] = rect;
    this.gSel.append(s("rect", { class: "iv-drag done", x: x0, y: y0, width: x1 - x0, height: y1 - y0 }));
  }

  /** A field changed on the form: its row says "Edited" with the new value, or "Not on this invoice" (``absent``);
   *  ``value`` null: the reading again. */
  setEdited(name, value, absent = false) {
    if (value === null || value === undefined) {
      if (!this.edits.delete(name)) return;
    } else {
      const was = this.edits.get(name);
      if (was && was.value === value && was.absent === absent) return;
      this.edits.set(name, { value: String(value), absent: Boolean(absent) });
    }
    if (!this.byName.has(name)) return;
    const focused = this.rows && [...this.rows.entries()].find(([, r]) => r === document.activeElement);
    this.renderPanel();
    this.renderSelection();
    if (focused) this.rows.get(focused[0])?.focus({ preventScroll: true });
  }

  /** The edits shown (to put back on Undo). */
  editsNow() {
    return new Map(this.edits);
  }

  restoreEdits(edits) {
    this.edits = new Map(edits);
    this.renderPanel();
    this.renderSelection();
  }

  /** A field has a box on the page to show. */
  hasBox(name) {
    const f = this.byName.get(name);
    return Boolean(f && f.status !== "missing" && (f.boxes || []).length);
  }

  /** The word under a point of the window on the page shown ({t, b, page} or null): the words must be set. */
  wordAt(clientX, clientY) {
    if (!this.pageInfo()) return null;
    const [x, y] = this.pageAt(clientX, clientY);
    const pad = 0.002;
    const w = (this.words[this.page - 1] || []).find((v) => x >= v.b[0] - pad && x <= v.b[2] + pad && y >= v.b[1] - pad && y <= v.b[3] + pad);
    return w ? { t: w.t, b: w.b, page: this.page } : null;
  }

  /** The words on the same printed line as ``word`` (a row of the line table), in reading order. */
  lineOf(word) {
    const cy = (word.b[1] + word.b[3]) / 2;
    const ht = word.b[3] - word.b[1];
    const same = (this.words[word.page - 1] || []).filter((v) => Math.abs((v.b[1] + v.b[3]) / 2 - cy) <= 0.6 * Math.max(ht, v.b[3] - v.b[1]));
    return readingOrder(same.map((v) => ({ t: v.t, b: v.b, page: word.page, x: v.b[0], cy: (v.b[1] + v.b[3]) / 2, h: v.b[3] - v.b[1] })));
  }

  /** Highlight a field's box (its input in the form got the focus), without telling the page back. */
  show(name) {
    if (!this.byName.has(name) || this.teach) return;
    this.select(name, { from: "form", send: false });
  }
}
