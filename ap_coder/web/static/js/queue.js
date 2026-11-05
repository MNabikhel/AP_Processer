/* The review queue (/review): coming back to it from an invoice opened from it (its Queue button, Alt+↑) shows it as
   it was left: the same filter, search and sort, scrolled to where it was (D-60). Kept for this browser tab only
   (sessionStorage); a browser that refuses it just opens the queue as usual.

   Only a click from the queue to an invoice leaves a mark (the view and where it was scrolled), and the next time the
   queue opens the mark is used up, whatever happens (B-626): a view looked at long before is never put back. The
   queue's address with a query of its own (a message: ?flash=, a search, a filter) is never replaced; the invoice's
   Queue button carries the view itself (B-1592). */

const KEY = "apc-queue";

function read() {
  try {
    const mark = JSON.parse(sessionStorage.getItem(KEY) || "null");
    sessionStorage.removeItem(KEY); // used up: once
    return mark;
  } catch {
    return null;
  }
}

function write(mark) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(mark));
  } catch {
    /* not kept: the queue opens as usual next time */
  }
}

function fromInvoice() {
  try {
    const ref = new URL(document.referrer);
    return ref.origin === location.origin && /^\/review\/\d+/.test(ref.pathname);
  } catch {
    return false;
  }
}

/** The query the page was opened with: app.js takes ?flash= out of the address before this runs. */
function openedWith() {
  try {
    const nav = performance.getEntriesByType("navigation")[0];
    return nav ? new URL(nav.name).search : location.search;
  } catch {
    return location.search;
  }
}

const VIEW = ["show", "q", "sort", "limit"]; // what an address says of the queue's view

/** An address's view, to compare two: "?show=all&q=&sort=amount" (the toolbar's form) and "?show=all&sort=amount"
    (the invoice's Queue button) are one view; ``rows``: how many it shows (Show more). */
function view(search) {
  const p = new URLSearchParams(search);
  const key = [p.get("show") || "all", p.get("q") || "", p.get("sort") || "priority"].join("\n");
  return { key, rows: Number(p.get("limit")) || 0 };
}

const mark = read();
const back = mark && (fromInvoice() || mark.coming);
const was = back && view(mark.search);
const here = view(location.search);
const same = was && was.key === here.key;
// The Queue button carries the view (B-1592), so the queue opens once (B-1593): it goes on to the view left only from
// a bare address, or when more rows were shown than the address says; a message (?flash=) is never replaced.
const opened = openedWith();
const onlyView = [...new URLSearchParams(opened).keys()].every((k) => VIEW.includes(k));
if (back && mark.search && (!opened || (same && onlyView && was.rows > here.rows))) {
  write({ ...mark, coming: true }); // the page it goes to scrolls back too
  location.replace(`/review${mark.search}`); // the view the invoice was opened from
} else if (same && was.rows === here.rows && mark.y > 0) {
  // once the rows are drawn (the module runs after the page is parsed)
  requestAnimationFrame(() => window.scrollTo(0, Math.min(mark.y, document.documentElement.scrollHeight)));
}

// An invoice opened from the queue: the mark to come back to.
document.addEventListener("click", (e) => {
  if (e.target.closest?.(".rq-list a.rq-row")) write({ search: location.search, y: Math.round(window.scrollY) });
});

// Back from an invoice (Alt+↑, Queue): the focus on that invoice's row, so the keyboard goes on from where it was.
if (fromInvoice()) {
  const left = new URL(document.referrer).pathname.match(/^\/review\/(\d+)/)?.[1];
  // that invoice's row (its link carries the view, B-1592); approved or set aside since, the first row
  const row = (left && document.querySelector(`.rq-list a.rq-row:is([href="/review/${left}"], [href^="/review/${left}?"])`)) ||
    document.querySelector(".rq-list a.rq-row");
  const active = document.activeElement;
  if (row && (!active || active === document.body || active.tagName === "MAIN")) {
    requestAnimationFrame(() => row.focus({ preventScroll: Boolean(back && mark.y > 0) }));
  }
}
