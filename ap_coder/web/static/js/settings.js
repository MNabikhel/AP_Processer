/* Settings: the self-test's progress, the restore confirmation, the editable tables of JD Edwards E1, the range
   slider's value, and old links to /settings#automation (the tabs are pages of their own: /settings/automation). */
import { pollJob } from "./app.js";

const TABS = ["review", "reading", "automation", "jde", "data", "about"];
const hash = location.hash.replace("#", "");
if (location.pathname.replace(/\/$/, "") === "/settings" && TABS.includes(hash) && hash !== "review") {
  location.replace(`/settings/${hash}`);
}

// While the page reader's self-test runs (in its own thread), show how far it got; draw the tab again when it ends.
const selfTest = document.getElementById("self-test");
if (selfTest && selfTest.dataset.state === "running") {
  const every = 1000 * Number(selfTest.dataset.every || 5);
  pollJob(selfTest.dataset.statusUrl, (snap) => {
    if (snap.state !== "running") return;
    document.getElementById("self-test-started").textContent = snap.started || "";
    document.getElementById("self-test-pages").textContent = snap.pages ? ` · ${snap.done} of ${snap.pages} pages read` : "";
  }, every).then(() => location.reload());
}

// Restore: the button only works once RESTORE is typed (the server checks it again).
document.querySelectorAll("form[data-restore]").forEach((form) => {
  const input = form.querySelector("input[name=confirm]");
  const button = form.querySelector('button[type="submit"]');
  const word = (input.dataset.word || "RESTORE").toUpperCase();
  const sync = () => (button.disabled = input.value.trim().toUpperCase() !== word);
  input.addEventListener("input", sync);
  // A new restore needs RESTORE typed again (another backup picked).
  form.querySelector("select[name=name]")?.addEventListener("change", () => {
    input.value = "";
    sync();
  });
  sync();
});

// Editable tables: add a row (a blank copy of the last one), remove a row.
document.addEventListener("click", (event) => {
  const add = event.target.closest("[data-add-row]");
  if (add) {
    const body = add.closest("form").querySelector("table[data-rows] tbody");
    const last = body.lastElementChild;
    const row = last.cloneNode(true);
    row.querySelectorAll("input").forEach((input) => (input.value = ""));
    body.appendChild(row);
    row.querySelector("input")?.focus();
    return;
  }
  const remove = event.target.closest("[data-remove-row]");
  if (remove) {
    const row = remove.closest("tr");
    const body = row.parentElement;
    if (body.children.length > 1) row.remove();
    else row.querySelectorAll("input").forEach((input) => (input.value = ""));
  }
});

// A range slider shows its value next to it.
document.querySelectorAll("input[type=range][data-output]").forEach((range) => {
  const out = document.getElementById(range.dataset.output);
  const show = () => (out.textContent = `${range.value}%`);
  range.addEventListener("input", show);
  show();
});
