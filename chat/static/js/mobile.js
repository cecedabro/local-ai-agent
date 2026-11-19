// Phones (chat/SPEC.md §9): the sidebar becomes a drawer behind ☰, and the theme switch and the model
// badge move into it (the top bar has no room). Human-owned. Plain script, no module: it only moves
// existing elements and toggles classes, so app.js keeps all its event listeners.
(function () {
  const body = document.body;
  const side = document.querySelector(".side");
  const menu = document.getElementById("menu");
  if (!side || !menu) return;
  const scrim = document.createElement("div");
  scrim.className = "scrim";
  body.appendChild(scrim);
  const open = (on) => {
    body.classList.toggle("drawer-open", on);
    menu.setAttribute("aria-expanded", String(on));
  };
  menu.setAttribute("aria-controls", "convs");
  menu.addEventListener("click", () => open(!body.classList.contains("drawer-open")));
  scrim.addEventListener("click", () => open(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") open(false); });
  // picking a chat, starting a new one or a folder action closes the drawer
  side.addEventListener("click", (e) => {
    if (e.target.closest(".conv:not(.more), #new-chat") && !e.target.closest(".more")) open(false);
  });
  // move the theme switch and model badge into the drawer on narrow screens, and back on wide ones
  const theme = document.querySelector(".top .seg.theme");
  const model = document.getElementById("model");
  const home = theme && theme.parentNode;
  const slot = document.createElement("div");
  slot.className = "side-extras";
  side.insertBefore(slot, side.querySelector(".me"));
  const narrow = window.matchMedia("(max-width: 760px)");
  const place = () => {
    if (narrow.matches) {
      if (model) slot.appendChild(model);
      if (theme) slot.appendChild(theme);
    } else {
      open(false);
      if (model && home) home.insertBefore(model, theme || null);
      if (theme && home) home.appendChild(theme);
    }
  };
  place();
  narrow.addEventListener("change", place);
  // the profile menu closes when you tap anywhere else, or pick a profile
  const pmenu = document.getElementById("pmenu");
  const profile = document.getElementById("profile");
  document.addEventListener("click", (e) => {
    if (!pmenu || pmenu.classList.contains("hidden")) return;
    if (e.target.closest("#profile")) return;
    if (!e.target.closest("#pmenu") || e.target.closest(".opt")) pmenu.classList.add("hidden");
  });
  if (profile) profile.setAttribute("aria-haspopup", "true");
})();
