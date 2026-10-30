/* AP Coder dashboard: what every page shares. Plain JavaScript, no framework, nothing from the internet.

   Every <form method="post"> is sent by fetch with the header X-AP-Coder: 1 (the server refuses a POST without it,
   so another website cannot post here). The server answers with JSON:
     {redirect: "/review?flash=…"}  go there (the message shows on that page)
     {toast: {text, tone}}          stay, show a message
     {error: "…"}                   stay, show the problem
     {job: "process", status: url}  background work started: the page polls the status URL (see pollJob)

   Attributes a template can use:
     form[data-confirm="Question?"]   ask first
     form[data-busy="Saving…"]        the button says so while the request runs
     a[data-busy="Gathering…"]        a link to a slow page says so until the page comes
     form[data-job]                   after it starts a job, show its progress (#job card), reload when done
     select[data-autosubmit]          submit its (GET) form on change
     input[type=checkbox][data-enables]  enables the submit button of its form while ticked
     [data-dismiss]                   removes its message
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

/** Show a short message in the corner; returns its element (gone after ``ms``). */
export function toast(msg, ms = 6000) {
  const box = document.getElementById("toasts");
  if (!box) return null;
  const el = messageElement(typeof msg === "string" ? { text: msg, tone: "info" } : msg);
  // Above a bar fixed at the window's foot (the review screen's approve bar), never over its buttons.
  const bar = document.querySelector(".inv-actionbar");
  const top = bar ? bar.getBoundingClientRect().top : window.innerHeight;
  box.style.bottom = `${Math.max(20, window.innerHeight - top + 12)}px`;
  box.appendChild(el);
  setTimeout(() => el.remove(), ms);
  return el;
}

/** POST to the dashboard with the page header; returns the JSON answer (an {error} on failure). An answer that is a
    file (a batch's JD Edwards ZIP, downloaded by POST: D-135) is saved under its name. */
export async function post(url, body) {
  let response;
  try {
    response = await fetch(url, { method: "POST", headers: HEADERS, body, credentials: "same-origin" });
  } catch {
    return { ok: false, error: "AP Coder didn't answer. Is it still running? Start it again with the AP Coder shortcut." };
  }
  const disposition = response.headers.get("content-disposition") || "";
  if (response.ok && /^attachment/i.test(disposition)) {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = decodeURIComponent((/filename\*=UTF-8''([^;]+)/i.exec(disposition) || [, "download"])[1]);
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 60000);
    return { ok: true };
  }
  try {
    return await response.json();
  } catch {
    return { ok: false, error: response.ok ? "" : `That didn't work (${response.status}).` };
  }
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

/** Poll a job's status URL until it is no longer running; onUpdate(snapshot) after each answer. */
export async function pollJob(url, onUpdate, everyMs = 800) {
  for (;;) {
    let snap = null;
    try {
      const response = await fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" });
      snap = await response.json();
    } catch {
      snap = null;
    }
    if (snap) {
      onUpdate(snap);
      if (snap.state !== "running") return snap;
    }
    await new Promise((resolve) => setTimeout(resolve, everyMs));
  }
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

/** Show a running job in the #job card (Process invoices), and reload the page when it is done. */
export function watchJob(statusUrl, onUpdate) {
  return pollJob(statusUrl, onUpdate || (() => {})).then((snap) => {
    location.reload();
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
  const answer = await post(form.action, body);
  if (answer.ask_why) {
    // The server asks a question before going on (invoice.js answers it, then sends the form again).
    delete form.dataset.sending;
    setBusy(form, false);
    document.dispatchEvent(new CustomEvent("apc:ask-why", { detail: { form, submitter, ask: answer.ask_why } }));
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
