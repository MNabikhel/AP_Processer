/* AP Coder dashboard: what every page shares. Plain JavaScript, no framework, nothing from the internet.

   Every <form method="post"> is sent by fetch with the header X-AP-Coder: 1 (the server refuses a POST without it,
   so another website cannot post here). The server answers with JSON:
     {redirect: "/review?flash=…"}  go there (the message shows on that page)
     {toast: {text, tone}}          stay, show a message
     {error: "…"}                   stay, show the problem
     {job: "process", status: url}  background work started: the page polls the status URL (see pollJob)
     {stale: {text, sub, url, link}} the review screen is out of date: it says so and keeps its boxes (invoice.js)

   Attributes a template can use:
     form[data-confirm="Question?"]   ask first
     form[data-busy="Saving…"]        the button says so while the request runs
     a[data-busy="Gathering…"]        a link to a slow page says so until the page comes
     form[data-job]                   after it starts a job, show its progress (#job card), reload when done
     form[data-once]                  a question in a message, answered once: the message goes when the server
                                      kept the answer
     form[data-long]                  an action the server runs whole in the request (a restore, an import):
                                      waited for up to LONG_MS (any other gives up after ACTION_MS)
     select[data-autosubmit]          submit its (GET) form on change
     input[type=checkbox][data-enables]  enables the submit button of its form while ticked
     [data-dismiss]                   removes its message
     [data-job-status="/jobs/name"]   background work under way: says how far it got, reloads the page when done
     a[data-download-job]             on the company server, a download its background process makes: asked for, its
                                      progress said on the link, the file downloaded once made (D-169)

   Every request goes through fetchWithin (a deadline, D-161; tests/meta checks no other fetch is left). An answer that
   is the login's (the session ended: a 401, or a look sent on to /login) leaves the page as it is: it says so once,
   with a link to log in again that comes back here, and the review screen keeps its edits in the tab (D-185).
*/

export const HEADERS = { "X-AP-Coder": "1" };

export function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const ICONS = { ok: "check_circle", info: "info", warn: "warning", err: "error" };

/** A message element (text is escaped). */
export function messageElement(msg) {
  const tone = msg.tone || "info";
  const el = document.createElement("div");
  el.className = `flash ${tone}`;
  el.setAttribute("role", "status");
  const details = (msg.details || []).map((d) => `<li>${escapeHtml(d)}</li>`).join("");
  el.innerHTML =
    `<span class="ms" aria-hidden="true">${escapeHtml(msg.icon || ICONS[tone] || "info")}</span>` +
    `<div><div>${escapeHtml(msg.text)}</div>${details ? `<ul>${details}</ul>` : ""}</div>` +
    `<button type="button" class="flash-close" data-dismiss aria-label="Dismiss"><span class="ms" aria-hidden="true">close</span></button>`;
  if (msg.action) {
    // An action beside the text (the review screen's Undo): pressed once, then the message goes.
    const act = document.createElement("button");
    act.type = "button";
    act.className = "flash-act";
    act.innerHTML = msg.action.icon ? `<span class="ms" aria-hidden="true">${escapeHtml(msg.action.icon)}</span>` : "";
    act.append(msg.action.label);
    if (msg.action.title) act.title = msg.action.title;
    act.addEventListener("click", () => {
      el.remove();
      msg.action.run();
    });
    el.querySelector(".flash-close").before(act);
  }
  return el;
}

/** The corner the messages show in, above a bar fixed at the window's foot (the review screen's approve bar), never
    over its buttons; null on a page without one. */
function toastBox() {
  const box = document.getElementById("toasts");
  if (!box) return null;
  const bar = document.querySelector(".inv-actionbar");
  const top = bar ? bar.getBoundingClientRect().top : window.innerHeight;
  box.style.bottom = `${Math.max(20, window.innerHeight - top + 12)}px`;
  return box;
}

