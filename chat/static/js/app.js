// The chat page (chat/coder/templates/chat.html). ES module. Hooks: chat/SPEC.md §9 and §11.
// Each function below is one task. Fill in only the one your task names; keep the others.
// Never set inline styles except the CSS variables named in the SPEC (style.setProperty("--x", v)).
import { readEvents, Typewriter, renderFade, placeTail, groupChats, metaText, Follow, codeBlocks, codeActions, sourcesHtml, citeLinks, sourceActions,
  orbHtml, startOrb, flyFrom, trailHtml, thoughtHtml, markVerify, animateTrail } from "./stream.js";

const $ = (sel) => document.querySelector(sel);
const md = window.markdownit({ html: false, linkify: true, breaks: false });
// links in answers open in a new tab
const defaultLink = md.renderer.rules.link_open || ((t, i, o, e, s) => s.renderToken(t, i, o));
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noopener noreferrer");
  return defaultLink(tokens, idx, options, env, self);
};
codeBlocks(md);                       // coloured code with a file name, Copy and Download (stream.js)

const state = { chats: [], folders: [], chatId: null, profile: "general", files: [], busy: false, follow: null };
const ORB = '<span class="orb" data-phase="idle"><i></i><i></i><i></i><i></i></span>';
const MARBLE = '<span class="orb marble" data-phase="idle"><i></i><i></i><i></i><i></i></span>';
const TINY = '<span class="orb tiny" data-phase="idle"><i></i><i></i><i></i><i></i></span>';
const EXAMPLES = {
  general: ["Explain a bug in my code", "Plan my week", "Summarise a file"],
  evidence: ["Helpt vitamine D tegen winterdepressie?", "Is ibuprofen veilig tijdens de zwangerschap?",
             "Wat zegt het RIVM over griepprik bij 60-plussers?"],
};

/** fetch() for /api with the CSRF header; on 401 go to /login. Returns the Response. (done) */
export async function api(path, opts = {}) {
  const headers = { "X-Coder": "1", ...(opts.json !== undefined ? { "Content-Type": "application/json" } : {}), ...(opts.headers || {}) };
  const res = await fetch(path, { method: opts.method || "GET", headers, signal: opts.signal,
    body: opts.json !== undefined ? JSON.stringify(opts.json) : opts.body });
  if (res.status === 401) { location.href = "/login"; throw new Error("signed out"); }
  return res;
}

/** Escape text for innerHTML. (done) */
export function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/**
 * TODO (task R10). One sidebar item: <div class="conv[ on]" data-id="ID" draggable="true"><span>TITLE</span>
 * [<em class="tag">EH</em> when profile is "evidence"]<button class="more" data-chat-menu="ID" title="More"
 * aria-label="More">⋯</button></div>  ("on" when c.id === state.chatId; TITLE escaped with esc()).
 */
function convHtml(c) {
  const on = c.id === state.chatId ? " on" : "";
  let tag = "";
  if (c.profile === "evidence") {
    tag = '<em class="tag">EH</em>';
  }
  return `<div class="conv${on}" data-id="${c.id}" draggable="true"><span>${esc(c.title)}</span>${tag}<button class="more" data-chat-menu="${c.id}" title="More" aria-label="More">⋯</button></div>`;
}

/**
 * TODO (task R10). Load GET /api/chats into state.chats and GET /api/folders into state.folders (both with
 * api(), in parallel), then draw #convs (SPEC §11.5). Filter the chats by #search (case-insensitive substring
 * of the title) first. Then, in this order:
 *   per folder: <details class="folder" open data-folder="ID"><summary><span>NAME</span><small>COUNT</small>
 *     <button class="more" data-folder-menu="ID" title="More" aria-label="More">⋯</button></summary>
 *     + convHtml(c) for each shown chat with c.folder_id === folder id </details>   (COUNT = that number)
 *   then for the shown chats with folder_id == null: per group of groupChats(those): <h4>LABEL</h4> + convHtml each.
 */
async function loadChats() {
  const [chatsRes, foldersRes] = await Promise.all([
    api("/api/chats"),
    api("/api/folders"),
  ]);
  state.chats = await chatsRes.json();
  state.folders = await foldersRes.json();

  const search = ($("#search")?.value || "").toLowerCase();
  const shown = state.chats.filter((c) => !search || c.title.toLowerCase().includes(search));

  const convs = $("#convs");
  let html = "";

  // Folders first
  for (const folder of state.folders) {
    const folderChats = shown.filter((c) => c.folder_id === folder.id);
    const count = folderChats.length;
    html += `<details class="folder" open data-folder="${folder.id}"><summary><span>${esc(folder.name)}</span><small>${count}</small><button class="more" data-folder-menu="${folder.id}" title="More" aria-label="More">⋯</button></summary>`;
    for (const c of folderChats) {
      html += convHtml(c);
    }
    html += "</details>";
  }

  // Then date groups for chats without a folder
  const unfiled = shown.filter((c) => c.folder_id == null);
  const groups = groupChats(unfiled);
  for (const g of groups) {
    html += `<h4>${g.label}</h4>`;
    for (const c of g.chats) {
      html += convHtml(c);
    }
  }

  convs.innerHTML = html;
}

