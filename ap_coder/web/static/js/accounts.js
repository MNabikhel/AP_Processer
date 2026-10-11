/* GL accounts & tax: ask before deleting ticked codes, and show a tax's GL account only when it has its own. */

const list = document.getElementById("accounts-form");
if (list) {
  list.addEventListener("change", () => {
    const n = list.querySelectorAll('input[name="delete"]:checked').length;
    const noun = list.dataset.noun || "code";
    // app.js asks this question before it sends the form (data-confirm).
    if (n) list.dataset.confirm = `Delete ${n} ${n === 1 ? noun : noun + "s"} and save the list?`;
    else delete list.dataset.confirm;
  });
}

const tax = document.getElementById("tax-form");
if (tax) {
  tax.addEventListener("change", (event) => {
    const type = event.target.dataset.treatment;
    if (!type) return;
    const perLine = event.target.value === "expense_to_line";
    tax.querySelector(`[data-gl="${type}"]`).hidden = perLine;
    tax.querySelector(`.per-line[data-for="${type}"]`).hidden = !perLine;
  });
}
