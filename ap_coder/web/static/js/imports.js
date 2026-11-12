/* Imports (D-234): a file box (templates/_import.html) says the name and size of the file chosen, and takes a file
   dragged onto it. The form is sent as any other (app.js); without this script the box still works as a file input. */

function kb(size) {
  return `${Math.max(1, Math.round(size / 1024)).toLocaleString()} KB`;
}

/* The box's button is the page's main one only once a file is chosen: five cards, one blue button at a time. */
function show(zone, input) {
  const go = zone.closest("form")?.querySelector('button[type="submit"]');
  if (go) go.classList.toggle("primary", Boolean(input.files && input.files.length));
  const said = zone.querySelector("[data-imp-chosen]");
  const file = input.files && input.files[0];
  if (!said) return;
  if (!said.dataset.empty) said.dataset.empty = said.innerHTML;
  zone.classList.toggle("chosen", Boolean(file));
  if (!file) {
    said.innerHTML = said.dataset.empty;
    return;
  }
  said.textContent = "";
  const name = document.createElement("b");
  name.textContent = file.name;
  said.append(name, ` · ${kb(file.size)} · chosen. Click “Check the file”, or choose another.`);
}

document.querySelectorAll("[data-imp-drop]").forEach((zone) => {
  const input = zone.querySelector("[data-imp-file]");
  if (!input) return;
  show(zone, input);
  input.addEventListener("change", () => show(zone, input));
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("over");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("over");
    if (!event.dataTransfer || !event.dataTransfer.files.length) return;
    input.files = event.dataTransfer.files;
    show(zone, input);
  });
});