const PROFILE_NAMES = { general: "General", evidence: "Clear Source" };

/** Show which profile the chat is in: the profile button, html[data-profile] (strip, colours) and the placeholder. */
function setProfile(p) {
  state.profile = p;
  $("#profile-name").textContent = PROFILE_NAMES[p] || p;
  $("#profile").dataset.profile = p;
  document.documentElement.dataset.profile = p;
  $("#input").placeholder = p === "evidence" ? "Ask Clear Source…" : "Message Dewdrop…";
}

async function openChat(id) {
  const res = await api(`/api/chats/${encodeURIComponent(id)}`);
  const chat = await res.json();
  state.chatId = chat.id;
  setProfile(chat.profile);
  $("#chat-title").textContent = chat.title;
  const $messages = $("#messages");
  $messages.innerHTML = "";
  if (chat.messages.length === 0) drawWelcome();
  else chat.messages.forEach((m) => drawMessage(m));
  drawContext(chat.context);
  state.follow?.bottom();
  await loadChats();
}

let welcomeOrb = null;

function drawWelcome() {
  const $messages = $("#messages");
  const username = document.body.dataset.username;
  const chips = EXAMPLES[state.profile].map((ex) => `<button data-example="${esc(ex)}">${esc(ex)}</button>`).join("");
  $messages.innerHTML =
    `<div class="welcome">${orbHtml("marble", "welcome")}<h2>Hi ${esc(username)}, what's up?</h2><div class="chips">${chips}</div></div>`;
  welcomeOrb?.stop();
  welcomeOrb = startOrb($messages.querySelector(".welcome .orb"), { range: 30, min: 3500, max: 6000 });
  $messages.querySelectorAll("button[data-example]").forEach((btn) => {
    btn.addEventListener("click", () => { $("#input").value = btn.dataset.example; send(); });
  });
}

/**
 * TODO (task R11). Fill the stats line `meta` of a finished answer:
 *   meta.innerHTML = TINY + `<span>${metaText(tokens, tps)}</span><button data-copy>Copy</button><button data-retry>Retry</button>`
 *   Copy -> navigator.clipboard.writeText(content).
 *   Retry -> if state.busy do nothing; else put retryText() in #input and send().
 *   if stopped: insert <div class="note stopped">Stopped</div> right after meta (meta.insertAdjacentHTML("afterend", ...)).
 */
function doneMeta(meta, content, tokens, tps, stopped, retryText, msgId) {
  meta.innerHTML = TINY + `<span>${metaText(tokens, tps)}</span><button data-copy>Copy</button><button data-retry>Retry</button>`;
  dlMenu(meta, msgId);
  meta.querySelector("[data-copy]").addEventListener("click", () => { navigator.clipboard.writeText(content); });
  meta.querySelector("[data-retry]").addEventListener("click", () => { if (state.busy) return; $("#input").value = retryText(); send(); });
  if (stopped) {
    meta.insertAdjacentHTML("afterend", '<div class="note stopped">Stopped</div>');
  }
}

/*
 * TODO (task B15): sources under answers (SPEC §12.4). Import sourcesHtml and citeLinks from ./stream.js.
 *   drawMessage, assistant: put sourcesHtml(m.sources) between the .bub and the .meta2 in its innerHTML, and after
 *     appending call citeLinks(its .bub, m.sources).
 *   send, on done: if data.sources is non-empty: bub.innerHTML = md.render(full); citeLinks(bub, data.sources);
 *     bub.insertAdjacentHTML("afterend", sourcesHtml(data.sources))  (before the stats line is drawn).
 */
/*
 * Notes (task E16): thinking, the search trail and the checker's marks (SPEC §13.3). Import trailHtml, thoughtHtml and
 * markVerify from ./stream.js. Judge: make chat-browser-test ("round 4: Clear Source…").
 *   drawMessage, assistant: put <div class="pre">${thoughtHtml(m.thinking, m.thought_s)}${trailHtml(m.trail)}</div> right
 *     before the .bub; after citeLinks: markVerify(the .bub, m.verify).
 *   send: the new div.ai gets an empty <div class="pre"></div> before its .bub. Keep thinking = "", th0 = null,
 *     thoughtSecs = null, report = null, steps = [] and drawPre(live) = pre.innerHTML = thoughtHtml(thinking, thoughtSecs)
 *     + trailHtml(steps, live). Events:
 *       thinking -> th0 = th0 ?? performance.now(); thinking += data.text; drawPre(true)
 *       trail    -> steps.push(data.step); drawPre(true)
 *       verify   -> report = data.report
 *       delta    -> the first time after thinking: thoughtSecs = (performance.now() - th0) / 1000; drawPre(true)
 *     done (after the sources and doneMeta): drawPre(false); markVerify(bub, report || data.verify)
 */
/**
 * TODO (task D11). A file size for people (SPEC §16.6): n < 1024 -> `${n} B`; n < 1024*1024 -> `${Math.round(n / 1024)} KB`;
 * else `${(n / 1024 / 1024).toFixed(1)} MB`.
 */
