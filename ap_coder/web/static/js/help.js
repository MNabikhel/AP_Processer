/* Help: the check list filters as you type (within the area chosen); Enter still searches on the server. */
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
