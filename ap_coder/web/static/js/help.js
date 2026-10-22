/* Help: the checks filter as you type (a group with a match opens, one without hides); Enter still searches on the
   server. Report a problem: the setup report comes after the page (it takes a few seconds), and each Copy button
   copies its report (data-copy-from names the text box), waiting for the setup report if it isn't there yet. */
import { toast } from "./app.js";

let setupReady = Promise.resolve();
const setupBox = document.getElementById("setup-report");
if (setupBox && setupBox.dataset.url && !setupBox.value) {
  setupReady = fetch(setupBox.dataset.url, { cache: "no-store" })
    .then((response) => (response.ok ? response.text() : Promise.reject(new Error(String(response.status)))))
    .then((text) => {
      setupBox.value = text;
    })
    .catch(() => {
      setupBox.value = "The setup report could not be made just now. Reload this page to try again.";
    })
    .finally(() => setupBox.removeAttribute("aria-busy"));
}

document.addEventListener("click", async (event) => {
  if (event.target.closest("[data-print]")) {
    window.print();
    return;
  }
  const button = event.target.closest("[data-copy-from]");
  if (!button) return;
  const box = document.getElementById(button.dataset.copyFrom);
  if (!box) return;
  if (box === setupBox) await setupReady;
  box.select();
  const copied = () => toast({ text: "Copied. Paste it into your message.", tone: "ok" }, 3000);
  const byHand = () => toast({ text: "The text is selected: press Ctrl+C to copy it.", tone: "info" });
  if (navigator.clipboard) navigator.clipboard.writeText(box.value).then(copied, byHand);
  else byHand();
});

const search = document.getElementById("check-search");
const groups = [...document.querySelectorAll("#check-groups [data-group]")];
if (search && groups.length) {
  const none = document.getElementById("check-none");
  const count = document.getElementById("check-count");
  const total = count ? count.textContent.replace(/^\d+/, "") : "";
  const wasOpen = groups.map((g) => g.open);
  search.addEventListener("input", () => {
    const words = search.value.trim().toLowerCase();
    let shown = 0;
    groups.forEach((group, i) => {
      let inGroup = 0;
      group.querySelectorAll("tbody tr").forEach((row) => {
        const hit = !words || row.dataset.text.includes(words);
        row.hidden = !hit;
        if (hit) inGroup += 1;
      });
      group.hidden = inGroup === 0;
      group.open = words ? inGroup > 0 : wasOpen[i];
      const n = group.querySelector("[data-group-count]");
      if (n) n.textContent = String(inGroup);
      shown += inGroup;
    });
    if (none) none.hidden = shown > 0;
    if (count) count.textContent = `${shown}${total}`;
  });
}