function fmtSize(n) { return ""; }

/**
 * TODO (task D11). The download cards of files the model made (SPEC §16.6). Empty or no list -> "". Else
 *   '<div class="files-out">' + per file f: ext = the part of f.name after its last "." (or f.kind), max 4 characters;
 *   `<a class="file-card" data-kind="KIND" href="/api/files/ID" download><span class="fi">EXT</span><span><b>NAME</b>` +
 *   `<small>SIZE</small></span></a>` (KIND, EXT, NAME escaped with esc(); ID = Number(f.id); SIZE = fmtSize(f.size)) + "</div>"
 */
function filesOutHtml(list) { return ""; }

/**
 * TODO (task D11). Add one made file's card to an answer while it streams (SPEC §16.6): box = the .files-out inside
 *   bub.parentElement; when there is none, insert '<div class="files-out"></div>' right after bub (afterend) and use it;
 *   then add the card (the <a class="file-card"> of filesOutHtml([f]), without the wrapping div) at its end.
 */
function addFileCard(bub, f) {}

/**
 * TODO (task D11). The "Download" button and its menu in a finished answer's stats line `meta` (SPEC §16.5, §16.6).
 *   No msgId -> nothing. Else add at the end of meta: <span class="dl-wrap"><button class="dl" type="button">Download</button>
 *   <span class="dl-menu" hidden> + four links <a href="/api/messages/ID/export?format=F" download>LABEL</a> for
 *   md Markdown, docx Word, pdf PDF, html HTML + </span></span>.
 *   Clicking .dl toggles the menu's hidden (stopPropagation); any other click on the document hides it.
 */
function dlMenu(meta, msgId) {}

/** The user text the page shows: the content without the file blocks the server added (SPEC §11.1). (done) */
function typedText(content) { return content.split("\n\nFile ")[0]; }

/**
 * TODO (task R11). Draw one stored message at the end of #messages:
 *   summary:   <div class="note summary">Earlier messages summarised</div>
 *   user:      <div class="u">CHIPS<div class="msg user">TEXT</div></div>, CHIPS = <span class="file">NAME</span> per
 *              m.files, TEXT = typedText(m.content) when it has files, else m.content (escaped)
 *   assistant: a div.ai with innerHTML = ORB + `<div class="content"><div class="bub">${md.render(m.content)}</div>
 *              <div class="meta2"></div></div>`, appended; then
 *              doneMeta(its .meta2, m.content, m.tokens, m.tps, m.stopped, () => the text of the last
 *              ".u .msg.user" in #messages at this moment, or "")
 */
function drawMessage(m) {
  if (m.role === "summary") {
    $("#messages").insertAdjacentHTML("beforeend", '<div class="note summary">Earlier messages summarised</div>');
    return;
  }
  if (m.role === "user") {
    let chips = "";
    if (m.files) {
      chips = m.files.map((f) => `<span class="file">${esc(f.name)}</span>`).join("");
    }
    const text = esc(m.files && m.files.length ? typedText(m.content) : m.content);   // always escaped
    $("#messages").insertAdjacentHTML("beforeend", `<div class="u">${chips}<div class="msg user">${text}</div></div>`);
    return;
  }
  // assistant
  const div = document.createElement("div");
  div.className = "ai";
  div.innerHTML = ORB + `<div class="content"><div class="pre">${thoughtHtml(m.thinking, m.thought_s)}${trailHtml(m.trail)}</div><div class="bub">${md.render(m.content)}</div>${filesOutHtml(m.files)}${sourcesHtml(m.sources)}<div class="meta2"></div></div>`;
  $("#messages").appendChild(div);
  citeLinks(div.querySelector(".bub"), m.sources);
  markVerify(div.querySelector(".bub"), m.verify);
  const meta2 = div.querySelector(".meta2");
  doneMeta(meta2, m.content, m.tokens, m.tps, m.stopped, () => {
    const lastU = $("#messages").querySelectorAll(".u .msg.user").item(-1);
    return lastU ? lastU.textContent : "";
  }, m.id);
}

/**
  * TODO (task R12). Mark the page busy or not: state.busy = on; #send gets class "stop" while on
  * (classList.toggle("stop", on)), its title and aria-label become "Stop" while on, else "Send".
  */
function setBusy(on) {
  state.busy = on;
  const sendBtn = $("#send");
  sendBtn.classList.toggle("stop", on);
  sendBtn.title = on ? "Stop" : "Send";
  sendBtn.setAttribute("aria-label", on ? "Stop" : "Send");
}

/** TODO (task R12). If state.busy and there is an open chat: POST /api/chats/ID/stop with api() (SPEC §11.2). */
async function stopAnswer() {
  if (!state.busy || !state.chatId) return;
  setBusy(false);
  await api(`/api/chats/${encodeURIComponent(state.chatId)}/stop`, { method: "POST" });
}