/** Show a short message in the corner; returns its element (gone after ``ms``). */
export function toast(msg, ms = 6000) {
  const box = toastBox();
  if (!box) return null;
  const el = messageElement(typeof msg === "string" ? { text: msg, tone: "info" } : msg);
  box.appendChild(el);
  setTimeout(() => el.remove(), ms);
  return el;
}

/** What to say when AP Coder gave no answer at all: a laptop's is started again by its shortcut; the company server's
    people can't start it, IT runs it (B-1595). */
export function noAnswer() {
  return document.body?.hasAttribute("data-server")
    ? "The AP Coder server didn't answer. Try again in a moment; if it keeps happening, tell IT."
    : "AP Coder didn't answer. Is it still running? Start it again with the AP Coder shortcut.";
}

// Every request has a deadline (D-161): past it, it is given up as one that got no answer (noAnswer's words).
export const LOOK_MS = 30000; // a look: a poll, the vendor list
export const ACTION_MS = 5 * 60000; // an action (post), the setup report, the words on the page
export const LONG_MS = 2 * 3600000; // data-long: a restore of gigabytes, a big import (D-185: no request waits for ever)

/** fetch(url, options), given up (an AbortError) once ``ms`` have passed without the answer and its body. The only
    fetch of the pages' scripts: an answer that is the login's is said on the page (``sessionEnded``). */
export function fetchWithin(url, options = {}, ms = LOOK_MS) {
  const said = (response) => {
    if (loggedOut(response)) sessionEnded(response.redirected && new URL(response.url).pathname === "/who" ? "/who" : "/login");
    return response;
  };
  const deadline = new AbortController();
  setTimeout(() => deadline.abort(), ms); // (once the body is read, aborting changes nothing)
  return fetch(url, { ...options, signal: deadline.signal }).then(said);
}

/** Whether an answer is the login's, the session having ended: a 401, or a look (a GET) sent on to the login page,
    or to "Who are you?" (a laptop shared on the office network asks again after AP Coder started again). */
export function loggedOut(response) {
  if (response.status === 401) return true;
  const path = response.redirected ? new URL(response.url).pathname : "";
  return path === "/login" || path.startsWith("/login/") || path === "/who";
}

/** Whether this page is one of the login's own (the login, its steps, "Who are you?"): its answers are its own. */
function loginPage() {
  const path = location.pathname;
  return path === "/login" || path.startsWith("/login/") || path === "/who";
}

const ENDED = "apc-session-ended";

/** The session ended (server mode), or the laptop shared on the office network asks again who this is (``where``:
    "/who"): the page stays as it is and says so, once, with a link to log in again that comes back to it; the review
    screen keeps its edits in this tab first ("apc:session-ended", B-1594), each time this is said and as the link is
    followed (D-185). */
export function sessionEnded(where = "/login") {
  if (loginPage()) return;
  document.dispatchEvent(new CustomEvent("apc:session-ended"));
  if (document.getElementById(ENDED)) return;
  const box = toastBox();
  if (!box) return;
  let kept = false;
  try {
    kept = Object.keys(sessionStorage).some((key) => key.startsWith("apc-unsaved-"));
  } catch {
    /* (no storage: nothing said kept) */
  }
  const who = where === "/who";
  const back = `${where}?next=${encodeURIComponent(location.pathname + location.search)}`;
  const said = who ? "AP Coder asks who you are again: say who you are" : "Your session ended: sign in again";
  const el = messageElement({
    tone: "warn",
    icon: "lock",
    text: `${said}${kept ? "; your edits are kept" : ""}.`,
    action: {
      label: who ? "Say who you are" : "Sign in again",
      run: () => {
        document.dispatchEvent(new CustomEvent("apc:session-ended")); // (the latest edits)
        location.assign(back);
      },
    },
  });
  el.id = ENDED;
  el.setAttribute("role", "alert");
  box.appendChild(el);
}

