/* The review queue (/review): coming back to it from an invoice opened from it (its Queue button, Alt+↑) shows it as
   it was left: the same filter, search and sort, scrolled to where it was (D-60). Kept for this browser tab only
   (sessionStorage); a browser that refuses it just opens the queue as usual.

   Only a click from the queue to an invoice leaves a mark (the view and where it was scrolled), and the next time the
   queue opens the mark is used up, whatever happens (B-626): a view looked at long before is never put back. The
   queue's address with a query of its own (a message: ?flash=, a search, a filter) is never replaced. */

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

const mark = read();
const back = mark && (fromInvoice() || mark.coming);
if (back && !openedWith() && mark.search) {
  write({ ...mark, coming: true }); // the page it goes to scrolls back too
  location.replace(`/review${mark.search}`); // the view the invoice was opened from
} else if (back && mark.search === location.search && mark.y > 0) {
  // once the rows are drawn (the module runs after the page is parsed)
  requestAnimationFrame(() => window.scrollTo(0, Math.min(mark.y, document.documentElement.scrollHeight)));
}

// An invoice opened from the queue: the mark to come back to.
document.addEventListener("click", (e) => {
  if (e.target.closest?.(".rq-list a.rq-row")) write({ search: location.search, y: Math.round(window.scrollY) });
});