/**
  * TODO (task R12). Send #input's text (and state.files) and write the answer as it streams in (SPEC §5, §11).
  *   0. text = #input value trimmed; nothing to send when !text && !state.files.length, or when state.busy.
  *   1. setBusy(true); everything below in try { ... } finally { setBusy(false) }.
  *   2. No open chat -> POST /api/chats {profile: state.profile}, state.chatId = its id.
  *      mode = the checked input[name=mode] value, or "quick".
  *   3. Remove .welcome; draw <div class="u">CHIPS<div class="msg user">TEXT</div></div> (escaped); then a div.ai with
  *      innerHTML = ORB + `<div class="content"><div class="bub"></div><div class="meta2"><span class="phase-label"></span></div></div>`.
  *      Keep bub, meta (.meta2), orb (.orb) and tiny = an element made from TINY (not in the page yet).
  *      Clear #input, #file-chips and state.files. Then state.follow.bottom()  (the page goes down to your message).
  *   4. full = ""; writeStart = performance.now();
  *      const typewriter = new Typewriter((shown, pieces) => {
  *        renderFade(bub, md.render(shown), pieces); placeTail(bub, tiny);
  *        fill = (0.82 * (1 - Math.exp(-((performance.now() - writeStart) / 1000) / 2.6))).toFixed(3);
  *        orb.style.setProperty("--fill", fill); tiny.dataset.phase = "writing"; tiny.style.setProperty("--fill", fill);
  *        meta.innerHTML = `<span class="live">${Math.round(shown.length / 4)} tokens</span>`;
  *        state.follow.stick();
  *      });
  *   5. res = POST /api/chats/ID/messages {content: text, mode, files: ids}. Not res.ok -> orb idle, meta empty,
  *      append <div class="note error">(the JSON error or "Something went wrong.")</div> to .content, return.
  *   6. await readEvents(res, (event, data) => ...):
  *        phase -> orb.dataset.phase = data.phase; meta.innerHTML = <span class="phase-label">LABEL</span>;
  *                 for "writing": writeStart = performance.now()
  *        delta -> full += data.text; typewriter.push(data.text)
  *        done / error -> remember it (finished = {event, data}); handle it after reading ends
  *   7. await typewriter.finish(); tiny.remove(); orb.dataset.phase = "idle".
  *      done: if !full && data.stopped: meta.innerHTML = "" else doneMeta(meta, full, data.tokens, data.tps, false, () => text);
  *            if data.stopped: <div class="note stopped">Stopped</div> after meta;
  *            if data.title: #chat-title text = data.title; drawContext(data.context); await loadChats()
  *      error or nothing: meta.innerHTML = ""; <div class="note error">(data.error or "The connection was lost.")</div>
  *      Last: state.follow.stick().
  */
