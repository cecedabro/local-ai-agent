// The profiles page (chat/coder/templates/profiles.html, chat/SPEC.md §12.5). ES module.
// Each function below is one task. Fill in only the one your task names; keep the others.
// Never set inline styles except the CSS variable --w on the budget segments.

const $ = (sel) => document.querySelector(sel);
const state = { profile: null, data: null, timers: {} };
const TYPES = [["systematic_review", "Systematic reviews"], ["meta_analysis", "Meta-analyses"], ["rct", "Randomised trials"],
  ["guideline", "Guidelines"], ["review", "Reviews"]];
const LANGS = [["english", "English"], ["dutch", "Dutch"]];
const KIND = { pubmed: "PubMed", website: "Website", file: "Document" };

/** fetch() for /api with the CSRF header; on 401 go to /login. (done) */
async function api(path, opts = {}) {
  const headers = { "X-Coder": "1", ...(opts.json !== undefined ? { "Content-Type": "application/json" } : {}) };
  const res = await fetch(path, { method: opts.method || "GET", headers,
    body: opts.json !== undefined ? JSON.stringify(opts.json) : opts.body });
  if (res.status === 401) { location.href = "/login"; throw new Error("signed out"); }
  return res;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/** "1.2k" for 1234, "950" for 950. (done) */
function fmt(n) {
  if (n < 1000) return String(n);
  const s = (n / 1000).toFixed(1);
  return (s.endsWith(".0") ? s.slice(0, -2) : s) + "k";
}

/** Show a short message under the sources (or clear it). (done) */
function say(text, bad = false) {
  const m = $("#pf-msg");
  m.textContent = text || "";
  m.classList.toggle("bad", bad);
}

/** Run fn after `ms` without new calls for the same key (typing in a note saves once you pause). (done) */
function later(key, fn, ms = 600) {
  clearTimeout(state.timers[key]);
  state.timers[key] = setTimeout(fn, ms);
}

/**
 * TODO (task B16). The context bar (SPEC §12.5): for budget = {used, max, parts: [{label, tokens}]}:
 *   #budget-bar: keep its span.half, then one <i> per part, each with the CSS variable --w = tokens / max (0–1),
 *     title "LABEL: N tokens"; class "over" on the bar when used > max / 2
 *   #budget-text: "USED of MAX tokens (P%)" with fmt() numbers and P = round(100 * used / max)
 *   #budget-legend: <li><b>LABEL</b> fmt(tokens)</li> per part
 *   #budget-hint: used > max / 2 -> "This profile takes more than half of the context: remove a document or shorten
 *     the notes so there is room for the conversation." ; else "Room left for the conversation: fmt(max - used) tokens."
 */
function drawBudget(budget) {
  // TODO (task B16): see the comment above.
}

/**
 * TODO (task B16). One source card (SPEC §12.5):
 * <article class="src-card[ off]" data-source="ID" data-kind="KIND">
 *   <header><span class="src-kind">PubMed|Website|Document</span><b>TITLE</b>[<small>fmt(tokens) tokens</small> for files]
 *     <label class="switch" title="On or off"><input type="checkbox" data-enabled[ checked]><span class="track"></span></label>
 *     <button type="button" class="link danger" data-delete>Remove</button></header>
 *   for pubmed: <div class="filters"> per TYPES <label><input type="checkbox" data-type="KEY"[ checked]> LABEL</label>,
 *     <label>Last <select data-years> options "" (any), 5, 10, 20 (selected = filters.years) </select> years</label>,
 *     <label><input type="checkbox" data-humans[ checked]> Humans only</label>,
 *     per LANGS <label><input type="checkbox" data-lang="KEY"[ checked]> LABEL</label> </div>
 *   <textarea data-note rows="2" maxlength="1000" placeholder="Note for the model, e.g. …">NOTE</textarea>
 * </article>
 * Placeholders: pubmed "Only use reliable studies: …", website "When to use this site…", file "What this document is…".
 */
function cardHtml(s) {
  // TODO (task B16): see the comment above.
  return "";
}

/** Draw the whole profile: tabs (aria-selected on the open one), instructions, web switch, cards, budget. (done) */
function draw() {
  const d = state.data;
  document.querySelectorAll(".pf-tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.profile === state.profile)));
  if (document.activeElement !== $("#pf-instructions")) $("#pf-instructions").value = d.instructions;
  $("#pf-web").checked = d.web;
  $("#pf-sources").innerHTML = d.sources.map(cardHtml).join("") || '<p class="small muted">No sources yet.</p>';
  $("#add-pubmed").hidden = d.sources.some((s) => s.kind === "pubmed");
  drawBudget(d.budget);
}

/** Load GET /api/profiles/PROFILE into state.data and draw it. (done) */
async function load(profile) {
  state.profile = profile;
  const res = await api(`/api/profiles/${profile}`);
  state.data = await res.json();
  try { localStorage.setItem("dewdrop-profile-tab", profile); } catch { /* ignore */ }
  draw();
}

/** Reload the profile after a change, keeping the cursor where it is. (done) */
async function refresh() {
  const res = await api(`/api/profiles/${state.profile}`);
  state.data = await res.json();
  const focus = document.activeElement?.closest("[data-source]") ? null : document.activeElement;
  if (!focus || !document.activeElement.matches("textarea")) draw();
  else drawBudget(state.data.budget);
}

/** The filters of a PubMed card from its checkboxes and select: {types, years, humans, languages}. (done) */
function readFilters(card) {
  const years = card.querySelector("[data-years]").value;
  return { types: [...card.querySelectorAll("[data-type]:checked")].map((x) => x.dataset.type),
    years: years ? Number(years) : null, humans: card.querySelector("[data-humans]").checked,
    languages: [...card.querySelectorAll("[data-lang]:checked")].map((x) => x.dataset.lang) };
}

/**
 * TODO (task B17). Wire the page once:
 *   .pf-tab click -> load(its profile)
 *   #pf-instructions input -> later("instr", PATCH /api/profiles/P {instructions}, then #pf-saved "Saved" and refresh())
 *   #pf-web change -> PATCH {web: checked}, refresh()
 *   #pf-sources: input on [data-note] -> later("note"+id, PATCH .../sources/ID {note}, refresh());
 *     change on [data-enabled] -> PATCH {enabled}; on a filter control -> PATCH {filters: readFilters(card)}; then refresh();
 *     click on [data-delete] -> confirm("Remove this source?"), DELETE .../sources/ID, refresh()
 *   #add-pubmed -> POST .../sources {kind: "pubmed"}; #add-website -> url = prompt("Website address (for example rivm.nl):"),
 *     POST {kind: "website", url}; not ok -> say(the error, true); then refresh()
 *   #add-doc change -> for each file: FormData "file", POST .../documents; not ok -> say(error, true) else say("Added NAME.");
 *     clear the input; refresh()
 * Then load the tab from localStorage "dewdrop-profile-tab" if it exists on the page, else the first tab.
 */
function start() {
  // TODO (task B17): see the comment above.
}

start();
