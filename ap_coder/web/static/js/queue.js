/* The review queue (/review): coming back to it from an invoice (its Queue button, Alt+↑, the menu) shows it as it was
   left: the same filter, search and sort, scrolled to where it was (D-60). Kept for this browser tab only
   (sessionStorage); a browser that refuses it just opens the queue as usual. */

const KEY = "apc-queue";

/** The address's query without the one-off message token (?flash=), which app.js takes out anyway. */
function query(search) {
  const params = new URLSearchParams(search);
  params.delete("flash");
  const text = params.toString();
  return text ? `?${text}` : "";
}

function save() {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ search: query(location.search), y: Math.round(window.scrollY) }));
  } catch {
    /* not kept: the queue opens at the top next time */
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

let saved = null;
try {
  saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
} catch {
  saved = null;
}

const here = query(location.search);
const back = saved && (fromInvoice() || saved.coming);
if (back && !here && saved.search) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ ...saved, coming: true })); // the page it goes to scrolls back too
  } catch {
    /* then it opens at the top */
  }
  location.replace(`/review${saved.search}`); // the filter it was left with
} else {
  if (back && saved.search === here && saved.y > 0) {
    // once the rows are drawn (the module runs after the page is parsed)
    requestAnimationFrame(() => window.scrollTo(0, Math.min(saved.y, document.documentElement.scrollHeight)));
  }
  window.addEventListener("pagehide", save);
  document.addEventListener("click", (e) => {
    if (e.target.closest?.("a[href]")) save();
  });
}
