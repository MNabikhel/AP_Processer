/* The picker: a searchable list for the long choices (a GL account, a cost center, a vendor), D-56. Plain
   JavaScript, nothing from the internet. Loaded on every page (base.html); page scripts import it as "./picker.js".

   * select[data-picker]: the select stays in the form, with its name, and is what is posted; it is only hidden, under
     a text box that shows its choice. Focus, a click or typing opens the list: every word typed must be in the code or
     the name (accents and case aside), the words found are marked. Picking sets the select's value and fires its
     "input" and "change", as a choice in the select itself would.
       data-options="gl-options"  the choices are the JSON [[value, label], ...] of <script id="gl-options">, printed
                                  once in the page (the select itself holds only its own choice); else its options
       data-groups="gl"           groups shown first while nothing is typed (registerGroups)
       data-picker-noun="account" "No account matches …", "All accounts"
       data-picker-line           its label says the line: "Line 3 GL account" (the row's line_number)
   * input[data-picker="free"]: the text typed is the value; the list only offers what to type.
       data-choices="CAD|Canadian dollar,USD|US dollar"   the choices, else setSource(input, () => items)
   * Keys: ↑/↓, PageUp/PageDown, Home/End move; Enter picks; Esc closes (a select's choice as it was); Tab picks the
     highlighted choice when something was typed, then moves on; Alt+↓ opens.
   * The list is drawn in one fixed layer on the page's body (a grid's overflow never cuts it off), under the box, or
     above it when there is no room below (the review screen's approve bar counts as the bottom of the window).

   Without JavaScript the select is an ordinary select (with data-options, holding only its own choice). */

const LIGATURES = { "œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ß": "ss", "ø": "o", "Ø": "O", "ł": "l" };

/** Lower case, without accents and ligatures: "Hydro-Québec" and "hydro-quebec" read the same. invoice.js vendorKey
    builds on it: keep it in step with memory.py (tests/test_web_invoice.py compares the vendor keys). */
export function fold(text) {
  return (text || "")
    .replace(/[œŒæÆßøØł]/g, (c) => LIGATURES[c])
    .normalize("NFKD")
    .replace(/\p{Mn}/gu, "")
    .toLowerCase();
}

const ROWS = 12; // rows on screen at most, the rest scrolls
const ROW = 30; // px, as .pk-option
const MOST = 400; // choices drawn at most: more is found by typing

const pickers = new WeakMap(); // select or input -> its Picker
const sources = new WeakMap(); // free input -> () => items
const groupMakers = new Map(); // data-groups name -> (select) => [{label, values}]
const jsonLists = new Map(); // script id -> items
let layer = null;
let listbox = null;
let note = null;
let shown = null; // the Picker whose list is open

/* ---------- Text, marked where the words typed are ---------- */

/** fold(text), with where in ``text`` each of its characters starts (from) and ends (to). */
function foldMap(text) {
  let folded = "";
  const from = [];
  const to = [];
  let i = 0;
  for (const ch of text) {
    const f = fold(ch);
    for (let k = 0; k < f.length; k++) {
      from.push(i);
      to.push(i + ch.length);
    }
    if (!f.length && to.length) to[to.length - 1] = i + ch.length; // a separate accent: marked with its letter
    folded += f;
    i += ch.length;
  }
  return { folded, from, to };
}

