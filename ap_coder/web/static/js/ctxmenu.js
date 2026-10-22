/* The right-click menu (D-60): one small menu for the review screen's fields, lines, tax lines and page. Plain
   JavaScript, nothing from the internet.

     import { register, openMenu } from "./ctxmenu.js";
     register(".inv-details [data-field]", (el, ctx) => [            // the first selector that matches wins
       { icon: "backspace", label: "Clear this value", hint: "", run: () => ... },
       { icon: "content_copy", label: "Copy value", disabled: "Nothing to copy", run: ... },  // greyed, the reason as its title
       "-",                                                          // a line between items
       { icon: "edit", label: "Use this word for…", items: [ ... ] }, // a submenu
     ]);                                                             // null or []: no menu (the browser's own)

   * Opens at the pointer on a right-click, or under the focused element with Shift+F10 or the ContextMenu key
     (ctx.keyboard). It stays inside the window.
   * Shift + right-click, and a right-click inside a text box while text is selected in it (or on selected text of the
     page), is left to the browser: its own menu, so copy and paste still work. So is the account picker's list.
   * role=menu / menuitem: ↑ ↓ Home End move, Enter or Space picks, → opens a submenu and ← or Esc closes it, Esc or
     Tab closes the menu (the focus goes back where it was), a letter goes to the next item starting with it. Every key
     pressed in the menu stays in it: the page viewer's keys (H, R, +), the popovers' Esc and teaching a field never
     see them. A click outside, a scroll, the window resized or left closes it.
   * ``make`` may answer a promise (the words of the page loading first); the menu opens when it settles.
*/

const providers = []; // [{selector, make}], in the order registered
let root = null; // the open menu
let sub = null; // its open submenu
let back = null; // the element focused before the menu opened (focused again on Esc and after a pick)
let openedAt = 0; // a scroll the browser reports just after the menu opened was before it (the click's own)
let keyOpenedAt = 0; // Shift+F10 / ContextMenu opened it: the contextmenu event the browser may send after is ours

/** A menu for the elements matching ``selector``: ``make(el, ctx)`` gives its items (ctx: {target, x, y, keyboard}). */
export function register(selector, make) {
  providers.push({ selector, make });
}

export function isOpen() {
  return Boolean(root);
}

/** The text box or the page has a selection the right-click is on: the browser's own menu (copy) is wanted. */
function nativeWanted(target) {
  if (target.closest?.("[data-picker-layer]")) return true; // the account picker's list: nothing special
  if (target.matches?.("input, textarea")) {
    try {
      if (typeof target.selectionStart === "number" && target.selectionStart !== target.selectionEnd) return true;
    } catch {
      /* a number box has no selection to ask about */
    }
  }
  const sel = window.getSelection?.();
  if (sel && !sel.isCollapsed && sel.rangeCount && !target.closest?.(".iv")) {
    for (let i = 0; i < sel.rangeCount; i++) if (sel.getRangeAt(i).intersectsNode(target)) return true;
  }
  return false;
}

function provider(target) {
  for (const p of providers) {
    const el = target.closest?.(p.selector);
    if (el) return { el, make: p.make };
  }
  return null;
}

function icon(name) {
  const s = document.createElement("span");
  s.className = "ms";
  s.setAttribute("aria-hidden", "true");
  s.textContent = name || "";
  return s;
}

