// Reading the answer stream and writing it smoothly (chat/SPEC.md §5, §9). Human-owned.
// Tests: chat/tests/js/stream.test.mjs (node --test). Only renderFade/placeTail touch the DOM.

/** Split server-sent events out of a growing text buffer: { events: [{event, data}], rest }. */
export function splitEvents(buffer) {
  const parts = buffer.split("\n\n");
  const rest = parts.pop();
  const events = [];
  for (const p of parts) {
    let event = "message", data = null;
    for (const line of p.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data = JSON.parse(line.slice(5));
    }
    if (data !== null) events.push({ event, data });
  }
  return { events, rest };
}

/** Read a fetch() Response body and call onEvent(event, data) for every event. */
export async function readEvents(response, onEvent) {
  const reader = response.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const r = splitEvents(buf);
    buf = r.rest;
    r.events.forEach((e) => onEvent(e.event, e.data));
  }
}

export const FADE_MS = 220;          // how long new letters take to fade in (matches .nw in chat.css)
const raf = (f) => (globalThis.requestAnimationFrame ? requestAnimationFrame(f) : setTimeout(() => f(performance.now()), 16));

/**
 * Writes text at a steady pace, however bursty it arrives (how the big chat apps do it).
 * Deltas go into a buffer; every screen frame some letters come out. The speed eases towards
 * "how much is waiting" (30–600 letters/s), so it never jumps and never falls far behind.
 *   const tw = new Typewriter((shown, pieces) => ...);  tw.push(delta) ...;  await tw.finish();
 * onFrame(shown, pieces) runs on every frame that released letters: `shown` is all text so far,
 * `pieces` the pieces released in the last FADE_MS ({len, at}), newest last, for renderFade().
 */
export class Typewriter {
  constructor(onFrame) {
    this.onFrame = onFrame;
    this.text = "";
    this.pos = 0;            // fractional position
    this.shown = 0;
    this.rate = 0;
    this.pieces = [];
    this.last = 0;
    this.running = false;
    this.waiters = [];
  }
  push(text) {
    this.text += text;
    if (!this.running) { this.running = true; this.last = performance.now(); raf((t) => this.frame(t)); }
  }
  frame(now) {
    const dt = Math.min(0.064, Math.max(0, (now - this.last) / 1000));
    this.last = now;
    const backlog = this.text.length - this.pos;
    const want = Math.max(30, Math.min(600, backlog * 3.5));
    this.rate += (want - this.rate) * Math.min(1, dt * 4);
    this.pos = Math.min(this.text.length, this.pos + this.rate * dt);
    const to = Math.floor(this.pos);
    this.pieces = this.pieces.filter((p) => now - p.at < FADE_MS);
    if (to > this.shown) {
      this.pieces.push({ len: to - this.shown, at: now });
      this.shown = to;
      this.onFrame(this.text.slice(0, to), this.pieces);
    }
    if (this.shown < this.text.length) { raf((t) => this.frame(t)); return; }
    this.running = false;
    if (this.done) this.waiters.splice(0).forEach((r) => r());
  }
  /** No more text will come: resolves once everything is on screen. */
  finish() {
    this.done = true;
    if (!this.running && this.shown >= this.text.length) return Promise.resolve();
    return new Promise((r) => this.waiters.push(r));
  }
}

/**
 * Put rendered HTML into `el` and let the newest letters fade in. Re-rendering the markdown every
 * frame would restart a fade; instead the last pieces get spans whose animation is already as far
 * along as their age (a negative animation-delay), so the fade continues smoothly.
 */
export function renderFade(el, html, pieces, now = performance.now()) {
  el.innerHTML = html;
  let node = lastText(el);
  for (let i = pieces.length - 1; i >= 0 && node; i--) {
    const p = pieces[i];
    const len = Math.min(p.len, node.data.length);
    if (len <= 0) break;
    const tail = node.splitText(node.data.length - len);
    const span = document.createElement("span");
    span.className = "nw";
    span.style.animationDelay = `${-(now - p.at)}ms`;
    tail.replaceWith(span);
    span.appendChild(tail);
    node = node.data.length ? node : null;
  }
}