/** POST to the dashboard with the page header; returns the JSON answer (an {error} on failure, or past ``ms``). An
    answer that is a file (a batch's JD Edwards ZIP, downloaded by POST: D-135) is saved under its name. */
export async function post(url, body, { ms = ACTION_MS } = {}) {
  let response;
  let file = null;
  try {
    response = await fetchWithin(url, { method: "POST", headers: HEADERS, body, credentials: "same-origin" }, ms);
    const attached = response.ok && /^attachment/i.test(response.headers.get("content-disposition") || "");
    if (attached) file = await response.blob(); // (within the deadline too)
  } catch {
    return { ok: false, error: noAnswer() };
  }
  const disposition = response.headers.get("content-disposition") || "";
  if (file) {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(file);
    link.download = decodeURIComponent((/filename\*=UTF-8''([^;]+)/i.exec(disposition) || [, "download"])[1]);
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 60000);
    return { ok: true };
  }
  let answer;
  try {
    answer = await response.json();
  } catch {
    return { ok: false, error: response.ok ? "" : `That didn't work (${response.status}).` };
  }
  // The session ended (server mode): said on the page (fetchWithin), which stays as it is, its edits kept; nothing to
  // follow or show besides (D-185). A login step's own 401 is followed as it is (B-1925).
  if (response.status === 401 && answer.session_ended) return { ok: false, sessionEnded: true };
  return answer;
}

/** Do what the server's answer says. Returns true when the page is being left. */
export function follow(answer) {
  if (answer.redirect) {
    location.assign(answer.redirect);
    return true;
  }
  if (answer.toast) toast(answer.toast);
  if (answer.error) toast({ text: answer.error, tone: "err" }, 9000);
  return false;
}

/** Wait ``ms``, and while the tab is hidden, until it is shown again: then at once (a look when it is back). */
function pause(ms) {
  return new Promise((resolve) => {
    let hidden = document.hidden;
    let due = false;
    const changed = () => {
      if (document.hidden) hidden = true;
      else if (hidden || due) end();
    };
    const timer = setTimeout(() => {
      due = true;
      if (!document.hidden) end();
    }, ms);
    function end() {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", changed);
      resolve();
    }
    document.addEventListener("visibilitychange", changed);
  });
}

/** Look at ``url`` (its JSON) until ``onAnswer(answer)`` says it is done (true; it may be async): returns that answer,
    or null once a look was answered by the login (``loggedOut``, said on the page): nothing more is asked then
    (B-1712), and the page stays as it is (D-185). Nothing is
    asked while the tab is hidden. After an answer the same as the last (or none), the wait is half as long again, up
    to ``mostMs`` (three times ``everyMs``); a change starts again at ``everyMs``. ``firstMs``: the wait before the
    first look. ``url`` may be a function, asked at each look (the reader panel's says what it shows, D-160). */
export async function poll(url, onAnswer, { everyMs, mostMs = 3 * everyMs, firstMs = 0 }) {
  let wait = everyMs;
  let last = null;
  await pause(firstMs);
  for (;;) {
    let seen = null;
    try {
      const address = typeof url === "function" ? url() : url;
      const response = await fetchWithin(address, { headers: { Accept: "application/json" }, cache: "no-store" });
      if (loggedOut(response)) return null;
      const text = await response.text();
      const answer = JSON.parse(text);
      seen = text;
      if (await onAnswer(answer)) return answer;
    } catch {
      /* no answer (or none to read): looked at again later */
    }
    wait = seen !== null && seen !== last ? everyMs : Math.min(mostMs, wait * 1.5);
    last = seen ?? last;
    await pause(wait);
  }
}

/** Poll a job's status URL until it is no longer running; onUpdate(snapshot) after each answer. Returns the last
    snapshot, or null when the session ended (said on the page, which stays as it is). While nothing changes it is
    asked less often, up to 5 seconds apart. */
export function pollJob(url, onUpdate, everyMs = 800) {
  const done = (snap) => {
    onUpdate(snap);
    return snap.state !== "running";
  };
  return poll(url, done, { everyMs, mostMs: Math.max(5000, 3 * everyMs) });
}