async function send() {
  // 0. text = #input value trimmed; nothing to send when !text && !state.files.length, or when state.busy.
  const text = $("#input").value.trim();
  if (!text && !state.files.length) return;
  if (state.busy) return;

  // 1. setBusy(true); everything below in try { ... } finally { setBusy(false) }.
  setBusy(true);
  try {
    // 2. No open chat -> POST /api/chats {profile: state.profile}, state.chatId = its id.
    let mode = document.querySelector('input[name="mode"]:checked')?.value || "quick";
    if (!state.chatId) {
      const res = await api("/api/chats", { method: "POST", json: { profile: state.profile } });
      state.chatId = (await res.json()).id;
    }

    // 3. Remove .welcome; draw user message; then a div.ai with ORB, bub, meta, orb, tiny.
    const welcome = $("#messages").querySelector(".welcome");
    if (welcome) welcome.remove();

    // Draw user message
    let userChips = "";
    if (state.files.length) {
      userChips = state.files.map((f) => `<span class="file">${esc(f.name)}</span>`).join("");
    }
    const userText = state.files.length ? esc(typedText(text)) : esc(text);
    $("#messages").insertAdjacentHTML("beforeend", `<div class="u">${userChips}<div class="msg user">${userText}</div></div>`);
    flyFrom($("#messages").lastElementChild, $("#send"));

    // Draw assistant message placeholder
    const aiDiv = document.createElement("div");
    aiDiv.className = "ai";
    aiDiv.innerHTML = orbHtml() + `<div class="content"><div class="pre"></div><div class="bub"></div><div class="meta2"><span class="phase-label"></span></div></div>`;
    $("#messages").appendChild(aiDiv);

    const bub = aiDiv.querySelector(".bub");
    const meta = aiDiv.querySelector(".meta2");
    const orb = aiDiv.querySelector(".orb");
    const liveOrb = startOrb(orb);
    const tiny = document.createElement("span");
    tiny.innerHTML = orbHtml("tiny");
    const tinyEl = tiny.firstElementChild;

    // E16: thinking, trail, and verify tracking
    const pre = aiDiv.querySelector(".pre");
    let thinking = "", th0 = null, thoughtSecs = null, report = null, steps = [];
    let trailAnim = { stop() {} };
    const drawPre = (live) => {                     // §15.6: the current stage's drop moves while it researches
      trailAnim.stop();
      pre.innerHTML = thoughtHtml(thinking, thoughtSecs) + trailHtml(steps, live);
      trailAnim = live ? animateTrail(pre) : { stop() {} };
    };

    // Clear #input, #file-chips and state.files
    $("#input").value = "";
    $("#file-chips").innerHTML = "";
    state.files = [];

    state.follow.bottom();

    // 4. full = ""; writeStart = performance.now(); Typewriter
    let full = "";
    let writeStart = performance.now();
    const typewriter = new Typewriter((shown, pieces) => {
      renderFade(bub, md.render(shown), pieces);
      placeTail(bub, tinyEl);
      const fill = (0.82 * (1 - Math.exp(-((performance.now() - writeStart) / 1000) / 2.6))).toFixed(3);
      orb.style.setProperty("--fill", fill);
      tinyEl.dataset.phase = "writing";
      tinyEl.style.setProperty("--fill", fill);
      meta.innerHTML = `<span class="live">${Math.round(shown.length / 4)} tokens</span>`;
      state.follow.stick();
    });

    // 5. res = POST /api/chats/ID/messages {content: text, mode, files: ids}
    const res = await api(`/api/chats/${encodeURIComponent(state.chatId)}/messages`, {
      method: "POST",
      json: { content: text, mode, files: state.files.map((f) => f.id) },
    });
    if (!res.ok) {
      orb.dataset.phase = "idle";
      liveOrb.stop();
      meta.innerHTML = "";
      const errObj = await res.json().catch(() => null);
      const errMsg = errObj?.error || "Something went wrong.";
      bub.insertAdjacentHTML("beforeend", `<div class="note error">${esc(errMsg)}</div>`);
      return;
    }

    // 6. await readEvents(res, (event, data) => ...)
    let finished = null;
    await readEvents(res, (event, data) => {
      if (event === "phase") {
        orb.dataset.phase = data.phase;
        meta.innerHTML = `<span class="phase-label">${data.label || data.phase}</span>`;
        if (data.phase === "writing") {
          writeStart = performance.now();
        }
      } else if (event === "thinking") {
        th0 = th0 ?? performance.now();
        thinking += data.text;
        drawPre(true);
      } else if (event === "trail") {
        steps.push(data.step);
        drawPre(true);
      } else if (event === "verify") {
        report = data.report;
      } else if (event === "file") {
        addFileCard(bub, data);
      } else if (event === "delta") {
        full += data.text;
        typewriter.push(data.text);
        if (th0 != null && thoughtSecs == null) {
          thoughtSecs = (performance.now() - th0) / 1000;
          drawPre(true);
        }
      } else if (event === "done" || event === "error") {
        finished = { event, data };
      }
    });

    // 7. await typewriter.finish(); tiny.remove(); orb.dataset.phase = "idle".
    await typewriter.finish();
    tinyEl.remove();
    orb.dataset.phase = "idle";
    liveOrb.stop();

    if (finished && finished.event === "done") {
      const data = finished.data;
      if (!full && data.stopped) {
        meta.innerHTML = "";
      } else {
        if (data.sources && data.sources.length) {
          bub.innerHTML = md.render(full);
          citeLinks(bub, data.sources);
          bub.insertAdjacentHTML("afterend", sourcesHtml(data.sources));
        }
        doneMeta(meta, full, data.tokens, data.tps, false, () => text, data.message_id);
      }
      drawPre(false);
      if (report || data.verify) {
        markVerify(bub, report || data.verify);
      }
      if (data.stopped) {
        meta.insertAdjacentHTML("afterend", '<div class="note stopped">Stopped</div>');
      }
      if (data.title) {
        $("#chat-title").textContent = data.title;
        drawContext(data.context);
        await loadChats();
      }
    } else {
      meta.innerHTML = "";
      const errMsg = finished?.data?.error || "The connection was lost.";
      bub.insertAdjacentHTML("beforeend", `<div class="note error">${esc(errMsg)}</div>`);
    }

    state.follow.stick();
  } finally {
    setBusy(false);
  }
}

function fmt(n) {
  if (n < 1000) return String(n);
  const s = (n / 1000).toFixed(1);
  return s.endsWith(".0") ? s.slice(0, -2) + "k" : s + "k";
}

function drawContext(ctx) {
  const { used, max, compress_at } = ctx;
  const barI = document.querySelector(".ctx .bar > i");
  const line = document.querySelector(".ctx .line");
  if (barI) barI.style.setProperty("--used", (used / max).toFixed(4));
  if (line) line.style.setProperty("--at", (compress_at / max).toFixed(4));
  const ctxText = $("#ctx-text");
  if (ctxText) ctxText.textContent = `Context ${fmt(used)} / ${fmt(max)}`;
  const compressBtn = $("#compress");
  if (compressBtn) {
    compressBtn.onclick = async () => {
      const id = state.chatId;
      if (!id) return;
      await api(`/api/chats/${id}/compress`, { method: "POST" });
      openChat(id);
    };
  }
}

