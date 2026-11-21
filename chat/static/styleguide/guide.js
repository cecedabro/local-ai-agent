// The style guide's samples, drawn with the app's own code (stream.js) so they look exactly like the app.
import { orbHtml, startOrb, trailHtml, thoughtHtml, sourcesHtml, citeLinks, markVerify, codeBlocks, sourceActions,
  metaText, animateTrail } from "/static/js/stream.js";

const $ = (s) => document.querySelector(s);
const md = window.markdownit({ html: false, linkify: true, breaks: false });
codeBlocks(md);

// ---------------------------------------------------------------- theme switch (does not save to your account)
const root = document.documentElement;
document.querySelectorAll('input[name="sgtheme"]').forEach((r) => {
  if (r.value === (root.dataset.themePref || root.dataset.theme)) r.checked = true;
  r.addEventListener("input", () => { window.setTheme ? window.setTheme(r.value, false) : (root.dataset.theme = r.value); drawSwatches(); });
});
$("#sg-brand").outerHTML = orbHtml("", "idle");
startOrb($(".sg-top .orb"));

// ---------------------------------------------------------------- colour tokens, read live
const TOKENS = [["--ink", "text"], ["--muted", "quiet text"], ["--link", "links"], ["--accent", "accent"], ["--primary", "primary buttons"],
  ["--glow", "glow"], ["--title-a", "titles"], ["--title-b", "title gradient"], ["--eh", "Clear Source"], ["--ok", "good"],
  ["--warn", "warning"], ["--bad", "error"], ["--glass", "glass panels"], ["--chip", "chips, lists"], ["--track", "tracks"],
  ["--stroke", "edges"], ["--line", "dividers"], ["--field", "fields"]];
function drawSwatches() {
  const box = $("#sg-swatches");
  const cs = getComputedStyle(root);
  box.innerHTML = TOKENS.map(([v, what]) => `<div class="sg-sw"><i></i><b>${v}</b><small>${what}</small><code></code></div>`).join("");
  box.querySelectorAll(".sg-sw").forEach((el, i) => {
    const v = TOKENS[i][0], val = cs.getPropertyValue(v).trim();
    el.querySelector("i").style.background = `var(${v})`;
    el.querySelector("code").textContent = val.length > 46 ? val.slice(0, 44) + "…" : val || "(not set)";
  });
}
drawSwatches();

// ---------------------------------------------------------------- the drop in every phase
const PHASES = [["idle", "idle"], ["thinking", "thinking"], ["searching", "searching"], ["reading", "reading"], ["writing", "writing"],
  ["welcome", "welcome (marble)"]];
$("#sg-drops").innerHTML = PHASES.map(([p, name]) => `<figure>${orbHtml(p === "welcome" ? "marble" : "", p)}<figcaption>${name}</figcaption></figure>`).join("") +
  `<figure>${orbHtml("tiny", "writing")}<figcaption>tiny (text tail)</figcaption></figure>`;
$("#sg-drops").querySelectorAll(".orb").forEach((o) => {
  startOrb(o, { range: 30 });
  if (o.dataset.phase === "writing") o.style.setProperty("--fill", ".55");
});

// ---------------------------------------------------------------- messages
const ANSWER = "Go a bit lower than for search: around **150–200 ms**.\n\n```javascript\nconst onInput = debounce(e => suggest(e.target.value), 180);\n```\n\n" +
  "- Cancel the previous request.\n- Cache recent prefixes.\n\n| Field | Delay |\n|---|---|\n| Autocomplete | 180 ms |\n| Search page | 300 ms |";
function aiHtml(inner, orb = '<span class="orb" data-phase="idle"><i></i><i></i><i></i><i></i></span>') {
  return `<div class="ai">${orb}<div class="content">${inner}</div></div>`;
}
const stats = (tokens, tps) => `<div class="meta2"><span class="orb tiny" data-phase="idle"><i></i><i></i><i></i><i></i></span><span>${metaText(tokens, tps)}</span>` +
  `<button>Copy</button><button>Retry</button><span class="dl-wrap"><button class="dl" type="button">Download</button></span></div>`;
$("#sg-messages").innerHTML =
  `<div class="u"><div class="msg user">What debounce delay should I use for autocomplete?</div></div>` +
  aiHtml(`<div class="pre"></div><div class="bub">${md.render(ANSWER)}</div>${stats(64, 41.2)}`) +
  `<div class="u"><span class="file">notes.pdf</span><div class="msg user">Summarise this file</div></div>` +
  aiHtml(`<div class="pre"></div><div class="bub"></div><div class="meta2"><span class="phase-label">Thinking… · 12 s</span></div>`,
    orbHtml("", "thinking"));
$("#sg-messages").querySelectorAll(".orb.live").forEach((o) => startOrb(o));

