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

test("sourcesHtml: cards that open to a quote and an Open link; escaped", async () => {
  const { sourcesHtml } = await import("../../static/js/stream.js");
  assert.equal(sourcesHtml([]), "");
  const html = sourcesHtml([{ n: 1, title: "A <b>", url: "https://www.rivm.nl/x", kind: "web", quote: "Wie <krijgt> hem" },
    { n: 2, title: "T", url: "https://pubmed.ncbi.nlm.nih.gov/1/", meta: "BMJ · 2021" }]);
  assert.match(html, /^<div class="sources"><div class="src-group" data-group="other"><h4>Other sources<\/h4><div class="src-list"><details class="src" data-n="1"><summary><span class="n">1<\/span><div><b>A &lt;b&gt;<\/b><small>rivm\.nl<\/small>/);
  assert.match(html, /<blockquote>Wie &lt;krijgt&gt; hem<\/blockquote><a class="src-open" href="https:\/\/www\.rivm\.nl\/x" target="_blank" rel="noopener noreferrer">Open on rivm\.nl ↗<\/a>/);
  assert.match(html, /<small>BMJ · 2021<\/small><\/div><\/summary><div class="src-body"><a class="src-open"/);
});

test("sourcesHtml groups guidelines first, then studies, with their kind (SPEC §15.6)", async () => {
  const { sourcesHtml } = await import("../../static/js/stream.js");
  const html = sourcesHtml([{ n: 1, title: "Trial", url: "https://pubmed.ncbi.nlm.nih.gov/1/", kind: "pubmed", type: "RCT", meta: "RCT · BMJ · 2021" },
    { n: 2, title: "CVRM", url: "https://richtlijnendatabase.nl/r", kind: "page", site: "richtlijnendatabase.nl", meta: "richtlijnendatabase.nl" }]);
  assert.ok(html.indexOf('data-group="guide"') < html.indexOf('data-group="study"'));
  assert.match(html, /<span class="stype" data-kind="rct">RCT<\/span> · BMJ · 2021<\/small>/);
  assert.match(html, /<span class="stype" data-kind="guide">Guideline<\/span> · richtlijnendatabase\.nl/);
});

test("trailHtml: a Clear Source trail in four stages; other trails stay a list", async () => {
  const { trailHtml } = await import("../../static/js/stream.js");
  const steps = [{ kind: "plan", about: "Lp(a) <risk>", queries: ["lpa"], mesh: ["Lipoprotein(a)"] },
    { kind: "search", source: "PubMed", query: "lpa", found: 2, url: "https://pubmed.ncbi.nlm.nih.gov/?term=lpa" },
    { kind: "search", source: "PubMed", query: "lpa", found: 9, url: null, broader: true },
    { kind: "read", n: 1, title: "A", url: "https://pubmed.ncbi.nlm.nih.gov/1/", quotes: 2, status: "used" },
    { kind: "read", n: 2, title: "B", url: "https://pubmed.ncbi.nlm.nih.gov/2/", quotes: 0, status: "not relevant" }];
  const done = trailHtml(steps);
  assert.match(done, /<summary>Researched · 2 searches · read 2 · used 1<\/summary>/);
  assert.equal((done.match(/<li class="stage/g) || []).length, 4);
  assert.match(done, /Lp\(a\) &lt;risk&gt;/);
  assert.match(done, /<span class="t">Lipoprotein\(a\)<small>MeSH<\/small><\/span>/);
  assert.match(done, /class="step search wider"><span class="where">broader/);
  const live = trailHtml(steps.slice(0, 2), true);
  assert.match(live, /<summary>Searching…<\/summary>/);
  assert.match(live, /class="stage now"><span class="orb live" data-phase="searching">/);
  assert.equal((live.match(/<li class="stage/g) || []).length, 2, "stages appear as they start");
  assert.match(trailHtml([{ kind: "search", source: "the web", query: "x", found: 1, url: null }]), /<summary>Searched 1 time<\/summary><ol><li class="step search">/);
});

test("orbHtml: a live drop with every phase layer", async () => {
  const { orbHtml, startOrb } = await import("../../static/js/stream.js");
  const h = orbHtml("tiny", "writing");
  assert.match(h, /^<span class="orb live tiny" data-phase="writing">/);
  for (const layer of ["pool", "clouds", "radar", "read", "sum", "water"]) assert.match(h, new RegExp(`class="${layer}"`));
  assert.equal((h.match(/class="wave/g) || []).length, 3);
  assert.doesNotThrow(() => startOrb(null).stop(), "no element, no animation: a no-op");
});

test("trailHtml / thoughtHtml: the search trail and the thinking block, escaped", async () => {
  const { trailHtml, thoughtHtml } = await import("../../static/js/stream.js");
  assert.equal(trailHtml([]), "");
  const h = trailHtml([{ kind: "search", source: "PubMed", query: "apixaban <renal>", found: 5, url: "https://pubmed.ncbi.nlm.nih.gov/?term=a" },
    { kind: "search", source: "rivm.nl", query: "griep", found: 0, url: null, error: "search answered 500" },
    { kind: "read", title: "Griepprik", url: "https://www.rivm.nl/griepprik" }], true);
  assert.match(h, /^<details class="trail" open><summary>Searched 2 times · read 1 page<\/summary><ol>/);
  assert.match(h, /Searched PubMed for <b>"apixaban &lt;renal&gt;"<\/b> · <a href="https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/\?term=a" target="_blank" rel="noopener noreferrer">5 found ↗<\/a>/);
  assert.match(h, /<span class="err">search answered 500<\/span>/);
  assert.match(h, /<li class="step read">Read <a href="https:\/\/www\.rivm\.nl\/griepprik"[^>]*>Griepprik ↗<\/a><\/li>/);
  assert.equal(thoughtHtml("", 3), "");
  assert.equal(thoughtHtml("a < b", 2.4), '<details class="thought"><summary>Thought for 2 s</summary><div class="thought-text">a &lt; b</div></details>');
});