/**
 * TODO (task D10). Upload one file and show it as a chip until sent (SPEC §16.6).
 *   res = await api("/api/files", {method: "POST", body: a FormData with "file" = file}); f = await res.json() (error -> {})
 *   not res.ok -> add `<span class="file err">NAME: ERROR</span>` to #file-chips (NAME = file.name, ERROR = f.error or
 *     "could not upload", both escaped) and remove that chip after 6000 ms; stop.
 *   else state.files.push(f); tokens = f.tokens >= 1000 ? `${Math.round(f.tokens / 1000)}k tokens` : `${f.tokens} tokens`;
 *     add `<span class="file" data-kind="KIND">NAME <small>TOKENS</small><button class="x" data-remove="ID">×</button></span>`
 *     to #file-chips (KIND, NAME escaped).
 */
async function uploadFile(file) {
  let f;
  let res;
  try {
    const fd = new FormData();
    fd.set("file", file);
    res = await api("/api/files", { method: "POST", body: fd });
    f = await res.json();
  } catch { return; }
  if (!res.ok) {
    const err = esc(f?.error || "could not upload");
    const name = esc(file.name);
    const chip = document.createElement("span");
    chip.className = "file err";
    chip.textContent = name + ": " + err;
    $("#file-chips").appendChild(chip);
    setTimeout(() => chip.remove(), 6000);
    return;
  }
  state.files.push(f);
  const tokens = f.tokens >= 1000 ? Math.round(f.tokens / 1000) + "k tokens" : f.tokens + " tokens";
  const kind = esc(f.kind);
  const name = esc(file.name);
  const id = f.id;
  const chip = document.createElement("span");
  chip.className = "file";
  chip.dataset.kind = kind;
  chip.innerHTML = name + " <small>" + tokens + "</small><button class=\"x\" data-remove=\"" + id + "\">&times;</button>";
  $("#file-chips").appendChild(chip);
}

/** The file input #file-input, dropping files on #composer and pasting them (SPEC §16.6). */
function setupFiles() {
  $("#file-input").addEventListener("change", async () => {
    for (const file of [...$("#file-input").files]) await uploadFile(file);
    $("#file-input").value = "";
  });
  const composer = $("#composer");
  composer.addEventListener("dragover", (e) => { e.preventDefault(); composer.classList.add("drop"); });
  composer.addEventListener("dragleave", () => composer.classList.remove("drop"));
  composer.addEventListener("drop", async (e) => {
    e.preventDefault();
    composer.classList.remove("drop");
    for (const file of [...(e.dataTransfer?.files || [])]) await uploadFile(file);
  });
  $("#input").addEventListener("paste", async (e) => {
    const list = [...(e.clipboardData?.files || [])];
    if (!list.length) return;
    e.preventDefault();
    for (const file of list) await uploadFile(file);
  });
  $("#file-chips").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-remove]");
    if (!btn) return;
    const id = Number(btn.dataset.remove);
    state.files = state.files.filter((ff) => ff.id !== id);
    btn.closest("span.file").remove();
  });
}

// ------------------------------------------------------------------ folders and the ⋯ menu (SPEC §11.3, §11.5)
/** TODO (task R13). PATCH /api/chats/CHAT {folder_id} (null = no folder), then loadChats(). */
async function moveChat(chatId, folderId) {
  await api(`/api/chats/${encodeURIComponent(chatId)}`, { method: "PATCH", json: { folder_id: folderId } });
  await loadChats();
}

/**
 * TODO (task R13). name = prompt("Folder name:") trimmed; empty -> return null. POST /api/folders {name};
 * not ok -> alert("A folder name needs 1 to 60 characters."), return null; else return the new folder's id.
 */
async function newFolder() {
  const name = prompt("Folder name:");
  if (name === null) return null;
  const trimmed = name.trim();
  if (!trimmed) { alert("A folder name needs 1 to 60 characters."); return null; }
  const res = await api("/api/folders", { method: "POST", json: { name: trimmed } });
  if (!res.ok) { alert("A folder name needs 1 to 60 characters."); return null; }
  return (await res.json()).id;
}

/**
 * TODO (task R13). Show #cmenu with `html` under `button`: menu.innerHTML = html; r = button.getBoundingClientRect();
 * set the CSS variables --x = `${Math.min(r.left, innerWidth - 210)}px` and --y = `${Math.min(r.bottom + 4, innerHeight - 260)}px`
 * (style.setProperty); menu.hidden = false.  closeMenu(): #cmenu hidden = true.
 */
function openMenu(button, html) {
  const menu = $("#cmenu");
  menu.innerHTML = html;
  const r = button.getBoundingClientRect();
  menu.style.setProperty("--x", `${Math.min(r.left, innerWidth - 210)}px`);
  menu.style.setProperty("--y", `${Math.min(r.bottom + 4, innerHeight - 260)}px`);
  menu.hidden = false;
}

function closeMenu() { $("#cmenu").hidden = true; }

/**
 * TODO (task R13). The ⋯ menu of a chat (SPEC §11.5): openMenu(button,
 *   <button data-act="rename">Rename</button><small>Move to</small>
 *   + per folder <button data-act="move" data-folder="ID">NAME</button>
 *   + <button data-act="move" data-folder="">No folder</button> only when the chat is in a folder now
 *   + <button data-act="newfolder">New folder…</button><hr><button data-act="delete" class="danger">Delete</button>)
 * then #cmenu.dataset.chat = chatId and delete #cmenu.dataset.folder.
 */
