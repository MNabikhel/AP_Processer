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
  return el;
}

/** Show a short message in the corner. */
export function toast(msg, ms = 6000) {
  const box = document.getElementById("toasts");
  if (!box) return;
  const el = messageElement(typeof msg === "string" ? { text: msg, tone: "info" } : msg);
  box.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

/** POST to the dashboard with the page header; returns the JSON answer (an {error} on failure). */
export async function post(url, body) {
  let response;
  try {
    response = await fetch(url, { method: "POST", headers: HEADERS, body, credentials: "same-origin" });
  } catch {
    return { ok: false, error: "AP Coder didn't answer. Is it still running? Start it again with the AP Coder shortcut." };
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
    form.querySelector('button[type="submit"], button:not([type])') ||
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
  const body = new FormData(form);
  if (submitter && submitter.name) body.append(submitter.name, submitter.value);
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
  const close = event.target.closest("[data-dismiss]");
  if (close) close.closest(".flash")?.remove();
  // A popover closes when you click elsewhere.
  document.querySelectorAll("details.pop[open]").forEach((d) => {
    if (!d.contains(event.target)) d.removeAttribute("open");
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") document.querySelectorAll("details.pop[open]").forEach((d) => d.removeAttribute("open"));
});

// The flash token has done its job: take it out of the address, so a reload doesn't look for it again.
if (new URLSearchParams(location.search).has("flash")) {
  const url = new URL(location.href);
  url.searchParams.delete("flash");
  history.replaceState(null, "", url.pathname + url.search + url.hash);
}
