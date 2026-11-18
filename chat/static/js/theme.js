// Runs first in <head> (a classic script): applies the theme before the page paints (chat/SPEC.md §9).
// Preference: auto | light | dark | blossom, kept in localStorage "coder-theme" and on the server (/api/me).
(function () {
  var KEY = "coder-theme", root = document.documentElement, mq = matchMedia("(prefers-color-scheme: dark)");
  function pref() { try { return localStorage.getItem(KEY) || "auto"; } catch (e) { return "auto"; } }
  function apply(p) {
    root.dataset.themePref = p;
    root.dataset.theme = p === "auto" ? (mq.matches ? "dark" : "light") : p;
  }
  apply(pref());
  mq.addEventListener("change", function () { apply(pref()); });
  window.setTheme = function (p, save) {
    try { localStorage.setItem(KEY, p); } catch (e) {}
    apply(p);
    if (save !== false) {
      fetch("/api/me", { method: "PATCH", headers: { "Content-Type": "application/json", "X-Coder": "1" },
        body: JSON.stringify({ theme: p }) }).catch(function () {});
    }
  };
})();
