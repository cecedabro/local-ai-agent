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
      if (theme && home) home.appendChild(theme);              // theme first: the model goes back in front of it
      if (model && home) home.insertBefore(model, theme && theme.parentNode === home ? theme : null);
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

  // The keyboard: iOS lays the keyboard over the page instead of shrinking it, and while you type it pans the
  // visible area (visualViewport) to keep the caret in view, so a full-height app slides down behind the
  // keyboard a bit with every letter. The app is pinned (CSS: position fixed) to the visible area instead:
  // its height and top follow visualViewport, so the composer always sits right on top of the keyboard.
  // The app is pinned to the screen's edges (top/bottom, not a height: in the home-screen app 100vh and friends can
  // be shorter than the screen). The keyboard's height is measured against a fixed, full-screen probe: the part of
  // the screen below the visible area. --kb lifts the app's bottom edge onto the keyboard.
  const vv = window.visualViewport;
  const root = document.documentElement;
  const probe = document.createElement("div");
  probe.setAttribute("aria-hidden", "true");
  probe.className = "vv-probe";
  document.body.appendChild(probe);
  let frame = 0;
  const fit = () => {
    if (!vv) return;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const full = probe.getBoundingClientRect().height || window.innerHeight;
      const top = Math.max(0, Math.round(vv.offsetTop));
      const kb = Math.max(0, Math.round(full - vv.height - vv.offsetTop));
      root.style.setProperty("--app-top", `${top}px`);
      root.style.setProperty("--kb", `${kb < 40 ? 0 : kb}px`);          // under 40 px is a toolbar wobble, not a keyboard
      root.classList.toggle("kb-open", kb >= 40);
      if (kb < 40 && window.scrollY) window.scrollTo(0, 0);              // iOS sometimes leaves the page scrolled after typing
    });
  };
  if (vv) {
    vv.addEventListener("resize", fit);
    vv.addEventListener("scroll", fit);
    // iOS reports the keyboard's size late on focus; measure again once it has settled
    document.addEventListener("focusin", () => { fit(); setTimeout(fit, 300); });
    document.addEventListener("focusout", () => { setTimeout(fit, 100); setTimeout(fit, 400); });
    window.addEventListener("orientationchange", () => setTimeout(fit, 400));
    fit();
  }

  // The text field grows with what you type (up to the CSS max-height) and shrinks again after sending.
  const input = document.getElementById("input");
  const grow = () => {
    if (!input) return;
    input.style.setProperty("--h", "auto");                 // measure the content at its natural height
    input.style.setProperty("--h", `${input.scrollHeight + 2}px`);
  };
  if (input) {
    input.addEventListener("input", grow);
    document.getElementById("composer")?.addEventListener("submit", () => setTimeout(grow, 0));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) setTimeout(grow, 0); });
  }

  // Tapping Quick/Think, the paperclip or send while typing keeps the keyboard open (no blur first),
  // so the tap always lands on the button.
  document.querySelector(".cbar")?.addEventListener("pointerdown", (e) => {
    if (document.activeElement === input && e.target.closest("label, button")) e.preventDefault();
  });
})();
