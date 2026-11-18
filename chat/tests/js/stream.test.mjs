// Judge for chat/static/js/stream.js (tasks S16–S18). Protected. Run: node --test chat/tests/js/
import test from "node:test";
import assert from "node:assert/strict";
import { splitEvents, readEvents, Typewriter, groupChats, metaText } from "../../static/js/stream.js";

test("S16 splitEvents", () => {
  const r = splitEvents('event: delta\ndata: {"text":"Hi"}\n\nevent: phase\ndata: {"phase":"writing"}\n\nevent: do');
  assert.deepEqual(r.events, [{ event: "delta", data: { text: "Hi" } }, { event: "phase", data: { phase: "writing" } }]);
  assert.equal(r.rest, "event: do");
  assert.deepEqual(splitEvents(""), { events: [], rest: "" });
});

test("S16 readEvents", async () => {
  const parts = ['event: delta\ndata: {"te', 'xt":"a"}\n\nevent: done\ndata: {"tokens":1}\n\n'];
  const enc = new TextEncoder();
  const body = new ReadableStream({ start(c) { parts.forEach(p => c.enqueue(enc.encode(p))); c.close(); } });
  const got = [];
  await readEvents({ body }, (e, d) => got.push([e, d]));
  assert.deepEqual(got, [["delta", { text: "a" }], ["done", { tokens: 1 }]]);
});

test("S17 Typewriter paces and finishes", async () => {
  const seen = [];
  const tw = new Typewriter((all, piece) => seen.push([all, piece]), 5);
  tw.push("Hello wor");
  tw.push("ld, how are you?");
  await tw.finish();
  assert.equal(seen.at(-1)[0], "Hello world, how are you?");
  assert.ok(seen.length >= 4, "released in several steps");
  assert.ok(seen.every(([, p]) => p.length > 0));
});

test("S17 Typewriter catches up on long queues", async () => {
  const seen = [];
  const tw = new Typewriter(all => seen.push(all), 5);
  tw.push(Array.from({ length: 90 }, (_, i) => "w" + i).join(" "));
  await tw.finish();
  assert.ok(seen.length < 90, "more than one piece per step when far behind");
  assert.ok(seen.at(-1).endsWith("w89"));
});

test("S18 groupChats", () => {
  const now = new Date(2026, 10, 18, 15, 0);
  const iso = (d, h = 10) => new Date(2026, 10, d, h, 0).toISOString();
  const g = groupChats([{ id: 1, updated: iso(18) }, { id: 2, updated: iso(17) }, { id: 3, updated: iso(14) },
                        { id: 4, updated: iso(1) }], now);
  assert.deepEqual(g.map(x => [x.label, x.chats.map(c => c.id)]),
    [["Today", [1]], ["Yesterday", [2]], ["Last 7 days", [3]], ["Older", [4]]]);
  assert.deepEqual(groupChats([], now), []);
});

test("S18 metaText", () => {
  assert.equal(metaText(412, 41), "Coder · 412 tokens · 41 tok/s");
  assert.equal(metaText(3, null), "Coder · 3 tokens");
});
