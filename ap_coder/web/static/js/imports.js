/* Imports (D-234): a file box (templates/_import.html) says the name and size of the file chosen, and takes a file
   dragged onto it. The form is sent as any other (app.js); without this script the box still works as a file input.
   While the file is read the box says so, with the seconds gone by and how long a real-size file takes; a file
   refused is said in the box's form (app.js), the box emptied and the focus on it, to choose another (B-2445).
   A page opened on what was just done takes the focus there (B-2446): the file shown, a load's result, or the
   message the address carries, moved into the part of the page it is about.
   A column picker (templates/_sheet.html) keeps its import's button closed, and names the needed fields still without
   a column, as its choices change: the server refuses the same (files.pick_refusal). */

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

/* The box while its file is read: what is read, the seconds so far (not read out each second), how long it takes. */
function working(zone, input, on) {
  const form = zone.closest("form");
  clearInterval(zone._ticking);
  zone.classList.toggle("working", on);
  form?.toggleAttribute("aria-busy", on);
  const icon = zone.querySelector(":scope > .ms");
  if (icon) {
    if (!icon.dataset.icon) icon.dataset.icon = icon.textContent;
    icon.textContent = on ? "hourglass_top" : icon.dataset.icon;
  }
  const said = zone.querySelector("[data-imp-chosen]");
  const file = input.files && input.files[0];
  if (!on || !said || !file) {
    if (!on) show(zone, input);
    return;
  }
  said.textContent = "";
  const name = document.createElement("b");
  name.textContent = file.name;
  const wait = form?.dataset.wait ? ` ${form.dataset.wait}` : "";
  const seconds = document.createElement("span");
  seconds.className = "imp-seconds";
  seconds.setAttribute("aria-hidden", "true");
  said.append("Reading ", name, `…${wait} Keep this page open.`, seconds);
  const started = Date.now();
  zone._ticking = setInterval(() => {
    seconds.textContent = ` ${Math.round((Date.now() - started) / 1000)} s`;
  }, 1000);
}

document.querySelectorAll("[data-imp-drop]").forEach((zone) => {
  const input = zone.querySelector("[data-imp-file]");
  if (!input) return;
  const form = zone.closest("form");
  show(zone, input);
  input.addEventListener("change", () => show(zone, input));
  form?.addEventListener("submit", () => working(zone, input, true));
  form?.addEventListener("apc:refused", () => {
    working(zone, input, false);
    input.value = "";
    show(zone, input);
    input.focus();
  });
  // Back to a page left while a file was read: the box as it was.
  window.addEventListener("pageshow", (event) => event.persisted && working(zone, input, false));
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

/* The needed fields of a picker without a column, by their labels: each needed one, and its "one of" fields as one. */
function unpicked(picker) {
  const chosen = (field) => picker.querySelector(`select[name="col_${field}"]`)?.value;
  const need = JSON.parse(picker.dataset.need || "{}");
  const missing = Object.keys(need).filter((field) => !chosen(field)).map((field) => need[field]);
  const oneOf = JSON.parse(picker.dataset.oneOf || "null");
  if (oneOf && !oneOf.fields.some(chosen)) missing.push(oneOf.label);
  return missing;
}

document.querySelectorAll("[data-imp-picker]").forEach((picker) => {
  const form = picker.closest("form");
  if (!form) return;
  const update = () => {
    const missing = unpicked(picker);
    const wait = form.querySelector("[data-imp-wait]");
    if (wait) {
      wait.hidden = !missing.length;
      const said = wait.querySelector("[data-imp-missing]");
      if (said) said.textContent = `Pick the column for: ${missing.join(", ")}.`;
    }
    form.querySelectorAll("[data-imp-go]").forEach((go) => {
      go.disabled = Boolean(missing.length);
    });
  };
  picker.addEventListener("change", update);
  update();
});

/* Where the page opened on what was just done: the address's #part when it is a result (a statement's comparison),
   JD Edwards files' line of what a load did, the file shown (its verdict), else the message the address carries.
   That message is moved into the address's #part, which opens when it is folded. Null on a page opened as it is. */
function arrivedAt() {
  const part = location.hash.length > 1 ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
  const messages = [...document.querySelectorAll("#flashes > .flash")];
  if (messages.length && part && !part.contains(messages[0])) {
    if (part instanceof HTMLDetailsElement) {
      part.open = true;
      part.querySelector(":scope > summary").after(...messages);
    } else {
      const head = part.querySelector(":scope > h2, :scope > h3, :scope > h4, :scope > .card-head");
      if (head) head.after(...messages);
      else part.prepend(...messages);
    }
  }
  if (part && part.hasAttribute("data-imp-result")) return part;
  const done = document.querySelector(".imp-done");
  if (done) return done;
  const preview = document.querySelector(".imp-preview");
  if (preview) return preview.querySelector(".imp-verdict") || preview.querySelector(".imp-file");
  return messages[0] || null;
}

// After the load: the browser's own going to the address's #part, at the load, would take the focus off it again.
function arrive() {
  const arrived = arrivedAt();
  if (!arrived) return;
  if (!arrived.hasAttribute("tabindex")) arrived.setAttribute("tabindex", "-1");
  arrived.focus();
}
if (document.readyState === "complete") arrive();
else window.addEventListener("load", () => setTimeout(arrive), { once: true });