/** The button a form is sent with: the one clicked (it may sit outside the form, with form="id"), else its first. */
function submitButton(form, submitter) {
  if (submitter instanceof HTMLButtonElement) return submitter;
  return (
    form.querySelector('button[type="submit"]:not([hidden]), button:not([type]):not([hidden])') ||
    (form.id ? document.querySelector(`button[type="submit"][form="${CSS.escape(form.id)}"]`) : null)
  );
}

function setBusy(form, busy, submitter) {
  const button = busy ? submitButton(form, submitter) : form._busyButton;
  if (busy) form._busyButton = button;
  if (!button) return;
  if (busy) {
    button.dataset.label = button.innerHTML;
    button.disabled = true;
    button.classList.add("busy");
    if (form.dataset.busy) button.textContent = form.dataset.busy;
  } else {
    button.disabled = false;
    button.classList.remove("busy");
    if (button.dataset.label) button.innerHTML = button.dataset.label;
  }
}

/** Follow a running job, and reload the page when it is done (not when the session ended: the page says so and stays,
    D-185). It asks only for the job's state, done and note (``?only=``), never its 500 lines. */
export function watchJob(statusUrl, onUpdate) {
  const url = new URL(statusUrl, location.href);
  url.searchParams.set("only", "done,note");
  return pollJob(url.pathname + url.search, onUpdate || (() => {})).then((snap) => {
    if (snap !== null) location.reload();
    return snap;
  });
}

async function submitForm(form, submitter) {
  if (form.dataset.sending) return; // already sent (a double click, Enter twice): never the same action twice
  if (form.dataset.confirm && !confirm(form.dataset.confirm)) return;
  let body = new FormData(form);
  if (submitter && submitter.name) body.append(submitter.name, submitter.value);
  if (form.hasAttribute("data-urlencoded")) body = new URLSearchParams(body); // the login: the server takes no multipart there
  form.dataset.sending = "1";
  setBusy(form, true, submitter);
  // A button with its own formaction sends the form there (the review screen's "Send to manager", D-138).
  const action = submitter && submitter.hasAttribute && submitter.hasAttribute("formaction") ? submitter.formAction : form.action;
  const answer = await post(action, body, { ms: form.hasAttribute("data-long") ? LONG_MS : ACTION_MS });
  if (answer.ask_why) {
    // The server asks a question before going on (invoice.js answers it, then sends the form again).
    delete form.dataset.sending;
    setBusy(form, false);
    document.dispatchEvent(new CustomEvent("apc:ask-why", { detail: { form, submitter, ask: answer.ask_why } }));
    return;
  }
  if (answer.stale) {
    // An approval from a review screen out of date (D-184): the screen says so and turns read-only, what was typed
    // still in its boxes (invoice.js); nothing more is sent from it.
    setBusy(form, false);
    document.dispatchEvent(new CustomEvent("apc:stale", { detail: { form, stale: answer.stale } }));
    return;
  }
  if (answer.job && answer.status) {
    document.dispatchEvent(new CustomEvent("apc:job", { detail: answer }));
    if (!document.getElementById("job")) await watchJob(answer.status);
    return;
  }
  if (!follow(answer)) {
    delete form.dataset.sending;
    setBusy(form, false);
    if (answer.ok && form.hasAttribute("data-once")) form.closest(".flash")?.remove();
  }
}

// Back to a page the browser kept as it was left (after a form sent it elsewhere): its forms work again.
window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  document.querySelectorAll("form[data-sending]").forEach((form) => {
    delete form.dataset.sending;
    setBusy(form, false);
  });
});

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || (form.method || "").toLowerCase() !== "post" || form.hasAttribute("data-upload")) return;
  event.preventDefault();
  submitForm(form, event.submitter);
});

