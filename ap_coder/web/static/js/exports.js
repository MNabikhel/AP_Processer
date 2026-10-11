/* Exports: the ticked invoices' count on the Export button, and the custom layout's live preview. */
import { escapeHtml, post } from "./app.js";

const form = document.getElementById("export-form");
if (form) {
  const picks = () => [...form.querySelectorAll("input.export-pick:not(:disabled)")];
  const all = document.getElementById("export-all");
  const button = document.getElementById("export-go");
  const label = document.getElementById("export-label");
  const update = () => {
    const n = picks().filter((p) => p.checked).length;
    label.textContent = `Export ${n.toLocaleString()} ${n === 1 ? "invoice" : "invoices"}`;
    button.disabled = n === 0;
    if (all) all.checked = n > 0 && n === picks().length;
  };
  form.addEventListener("change", (event) => {
    if (event.target === all) picks().forEach((p) => (p.checked = all.checked));
    update();
  });
  update();
}

const layout = document.getElementById("layout-form");
if (layout) {
  const box = document.getElementById("layout-preview-box");
  const pre = document.getElementById("layout-preview");
  const warnings = document.getElementById("layout-warnings");
  let timer = null;
  let asked = 0;
  const refresh = async () => {
    const n = ++asked;
    const answer = await post("/exports/layout/preview", new FormData(layout));
    if (n !== asked || !answer.ok) return; // a newer preview is on its way, or AP Coder didn't answer
    pre.textContent = answer.preview || "";
    box.hidden = !answer.preview;
    warnings.innerHTML = (answer.warnings || [])
      .map((w) => `<div class="flash warn"><span class="ms" aria-hidden="true">warning</span><div>${escapeHtml(w)}</div></div>`)
      .join("");
  };
  const soon = () => {
    clearTimeout(timer);
    timer = setTimeout(refresh, 350);
  };
  layout.addEventListener("input", soon);
  layout.addEventListener("change", soon);
}