function build(items, label) {
  const menu = document.createElement("div");
  menu.className = "cm";
  menu.setAttribute("role", "menu");
  menu.tabIndex = -1;
  if (label) menu.setAttribute("aria-label", label);
  let last = "-";
  for (const it of items) {
    if (!it) continue;
    if (it === "-") {
      if (last !== "-") menu.append(Object.assign(document.createElement("div"), { className: "cm-sep", role: "separator" }));
      last = "-";
      continue;
    }
    last = it;
    const row = document.createElement("div");
    row.className = "cm-item";
    row.setAttribute("role", "menuitem");
    row.tabIndex = -1;
    if (it.danger) row.classList.add("danger");
    if (it.id) row.dataset.cm = it.id;
    const words = document.createElement("span");
    words.className = "cm-label";
    words.textContent = it.label;
    row.append(icon(it.icon), words);
    if (it.items) {
      row.setAttribute("aria-haspopup", "menu");
      row.setAttribute("aria-expanded", "false");
      row.append(Object.assign(icon("chevron_right"), { className: "ms cm-more" }));
    } else if (it.hint) {
      const hint = document.createElement("span");
      hint.className = "cm-hint";
      hint.textContent = it.hint;
      row.append(hint);
    }
    if (it.disabled) {
      row.setAttribute("aria-disabled", "true");
      if (typeof it.disabled === "string") {
        row.title = it.disabled;
        row.setAttribute("aria-description", it.disabled);
      }
    }
    row._item = it;
    menu.append(row);
  }
  menu.lastElementChild?.classList.contains("cm-sep") && menu.lastElementChild.remove();
  menu.addEventListener("keydown", onKey);
  menu.addEventListener("click", (e) => {
    const row = e.target.closest(".cm-item");
    if (row) activate(row);
  });
  menu.addEventListener("pointerover", (e) => {
    const row = e.target.closest(".cm-item");
    if (!row || row === document.activeElement) return;
    row.focus({ preventScroll: true });
    clearTimeout(menu._subTimer);
    if (menu === root) menu._subTimer = setTimeout(() => (row._item.items && !row._item.disabled ? openSub(row, false) : closeSub()), 180);
  });
  menu.addEventListener("contextmenu", (e) => e.preventDefault()); // a right-click on the menu picks nothing
  return menu;
}