document.addEventListener("change", (event) => {
  const el = event.target;
  if (el.matches("select[data-autosubmit]") && el.form) el.form.requestSubmit();
  if (el.matches("input[type=checkbox][data-enables]") && el.form) {
    el.form.querySelectorAll('button[type="submit"]').forEach((b) => (b.disabled = !el.checked));
  }
});

document.addEventListener("click", (event) => {
  // A link to a page that takes a moment (Report a problem gathers its reports): it says so meanwhile (B-440).
  const slow = event.target.closest("a[data-busy]");
  if (slow && !event.defaultPrevented && event.button === 0 && !(event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)) {
    slow.setAttribute("aria-busy", "true");
    slow.classList.add("busy");
    if (!slow.dataset.label) slow.dataset.label = slow.innerHTML;
    slow.textContent = slow.dataset.busy;
    // The page never came (AP Coder stopped, the network dropped): after a while the link reads as before.
    clearTimeout(slow._busyTimer);
    slow._busyTimer = setTimeout(() => resetSlowLink(slow), window.AP_SLOW_LINK_MS || 15000);
  }
  const close = event.target.closest("[data-dismiss]");
  if (close) close.closest(".flash")?.remove();
  // A popover closes when you click elsewhere (a choice in a picker's list, drawn on the body, is in it).
  if (event.target.closest("[data-picker-layer]")) return;
  document.querySelectorAll("details.pop[open]").forEach((d) => {
    if (!d.contains(event.target)) d.removeAttribute("open");
  });
});

function resetSlowLink(a) {
  a.removeAttribute("aria-busy");
  a.classList.remove("busy");
  if (a.dataset.label) a.innerHTML = a.dataset.label;
}

// Back to a page whose slow link was clicked (the browser keeps it): the link reads as it did.
window.addEventListener("pageshow", () => document.querySelectorAll("a[data-busy][aria-busy]").forEach(resetSlowLink));

// Work going on in the background when the page was drawn (the Spend workbook on the company server): its line says how
// far it got, and the page is drawn again once it is done.
document.querySelectorAll("[data-job-status]").forEach((el) => {
  const said = el.textContent.split(":")[0];
  watchJob(el.dataset.jobStatus, (snap) => {
    if (snap.state === "running" && snap.note) el.textContent = `${said}: ${snap.note}`;
  });
});

// A long download on the company server (a[data-download-job]: search to Excel, the Activity CSV...) is made by its
// background process (D-169): the link asks for it, says how far it got, and the file downloads once made. On a laptop
// the link downloads as it always did.
document.addEventListener("click", async (event) => {
  const link = event.target.closest("a[data-download-job]");
  if (!link || !document.body.hasAttribute("data-server") || event.defaultPrevented || event.button !== 0) return;
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  if (link.getAttribute("aria-busy") === "true") return; // asked already: being made
  const label = link.innerHTML;
  const say = (text) => (link.textContent = text);
  link.setAttribute("aria-busy", "true");
  link.classList.add("busy");
  say("Making the file…");
  const again = () => {
    link.removeAttribute("aria-busy");
    link.classList.remove("busy");
    link.innerHTML = label;
  };
  let answer;
  try {
    const asked = { headers: { ...HEADERS, Accept: "application/json" }, cache: "no-store", credentials: "same-origin" };
    const response = await fetchWithin(link.href, asked);
    if (loggedOut(response)) return again(); // the session ended: said on the page, which stays (D-185)
    answer = await response.json();
  } catch {
    answer = { ok: false, error: noAnswer() };
  }
  let snap = null;
  if (answer.ok && answer.status) {
    const told = (s) => s.state === "running" && say(s.note ? `Making the file: ${s.note}` : "Making the file…");
    snap = await pollJob(answer.status, told, 500);
    if (snap === null) return again(); // (likewise)
  }
  again();
  if (snap && snap.state === "done") return location.assign(answer.file);
  follow({ error: (snap && snap.error) || answer.error || "The file could not be made. Try again." });
});

