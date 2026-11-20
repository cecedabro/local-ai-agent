// "How Clear Source works": start the drops (the app's own startOrb) and the theme switch.
import { orbHtml, startOrb } from "/static/js/stream.js";

const root = document.documentElement;
document.querySelectorAll('input[name="sgtheme"]').forEach((r) => {
  if (r.value === (root.dataset.themePref || root.dataset.theme)) r.checked = true;
  r.addEventListener("input", () => (window.setTheme ? window.setTheme(r.value, false) : (root.dataset.theme = r.value)));
});

// The drops are placeholders in the page; swap each for the app's own drop (orbHtml) and let it move.
document.querySelectorAll(".orb[data-fl]").forEach((ph) => {
  const tmp = document.createElement("span");
  tmp.innerHTML = orbHtml("", ph.dataset.phase);
  const o = tmp.firstElementChild;
  ph.replaceWith(o);
  if (o.dataset.phase === "writing") o.style.setProperty("--fill", ".6");
  startOrb(o, { range: 30 });
});

// Light up the step you're reading.
const steps = [...document.querySelectorAll(".fl-step")];
const seen = new IntersectionObserver((entries) => {
  for (const e of entries) e.target.classList.toggle("on", e.isIntersecting);
}, { rootMargin: "-35% 0px -45% 0px" });
steps.forEach((s) => seen.observe(s));
