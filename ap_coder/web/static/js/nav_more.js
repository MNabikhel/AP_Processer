/* The rail's More menu (base.html): remembered open or closed per browser; a page under More opens it without changing
   what was chosen. A classic script loaded right where the menu is drawn, so it runs before the rest of the page, as
   the inline script it replaces did (the server's Content-Security-Policy runs no inline script, D-99). */
(function () {
  var more = document.getElementById("nav-more");
  if (!more) return;
  try { if (!more.open && localStorage.getItem("apc-nav-more") === "open") more.open = true; } catch (e) {}
  var here = document.querySelector(".rail .nav-link.on");
  var fixed = getComputedStyle(more.closest(".rail")).position === "fixed";  // (a phone: the page scrolls)
  if (here && fixed && more.contains(here)) {  // once the menu below it is drawn too
    document.addEventListener("DOMContentLoaded", function () { here.scrollIntoView({block: "nearest"}); });
  }
  more.querySelector("summary").addEventListener("click", function () {
    setTimeout(function () { try { localStorage.setItem("apc-nav-more", more.open ? "open" : "closed"); } catch (e) {} });
  });
})();
