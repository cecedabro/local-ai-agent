// Judge for chat/static/js/stream.js. Protected. Run: node --test chat/tests/js/stream.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { splitEvents, readEvents, Typewriter, FADE_MS, groupChats, metaText, Follow, codeInfo, highlight, codeBlocks } from "../../static/js/stream.js";

test("splitEvents", () => {
  const r = splitEvents('event: delta\ndata: {"text":"Hi"}\n\nevent: phase\ndata: {"phase":"writing"}\n\nevent: do');
  assert.deepEqual(r.events, [{ event: "delta", data: { text: "Hi" } }, { event: "phase", data: { phase: "writing" } }]);
  assert.equal(r.rest, "event: do");
  assert.deepEqual(splitEvents(""), { events: [], rest: "" });
});

test("readEvents", async () => {
  const parts = ['event: delta\ndata: {"te', 'xt":"a"}\n\nevent: done\ndata: {"tokens":1}\n\n'];
  const enc = new TextEncoder();
  const body = new ReadableStream({ start(c) { parts.forEach((p) => c.enqueue(enc.encode(p))); c.close(); } });
  const got = [];
  await readEvents({ body }, (e, d) => got.push([e, d]));
  assert.deepEqual(got, [["delta", { text: "a" }], ["done", { tokens: 1 }]]);
});

test("Typewriter: steady, finishes, recent pieces", async () => {
  const frames = [];
  const tw = new Typewriter((shown, pieces) => frames.push({ shown, pieces: pieces.map((p) => ({ ...p })), now: performance.now() }));
  tw.push("Hello wor");
  setTimeout(() => tw.push("ld, how are you today? Fine, thanks."), 30);
  await new Promise((r) => setTimeout(r, 40));
  await tw.finish();
  assert.equal(frames.at(-1).shown, "Hello world, how are you today? Fine, thanks.");
  assert.ok(frames.length >= 5, "released over several frames, not at once");
  for (const f of frames) {
    assert.ok(f.pieces.length >= 1);
    assert.ok(f.pieces.every((p) => p.len > 0 && f.now - p.at < FADE_MS + 20));
  }
  assert.ok(frames.every((f, i) => i === 0 || f.shown.startsWith(frames[i - 1].shown)), "text only grows");
});

test("Typewriter catches up with a big burst", async () => {
  let shown = "", n = 0;
  const tw = new Typewriter((s) => { shown = s; n++; });
  const big = "word ".repeat(400);
  const t0 = performance.now();
  tw.push(big);
  await tw.finish();
  assert.equal(shown, big);
  assert.ok(performance.now() - t0 < 6000, "speeds up when far behind");
  assert.ok(n > 20, "still spread over many frames");
});

test("groupChats", () => {
  const now = new Date(2026, 10, 18, 15, 0);
  const iso = (d, h = 10) => new Date(2026, 10, d, h, 0).toISOString();
  const g = groupChats([{ id: 1, updated: iso(18) }, { id: 2, updated: iso(17) }, { id: 3, updated: iso(14) },
                        { id: 4, updated: iso(1) }], now);
  assert.deepEqual(g.map((x) => [x.label, x.chats.map((c) => c.id)]),
    [["Today", [1]], ["Yesterday", [2]], ["Last 7 days", [3]], ["Older", [4]]]);
  assert.deepEqual(groupChats([], now), []);
});

test("metaText", () => {
  assert.equal(metaText(412, 41), "Dewdrop · 412 tokens · 41 tok/s");
  assert.equal(metaText(3, null), "Dewdrop · 3 tokens");
});

test("codeInfo: language, file name, safe names", () => {
  assert.deepEqual(codeInfo("html index.html"), { lang: "html", file: "index.html" });
  assert.deepEqual(codeInfo("index.html"), { lang: "html", file: "index.html" });
  assert.deepEqual(codeInfo("python"), { lang: "python", file: "code.py" });
  assert.deepEqual(codeInfo(""), { lang: "", file: "code.txt" });
  assert.deepEqual(codeInfo("JavaScript app.js"), { lang: "javascript", file: "app.js" });
  assert.equal(codeInfo("dockerfile").file, "Dockerfile");
  assert.equal(codeInfo("sh ../../etc/x.sh").file.includes("/"), false);
});

test("highlight: known language via hljs, else escaped text", () => {
  const fake = { getLanguage: (n) => n === "xml", highlight: (c, o) => ({ value: `<span class="hljs-tag">${o.language}</span>` }) };
  assert.equal(highlight("<p>", "html", fake), '<span class="hljs-tag">xml</span>');
  assert.equal(highlight("<p>&", "klingon", fake), "&lt;p&gt;&amp;");
  assert.equal(highlight("a<b", "", undefined), "a&lt;b");
});

test("codeBlocks: fence with head, Copy and Download", () => {
  const md = { renderer: { rules: {} } };
  codeBlocks(md, { getLanguage: () => false });
  const html = md.renderer.rules.fence([{ info: "html page.html", content: "<b>hi</b>\n" }], 0);
  assert.match(html, /^<div class="code"><div class="code-head"><span class="lang">page\.html<\/span>/);
  assert.match(html, /data-copy-code/);
  assert.match(html, /data-download="page\.html"/);
  assert.match(html, /<pre><code class="hljs language-html">&lt;b&gt;hi&lt;\/b&gt;\n<\/code><\/pre><\/div>/);
});

test("Follow: stops on scroll up, follows again at the bottom", () => {
  const el = new EventTarget();
  Object.assign(el, { scrollTop: 600, scrollHeight: 1000, clientHeight: 400 });
  const seen = [];
  const f = new Follow(el, (on) => seen.push(on));
  el.scrollHeight = 1200; f.stick();
  assert.equal(el.scrollTop, 1200);                         // following: pinned to the bottom
  el.dispatchEvent(Object.assign(new Event("wheel"), { deltaY: -40 }));
  assert.equal(f.on, false);
  el.scrollHeight = 1500; el.scrollTop = 700; f.stick();
  assert.equal(el.scrollTop, 700);                          // the reader's place is kept
  el.scrollTop = 1100; el.dispatchEvent(new Event("scroll"));
  assert.equal(f.on, true);                                 // back at the bottom
  el.scrollTop = 500; el.dispatchEvent(new Event("scroll"));
  assert.equal(f.on, false);                                // scrollbar dragged up
  f.bottom();
  assert.equal(f.on, true); assert.equal(el.scrollTop, 1500);
  assert.deepEqual(seen, [false, true, false, true]);
});
