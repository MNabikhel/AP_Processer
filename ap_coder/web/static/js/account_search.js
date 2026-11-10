/* The review screen's account list (D-214): what an Account box offers, for an install with a chart of accounts.
   Plain JavaScript; invoice.js fetches the list (GET /review/accounts.json, once, kept by the browser) and registers
   ``search`` as the picker's sections for the boxes marked data-picker-sections="account".

   Nothing typed: Suggested for this line (the line's candidates) · This supplier's accounts last year · On the
   line's business unit (the box's own, else the supplier's most used, else the home one).
   Typed: every word must be in the account string (with or without its dots), the account's name, or its business
   unit's name or number. In this order: the whole string; with an account in the box and a business unit typed, the
   same account on that business unit; a match at the start of a part of the number or of a word of a name; the
   supplier's own accounts; the line's business unit; the rest. At most MOST rows are drawn. */

import { fold, makeItem } from "./picker.js";

export const MOST = 50; // rows drawn at most: more is found by typing
const GROUP_MOST = 8; // of the supplier's accounts, and of the business unit's

/** The fetched list ([[account string, name, business unit name], ...]) as the search reads it. */
export function prepare(rows) {
  return rows.map(([value, name, unit]) => entry(value, name, unit));
}

function entry(value, name, unit) {
  const dot = value.indexOf(".");
  const plain = value.replaceAll(".", "");
  const code = dot > 0 ? value.slice(dot + 1) : "";
  return {
    value, name: name || "", unit: unit || "", bu: dot > 0 ? value.slice(0, dot) : value, code,
    starts: [...value.split("."), code, code.replaceAll(".", ""), plain], // where a number typed may start
    plain,
    hay: fold(`${value} ${plain} ${name || ""} ${unit || ""}`),
    words: ` ${fold(`${name || ""} ${unit || ""}`)}`,
    unitWords: fold(`${unit || ""} ${dot > 0 ? value.slice(0, dot) : ""}`),
  };
}

const shown = (e, why = "") => makeItem(e.value, e.value, e.name, [e.unit, why].filter(Boolean).join(" · "));

/** An account's parts pasted with spaces or tabs between them, as one string with dots ("" when it is not that). */
export function joined(text) {
  const parts = text.trim().split(/\s+/);
  return parts.length > 1 && parts.every((p) => /^[\d.]+$/.test(p)) ? parts.map((p) => p.replace(/^\.+|\.+$/g, "")).join(".") : "";
}

/** The picker's sections for one Account box. ``state``: {entries (null until the list is here), byValue, total,
    supplier: [[account, lines, name, unit]], home, failed}; ``line``: {candidates: [[account, name, unit, reason]],
    was: the account the box held before it was typed in}. Returns {sections: [{label, items}], note}. */
export function search(state, line, typed) {
  const text = typed.trim();
  const known = (account, name, unit) => state.byValue.get(account) || entry(account, name, unit);
  const candidates = (line.candidates || []).map(([account, name, unit, reason]) => [known(account, name, unit), reason]);
  const supplier = (state.supplier || []).map(([account, lines, name, unit]) => [known(account, name, unit), lines]);
  const wasDot = (line.was || "").indexOf(".");
  const wasUnit = wasDot > 0 ? line.was.slice(0, wasDot) : "";
  const wasCode = wasDot > 0 ? line.was.slice(wasDot + 1) : "";
  const unit = wasUnit || supplier[0]?.[0].bu || state.home || "";
  const loading = state.entries ? "" : state.failed ? "The chart's accounts could not be loaded: type the whole account." : "Loading the chart's accounts…";
  if (!text) {
    const seen = new Set();
    const fresh = (rows) => rows.filter(([e]) => !seen.has(e.value) && seen.add(e.value));
    const sections = [
      { label: "Suggested for this line", items: fresh(candidates).map(([e, why]) => shown(e, why)) },
      { label: "This supplier's accounts last year", items: fresh(supplier).slice(0, GROUP_MOST).map(([e, n]) => shown(e, `${n} ${n === 1 ? "line" : "lines"}`)) },
    ];
    // the line's business unit: the box's own account first (it is the row highlighted), then the unit's others
    const own = state.byValue.get(line.was || "");
    const onUnit = fresh([...(own ? [own] : []), ...(state.entries || []).filter((e) => unit && e.bu === unit)].map((e) => [e]))
      .slice(0, GROUP_MOST);
    if (onUnit.length) sections.push({ label: `On ${unit}${onUnit[0][0].unit ? ` · ${onUnit[0][0].unit}` : ""}`, items: onUnit.map(([e]) => shown(e)) });
    const note = loading || `Type a number, words or a business unit's name: all ${state.total.toLocaleString("en-CA")} accounts are searched.`;
    return { sections: sections.filter((s) => s.items.length), note };
  }
  const whole = joined(text) || text;
  const ws = fold(text).split(/\s+/).filter(Boolean);
  const pool = state.entries || [...new Map([...candidates, ...supplier].map(([e]) => [e.value, e])).values()];
  const mine = new Set(supplier.map(([e]) => e.value));
  // a business unit typed (its number or words of its name) while the box holds an account: that account there
  const unitTyped = (e) => wasCode && e.code === wasCode && ws.every((w) => e.unitWords.includes(w));
  const rank = (e) => {
    if (e.value === whole || e.plain === whole) return 0;
    if (unitTyped(e)) return 1;
    if (ws.some((w) => e.starts.some((p) => p.startsWith(w)) || e.words.includes(` ${w}`))) return mine.has(e.value) ? 2 : 3;
    return mine.has(e.value) ? 4 : e.bu === unit ? 5 : 6;
  };
  const found = [];
  pool.forEach((e, i) => {
    if (e.value === whole || e.plain === whole || ws.every((w) => e.hay.includes(w))) found.push([rank(e), i, e]);
  });
  found.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const drawn = found.slice(0, MOST);
  const there = drawn.filter(([r]) => r === 1).map(([, , e]) => shown(e, "the same account on this business unit"));
  const rest = drawn.filter(([r]) => r !== 1).map(([, , e]) => shown(e, mine.has(e.value) ? "this supplier's" : ""));
  let note = loading;
  if (!note && !found.length) note = `No account of the chart matches “${text}”: what is typed stays and is checked.`;
  else if (!note && found.length > MOST) note = `${found.length.toLocaleString("en-CA")} of ${state.total.toLocaleString("en-CA")} match · the first ${MOST} are listed`;
  // an account in the box and a business unit typed: the same account there first, said as such
  const sections = there.length ? [{ label: `${wasCode} on the business unit typed`, items: there }, { label: "Other matches", items: rest }] : [{ label: "", items: rest }];
  return { sections: sections.filter((s) => s.items.length), note };
}
