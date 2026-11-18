// Pure helpers for app.js: reading the answer stream and pacing the text (chat/SPEC.md §5, §9).
// Tests: chat/tests/js/stream.test.mjs (node --test). No DOM in this file.

/**
 * Split server-sent events out of a growing text buffer.
 * TODO (task S16). `buffer` is everything received so far that was not yet consumed.
 * Events are separated by a blank line ("\n\n"). Each event has lines "event: <name>" and
 * "data: <json>". Return { events: [{event, data}], rest } where `data` is JSON.parse'd and
 * `rest` is the unfinished tail after the last "\n\n" (keep it for the next call).
 * Example: splitEvents('event: delta\ndata: {"text":"Hi"}\n\nevent: do')
 *   -> { events: [{event: "delta", data: {text: "Hi"}}], rest: "event: do" }
 */
export function splitEvents(buffer) {
  throw new Error("TODO S16");
}

/**
 * Read a fetch() Response body and call onEvent(event, data) for each event.
 * TODO (task S16). const reader = response.body.getReader(); const dec = new TextDecoder();
 * let buf = ""; loop { const {done, value} = await reader.read(); if (done) break;
 * buf += dec.decode(value, {stream: true}); const r = splitEvents(buf); buf = r.rest;
 * r.events.forEach(e => onEvent(e.event, e.data)); }
 */
export async function readEvents(response, onEvent) {
  throw new Error("TODO S16");
}

/**
 * Releases text at a steady pace, however bursty the deltas arrive.
 * TODO (task S17).
 *   constructor(onText, stepMs = 25): onText(textSoFar, newPiece) is called on every step.
 *   push(text): split it into pieces with
 *     text.match(new RegExp("\\s*\\S+\\s*", "g")) || [text]   (a word plus its spaces)
 *     and append them to the queue.
 *   Each step (setInterval(stepMs)) takes ONE piece from the queue, or 3 pieces when the queue is
 *   longer than 40 (catching up), joins them into `released`, does shown += released and calls
 *   onText(shown, released) once.
 *   finish(): returns a Promise that resolves once the queue is empty (then clears the interval).
 *   The interval starts on the first push and stops when finish() resolves.
 */
export class Typewriter {
  constructor(onText, stepMs = 25) {
    throw new Error("TODO S17");
  }
}

/**
 * Group chats for the sidebar.
 * TODO (task S18). chats = [{id, title, profile, updated (ISO)}] newest first; now = Date.
 * Return [{label, chats}] in this order, leaving out empty groups:
 *   "Today" (same local date as now), "Yesterday", "Last 7 days" (2–7 days ago), "Older".
 */
export function groupChats(chats, now = new Date()) {
  throw new Error("TODO S18");
}

/** TODO (task S18). "Coder · 412 tokens · 41 tok/s" (leave out the tok/s part when tps is null). */
export function metaText(tokens, tps) {
  throw new Error("TODO S18");
}