// Esc closes the open popovers; the keyboard focus inside one goes back to its button (Split a line…, Apply to all).
// (A picker's list open in it closes first: picker.js stops that Esc.)
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || event.defaultPrevented) return;
  document.querySelectorAll("details.pop[open]").forEach((d) => {
    const inside = d.contains(document.activeElement);
    d.removeAttribute("open");
    if (inside) {
      d.querySelector(":scope > summary")?.focus();
      event.preventDefault(); // used: teaching a field (invoice_viewer.js) goes on
    }
  });
});

/* The Menu buttons: the menu's own, under 1200 px (base.html: its icons only, or a bar above the page under 901 px),
   and the review screen's with a chart of accounts (the menu is off that screen below 1820 px). One opens the whole
   menu over the page, the focus on its first link; Esc, a click or the focus outside it, or a second press closes it. */
const MENU_TOGGLES = "[data-rail-toggle], [data-menu-toggle]";
let menuOpener = null;
const menuOpen = () => document.documentElement.classList.contains("menu-open");
function showMenu(open, opener = null) {
  document.documentElement.classList.toggle("menu-open", open);
  document.querySelectorAll(MENU_TOGGLES).forEach((b) => b.setAttribute("aria-expanded", String(open)));
  if (open) {
    menuOpener = opener;
    document.querySelector(".rail .nav a")?.focus({ preventScroll: true });
  }
}
document.addEventListener("click", (event) => {
  const toggle = event.target.closest?.(MENU_TOGGLES);
  if (toggle) showMenu(!menuOpen(), toggle);
  else if (menuOpen() && !event.target.closest?.(".rail")) showMenu(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !menuOpen()) return;
  event.preventDefault();
  showMenu(false);
  const shown = (b) => b?.getClientRects().length > 0;
  (shown(menuOpener) ? menuOpener : [...document.querySelectorAll(MENU_TOGGLES)].find(shown))?.focus({ preventScroll: true });
});
document.addEventListener("focusin", (event) => {
  if (menuOpen() && !event.target.closest?.(`.rail, ${MENU_TOGGLES}`)) showMenu(false);
});

// A popover opens inside the screen: on a narrow one (a phone), a popover lined up with the right of its button would
// start past the left edge, where nothing scrolls to it. ("toggle" doesn't bubble: it is caught on its way down.)
document.addEventListener(
  "toggle",
  (event) => {
    const pop = event.target;
    if (!(pop instanceof HTMLDetailsElement) || !pop.open || !pop.classList.contains("pop")) return;
    const body = pop.querySelector(":scope > .pop-body");
    if (!body) return;
    body.style.translate = "";
    const box = body.getBoundingClientRect();
    const gap = 8;
    const right = document.documentElement.clientWidth - gap;
    const shift = box.left < gap ? gap - box.left : box.right > right ? Math.max(gap - box.left, right - box.right) : 0;
    if (shift) body.style.translate = `${Math.round(shift)}px 0`;
  },
  true,
);

// The flash token has done its job: take it out of the address, so a reload doesn't look for it again.
if (new URLSearchParams(location.search).has("flash")) {
  const url = new URL(location.href);
  url.searchParams.delete("flash");
  history.replaceState(null, "", url.pathname + url.search + url.hash);
}

/* Ctrl+Z outside a text box, with a message that offers Undo (an approval just made, B-823): that Undo. The review
   screen's own Ctrl+Z comes first (it undoes the last edit there, and says so); on the other pages, this. */
window.addEventListener("keydown", (e) => {
  if (e.defaultPrevented || !(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey || (e.key !== "z" && e.key !== "Z")) return;
  const t = e.target;
  if (t.isContentEditable || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || (t.tagName === "INPUT" && !["checkbox", "radio", "button", "submit"].includes(t.type))) return;
  const undo = document.querySelector("#flashes form.flash-undo");
  if (!undo) return;
  e.preventDefault();
  undo.requestSubmit();
});