function chatMenu(button, chatId) {
  const chat = state.chats.find((c) => c.id === chatId);
  const chatInFolder = chat ? chat.folder_id : null;
  let html = `<button data-act="rename">Rename</button><small>Move to</small>`;
  for (const f of state.folders) {
    html += `<button data-act="move" data-folder="${f.id}">${esc(f.name)}</button>`;
  }
  if (chatInFolder !== null) {
    html += `<button data-act="move" data-folder="">No folder</button>`;
  }
  html += `<button data-act="newfolder">New folder…</button><hr><button data-act="delete" class="danger">Delete</button>`;
  openMenu(button, html);
  $("#cmenu").dataset.chat = String(chatId);
  delete $("#cmenu").dataset.folder;
}

/**
 * TODO (task R13). The ⋯ menu of a folder: openMenu(button, <button data-act="rename-folder">Rename</button><hr>
 * <button data-act="delete-folder" class="danger">Delete folder</button>); #cmenu.dataset.folder = folderId; delete dataset.chat.
 */
function folderMenu(button, folderId) {
  openMenu(button, `<button data-act="rename-folder">Rename</button><hr><button data-act="delete-folder" class="danger">Delete folder</button>`);
  $("#cmenu").dataset.folder = String(folderId);
  delete $("#cmenu").dataset.chat;
}

/**
 * TODO (task R13). Run a menu choice. chatId = Number(#cmenu.dataset.chat), folderId = Number(#cmenu.dataset.folder); closeMenu(); then
 *   rename        -> title = prompt("Rename chat:", its title) trimmed; empty: stop; PATCH {title}; when it is the open chat
 *                    set #chat-title; loadChats()
 *   move          -> moveChat(chatId, btn.dataset.folder ? Number(btn.dataset.folder) : null)
 *   newfolder     -> id = await newFolder(); if id: moveChat(chatId, id)
 *   delete        -> confirm("Delete this chat? This cannot be undone.") or stop; DELETE /api/chats/ID; when it was the
 *                    open chat: state.chatId = null, #chat-title "New chat", drawWelcome(); loadChats()
 *   rename-folder -> name = prompt("Rename folder:", its name) trimmed; empty: stop; PATCH /api/folders/ID {name}; loadChats()
 *   delete-folder -> confirm("Delete this folder? Its chats are kept.") or stop; DELETE /api/folders/ID; loadChats()
 */
async function menuAction(act, btn) {
  const menu = $("#cmenu");
  const chatId = Number(menu.dataset.chat);
  const folderId = Number(menu.dataset.folder);
  closeMenu();

  if (act === "rename" && chatId) {
    const chat = state.chats.find((c) => c.id === chatId);
    const title = prompt("Rename chat:", chat?.title || "");
    if (!title) return;
    await api(`/api/chats/${encodeURIComponent(chatId)}`, { method: "PATCH", json: { title } });
    if (state.chatId === chatId) $("#chat-title").textContent = title;
    await loadChats();
  } else if (act === "move" && chatId) {
    const folderVal = btn.dataset.folder;
    await moveChat(chatId, folderVal ? Number(folderVal) : null);
  } else if (act === "newfolder" && chatId) {
    const id = await newFolder();
    if (id) await moveChat(chatId, id);
  } else if (act === "delete" && chatId) {
    if (!confirm("Delete this chat? This cannot be undone.")) return;
    const wasOpen = state.chatId === chatId;
    await api(`/api/chats/${encodeURIComponent(chatId)}`, { method: "DELETE" });
    if (wasOpen) {
      state.chatId = null;
      $("#chat-title").textContent = "New chat";
      drawWelcome();
    }
    await loadChats();
  } else if (act === "rename-folder" && folderId) {
    const folder = state.folders.find((f) => f.id === folderId);
    const name = prompt("Rename folder:", folder?.name || "");
    if (!name) return;
    await api(`/api/folders/${encodeURIComponent(folderId)}`, { method: "PATCH", json: { name } });
    await loadChats();
  } else if (act === "delete-folder" && folderId) {
    if (!confirm("Delete this folder? Its chats are kept.")) return;
    await api(`/api/folders/${encodeURIComponent(folderId)}`, { method: "DELETE" });
    await loadChats();
  }
}

/**
 * TODO (task R14). Wire the sidebar once (start() calls this):
 *   #convs click: a [data-chat-menu] -> preventDefault + stopPropagation, chatMenu(it, Number(its dataset.chatMenu));
 *                 a [data-folder-menu] -> the same with folderMenu; else a .conv -> openChat(Number(id)) unless state.busy
 *   drag and drop: dragstart on a .conv -> e.dataTransfer.setData("text/plain", its id);
 *                 dragover -> preventDefault, remove class "drop" everywhere, add it to the .folder under the pointer (or #convs);
 *                 drop -> preventDefault, remove "drop", moveChat(id, the .folder's Number(dataset.folder) or null)
 *   #cmenu click on a button[data-act] -> menuAction(its data-act, the button)
 *   a click anywhere outside #cmenu while it is open, or Escape -> closeMenu()
 *   #new-folder click -> if (await newFolder()) loadChats()
 */