/** Put a menu at (x, y) of the window, inside it (8 px from each edge), flipped left or up when there is no room. */
function place(menu, x, y, flipX = null) {
  menu.style.left = "0px";
  menu.style.top = "0px";
  menu.style.maxHeight = `${Math.max(120, innerHeight - 16)}px`;
  const w = menu.offsetWidth;
  const h = menu.offsetHeight;
  const vw = document.documentElement.clientWidth;
  let left = x;
  if (left + w > vw - 8) left = flipX !== null ? flipX - w : x - w;
  left = Math.max(8, Math.min(left, vw - w - 8));
  let top = y;
  if (top + h > innerHeight - 8) top = Math.max(8, innerHeight - h - 8);
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(Math.max(8, top))}px`;
}

function rows(menu) {
  return [...menu.querySelectorAll(":scope > .cm-item")];
}

function focusRow(menu, i) {
  const list = rows(menu);
  if (!list.length) return;
  const row = list[(i + list.length) % list.length];
  row.focus({ preventScroll: true });
  row.scrollIntoView?.({ block: "nearest" });
}

/** Open a menu of ``items`` at (x, y) of the window. */
export function openMenu(items, { x = 0, y = 0, label = "", keyboard = false } = {}) {
  closeMenu(false);
  const list = (items || []).filter(Boolean);
  if (!list.some((it) => it !== "-")) return null;
  back = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
  root = build(list, label);
  document.body.append(root);
  place(root, x, y);
  root.classList.add("open");
  root._keyboard = keyboard;
  openedAt = Date.now();
  focusRow(root, 0);
  return root;
}

function openSub(row, focusFirst) {
  if (sub && sub._parent === row) {
    if (focusFirst) focusRow(sub, 0);
    return;
  }
  closeSub();
  sub = build(row._item.items, row._item.label);
  sub._parent = row;
  sub.classList.add("cm-sub");
  document.body.append(sub);
  const r = row.getBoundingClientRect();
  place(sub, r.right + 2, r.top - 5, r.left - 2);
  sub.classList.add("open");
  row.setAttribute("aria-expanded", "true");
  if (focusFirst) focusRow(sub, 0);
}

function closeSub() {
  if (!sub) return;
  sub._parent?.setAttribute("aria-expanded", "false");
  sub.remove();
  sub = null;
}

/** Close the menu; ``restore``: the focus goes back where it was (Esc, Tab, a pick), not on a click elsewhere. */
export function closeMenu(restore = true) {
  if (!root) return;
  clearTimeout(root._subTimer);
  closeSub();
  root.remove();
  root = null;
  if (restore && back?.isConnected) back.focus({ preventScroll: true });
  back = null;
}

function activate(row) {
  const it = row._item;
  if (!it || it.disabled) return;
  if (it.items) {
    openSub(row, true);
    return;
  }
  closeMenu(true);
  try {
    const done = it.run?.();
    done?.catch?.((err) => console.error("menu:", err)); // eslint-disable-line no-console
  } catch (err) {
    console.error("menu:", err); // eslint-disable-line no-console
  }
}

function onKey(e) {
  const menu = e.currentTarget;
  e.stopPropagation(); // the page's own keys (the viewer's H, R, +; Esc of a popover or of teaching) never see it
  const list = rows(menu);
  const i = list.indexOf(document.activeElement);
  const k = e.key;
  if (k === "ArrowDown" || k === "ArrowUp") {
    e.preventDefault();
    focusRow(menu, i < 0 ? 0 : i + (k === "ArrowDown" ? 1 : -1));
  } else if (k === "Home" || k === "End") {
    e.preventDefault();
    focusRow(menu, k === "Home" ? 0 : list.length - 1);
  } else if (k === "Enter" || k === " ") {
    e.preventDefault();
    if (i >= 0) activate(list[i]);
  } else if (k === "ArrowRight") {
    e.preventDefault();
    if (i >= 0 && list[i]._item.items && !list[i]._item.disabled) openSub(list[i], true);
  } else if (k === "ArrowLeft" || k === "Escape") {
    e.preventDefault();
    if (menu === sub) {
      const parent = sub._parent;
      closeSub();
      parent?.focus({ preventScroll: true });
    } else if (k === "Escape") closeMenu(true);
  } else if (k === "Tab") {
    e.preventDefault();
    closeMenu(true);
  } else if (k.length === 1 && /\S/.test(k) && !e.ctrlKey && !e.metaKey && !e.altKey) {
    const letter = k.toLowerCase();
    const from = i < 0 ? 0 : i + 1;
    const order = [...list.slice(from), ...list.slice(0, from)];
    const hit = order.find((r) => r.querySelector(".cm-label").textContent.trim().toLowerCase().startsWith(letter));
    if (hit) {
      e.preventDefault();
      hit.focus({ preventScroll: true });
    }
  }
}

async function show(found, ctx) {
  let items;
  try {
    items = await found.make(found.el, ctx);
  } catch (err) {
    console.error("menu:", err); // eslint-disable-line no-console
    return;
  }
  if (items && items.length) openMenu(items, { x: ctx.x, y: ctx.y, label: ctx.label, keyboard: ctx.keyboard });
}

/** Where a menu opened from the keyboard goes: under the focused element (its first 40 px for a tall one). */
function under(el) {
  const r = el.getBoundingClientRect();
  return { x: Math.max(8, r.left + Math.min(16, r.width / 2)), y: Math.min(r.top + Math.min(r.height, 40), innerHeight - 8) + 2 };
}

document.addEventListener(
  "contextmenu",
  (e) => {
    if (root && (root.contains(e.target) || sub?.contains(e.target))) return;
    if (Date.now() - keyOpenedAt < 600) {
      e.preventDefault(); // the event the browser sends after Shift+F10 / ContextMenu, which opened ours already
      return;
    }
    closeMenu(false);
    if (e.shiftKey || nativeWanted(e.target)) return; // the browser's own menu: copy, paste
    const found = provider(e.target);
    if (!found) return;
    e.preventDefault();
    const keyboard = e.button !== 2 && e.clientX === 0 && e.clientY === 0;
    const at = keyboard ? under(found.el) : { x: e.clientX, y: e.clientY };
    show(found, { target: e.target, x: at.x, y: at.y, keyboard, event: e });
  },
  true,
);

document.addEventListener(
  "keydown",
  (e) => {
    if (!((e.key === "F10" && e.shiftKey) || e.key === "ContextMenu") || e.ctrlKey || e.altKey || e.metaKey) return;
    const target = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
    if (!target || root?.contains(target)) return;
    const found = provider(target);
    if (!found) return;
    e.preventDefault();
    keyOpenedAt = Date.now();
    const at = under(target);
    show(found, { target, x: at.x, y: at.y, keyboard: true, event: e });
  },
  true,
);

document.addEventListener(
  "pointerdown",
  (e) => {
    if (root && !root.contains(e.target) && !sub?.contains(e.target)) closeMenu(false);
  },
  true,
);
document.addEventListener(
  "scroll",
  (e) => {
    if (root && Date.now() - openedAt > 250 && !root.contains(e.target) && !sub?.contains(e.target)) closeMenu(false);
  },
  { capture: true, passive: true },
);
window.addEventListener("resize", () => closeMenu(false));
window.addEventListener("blur", () => closeMenu(false));