function marked(text, words) {
  const out = document.createDocumentFragment();
  if (!words.length || !text) {
    out.append(text || "");
    return out;
  }
  const { folded, from, to } = foldMap(text);
  const ranges = [];
  for (const w of words) {
    for (let j = folded.indexOf(w); j >= 0; j = folded.indexOf(w, j + w.length)) {
      ranges.push([from[j], to[j + w.length - 1]]);
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  let at = 0;
  for (const [a, b] of ranges) {
    if (b <= at) continue;
    const start = Math.max(a, at);
    if (start > at) out.append(text.slice(at, start));
    const m = document.createElement("mark");
    m.textContent = text.slice(start, b);
    out.append(m);
    at = b;
  }
  if (at < text.length) out.append(text.slice(at));
  return out;
}

/* ---------- Choices ---------- */

/** {value, label, code, name, extra}: "6000 · Office supplies" is code 6000, name "Office supplies". */
function item(value, label, extra = "") {
  const text = String(label ?? value ?? "");
  const cut = text.indexOf(" · ");
  return made(value, text, cut > 0 ? text.slice(0, cut) : "", cut > 0 ? text.slice(cut + 3) : text, extra);
}

function made(value, label, code, name, extra) {
  return { value: String(value ?? ""), label, code, name, extra, hay: fold(`${code} ${name} ${extra}`), lead: fold(code || name) };
}

function fromJson(id) {
  if (jsonLists.has(id)) return jsonLists.get(id);
  let rows = [];
  try {
    rows = JSON.parse(document.getElementById(id)?.textContent || "[]") || [];
  } catch {
    rows = [];
  }
  const items = rows.map((r) => (Array.isArray(r) ? item(r[0], r[1]) : item(r.value, r.label, r.extra)));
  jsonLists.set(id, items);
  return items;
}

function words(query) {
  return fold(query).split(/\s+/).filter(Boolean);
}

/** The choices with every word in them: the ones whose code (or name) starts with the first word first, then a word
    of the name starting with it, then the rest, each in the list's order. */
function matching(items, ws) {
  const first = ws[0];
  const ranked = [];
  items.forEach((it, i) => {
    if (!ws.every((w) => it.hay.includes(w))) return;
    const rank = it.lead.startsWith(first) ? 0 : ` ${it.hay}`.includes(` ${first}`) ? 1 : 2;
    ranked.push([rank, i, it]);
  });
  return ranked.sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((r) => r[2]);
}

/* ---------- The layer the list is drawn in ---------- */

function makeLayer() {
  if (layer) return;
  layer = document.createElement("div");
  layer.className = "pk-layer";
  layer.dataset.pickerLayer = "";
  layer.hidden = true;
  listbox = document.createElement("div");
  listbox.className = "pk-list";
  listbox.id = "pk-listbox";
  listbox.setAttribute("role", "listbox");
  note = document.createElement("div");
  note.className = "pk-note";
  note.hidden = true;
  layer.append(listbox, note);
  document.body.append(layer);
  layer.addEventListener("mousedown", (e) => e.preventDefault()); // the focus stays in the box
  layer.addEventListener("click", (e) => {
    const opt = e.target.closest("[role=option]");
    if (opt && shown) shown.pick(Number(opt.dataset.i));
  });
  layer.addEventListener("mousemove", (e) => {
    const opt = e.target.closest("[role=option]");
    if (opt && shown && Number(opt.dataset.i) !== shown.active) shown.setActive(Number(opt.dataset.i), false);
  });
}

function labelText(select) {
  const own = select.getAttribute("aria-label");
  if (own) return own;
  const label = select.labels?.[0];
  if (!label) return "";
  return [...label.childNodes].filter((n) => n.nodeType === Node.TEXT_NODE).map((n) => n.textContent).join(" ").trim();
}

/* ---------- A picker ---------- */

class Picker {
  constructor(el) {
    this.free = el.tagName === "INPUT";
    this.select = this.free ? null : el;
    this.active = -1;
    this.view = [];
    this.typed = false;
    if (this.free) {
      this.input = el;
    } else {
      const wrap = document.createElement("span");
      wrap.className = "pk";
      el.before(wrap);
      wrap.append(el);
      this.input = document.createElement("input");
      this.input.type = "text";
      this.input.className = "pk-input";
      this.input.dataset.pickerName = el.name || "";
      wrap.append(this.input);
      el.classList.add("pk-native");
      el.tabIndex = -1;
      el.setAttribute("aria-hidden", "true");
      el.addEventListener("focus", () => this.input.focus()); // focused by a script (G, a finding's link)
    }
    const input = this.input;
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", "pk-listbox");
    input.autocomplete = "off";
    input.spellcheck = false;
    this.name();
    this.sync();
    input.addEventListener("focus", () => {
      this.name();
      if (!this.free) this.open();
    });
    input.addEventListener("click", () => this.open());
    input.addEventListener("input", (e) => {
      if (!e.isTrusted) return; // ours, after a pick
      if (!this.isOpen()) this.open(true);
      this.typed = true;
      this.render();
    });
    input.addEventListener("keydown", (e) => this.key(e));
    input.addEventListener("blur", () => this.close());
  }

  /** The box's label: its select's, with the line number when it is a line's ("Line 3 GL account"). */
  name() {
    if (this.free) return;
    let text = labelText(this.select);
    if ("pickerLine" in this.select.dataset) {
      const n = this.select.closest("tr")?.querySelector('[name="line_number"]')?.value;
      text = `${n ? `Line ${n} ` : ""}${text.replace(/^Line \S+ /, "")}`;
    }
    if (text) this.input.setAttribute("aria-label", text);
  }

  noun() {
    return (this.free ? this.input : this.select).dataset.pickerNoun || "choice";
  }

  items() {
    const el = this.free ? this.input : this.select;
    if (el.dataset.options) return fromJson(el.dataset.options);
    if (this.free) {
      const source = sources.get(el);
      if (source) return source().map((v) => made(v.value, v.value, "", v.name || v.value, v.extra || ""));
      return (el.dataset.choices || "").split(",").filter(Boolean).map((c) => {
        const [value, about] = c.split("|");
        return item(value.trim(), about ? `${value.trim()} · ${about.trim()}` : value.trim());
      });
    }
    return [...this.select.options].map((o) => item(o.value, o.textContent));
  }

  /** The text box shows the select's choice (also after a script changed it). */
  sync() {
    if (this.free || this.isOpen()) return;
    const label = this.select.selectedOptions[0]?.textContent ?? "";
    this.input.value = label;
    this.input.title = label;
  }

  isOpen() {
    return shown === this;
  }

  open(typing = false) {
    if (this.isOpen()) return;
    if (shown) shown.close();
    makeLayer();
    shown = this;
    this.all = this.items();
    this.typed = typing;
    this.input.setAttribute("aria-expanded", "true");
    const noun = this.noun();
    listbox.setAttribute("aria-label", this.input.getAttribute("aria-label") || `${noun[0].toUpperCase()}${noun.slice(1)} list`);
    if (!this.free && !typing) this.input.select(); // typing replaces the choice shown
    layer.hidden = false;
    this.render();
  }

  close(restore = true) {
    if (!this.isOpen()) return;
    shown = null;
    layer.hidden = true;
    listbox.replaceChildren();
    this.view = [];
    this.active = -1;
    this.input.setAttribute("aria-expanded", "false");
    this.input.removeAttribute("aria-activedescendant");
    if (restore) this.sync();
  }

  query() {
    return this.typed ? this.input.value : "";
  }

  groups() {
    const make = this.select && groupMakers.get(this.select.dataset.groups || "");
    if (!make) return [];
    const byValue = new Map();
    for (const it of this.all) if (!byValue.has(it.value)) byValue.set(it.value, it);
    return make(this.select)
      .map((g) => ({ label: g.label, items: [...new Set(g.values)].map((v) => byValue.get(v)).filter(Boolean) }))
      .filter((g) => g.items.length);
  }

  render() {
    const typed = this.query().trim();
    const ws = words(typed);
    const sections = [];
    let groups = [];
    if (ws.length) sections.push({ label: "", items: matching(this.all, ws) });
    else {
      groups = this.groups();
      const noun = this.noun();
      sections.push(...groups, { label: groups.length ? `All ${noun}s` : "", items: this.all });
    }
    listbox.replaceChildren();
    this.view = [];
    let left = MOST;
    let hidden = 0;
    const current = this.free ? this.input.value : this.select.value;
    sections.forEach((section, s) => {
      const box = document.createElement("div");
      box.setAttribute("role", section.label ? "group" : "presentation");
      if (section.label) {
        const head = document.createElement("div");
        head.className = "pk-group";
        head.id = `pk-g-${s}`;
        head.setAttribute("role", "presentation");
        head.textContent = section.label;
        box.setAttribute("aria-labelledby", head.id);
        box.append(head);
      }
      for (const it of section.items) {
        if (left <= 0) {
          hidden++;
          continue;
        }
        left--;
        const i = this.view.length;
        this.view.push(it);
        const opt = document.createElement("div");
        opt.className = "pk-option";
        opt.id = `pk-o-${i}`;
        opt.dataset.i = String(i);
        opt.setAttribute("role", "option");
        opt.setAttribute("aria-selected", "false");
        opt.title = it.extra ? `${it.label} · ${it.extra}` : it.label;
        if (it.code) {
          const code = document.createElement("span");
          code.className = "pk-code";
          code.append(marked(it.code, ws));
          opt.append(code);
        }
        const name = document.createElement("span");
        name.className = "pk-name";
        name.append(marked(it.name, ws));
        opt.append(name);
        if (it.extra) {
          const extra = document.createElement("span");
          extra.className = "pk-extra";
          extra.append(marked(it.extra, ws));
          opt.append(extra);
        }
        if (it.value === current && !this.free) {
          opt.classList.add("is-current");
          const tick = document.createElement("span");
          tick.className = "ms pk-tick";
          tick.setAttribute("aria-hidden", "true");
          tick.textContent = "check";
          opt.append(tick);
        }
        box.append(opt);
      }
      if (box.childElementCount) listbox.append(box);
    });
    note.hidden = true;
    if (!this.view.length && ws.length) {
      note.textContent = `No ${this.noun()} matches “${typed}”`;
      note.hidden = false;
    } else if (hidden) {
      note.textContent = `${hidden} more: type to find them`;
      note.hidden = false;
    }
    // A free box offers nothing when nothing matches: what is typed is the value.
    layer.hidden = this.free && !this.view.length;
    let active = -1;
    if (ws.length && !this.free) active = this.view.length ? 0 : -1;
    else if (!ws.length && !this.free && !groups.length) {  // with groups, they show from the top, none highlighted
      const at = this.view.findIndex((it) => it.value === current);
      active = current && current !== "UNASSIGNED" && at >= 0 ? at : this.view.length ? 0 : -1;
    }
    this.place();
    this.setActive(active, true);
    if (active <= 0) listbox.scrollTop = 0;
  }

  setActive(i, scroll = true) {
    listbox.querySelector(".pk-option.is-active")?.classList.remove("is-active");
    listbox.querySelector('[aria-selected="true"]')?.setAttribute("aria-selected", "false");
    this.active = i;
    const opt = i >= 0 ? listbox.querySelector(`#pk-o-${i}`) : null;
    if (!opt) {
      this.input.removeAttribute("aria-activedescendant");
      return;
    }
    opt.classList.add("is-active");
    opt.setAttribute("aria-selected", "true");
    this.input.setAttribute("aria-activedescendant", opt.id);
    if (!scroll) return;
    // the group's heading too when it is the group's first choice
    const head = opt.previousElementSibling?.classList.contains("pk-group") ? opt.previousElementSibling : opt;
    if (head.offsetTop < listbox.scrollTop) listbox.scrollTop = head.offsetTop;
    else if (opt.offsetTop + opt.offsetHeight > listbox.scrollTop + listbox.clientHeight) {
      listbox.scrollTop = opt.offsetTop + opt.offsetHeight - listbox.clientHeight;
    }
  }

  move(by) {
    const n = this.view.length;
    if (!n) return;
    const from = this.active < 0 ? (by > 0 ? -1 : n) : this.active;
    this.setActive(Math.max(0, Math.min(n - 1, from + by)));
  }

  pick(i) {
    const it = this.view[i];
    if (!it) return;
    if (this.free) {
      this.close(false);
      if (this.input.value !== it.value) {
        this.input.value = it.value;
        fire(this.input);
      }
      return;
    }
    const select = this.select;
    if (![...select.options].some((o) => o.value === it.value)) select.append(new Option(it.label, it.value));
    const changed = select.value !== it.value;
    select.value = it.value;
    this.close();
    if (changed) fire(select);
    this.sync();
  }

  key(e) {
    if (e.ctrlKey || e.metaKey) return;
    const open = this.isOpen();
    const k = e.key;
    if (k === "ArrowDown" || k === "ArrowUp") {
      e.preventDefault();
      if (!open) this.open();
      else if (e.altKey && k === "ArrowUp") this.close();
      else if (!e.altKey) this.move(k === "ArrowDown" ? 1 : -1);
      return;
    }
    if (!open) {
      if (k === "Enter" && !this.free) {
        e.preventDefault(); // never sends the form the box is in
        this.open();
      }
      return;
    }
    if (k === "PageDown" || k === "PageUp") {
      e.preventDefault();
      this.move(k === "PageDown" ? ROWS - 1 : 1 - ROWS);
    } else if ((k === "Home" || k === "End") && !this.free) {
      e.preventDefault();
      this.setActive(k === "Home" ? 0 : this.view.length - 1);
    } else if (k === "Enter") {
      e.preventDefault();
      if (this.active >= 0) this.pick(this.active);
      else this.close();
    } else if (k === "Escape") {
      e.preventDefault();
      e.stopPropagation(); // only the list closes, not the popover it is in, nor teaching a field
      this.close();
    } else if (k === "Tab") {
      if (this.active >= 0 && (this.free || this.typed)) this.pick(this.active);
      else this.close();
    }
  }

  place() {
    if (layer.hidden) return;
    const r = this.input.getBoundingClientRect();
    const bar = document.querySelector(".inv-actionbar");
    const barTop = bar ? bar.getBoundingClientRect().top : innerHeight;
    const bottom = barTop > r.bottom + 40 ? barTop : innerHeight;
    const gap = 4;
    const edge = 8;
    const noteH = note.hidden ? 0 : 34;
    const want = Math.min(listbox.scrollHeight, ROWS * ROW + 8) + noteH;
    const below = bottom - r.bottom - gap - edge;
    const above = r.top - gap - edge;
    const up = below < Math.min(want, 180) && above > below;
    const room = Math.max(ROW * 3, up ? above : below);
    listbox.style.maxHeight = `${Math.max(ROW * 2, Math.min(ROWS * ROW + 8, room - noteH))}px`;
    const width = Math.min(Math.max(r.width, 360), innerWidth - 2 * edge);
    layer.style.width = `${width}px`;
    layer.style.left = `${Math.max(edge, Math.min(r.left, innerWidth - width - edge))}px`;
    const tall = layer.offsetHeight;
    layer.style.top = `${up ? Math.max(edge, r.top - gap - tall) : r.bottom + gap}px`;
    layer.classList.toggle("up", up);
  }
}

function fire(el) {
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

/* ---------- What pages use ---------- */

/** Make a picker of every select[data-picker] and input[data-picker="free"] in ``root`` (again after new rows came:
    the ones done are left as they are). */
export function enhance(root = document) {
  const found = [];
  if (root.matches?.('select[data-picker], input[data-picker="free"]')) found.push(root);
  found.push(...(root.querySelectorAll?.('select[data-picker], input[data-picker="free"]') || []));
  for (const el of found) if (!pickers.has(el)) pickers.set(el, new Picker(el));
}

/** The box shows its select's choice again: after a script set the select's value. */
export function sync(select) {
  pickers.get(select)?.sync();
}

/** Set a picker's select to ``value`` (a choice it doesn't hold yet is added, with its label from the list). */
export function setValue(select, value) {
  const p = pickers.get(select);
  if (![...select.options].some((o) => o.value === value)) {
    const it = (p ? p.items() : []).find((x) => x.value === value);
    if (!it) return false;
    select.append(new Option(it.label, it.value));
  }
  select.value = value;
  p?.sync();
  return true;
}

/** A free box's choices: ``make()`` gives [{value, name?, extra?}], asked again each time the list opens. */
export function setSource(input, make) {
  sources.set(input, make);
}

/** Groups shown above the whole list while nothing is typed, for selects with data-groups="name". */
export function registerGroups(name, make) {
  groupMakers.set(name, make);
}

document.addEventListener(
  "change",
  (e) => {
    if (e.target instanceof HTMLSelectElement) pickers.get(e.target)?.sync();
  },
  true,
);
document.addEventListener("pointerdown", (e) => {
  if (shown && e.target !== shown.input && !layer.contains(e.target)) shown.close();
});
window.addEventListener("resize", () => shown?.place());
document.addEventListener("scroll", (e) => {
  if (shown && !layer.contains(e.target)) shown.place();
}, { capture: true, passive: true });

enhance();
new MutationObserver((changes) => {
  for (const change of changes) {
    if (layer && layer.contains(change.target)) continue;
    for (const node of change.addedNodes) if (node.nodeType === Node.ELEMENT_NODE) enhance(node);
  }
}).observe(document.body, { childList: true, subtree: true });