function lastText(el) {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.data.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
  });
  let last = null;
  while (walker.nextNode()) last = walker.currentNode;
  return last;
}

/** Append `node` (the tiny writing drop) at the end of the last line of text inside `el`. */
export function placeTail(el, node) {
  let t = el;
  const containers = new Set(["UL", "OL", "BLOCKQUOTE", "TABLE", "THEAD", "TBODY", "TR", "DIV"]);
  while (t.lastElementChild && (t === el || containers.has(t.tagName))) t = t.lastElementChild;
  if (t.tagName === "PRE") t = t.querySelector("code") || t;
  t.appendChild(node);
}

/** Sidebar groups: [{label, chats}] for Today, Yesterday, Last 7 days, Older (empty ones left out). */
export function groupChats(chats, now = new Date()) {
  const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const today = day(now);
  const g = { "Today": [], "Yesterday": [], "Last 7 days": [], "Older": [] };
  for (const c of chats) {
    const diff = Math.round((today - day(new Date(c.updated))) / 864e5);
    (diff <= 0 ? g.Today : diff === 1 ? g.Yesterday : diff <= 7 ? g["Last 7 days"] : g.Older).push(c);
  }
  return Object.entries(g).filter(([, v]) => v.length).map(([label, list]) => ({ label, chats: list }));
}

/** "Dewdrop · 412 tokens · 41 tok/s" (no tok/s part when tps is null). */
export function metaText(tokens, tps) {
  return `Dewdrop · ${tokens} tokens` + (tps == null ? "" : ` · ${tps} tok/s`);
}

// ------------------------------------------------------------------ round 2 (SPEC §11)

/**
 * Keeps the thread pinned to the newest text while an answer is written, until the reader takes
 * over: any scroll up (wheel, touch, keys, the scrollbar) stops following, so the screen stays
 * exactly where they are reading. Scrolling back to the bottom (or bottom()) follows again.
 * onChange(following) runs when that flips (app.js shows the "↓ Latest" button while false).
 *   const follow = new Follow(thread, (on) => (jump.hidden = on));
 *   follow.stick()   // after new text: scroll down only while following
 *   follow.bottom()  // after sending: always jump to the bottom and follow again
 *   follow.bottom(true)  // the same, scrolling smoothly ("↓ Latest")
 */
export class Follow {
  constructor(el, onChange = () => {}) {
    this.el = el;
    this.on = true;
    this.onChange = onChange;
    this.lastTop = el.scrollTop;
    const off = () => this.set(false);
    el.addEventListener("wheel", (e) => { if (e.deltaY < 0) off(); }, { passive: true });
    el.addEventListener("touchmove", () => { if (this.gap() > 4) off(); }, { passive: true });
    el.addEventListener("keydown", (e) => { if (["ArrowUp", "PageUp", "Home"].includes(e.key)) off(); });
    el.addEventListener("scroll", () => {
      if (el.scrollTop < this.lastTop - 2 && this.gap() > 4) off();   // dragged the scrollbar up
      else if (this.gap() <= 4) this.set(true);                        // back at the bottom
      this.lastTop = el.scrollTop;
    }, { passive: true });
  }
  gap() { return this.el.scrollHeight - this.el.scrollTop - this.el.clientHeight; }
  set(on) { if (on !== this.on) { this.on = on; this.onChange(on); } }
  stick() { if (this.on) { this.el.scrollTop = this.el.scrollHeight; this.lastTop = this.el.scrollTop; } }
  bottom(smooth = false) {
    this.set(true);
    if (smooth && this.el.scrollTo) this.el.scrollTo({ top: this.el.scrollHeight, behavior: "smooth" });
    else this.stick();
  }
}

