/* The menu's fold button (base.html, D8): the menu folded to its icons, the page taking the room, or shown with its
   words; remembered by this browser. A classic script loaded right where the button is drawn, before the page beside
   the menu, so a folded menu is never drawn open first (the server's Content-Security-Policy runs no inline script). */
(function () {
  var KEY = "apc-menu-folded";
  var root = document.documentElement;
  var button = document.querySelector("[data-rail-fold]");
  if (!button) return;
  function show(folded) {
    var words = folded ? "Show the menu's words" : "Fold the menu to its icons";
    root.classList.toggle("menu-folded", folded);
    button.setAttribute("aria-label", words);
    button.title = words;
    button.firstElementChild.textContent = folded ? "left_panel_open" : "left_panel_close";
  }
  var folded = false;
  try { folded = localStorage.getItem(KEY) === "folded"; } catch (e) {}
  show(folded);
  button.addEventListener("click", function () {
    folded = !folded;
    show(folded);
    if (folded && root.classList.contains("menu-open")) {  // opened over the review screen: folded in its place
      root.classList.remove("menu-open");
      document.querySelectorAll("[data-rail-toggle], [data-menu-toggle]").forEach(function (b) {
        b.setAttribute("aria-expanded", "false");
      });
    }
    try { localStorage.setItem(KEY, folded ? "folded" : "open"); } catch (e) {}
  });
})();
