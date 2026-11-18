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
  bottom() { this.set(true); this.stick(); }
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