const EXT = { javascript: "js", js: "js", mjs: "mjs", jsx: "jsx", typescript: "ts", ts: "ts", tsx: "tsx", python: "py",
  py: "py", html: "html", xml: "xml", svg: "svg", css: "css", scss: "scss", less: "less", json: "json", bash: "sh",
  sh: "sh", shell: "sh", zsh: "sh", yaml: "yml", yml: "yml", markdown: "md", md: "md", sql: "sql", c: "c", cpp: "cpp",
  "c++": "cpp", java: "java", go: "go", rust: "rs", rs: "rs", ruby: "rb", rb: "rb", php: "php", swift: "swift",
  kotlin: "kt", kt: "kt", csharp: "cs", cs: "cs", lua: "lua", r: "r", ini: "ini", toml: "toml", diff: "diff",
  makefile: "mk", dockerfile: "Dockerfile", text: "txt", txt: "txt", plaintext: "txt", csv: "csv", ino: "ino",
  arduino: "ino", vue: "vue" };
const ALIAS = { html: "xml", svg: "xml", vue: "xml", sh: "bash", zsh: "bash", shell: "bash", yml: "yaml",
  ino: "cpp", arduino: "cpp", toml: "ini", py: "python", js: "javascript", mjs: "javascript", ts: "typescript",
  rb: "ruby", rs: "rust", kt: "kotlin", cs: "csharp", md: "markdown", txt: "plaintext", text: "plaintext" };
const MIME = { html: "text/html", css: "text/css", js: "text/javascript", mjs: "text/javascript", json: "application/json",
  svg: "image/svg+xml", xml: "application/xml", md: "text/markdown", csv: "text/csv" };

/**
 * What a code fence's info string says: "html index.html" -> {lang: "html", file: "index.html"};
 * "index.html" -> {lang: "html", file: "index.html"}; "python" -> {lang: "python", file: "code.py"};
 * "" -> {lang: "", file: "code.txt"}. File names keep only safe characters (no folders).
 */
export function codeInfo(info) {
  const words = String(info || "").trim().split(/\s+/).filter(Boolean);
  let lang = "", file = "";
  for (const w of words) {
    if (!file && /^[\w.-]+\.[A-Za-z0-9]+$/.test(w) && (lang || words.length === 1)) file = w;
    else if (!lang) lang = w.toLowerCase();
  }
  if (!lang && file) lang = file.split(".").pop().toLowerCase();
  if (file) file = file.replace(/[^\w.-]/g, "_").replace(/^\.+/, "").slice(0, 80);
  if (!file) {
    const ext = EXT[lang] || "txt";
    file = ext === "Dockerfile" ? "Dockerfile" : "code." + ext;
  }
  return { lang, file };
}

function escHtml(s) { return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }

/** Coloured code (highlight.js, window.hljs) as HTML; plain escaped text when the language is unknown. */
export function highlight(code, lang, hljs = globalThis.hljs) {
  const name = ALIAS[lang] || lang;
  if (hljs && name && hljs.getLanguage(name)) {
    try { return hljs.highlight(code, { language: name, ignoreIllegals: true }).value; } catch { /* plain below */ }
  }
  return escHtml(code);
}

/**
 * Teach a markdown-it instance Dewdrop's code blocks: coloured, with a head that names the file and
 * has Copy and Download buttons (codeActions() makes them work):
 *   <div class="code"><div class="code-head"><span class="lang">FILE</span>
 *   <button type="button" data-copy-code>Copy</button><button type="button" data-download="FILE">Download</button></div>
 *   <pre><code class="hljs language-LANG">…</code></pre></div>
 */
export function codeBlocks(md, hljs = globalThis.hljs) {
  md.renderer.rules.fence = (tokens, idx) => {
    const t = tokens[idx];
    const { lang, file } = codeInfo(t.info);
    const cls = lang ? ` class="hljs language-${escHtml(lang)}"` : ' class="hljs"';
    return `<div class="code"><div class="code-head"><span class="lang">${escHtml(file)}</span>` +
      `<button type="button" data-copy-code>Copy</button><button type="button" data-download="${escHtml(file)}">Download</button></div>` +
      `<pre><code${cls}>${highlight(t.content, lang, hljs)}</code></pre></div>\n`;
  };
  return md;
}

