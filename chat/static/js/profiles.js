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
  const { used, max, parts } = budget;
  const bar = $("#budget-bar");
  // Clear existing <i> elements, keep span.half
  bar.querySelectorAll("i").forEach((i) => i.remove());
  for (const p of parts) {
    const i = document.createElement("i");
    i.style.setProperty("--w", (p.tokens / max).toFixed(3));
    i.title = `${p.label}: ${fmt(p.tokens)} tokens`;
    bar.appendChild(i);
  }
  bar.classList.toggle("over", used > max / 2);
  const pct = round(100 * used / max);
  $("#budget-text").textContent = `${fmt(used)} of ${fmt(max)} tokens (${pct}%)`;
  const legend = $("#budget-legend");
  legend.innerHTML = parts.map((p) => `<li><b>${esc(p.label)}</b> ${fmt(p.tokens)}</li>`).join("");
  const hint = $("#budget-hint");
  if (used > max / 2) {
    hint.textContent = "This profile takes more than half of the context: remove a document or shorten the notes so there is room for the conversation.";
  } else {
    hint.textContent = `Room left for the conversation: ${fmt(max - used)} tokens.`;
  }
}

function round(n) { return Math.round(n); }

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
  const enabled = s.enabled ? " checked" : "";
  const off = s.enabled ? "" : " off";
  const kindLabel = KIND[s.kind];
  let h = `<article class="src-card${off}" data-source="${esc(s.id)}" data-kind="${s.kind}">`;
  h += `<header><span class="src-kind">${kindLabel}</span><b>${esc(s.title)}</b>`;
  if (s.kind === "file" && s.tokens) {
    h += `<small>${fmt(s.tokens)} tokens</small>`;
  }
  h += `<label class="switch" title="${s.enabled ? "On" : "Off"}"><input type="checkbox" data-enabled${enabled}><span class="track"></span></label>`;
  h += `<button type="button" class="link danger" data-delete>Remove</button></header>`;
  if (s.kind === "pubmed") {
    const f = s.filters || {};
    h += `<div class="filters">`;
    for (const [key, label] of TYPES) {
      const on = (f.types && f.types.includes(key)) ? " checked" : "";
      h += `<label><input type="checkbox" data-type="${key}"${on}> ${label}</label>`;
    }
    const yr = f.years;
    h += `<label>Last <select data-years>`;
    for (const y of ["", 5, 10, 20]) {
      h += `<option value="${y}"${y === String(yr) ? " selected" : ""}>${y || "any"}</option>`;
    }
    h += `</select> years</label>`;
    const humans = f.humans ? " checked" : "";
    h += `<label><input type="checkbox" data-humans${humans}> Humans only</label>`;
    for (const [key, label] of LANGS) {
      const on = (f.languages && f.languages.includes(key)) ? " checked" : "";
      h += `<label><input type="checkbox" data-lang="${key}"${on}> ${label}</label>`;
    }
    h += `</div>`;
  }
  const placeholders = { pubmed: "Only use reliable studies: \u2026", website: "When to use this site\u2026", file: "What this document is\u2026" };
  h += `<textarea data-note rows="2" maxlength="1000" placeholder="${placeholders[s.kind]}">${esc(s.note || "")}</textarea>`;
  h += `</article>`;
  return h;
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
async function start() {
  document.querySelectorAll(".pf-tab").forEach((t) => {
    t.addEventListener("click", () => load(t.dataset.profile));
  });
  const profileApi = (json) => api(`/api/profiles/${state.profile}`, { method: "PATCH", json });
  const sourceApi = (id, json) => api(`/api/profiles/${state.profile}/sources/${id}`, { method: "PATCH", json });
  $("#pf-instructions").addEventListener("input", () => {
    later("instr", async () => {
      try { await profileApi({ instructions: $("#pf-instructions").value }); } catch { /* ignore */ }
      const s = $("#pf-saved");
      s.textContent = "Saved";
      setTimeout(() => { s.textContent = ""; }, 1500);
      refresh();
    });
  });
  $("#pf-web").addEventListener("change", async () => {
    try { await profileApi({ web: $("#pf-web").checked }); } catch { /* ignore */ }
    refresh();
  });
  $("#pf-sources").addEventListener("input", (ev) => {
    const target = ev.target;
    if (target.matches("[data-note]") && target.dataset.source) {
      const id = target.dataset.source;
      later("note" + id, async () => {
        try { await sourceApi(id, { note: target.value }); } catch { /* ignore */ }
        refresh();
      });
    } else if (target.matches("[data-enabled]") && target.dataset.source) {
      const id = target.dataset.source;
      const enabled = target.checked;
      setTimeout(async () => {
        try { await sourceApi(id, { enabled }); } catch { /* ignore */ }
        refresh();
      }, 0);
    } else if ((target.matches("[data-type]") || target.matches("[data-years]") || target.matches("[data-humans]") || target.matches("[data-lang]")) && target.closest("[data-source]")) {
      const card = target.closest("[data-source]");
      const id = card.dataset.source;
      setTimeout(async () => {
        try { await sourceApi(id, { filters: readFilters(card) }); } catch { /* ignore */ }
        refresh();
      }, 0);
    }
  });
  $("#pf-sources").addEventListener("click", (ev) => {
    const del = ev.target.closest("[data-delete]");
    if (del) {
      const card = del.closest("[data-source]");
      const id = card.dataset.source;
      if (confirm("Remove this source?")) {
        const removeCard = () => card.remove();
        const restoreCard = () => {
          const container = $("#pf-sources");
          const msg = container.querySelector("p.small");
          if (msg) msg.remove();
          container.insertAdjacentHTML("beforeend", cardHtml({ id, kind: card.dataset.kind, title: "", enabled: true, note: "", filters: {}, tokens: 0 }));
          const newCard = container.querySelector(`[data-source="${id}"]`);
          if (newCard) {
            newCard.querySelectorAll("[data-note]").forEach((el) => { el.value = card.querySelector("[data-note]")?.value || ""; });
            newCard.querySelectorAll("[data-enabled]").forEach((el) => { el.checked = card.querySelector("[data-enabled]")?.checked || false; });
            newCard.querySelectorAll("[data-type]").forEach((el) => { el.checked = card.querySelector(`[data-type="${el.dataset.type}"]`)?.checked || false; });
            newCard.querySelectorAll("[data-lang]").forEach((el) => { el.checked = card.querySelector(`[data-lang="${el.dataset.lang}"]`)?.checked || false; });
            newCard.querySelectorAll("[data-humans]").forEach((el) => { el.checked = card.querySelector("[data-humans]")?.checked || false; });
            const yrs = newCard.querySelector("[data-years]");
            const oldYrs = card.querySelector("[data-years]");
            if (yrs && oldYrs) yrs.value = oldYrs.value;
          }
        };
        (async () => {
          try {
            await api(`/api/profiles/${state.profile}/sources/${id}`, { method: "DELETE" });
            removeCard();
          } catch {
            restoreCard();
          }
          refresh();
        })();
      }
    }
  });
  $("#add-pubmed").addEventListener("click", async () => {
    try {
      const res = await api(`/api/profiles/${state.profile}/sources`, { method: "POST", json: { kind: "pubmed" } });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: "Failed to add PubMed source" }));
        say(err.error || "Failed to add PubMed source", true);
        return;
      }
    } catch (e) {
      say(String(e), true);
      return;
    }
    refresh();
  });
  $("#add-website").addEventListener("click", async () => {
    const url = prompt("Website address (for example rivm.nl):");
    if (!url) return;
    try {
      const res = await api(`/api/profiles/${state.profile}/sources`, { method: "POST", json: { kind: "website", url } });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: "Failed to add website" }));
        say(err.error || "Failed to add website", true);
        return;
      }
    } catch (e) {
      say(String(e), true);
      return;
    }
    refresh();
  });
  $("#add-doc").addEventListener("change", async () => {
    const files = $("#add-doc").files;
    for (const file of files) {
      const fd = new FormData();
      fd.append("file", file);
      try {
        const res = await fetch(`/api/profiles/${state.profile}/documents`, {
          method: "POST", headers: { "X-Coder": "1" }, body: fd
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ error: "Failed to add document" }));
          say(err.error || "Failed to add document", true);
        } else {
          say("Added " + file.name);
        }
      } catch (e) {
        say(String(e), true);
      }
    }
    $("#add-doc").value = "";
    refresh();
  });
  const saved = localStorage.getItem("dewdrop-profile-tab");
  if (saved) {
    await load(saved);
  } else {
    const first = document.querySelector(".pf-tab");
    if (first) await load(first.dataset.profile);
  }
}

start();
