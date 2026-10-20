/* Help: the check list filters as you type (within the area chosen); Enter still searches on the server.
   Report a problem: each Copy button copies its report (data-copy-from names the text box). */
import { toast } from "./app.js";

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-copy-from]");
  if (!button) return;
  const box = document.getElementById(button.dataset.copyFrom);
  if (!box) return;
  box.select();
  const copied = () => toast({ text: "Copied. Paste it into your message.", tone: "ok" }, 3000);
  const byHand = () => toast({ text: "The text is selected: press Ctrl+C to copy it.", tone: "info" });
  if (navigator.clipboard) navigator.clipboard.writeText(box.value).then(copied, byHand);
  else byHand();
});

const search = document.getElementById("check-search");
const table = document.getElementById("check-table");
if (search && table) {
  const rows = [...table.querySelectorAll("tbody tr")];
  const none = document.getElementById("check-none");
  const count = document.getElementById("check-count");
  const total = count ? count.textContent.replace(/^\d+/, "") : "";
  search.addEventListener("input", () => {
    const words = search.value.trim().toLowerCase();
    let shown = 0;
    rows.forEach((row) => {
      const hit = !words || row.dataset.text.includes(words);
      row.hidden = !hit;
      if (hit) shown += 1;
    });
    if (none) none.hidden = shown > 0;
    if (count) count.textContent = `${shown}${total}`;
  });
}