/** One click handler on `root` for every code block's Copy and Download buttons. */
export function codeActions(root) {
  root.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-copy-code],[data-download]");
    if (!btn) return;
    const code = btn.closest(".code")?.querySelector("pre code")?.textContent ?? "";
    if (btn.hasAttribute("data-copy-code")) {
      navigator.clipboard?.writeText(code);
      btn.textContent = "Copied";
      setTimeout(() => { btn.textContent = "Copy"; }, 1500);
      return;
    }
    const name = btn.dataset.download || "code.txt";
    const ext = name.split(".").pop().toLowerCase();
    const url = URL.createObjectURL(new Blob([code], { type: (MIME[ext] || "text/plain") + ";charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

// ------------------------------------------------------------------ round 3: sources (SPEC §12.4)

/**
 * The cards under an answer (SPEC §12.4): each opens to show its quote and an "Open" link.
 * <div class="sources"><details class="src" data-n="N"><summary><span class="n">N</span><div><b>TITLE</b>
 * <small>META or the host</small></div></summary><div class="src-body">[<blockquote>QUOTE</blockquote>]
 * <a class="src-open" href=URL target=_blank rel=noopener noreferrer>Open on HOST ↗</a></div></details>…</div>
 * "" when there are none.
 */
export function sourcesHtml(sources) {
  if (!sources || !sources.length) return "";
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
  const groups = [["file", "Your files"], ["guide", "Guidelines"], ["study", "Studies"], ["other", "Other sources"]];
  const groupOf = (s) => (s.kind === "file" ? "file" : s.site || s.type === "Guideline" ? "guide" : s.kind === "pubmed" ? "study" : "other");
  const item = (s) => {
    const kind = sourceKind(s);
    const meta = (s.type ? String(s.meta || "").replace(s.type, "").replace(/^\s*·\s*/, "") : s.meta) || host(s.url);
    return `<details class="src" data-n="${Number(s.n)}"><summary><span class="n">${Number(s.n)}</span>` +
      `<div><b>${escHtml(s.title || s.url)}</b><small>${kind ? `<span class="stype" data-kind="${escHtml(kind[0])}">${escHtml(kind[1])}</span> · ` : ""}` +
      `${escHtml(meta)}</small></div></summary>` +
      `<div class="src-body">${s.quote ? `<blockquote>${escHtml(s.quote)}</blockquote>` : ""}` +
      (s.url ? `<a class="src-open" href="${escHtml(s.url)}" target="_blank" rel="noopener noreferrer">Open on ${escHtml(host(s.url) || "the site")} ↗</a>` : "") +
      "</div></details>";
  };
  const parts = groups.map(([g, name]) => {
    const list = sources.filter((s) => groupOf(s) === g);
    return list.length ? `<div class="src-group" data-group="${g}"><h4>${name}</h4><div class="src-list">${list.map(item).join("")}</div></div>` : "";
  });
  return '<div class="sources">' + parts.join("") + "</div>";
}

const SITE_KINDS = { "richtlijnendatabase.nl": "Guideline", "rivm.nl": "Guideline", "thuisarts.nl": "Patient information",
  "farmacotherapeutischkompas.nl": "Drug information", "kinderformularium.nl": "Drug information", "ge-bu.nl": "Drug review",
  "lareb.nl": "Side effects" };

/** A source's kind for the list under an answer: [css key, label] or null. */
export function sourceKind(s) {
  if (s.kind === "file") return ["file", "Your file"];
  if (s.type) return [{ Guideline: "guide", "Meta-analysis": "review", "Systematic review": "review", RCT: "rct" }[s.type] || "study", s.type];
  if (s.site) return ["guide", SITE_KINDS[s.site] || "Website"];
  return null;
}

/** Turn "[n]" in the text of `el` into <a class="cite" href=URL>n</a> for the answer's sources (not inside code). */
export function citeLinks(el, sources) {
  if (!sources || !sources.length) return;
  const byN = new Map(sources.map((s) => [String(s.n), s]));
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    const n = walker.currentNode;
    if (/\[\d+(?:\s*,\s*\d+)*\]/.test(n.data) && !n.parentElement.closest("code,pre,a")) nodes.push(n);
  }
  for (const node of nodes) {
    const frag = document.createDocumentFragment();
    let last = 0;
    node.data.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (m, list, at) => {
      const found = list.split(",").map((x) => byN.get(x.trim())).filter(Boolean);
      if (!found.length) return m;
      frag.append(node.data.slice(last, at));
      for (const s of found) {
        const a = document.createElement("a");
        a.className = "cite";
        if (s.url) a.href = s.url;            // a file of yours has no link (SPEC §16.2)
        a.dataset.n = String(s.n);
        a.title = s.title || s.url;
        a.textContent = String(s.n);
        frag.append(a);
      }
      last = at + m.length;
      return m;
    });
    frag.append(node.data.slice(last));
    node.replaceWith(frag);
  }
}

/** Tapping a [n] opens that source's card under the same answer (and scrolls to it) instead of leaving the app. */
export function sourceActions(root) {
  root.addEventListener("click", (e) => {
    const a = e.target.closest("a.cite");
    if (!a) return;
    const card = a.closest(".ai")?.querySelector(`details.src[data-n="${a.dataset.n}"]`);
    if (!card) return;
    e.preventDefault();
    card.open = true;
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    card.classList.add("flash");
    setTimeout(() => card.classList.remove("flash"), 1200);
  });
}

// ------------------------------------------------------------------ round 12: the live drop (orb.css), M3 fly

/** Markup of a live drop: every phase layer inside one .orb.live (orb.css). `cls` adds classes ("tiny", …). */
export function orbHtml(cls = "", phase = "idle") {
  return `<span class="orb live${cls ? " " + cls : ""}" data-phase="${phase}"><span class="pool"></span>` +
    '<span class="clouds"><i></i><i></i><i></i><i></i></span>' +
    '<span class="radar"><span class="ring"></span><span class="ring"></span><span class="beam"></span></span>' +
    '<span class="read"><span class="lines"></span><span class="beam"></span></span>' +
    '<span class="sum"><em></em><b></b><b></b><b></b></span>' +
    '<span class="water"><span class="body"><span class="slosh"><span class="deep"></span><span class="wave c"></span>' +
    '<span class="wave b"></span><span class="wave"></span></span></span></span></span>';
}

const BEAM_MS = 2400;

/**
 * Bring a live drop to life: its clouds drift to new random spots (round 12: further and faster, eased) and its
 * radar beam turns, drawing a source exactly when the beam reaches it (round 3). Everything is Web Animations
 * on transform/opacity. Returns {stop()} to end it all. Does nothing without element.animate (tests, old browsers).
 */
export function startOrb(orb, { range = 45, min = 1500, max = 2600 } = {}) {
  if (!orb || typeof orb.animate !== "function") return { stop() {} };
  const anims = [];
  let alive = true;
  orb.querySelectorAll(".clouds i").forEach((el) => {
    let x = 0, y = 0, sc = 1;
    const go = () => {
      if (!alive) return;
      const nx = (Math.random() * 2 - 1) * range, ny = (Math.random() * 2 - 1) * range, s = .7 + Math.random() * .6;
      const a = el.animate([{ transform: `translate3d(${x}%,${y}%,0) scale(${sc})` }, { transform: `translate3d(${nx}%,${ny}%,0) scale(${s})` }],
        { duration: min + Math.random() * (max - min), easing: "cubic-bezier(.45,0,.55,1)", fill: "forwards" });
      x = nx; y = ny; sc = s;
      a.onfinish = go;
      anims.push(a);
      if (anims.length > 40) anims.splice(0, 20);
    };
    go();
  });
  const box = orb.querySelector(".radar"), beam = box && box.querySelector(".beam");
  let timer = null;
  if (beam) {
    anims.push(beam.animate([{ transform: "rotate(0deg)" }, { transform: "rotate(360deg)" }], { duration: BEAM_MS, iterations: Infinity }));
    const t0 = performance.now();
    let n = 0;
    timer = setInterval(() => {
      if (orb.dataset.phase !== "searching") return;
      if (n >= 8) { box.querySelectorAll(".blip").forEach((b) => b.remove()); n = 0; }
      const ang = Math.random() * 360, r = 14 + Math.random() * 28;
      const x = 50 + r * Math.sin(ang * Math.PI / 180), y = 50 - r * Math.cos(ang * Math.PI / 180);
      const wait = (ang / 360 * BEAM_MS - (performance.now() - t0) % BEAM_MS + BEAM_MS) % BEAM_MS;
      setTimeout(() => {
        if (!alive) return;
        const b = document.createElement("span");
        b.className = "blip";
        b.style.setProperty("--x", x + "%");
        b.style.setProperty("--y", y + "%");
        box.appendChild(b);
        b.animate([{ opacity: 1, transform: "scale(1.7)" }, { opacity: .85, transform: "scale(1)", offset: .12 }, { opacity: .3, transform: "scale(1)" }],
          { duration: BEAM_MS, iterations: Infinity });
        n++;
      }, wait);
    }, 1000);
  }
  return {
    stop() {
      alive = false;
      clearInterval(timer);
      anims.forEach((a) => a.cancel());
      box && box.querySelectorAll(".blip").forEach((b) => b.remove());
    },
  };
}

/** M3: `el` (your new message) flies out of `from` (the send button) to its place. */
export function flyFrom(el, from) {
  if (!el || !from || !el.getBoundingClientRect) return;
  const a = el.getBoundingClientRect(), b = from.getBoundingClientRect();
  el.style.setProperty("--dx", `${Math.round(b.left + b.width / 2 - a.right)}px`);
  el.style.setProperty("--dy", `${Math.round(b.top + b.height / 2 - a.bottom)}px`);
  el.classList.add("fly");
}

// ------------------------------------------------------------------ §13.3: the search trail, thinking, the checker's marks

/** One trail step as a list item (search: "Searched PubMed for "x" · 5 found ↗"; read: "Read <title> ↗"). */
export function trailStep(s) {
  const link = (url, text) => (url ? `<a href="${escHtml(url)}" target="_blank" rel="noopener noreferrer">${text} ↗</a>` : text);
  if (s.kind === "read" || s.kind === "open") return `<li class="step read">Read ${link(s.url, escHtml(s.title || s.url || "a page"))}</li>`;
  const found = s.error ? `<span class="err">${escHtml(s.error)}</span>` : `${Number(s.found) || 0} found`;
  const mesh = (s.mesh || []).length ? `<span class="mesh" title="${escHtml(s.translation || "")}">` +
    s.mesh.map((m) => `<i>${escHtml(m)}</i>`).join("") + "</span>" : "";
  return `<li class="step search">Searched ${escHtml(s.source || "the web")} for <b>"${escHtml(s.query || "")}"</b> · ` +
    `${s.url ? link(s.url, found) : found}${mesh}</li>`;
}

const STAGES = [["thinking", "Understanding the question"], ["searching", "Searching"], ["reading", "Reading the sources one by one"],
  ["writing", "Writing the answer"]];

/**
 * The search trail under an answer (SPEC §13.3, §15.6): <details class="trail">. A Clear Source trail (it has "plan"
 * steps) shows four stages, each with the drop doing that stage; other trails stay a plain list of steps. `open` while
 * the answer is still being researched: the current stage's drop moves (animateTrail).
 */
export function trailHtml(steps, open = false) {
  if (!steps || !steps.length) return "";
  if (!steps.some((s) => s.kind === "plan")) {
    const n = steps.filter((s) => s.kind === "search").length, m = steps.filter((s) => s.kind === "read" || s.kind === "open").length;
    const sum = `Searched ${n} time${n === 1 ? "" : "s"}` + (m ? ` · read ${m} page${m === 1 ? "" : "s"}` : "");
    return `<details class="trail"${open ? " open" : ""}><summary>${sum}</summary><ol>${steps.map(trailStep).join("")}</ol></details>`;
  }
  const link = (url, text, cls) => (url ? `<a class="${cls}" href="${escHtml(url)}" target="_blank" rel="noopener noreferrer">${text} ↗</a>`
    : `<span class="${cls}">${text}</span>`);
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
  const plans = steps.filter((s) => s.kind === "plan"), searches = steps.filter((s) => s.kind === "search");
  const reads = steps.filter((s) => s.kind === "read" && s.status);
  const used = reads.filter((s) => s.status === "used").length;
  const terms = [];
  for (const p of plans) {
    for (const m of p.mesh || []) if (!terms.includes(m + "\u0000")) terms.push(m + "\u0000");
    for (const q of p.queries || []) if (!terms.includes(q)) terms.push(q);
  }
  const about = plans.find((p) => p.about)?.about || "";
  const body = [
    (about ? `<p>${escHtml(about)}</p>` : "") + (terms.length ? `<div class="terms">${terms.map((t) => t.endsWith("\u0000")
      ? `<span class="t">${escHtml(t.slice(0, -1))}<small>MeSH</small></span>` : `<span class="t">${escHtml(t)}</span>`).join("")}</div>` : ""),
    searches.length ? `<ol class="rows">${searches.map((s) => {
      const n = s.error ? escHtml(s.error) : `${Number(s.found) || 0} found`;
      const mesh = (s.mesh || []).length ? `<span class="mesh">${s.mesh.map((m) => `<i>${escHtml(m)}</i>`).join("")}</span>` : "";
      return `<li class="step search${s.broader ? " wider" : ""}"><span class="where">${s.broader ? "broader" : escHtml(s.source || "the web")}</span>` +
        `<span class="what">${escHtml(s.query || "")}${mesh}</span>${link(s.url, n, s.error || !Number(s.found) ? "n none" : "n")}</li>`;
    }).join("")}</ol>` : "",
    reads.length ? `<ol class="rows">${reads.map((s) => {
      const res = s.status === "used" ? `<b>${Number(s.quotes)}</b> quote${s.quotes === 1 ? "" : "s"}`
        : s.status === "error" ? "could not read" : "not relevant";
      return `<li class="step readone ${s.status === "used" ? "used" : "skip"}"><span class="where">${escHtml(host(s.url) || "")}</span>` +
        `${link(s.url, escHtml(s.title || s.url || ""), "what")}<span class="res">${res}</span></li>`;
    }).join("")}</ol>` : "",
    open ? "" : `<p>From ${used} source${used === 1 ? "" : "s"}, every quote checked word for word.</p>`,
  ];
  const reached = open ? (reads.length ? 2 : searches.length ? 1 : 0) : 3;
  const stages = STAGES.map(([phase, name], i) => {
    if (i > reached) return "";
    const now = open && i === reached;
    const extra = i === 1 && searches.length ? `<span>${searches.length} search${searches.length === 1 ? "" : "es"}</span>`
      : i === 2 && reads.length ? `<span>${reads.length} read · ${used} useful</span>` : "";
    return `<li class="stage${now ? " now" : ""}">${orbHtml("", now ? phase : "idle")}<h5>${name}${extra}</h5>` +
      `<div class="stage-body">${body[i]}</div></li>`;
  }).join("");
  const sum = open ? `${STAGES[reached][1]}…` : `Researched · ${searches.length} search${searches.length === 1 ? "" : "es"} · read ${reads.length} · used ${used}`;
  return `<details class="trail staged"${open ? " open" : ""}><summary>${sum}</summary><ol class="stages">${stages}</ol></details>`;
}

/** Start the moving drop of the trail's current stage (clouds, radar); call after drawing a live trail. */
export function animateTrail(root) {
  const orb = root?.querySelector(".trail .stage.now .orb.live");
  return orb ? startOrb(orb, { range: 30 }) : { stop() {} };
}

/** What the model thought: <details class="thought"><summary>Thought for N s</summary><div class="thought-text">…</div></details>. */
export function thoughtHtml(text, secs) {
  if (!text) return "";
  const t = secs == null ? "Thought" : `Thought for ${Math.max(1, Math.round(secs))} s`;
  return `<details class="thought"><summary>${t}</summary><div class="thought-text">${escHtml(text)}</div></details>`;
}

const SHORT = /short answer|kort antwoord|summary|samenvatting|in short|in het kort/i;

/** Put a Clear Source answer's opening summary (the "Short answer" section, or the text before the first heading) in
 *  <div class="short">: it summarises the quotes below it (SPEC §15.5). */
export function shortBox(bub) {
  if (!bub || bub.querySelector(":scope > .short")) return;
  const kids = [...bub.children], isH = (el) => /^H[1-4]$/.test(el.tagName);
  if (!kids.some(isH)) return;
  let from = 0;
  if (isH(kids[0])) { if (!SHORT.test(kids[0].textContent)) return; from = 1; }
  const part = [];
  for (let i = from; i < kids.length && !isH(kids[i]); i++) part.push(kids[i]);
  if (!part.length) return;
  const box = document.createElement("div");
  box.className = "short";
  box.innerHTML = `<div class="lbl">${from ? escHtml(kids[0].textContent) : "Short answer"} <span>· summary of the sources below</span></div>`;
  part[0].before(box);
  box.append(...part);
  if (from) kids[0].remove();
}

const norm = (t) => String(t || "").replace(/[‘’]/g, "'").replace(/[“”„]/g, '"').replace(/[–—−]/g, "-").replace(/\s+/g, " ")
  .trim().toLowerCase();

/**
 * Show the checker's report (SPEC §13.1) in a rendered answer: every blockquote and inline quote the report checked
 * gets class q-ok (✓, "Found word for word in source n") or q-bad (⚠ + the reason); sentences without a source and
 * references to sources that don't exist are listed in <div class="verify-note"> after `bub`.
 */
export function markVerify(bub, report) {
  if (!bub || !report) return;
  shortBox(bub);
  const qs = report.quotes || [];
  const find = (text) => qs.find((q) => {
    const a = norm(q.text), b = norm(text).replace(/\s*\[\d+(\s*,\s*\d+)*\]\s*/g, " ").replace(/^["']|["']$/g, "").trim();
    return a && (b.includes(a) || a.includes(b));
  });
  const mark = (el, q) => {
    el.classList.add(q.ok ? "q-ok" : "q-bad");
    el.title = q.ok ? `Found word for word in source ${q.cites.join(", ")}` : `Not verified: ${q.why}`;
    if (el.querySelector(".q-mark")) return;
    const last = el.matches("blockquote") ? [...el.querySelectorAll("p")].pop() || el : el;
    last.insertAdjacentHTML("beforeend", `<span class="q-mark">${q.ok ? "✓" : "⚠ " + escHtml(q.why || "not verified")}</span>`);
  };
  bub.querySelectorAll("blockquote").forEach((el) => { const q = find(el.textContent); if (q) mark(el, q); });
  // inline quotes: wrap "…" text in a span when the report checked it
  const walker = document.createTreeWalker(bub, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) if (/["“][^"”]{10,}["”]/.test(walker.currentNode.data) && !walker.currentNode.parentElement.closest("blockquote,code,pre")) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const m = node.data.match(/["“]([^"”]{10,})["”]/);
    const q = m && find(m[1]);
    if (!q) continue;
    const at = node.data.indexOf(m[0]);
    const span = document.createElement("span");
    span.className = "q-inline";
    const mid = node.splitText(at);
    mid.splitText(m[0].length);
    mid.replaceWith(span);
    span.append(mid);
    mark(span, q);
  }
  const bits = [];
  if ((report.uncited || []).length) bits.push(`${report.uncited.length} sentence${report.uncited.length === 1 ? " has" : "s have"} no source`);
  if ((report.bad_refs || []).length) bits.push(`source ${report.bad_refs.join(", ")} does not exist`);
  const bad = qs.filter((q) => !q.ok).length;
  if (bad) bits.unshift(`${bad} quote${bad === 1 ? " was" : "s were"} not found in the source`);
  if (!bits.length) return;
  const list = (report.uncited || []).map((u) => `<li>${escHtml(u)}</li>`).join("");
  bub.insertAdjacentHTML("afterend", `<details class="verify-note"><summary>⚠ ${escHtml(bits.join(" · "))}</summary>` +
    (list ? `<ul>${list}</ul>` : "") + "</details>");
}
