/* AP Coder invoice viewer: the invoice page with a box over every field read from it.
 *
 * A Streamlit custom component with no build step. It speaks Streamlit's component protocol itself:
 * "streamlit:render" messages bring the arguments; it answers with componentReady, setFrameHeight and
 * setComponentValue. Coordinates are fractions of the page (0..1, origin top-left), pages numbered from 1.
 * All state lives on the Viewer instance, so nothing is shared between two viewers.
 */
(function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const CSS_PX_PER_PT = 96 / 72; // 100% = the page's printed size on a 96 dpi screen
  const ZOOMS = [
    ["fit", "Fit", "Fit the page to the width"],
    ["100", "100%", "Printed size"],
    ["150", "150%", "Half as big again"],
  ];
  const ECHO_MS = 20000; // a `selected` from Python equal to a field clicked here this recently is its echo
  const SMALL_SHEET = 560; // px: a page drawn narrower than this shows a label tag only on hover or selection
  const ZOOM_KEY = "apc-invoice-viewer-zoom";
  const FIELDS_KEY = "apc-invoice-viewer-fields"; // the field list shown or folded, when it sits above the page
  const FOLD_BELOW = 640; // px: a viewer shorter than this starts with the field list folded (above the page only)
  const VIEWPORT_MARGIN = 230; // px of the browser window kept for the card around the viewer and the action bar
  const MIN_FIT_HEIGHT = 360;
  // The field list, grouped as an AP clerk reads an invoice; fields not found go last, folded away.
  const GROUPS = [
    ["Supplier", ["vendor_name", "gst_hst_registration_number", "qst_registration_number"]],
    ["Invoice", ["invoice_number", "invoice_date", "due_date", "po_number", "payment_terms", "currency"]],
    ["Amounts & tax", ["subtotal", "other_charges", "gst_amount", "hst_amount", "pst_amount", "qst_amount", "tax_total", "grand_total"]],
  ];
  // A shape for each status as well as a colour, so the status never depends on colour alone.
  const GLYPH = { verified: "\u2713", likely: "\u2713", check: "!", failed: "\u2715", missing: "" };
  const STATUS_TEXT = { verified: "Verified", likely: "Likely", check: "Check", missing: "Not found", failed: "Failed check" };
  const STATUS_HELP = {
    verified: "Readers agree and the checks pass",
    likely: "One strong reading, checks pass: a quick look",
    check: "Readers disagree or are unsure: look at it",
    missing: "Not found on the invoice",
    failed: "A check failed: look at it",
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
  };

  function icon(name, size = 16) {
    const el = s("svg", { viewBox: "0 0 24 24", width: size, height: size, class: "iv-ic", "aria-hidden": "true" });
    el.append(s("path", { d: ICONS[name], fill: "none", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" }));
    return el;
  }

  function isTyping(target) {
    return target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) && target.type !== "checkbox";
  }

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

  /* ---------- Streamlit's component protocol ---------- */

  function post(type, data) {
    window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type }, data || {}), "*");
  }

  /* ---------- The viewer ---------- */

  class Viewer {
    constructor(root) {
      this.root = root;
      this.args = null;
      this.pages = [];
      this.fields = [];
      this.byName = new Map();
      this.words = [];
      this.lineItems = [];
      this.page = 1;
      this.zoom = this.savedZoom();
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
      this.build();
    }

    /* ----- Building the frame (once) ----- */

    build() {
      this.summary = h("span", { class: "iv-summary" });
      // Only shown when the list sits above the page (a narrow viewer): it folds away to give the page the room.
      this.foldBtn = h("button", { type: "button", class: "iv-fold", "aria-expanded": "true", onclick: () => this.setFieldsOpen(!this.root.classList.contains("fields-open"), true) }, "Hide");
      this.meter = h("div", { class: "iv-meter", "aria-hidden": "true" });
      this.list = h("div", { class: "iv-list", "aria-label": "Fields read from the invoice" });
      this.panel = h(
        "aside",
        { class: "iv-panel", "aria-label": "Fields" },
        h("div", { class: "iv-panel-head" }, h("h2", null, "Fields"), this.summary, this.foldBtn),
        this.meter,
        this.list
      );

      this.zoomButtons = ZOOMS.map(([value, words, title]) =>
        h("button", { type: "button", class: "iv-seg", title, "aria-pressed": String(value === this.zoom), dataset: { zoom: value }, onclick: () => this.setZoom(value) }, words)
      );
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
      this.onlyInput = h("input", { type: "checkbox", role: "switch", onchange: () => this.setOnlyCheck(this.onlyInput.checked) });
      const only = h("label", { class: "iv-switch" }, this.onlyInput, h("span", { class: "iv-track", "aria-hidden": "true" }), h("span", null, "Only what needs checking"));
      this.toolbar = h(
        "div",
        { class: "iv-toolbar", role: "toolbar", "aria-label": "Page tools" },
        h("div", { class: "iv-segs", role: "group", "aria-label": "Zoom" }, this.zoomButtons),
        this.pager,
        h("div", { class: "iv-check" }, this.stepBtn, this.count),
        only
      );
      this.legend = h("div", { class: "iv-legend" });

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
      this.svg.append(this.gLines, this.gFields, this.gWords);
      this.tags = h("div", { class: "iv-tags" });
      this.sheet = h("div", { class: "iv-sheet loading" }, this.img, this.svg, this.tags);
      this.empty = h("p", { class: "iv-empty", hidden: true }, "No page to show.");
      this.stage = h("div", { class: "iv-stage", tabindex: "-1" }, this.sheet, this.empty);
      this.main = h("section", { class: "iv-main", "aria-label": "Invoice page" }, this.toolbar, this.legend, this.teachBar, this.stage);
      this.tip = h("div", { class: "iv-tip", role: "tooltip", id: "iv-tip", hidden: true });
      replace(this.root, this.panel, this.main, this.tip);

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
        const g = e.target.closest(".iv-f");
        if (g) this.select(g.dataset.field, { from: "page" });
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
        if (r) this.showTip(r, [h("div", { class: "iv-tip-head" }, h("b", null, "Line item")), h("p", { class: "iv-tip-value" }, r.dataset.label)], 0);
      });
      this.gLines.addEventListener("pointerout", () => this.hideTip());
      this.gWords.addEventListener("click", (e) => {
        const r = e.target.closest(".iv-w");
        if (r) this.toggleWord(Number(r.dataset.i));
      });

      this.stage.addEventListener("scroll", () => this.hideTip(), { passive: true });
      window.addEventListener("keydown", (e) => this.onKey(e));
      window.addEventListener("blur", () => this.hideTip());
      new ResizeObserver(() => {
        this.layoutTags();
        this.reportHeight();
      }).observe(this.root);
      new ResizeObserver(() => this.layoutTags()).observe(this.sheet);
      try {
        window.parent.addEventListener("resize", () => {
          this.applyHeight();
          this.reportHeight();
        });
      } catch (_e) {
        /* the height then follows only the arguments */
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
      if (!samePages) this.page = 1;
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
      let height = this.maxHeight;
      try {
        const vh = window.parent.innerHeight || window.innerHeight;
        if (vh) height = Math.min(height, Math.max(MIN_FIT_HEIGHT, vh - VIEWPORT_MARGIN));
      } catch (_e) {
        /* a frame that cannot see its page keeps the height asked for */
      }
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
        const tone = toneOf(f);
        const missing = f.status === "missing";
        const value = missing || !f.display ? h("span", { class: "iv-row-value none" }, missing ? "Not found" : "—") : h("span", { class: "iv-row-value" }, f.display);
        const row = h(
          "button",
          {
            type: "button",
            role: "listitem",
            class: `iv-row t-${tone}`,
            dataset: { field: f.field },
            "aria-describedby": "iv-tip",
            "aria-label": `${f.label}: ${missing ? "not found" : f.display || "blank"}. ${STATUS_TEXT[tone]}${missing ? "" : `, ${pct(f.confidence)} confidence`}.`,
            onclick: () => {
              // The card would cover the box just brought into view: it stays shut until the pointer leaves.
              this.tipMuted = row;
              this.hideTip();
              this.select(f.field, { from: "panel" });
            },
          },
          h("span", { class: "iv-dot", "aria-hidden": "true" }, GLYPH[tone]),
          h("span", { class: "iv-row-text" }, h("span", { class: "iv-row-label" }, f.label), value),
          h("span", { class: "iv-badge", "aria-hidden": "true", title: STATUS_TEXT[tone] }, missing ? "—" : pct(f.confidence))
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
                const said = plainValue(value);
                const agrees = said !== "" && (norm(value) === want || norm(value) === shown);
                return h(
                  "tr",
                  null,
                  h("th", { scope: "row" }, READERS[reader] || reader.replace(/_/g, " ")),
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
      this.sheet.hidden = !info;
      const many = this.pages.length > 1;
      this.pager.hidden = !many;
      replace(this.where, info ? `Page ${this.page} of ${this.pages.length}` : "");
      this.prevBtn.disabled = this.page <= 1;
      this.nextBtn.disabled = this.page >= this.pages.length;
      if (!info) return;
      if (newPicture || this.img.getAttribute("src") !== info.src) {
        this.sheet.classList.add("loading");
        this.img.src = info.src;
        this.img.alt = `Invoice page ${this.page} of ${this.pages.length}`;
      }
      this.sheet.style.aspectRatio = `${info.width} / ${info.height}`;
      this.sizeSheet();

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
        const first = boxes[0];
        const tag = h("span", { class: `iv-tag t-${tone}`, dataset: { field: f.field, box: JSON.stringify([first[1] - padX, first[2] - padY, first[3] + padX, first[4] + padY]) } }, f.label);
        this.tags.append(tag);
        this.tagEls.set(f.field, tag);
      }
      this.renderWords();
      this.renderSelection();
      this.layoutTags();
    }

    sizeSheet() {
      const info = this.pageInfo();
      if (!info) return;
      const zoomed = this.zoom !== "fit";
      this.sheet.style.width = zoomed ? `${Math.round(info.width * CSS_PX_PER_PT * (Number(this.zoom) / 100))}px` : "";
      this.stage.classList.toggle("zoomed", zoomed);
      for (const b of this.zoomButtons) b.setAttribute("aria-pressed", String(b.dataset.zoom === this.zoom));
      const fitPct = this.sheet.clientWidth ? Math.round((this.sheet.clientWidth / (info.width * CSS_PX_PER_PT)) * 100) : 0;
      this.zoomButtons[0].title = fitPct ? `Fit the page to the width (${fitPct}%)` : "Fit the page to the width";
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

    setZoom(value) {
      if (!ZOOMS.some(([v]) => v === value)) return;
      // After zooming, the selected box on this page is in the middle; else whatever was in the middle stays there.
      const st = this.stage;
      const sx = this.sheet.offsetLeft;
      const sy = this.sheet.offsetTop;
      let cx = (st.scrollLeft + st.clientWidth / 2 - sx) / (this.sheet.offsetWidth || 1);
      let cy = (st.scrollTop + st.clientHeight / 2 - sy) / (this.sheet.offsetHeight || 1);
      const f = this.byName.get(this.selected);
      const b = f && (f.boxes || []).find((x) => x[0] === this.page);
      if (b) {
        cx = (b[1] + b[3]) / 2;
        cy = (b[2] + b[4]) / 2;
      }
      this.zoom = value;
      this.saveZoom(value);
      this.hideTip();
      this.sizeSheet();
      this.layoutTags();
      st.scrollLeft = this.sheet.offsetLeft + cx * this.sheet.offsetWidth - st.clientWidth / 2;
      st.scrollTop = this.sheet.offsetTop + cy * this.sheet.offsetHeight - st.clientHeight / 2;
      this.reportHeight();
    }

    savedZoom() {
      try {
        const v = window.localStorage.getItem(ZOOM_KEY);
        return ZOOMS.some(([z]) => z === v) ? v : "fit";
      } catch (_e) {
        return "fit"; // storage can be refused (private windows, sandboxed frames)
      }
    }

    saveZoom(value) {
      try {
        if (value === "fit") window.localStorage.removeItem(ZOOM_KEY);
        else window.localStorage.setItem(ZOOM_KEY, value);
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

    /** Bring a box into view: inside the page's own scroll area, and the Streamlit page around it if needed. */
    scrollToBox(b, g) {
      const behavior = reducedMotion() ? "auto" : "smooth";
      const st = this.stage;
      const W = this.sheet.offsetWidth;
      const H = this.sheet.offsetHeight;
      const ox = this.sheet.offsetLeft;
      const oy = this.sheet.offsetTop;
      const scrollsX = st.scrollWidth > st.clientWidth + 1;
      const scrollsY = st.scrollHeight > st.clientHeight + 1;
      if (scrollsX || scrollsY) {
        const cx = ox + ((b[1] + b[3]) / 2) * W;
        const cy = oy + ((b[2] + b[4]) / 2) * H;
        st.scrollTo({ left: scrollsX ? cx - st.clientWidth / 2 : st.scrollLeft, top: scrollsY ? cy - st.clientHeight / 2 : st.scrollTop, behavior });
      }
      if (!this.root.classList.contains("fixed")) {
        // The viewer grows to fit the page, so the Streamlit page is what scrolls: only as far as needed.
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
      if (isTyping(e.target)) return;
      if ((e.key === "n" || e.key === "N") && !e.shiftKey) {
        e.preventDefault();
        this.nextToCheck();
        return;
      }
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

    /* ----- Talking to Streamlit ----- */

    emit(value) {
      this.lastSeq = Math.max(this.lastSeq + 1, Date.now()); // counts up, also across a reload of the frame
      post("streamlit:setComponentValue", { value: Object.assign({}, value, { seq: this.lastSeq }), dataType: "json" });
    }

    reportHeight() {
      requestAnimationFrame(() => {
        const height = Math.ceil(this.root.getBoundingClientRect().height);
        if (height && height !== this.lastHeight) {
          this.lastHeight = height;
          post("streamlit:setFrameHeight", { height });
        }
      });
    }
  }

  /* ---------- Start ---------- */

  const viewer = new Viewer(document.getElementById("iv"));

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.type !== "streamlit:render") return;
    const theme = data.theme;
    if (theme && (theme.base === "dark" || theme.base === "light")) document.documentElement.dataset.theme = theme.base;
    try {
      viewer.update(data.args || {});
    } catch (err) {
      console.error("invoice viewer:", err); // eslint-disable-line no-console
    }
  });

  post("streamlit:componentReady", { apiVersion: 1 });
})();