function setupSidebar() {
  const convs = $("#convs");
  const cmenu = $("#cmenu");

  // Click handling on sidebar
  convs.addEventListener("click", async (e) => {
    const chatMenuBtn = e.target.closest("[data-chat-menu]");
    if (chatMenuBtn) {
      e.preventDefault();
      e.stopPropagation();
      chatMenu(chatMenuBtn, Number(chatMenuBtn.dataset.chatMenu));
      return;
    }
    const folderMenuBtn = e.target.closest("[data-folder-menu]");
    if (folderMenuBtn) {
      e.preventDefault();
      e.stopPropagation();
      folderMenu(folderMenuBtn, Number(folderMenuBtn.dataset.folderMenu));
      return;
    }
    const conv = e.target.closest(".conv");
    if (conv) {
      const id = Number(conv.dataset.id);
      if (!state.busy) openChat(id);
    }
  });

  // Drag and drop on sidebar
  convs.addEventListener("dragstart", (e) => {
    const conv = e.target.closest(".conv");
    if (conv) {
      e.dataTransfer.setData("text/plain", String(conv.dataset.id));
    }
  });

  convs.addEventListener("dragover", (e) => {
    e.preventDefault();
    document.querySelectorAll(".drop").forEach((el) => el.classList.remove("drop"));
    const folder = e.target.closest(".folder");
    if (folder) {
      folder.classList.add("drop");
    } else {
      convs.classList.add("drop");
    }
  });

  convs.addEventListener("drop", (e) => {
    e.preventDefault();
    document.querySelectorAll(".drop").forEach((el) => el.classList.remove("drop"));
    const chatId = Number(e.dataTransfer.getData("text/plain"));
    const folder = e.target.closest(".folder");
    const folderId = folder ? Number(folder.dataset.folder) : null;
    moveChat(chatId, folderId);
  });

  // Context menu click handling
  cmenu.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (btn) {
      menuAction(btn.dataset.act, btn);
    }
  });

  // Close menu when clicking outside
  document.addEventListener("click", (e) => {
    if (!cmenu.hidden && !cmenu.contains(e.target)) {
      closeMenu();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !cmenu.hidden) {
      closeMenu();
    }
  });

  // New folder button
  $("#new-folder").addEventListener("click", async () => {
    const id = await newFolder();
    if (id) await loadChats();
  });
}

/** Wire everything once at start. */
async function start() {
  state.follow = new Follow($("#thread"), (on) => { $("#jump").hidden = on; });
  $("#jump").addEventListener("click", () => state.follow.bottom(true));
  codeActions($("#messages"));
  sourceActions($("#messages"));
  $("#composer").addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.busy) stopAnswer(); else send();
  });
  $("#input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  $("#new-chat").addEventListener("click", () => {
    if (state.busy) return;
    state.chatId = null;
    $("#chat-title").textContent = "New chat";
    drawWelcome();
    loadChats();
  });
  $("#chat-title").addEventListener("dblclick", async () => {
    if (!state.chatId) return;
    const newTitle = (prompt("Rename chat:", $("#chat-title").textContent) || "").trim();
    if (!newTitle) return;
    await api(`/api/chats/${encodeURIComponent(state.chatId)}`, { method: "PATCH", json: { title: newTitle } });
    $("#chat-title").textContent = newTitle;
    await loadChats();
  });
  $("#search").addEventListener("input", () => loadChats());
  $("#profile").addEventListener("click", () => $("#pmenu").classList.toggle("hidden"));
  $("#pmenu").querySelectorAll(".opt").forEach((opt) => {
    opt.addEventListener("click", () => {
      setProfile(opt.dataset.profile);
      $("#pmenu").classList.add("hidden");
      state.chatId = null;
      $("#chat-title").textContent = "New chat";
      drawWelcome();
    });
  });
  document.querySelectorAll('input[name="theme"]').forEach((radio) => {
    if (radio.value === document.documentElement.dataset.themePref) radio.checked = true;
    radio.addEventListener("input", () => window.setTheme(radio.value));
  });
  let savedMode = null;
  try { savedMode = localStorage.getItem("coder-mode"); } catch { /* private mode */ }
  if (savedMode) {
    const el = document.querySelector(`input[name="mode"][value="${savedMode}"]`);
    if (el) el.checked = true;
  }
  document.querySelectorAll('input[name="mode"]').forEach((radio) => {
    radio.addEventListener("input", () => { try { localStorage.setItem("coder-mode", radio.value); } catch { /* ignore */ } });
  });
  $("#logout").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST" });
    location.href = "/login";
  });
  setupFiles();
  setupSidebar();
  setProfile(state.profile);
  await loadChats();
  if (state.chats.length > 0) await openChat(state.chats[0].id);
  else drawWelcome();
}

start();
