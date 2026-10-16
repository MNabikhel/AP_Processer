/* The invoice directory: put the columns in the order wanted (drag, or the arrow buttons), in the Columns popover.
   The order of the checkboxes in the page is the order the columns are sent in (and shown). */

const list = document.querySelector("[data-col-list]");
if (list) {
  let dragged = null;
  list.addEventListener("dragstart", (event) => {
    dragged = event.target.closest(".dir-col");
    if (!dragged) return;
    dragged.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", "");
  });
  list.addEventListener("dragover", (event) => {
    const over = event.target.closest(".dir-col");
    if (!dragged || !over || over === dragged) return;
    event.preventDefault();
    list.querySelectorAll(".over").forEach((el) => el.classList.remove("over"));
    over.classList.add("over");
  });
  list.addEventListener("drop", (event) => {
    const over = event.target.closest(".dir-col");
    if (!dragged || !over || over === dragged) return;
    event.preventDefault();
    const after = [...list.children].indexOf(over) > [...list.children].indexOf(dragged);
    over.parentNode.insertBefore(dragged, after ? over.nextSibling : over);
  });
  list.addEventListener("dragend", () => {
    list.querySelectorAll(".over, .dragging").forEach((el) => el.classList.remove("over", "dragging"));
    dragged = null;
  });
  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-move]");
    if (!button) return;
    const item = button.closest(".dir-col");
    const sibling = button.dataset.move === "-1" ? item.previousElementSibling : item.nextElementSibling;
    if (!sibling) return;
    if (button.dataset.move === "-1") list.insertBefore(item, sibling);
    else list.insertBefore(sibling, item);
    button.focus();
  });
}
