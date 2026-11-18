// The chat page (chat/coder/templates/chat.html). ES module. Hooks: chat/SPEC.md §9 and §11.
// Each function below is one task. Fill in only the one your task names; keep the others.
// Never set inline styles except the CSS variables named in the SPEC (style.setProperty("--x", v)).
import { readEvents, Typewriter, renderFade, placeTail, groupChats, metaText, Follow, codeBlocks, codeActions } from "./stream.js";

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
  return "";
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
}

async function openChat(id) {
  const res = await api(`/api/chats/${encodeURIComponent(id)}`);
  const chat = await res.json();
  state.chatId = chat.id;
  state.profile = chat.profile;
  $("#chat-title").textContent = chat.title;
  const $messages = $("#messages");
  $messages.innerHTML = "";
  if (chat.messages.length === 0) drawWelcome();
  else chat.messages.forEach((m) => drawMessage(m));
  drawContext(chat.context);
  state.follow?.bottom();
  await loadChats();
}

function drawWelcome() {
  const $messages = $("#messages");
  const username = document.body.dataset.username;
  const chips = EXAMPLES[state.profile].map((ex) => `<button data-example="${esc(ex)}">${esc(ex)}</button>`).join("");
  $messages.innerHTML =
    `<div class="welcome">${MARBLE}<h2>Hi ${esc(username)}, what's up?</h2><div class="chips">${chips}</div></div>`;
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
function doneMeta(meta, content, tokens, tps, stopped, retryText) {
}

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
}

/**
 * TODO (task R12). Mark the page busy or not: state.busy = on; #send gets class "stop" while on
 * (classList.toggle("stop", on)), its title and aria-label become "Stop" while on, else "Send".
 */
function setBusy(on) {
}

/** TODO (task R12). If state.busy and there is an open chat: POST /api/chats/ID/stop with api() (SPEC §11.2). */
async function stopAnswer() {
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

/** The file input #file-input: upload each file and show it as a chip until sent. */
function setupFiles() {
  $("#file-input").addEventListener("change", async () => {
    for (const file of $("#file-input").files) {
      const formData = new FormData();
      formData.append("file", file);
      const res = await api("/api/files", { method: "POST", body: formData });
      if (res.status === 413) { alert("File too big (max 200 KB)"); continue; }
      if (res.status === 415) { alert("Only text files"); continue; }
      const f = await res.json();
      state.files.push(f);
      $("#file-chips").insertAdjacentHTML("beforeend", `<span class="file">${esc(f.name)} <button data-remove="${f.id}">×</button></span>`);
    }
    $("#file-input").value = "";
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
}

/**
 * TODO (task R13). name = prompt("Folder name:") trimmed; empty -> return null. POST /api/folders {name};
 * not ok -> alert("A folder name needs 1 to 60 characters."), return null; else return the new folder's id.
 */
async function newFolder() {
}

/**
 * TODO (task R13). Show #cmenu with `html` under `button`: menu.innerHTML = html; r = button.getBoundingClientRect();
 * set the CSS variables --x = `${Math.min(r.left, innerWidth - 210)}px` and --y = `${Math.min(r.bottom + 4, innerHeight - 260)}px`
 * (style.setProperty); menu.hidden = false.  closeMenu(): #cmenu hidden = true.
 */
function openMenu(button, html) {
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
}

/**
 * TODO (task R13). The ⋯ menu of a folder: openMenu(button, <button data-act="rename-folder">Rename</button><hr>
 * <button data-act="delete-folder" class="danger">Delete folder</button>); #cmenu.dataset.folder = folderId; delete dataset.chat.
 */
function folderMenu(button, folderId) {
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
}

/** Wire everything once at start. */
async function start() {
  state.follow = new Follow($("#thread"), (on) => { $("#jump").hidden = on; });
  $("#jump").addEventListener("click", () => state.follow.bottom());
  codeActions($("#messages"));
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
      state.profile = opt.dataset.profile;
      $("#profile-name").textContent = opt.querySelector("b").textContent;
      $("#profile").dataset.profile = state.profile;
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
  await loadChats();
  if (state.chats.length > 0) await openChat(state.chats[0].id);
  else drawWelcome();
}

start();