// ---------------------------------------------------------------- Clear Source
const SOURCES = [
  { n: 1, title: "Urineweginfecties bij volwassenen", url: "https://richtlijnendatabase.nl/", kind: "website", site: "richtlijnendatabase.nl", meta: "richtlijnendatabase.nl",
    quote: "Behandel een ongecompliceerde cystitis bij niet-zwangere vrouwen met nitrofurantoïne." },
  { n: 2, title: "Nitrofurantoïne", url: "https://www.farmacotherapeutischkompas.nl/", kind: "website", site: "farmacotherapeutischkompas.nl", meta: "farmacotherapeutischkompas.nl" },
  { n: 3, title: "Short-course antibiotics for uncomplicated cystitis: a meta-analysis", url: "https://pubmed.ncbi.nlm.nih.gov/1/", kind: "pubmed", type: "Meta-analysis", meta: "Meta-analysis · BMJ · 2021" },
  { n: 4, title: "Nitrofurantoin vs fosfomycin: a randomised trial", url: "https://pubmed.ncbi.nlm.nih.gov/2/", kind: "pubmed", type: "RCT", meta: "RCT · JAMA · 2018" },
  { n: 5, title: "Urinary tract infection (lower): antimicrobial prescribing", url: "https://www.nice.org.uk/guidance/ng109", kind: "website", site: "nice.org.uk", meta: "nice.org.uk" },
];
const STEPS = [
  { kind: "plan", about: "First-choice treatment of an uncomplicated bladder infection in women.", mesh: ["Cystitis", "Anti-Bacterial Agents"],
    queries: ["cystitis women first line treatment", "blaasontsteking behandeling eerste keus"] },
  { kind: "search", source: "richtlijnendatabase.nl", query: "blaasontsteking behandeling eerste keus", found: 5 },
  { kind: "search", source: "PubMed", query: "cystitis women first line treatment", found: 1, mesh: ["Cystitis"], url: "https://pubmed.ncbi.nlm.nih.gov/?term=cystitis" },
  { kind: "search", source: "PubMed", query: "cystitis women first line treatment", found: 8, broader: true, url: "https://pubmed.ncbi.nlm.nih.gov/?term=cystitis" },
  { kind: "read", status: "used", url: "https://richtlijnendatabase.nl/", title: "Urineweginfecties bij volwassenen", quotes: 2 },
  { kind: "read", status: "skip", url: "https://pubmed.ncbi.nlm.nih.gov/9/", title: "Cranberries for preventing urinary tract infections" },
  { kind: "read", status: "error", url: "https://example.org/", title: "A page that could not be read" },
];
const CS_ANSWER = "## Kort antwoord\nDe eerste keus is nitrofurantoïne, vijf dagen [1].\n\n## Nederlandse richtlijnen\n" +
  '> "Behandel een ongecompliceerde cystitis bij niet-zwangere vrouwen met nitrofurantoïne." [1]\n\n' +
  "De richtlijn noemt nitrofurantoïne als eerste middel; de dosering staat in het Kompas [2].\n\n## Onderzoek\n" +
  '> "Three to five days of treatment were as effective as seven days." [3]\n\n' +
  'Een trial vond dat "nitrofurantoin cured more women than fosfomycin" [4], maar dit citaat staat niet in de bron.\n\n' +
  "Buiten Nederland adviseert NICE hetzelfde [5].";
const REPORT = { quotes: [
  { text: "Behandel een ongecompliceerde cystitis bij niet-zwangere vrouwen met nitrofurantoïne.", ok: true, cites: [1] },
  { text: "Three to five days of treatment were as effective as seven days.", ok: true, cites: [3] },
  { text: "nitrofurantoin cured more women than fosfomycin", ok: false, cites: [4], why: "not in source 4" }],
  uncited: [], bad_refs: [] };
$("#sg-cs").innerHTML = `<div class="u"><div class="msg user">Wat is de eerste keus bij een blaasontsteking bij vrouwen?</div></div>` +
  aiHtml(`<div class="pre">${thoughtHtml("The question is about uncomplicated cystitis in women. Guidelines first, then trials.", 6)}${trailHtml(STEPS)}</div>` +
    `<div class="bub">${md.render(CS_ANSWER)}</div>${sourcesHtml(SOURCES)}${stats(412, 38.5)}`);
const bub = $("#sg-cs .bub");
citeLinks(bub, SOURCES);
markVerify(bub, REPORT);
sourceActions($("#sg-cs"));
$("#sg-cs-live").innerHTML = aiHtml(`<div class="pre">${trailHtml(STEPS.slice(0, 4), true)}</div><div class="bub"></div>` +
  `<div class="meta2"><span class="phase-label">Reading Urineweginfecties bij volwassenen… · 8 s</span></div>`, orbHtml("", "reading"));
startOrb($("#sg-cs-live .ai>.orb"));
animateTrail($("#sg-cs-live .pre"));

// ---------------------------------------------------------------- the stop drop (same markup and classes as app.js)
const WORDS = ["Thinking", "Searching", "Reading", "Writing"];
$("#sg-drop-btn").addEventListener("click", () => {
  const btn = $("#sg-send");
  if (btn.classList.contains("has-drop")) return;
  const tmp = document.createElement("span");
  tmp.innerHTML = orbHtml("", "thinking");
  const o = tmp.firstElementChild;
  o.classList.add("sd-orb");
  btn.prepend(o);
  btn.classList.add("stop", "has-drop");
  const motion = startOrb(o);
  WORDS.forEach((w, i) => i && setTimeout(() => { o.dataset.phase = w.toLowerCase(); btn.title = `Stop · ${w}`; }, i * 1600));
  setTimeout(() => {
    btn.classList.add("drop-out");
    setTimeout(() => { motion.stop(); o.remove(); btn.classList.remove("stop", "has-drop", "drop-out"); btn.title = "Send"; }, 380);
  }, WORDS.length * 1600);
});

// context bar sample
$("#sg-ctx")?.style.setProperty("--used", ".05");
$("#sg-line")?.style.setProperty("--at", ".75");
