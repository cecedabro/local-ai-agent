// The chat page (chat/coder/templates/chat.html). ES module. Hooks: chat/SPEC.md §9.
// Each function below is one task (S19–S24). Fill in only the one your task names; keep the others.
// Never set inline styles except the CSS variables named in SPEC §9 (style.setProperty("--x", v)).
import { readEvents, Typewriter, renderFade, placeTail, groupChats, metaText } from "./stream.js";

const $ = (sel) => document.querySelector(sel);
const md = window.markdownit({ html: false, linkify: true, breaks: false });
// links in answers open in a new tab
const defaultLink = md.renderer.rules.link_open || ((t, i, o, e, s) => s.renderToken(t, i, o));
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noopener noreferrer");
  return defaultLink(tokens, idx, options, env, self);
};

const state = { chats: [], chatId: null, profile: "general", files: [], busy: false };
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
  const res = await fetch(path, { method: opts.method || "GET", headers,
    body: opts.json !== undefined ? JSON.stringify(opts.json) : opts.body });
  if (res.status === 401) { location.href = "/login"; throw new Error("signed out"); }
  return res;
}

/** Escape text for innerHTML. (done) */
export function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/**
 * TODO (task S19). Load GET /api/chats into state.chats and draw the sidebar #convs:
 * for each group of groupChats(state.chats): an <h4> with the label, then per chat
 * <div class="conv" data-id="ID"><span>TITLE (escaped)</span></div>, plus <em class="tag">EH</em>
 * inside it when chat.profile === "evidence", and class "on" when chat.id === state.chatId.
 * Filter by the #search input's value (case-insensitive substring of the title).
 */
async function loadChats() {
  const res = await api("/api/chats");
  state.chats = await res.json();
  const query = ($("#search").value || "").toLowerCase();
  const groups = groupChats(state.chats).map((g) => ({
    label: g.label,
    chats: g.chats.filter((c) => !query || c.title.toLowerCase().includes(query)),
  })).filter((g) => g.chats.length);
  const sidebar = $("#convs");
  sidebar.innerHTML = groups.map((g) =>
    `<h4>${esc(g.label)}</h4>` +
    g.chats.map((c) =>
      `<div class="conv${c.id === state.chatId ? " on" : ""}" data-id="${c.id}">` +
      `<span>${esc(c.title)}</span>` +
      (c.profile === "evidence" ? `<em class="tag">EH</em>` : "") +
      `</div>`
    ).join("")
  ).join(""));
}

/**
 * TODO (task S20). Open a chat: GET /api/chats/ID, set state.chatId and state.profile, set
 * #chat-title to its title, and draw every message into #messages with drawMessage(m).
 * With no messages draw the welcome screen: <div class="welcome">MARBLE<h2>Hi USERNAME, what's up?</h2>
 * <div class="chips"> one <button> per EXAMPLES[profile] item </div></div> (USERNAME from
 * document.body.dataset.username); clicking an example puts its text in #input and sends.
 * Then call drawContext(chat.context) and loadChats() (to update the "on" marker).
 */
async function openChat(id) {
  const res = await api(`/api/chats/${encodeURIComponent(id)}`);
  const chat = await res.json();
  state.chatId = chat.id;
  state.profile = chat.profile;
  $("#chat-title").textContent = chat.title;
  const $messages = $("#messages");
  $messages.innerHTML = "";
  if (chat.messages.length === 0) {
    drawWelcome();
  } else {
    chat.messages.forEach((m) => drawMessage(m));
  }
  drawContext(chat.context);
  await loadChats();
}

function drawWelcome() {
  const $messages = $("#messages");
  $messages.innerHTML = "";
  const username = document.body.dataset.username;
  const chips = EXAMPLES[state.profile].map((ex) =>
    `<button data-example="${esc(ex)}">${esc(ex)}</button>`
  ).join("");
  $messages.innerHTML =
    `<div class="welcome">${MARBLE}<h2>Hi ${username}, what's up?</h2><div class="chips">${chips}</div></div>`;
  $messages.querySelectorAll("button[data-example]").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("#input").value = btn.dataset.example;
      send();
    });
  });
}

/**
 * TODO (task S20). Return the element for one message and append it to #messages:
 *   user:      <div class="u"> file chips (<span class="file">NAME</span>) + <div class="msg user">TEXT</div></div>
 *              (TEXT = the content without the "\n\nFile NAME:\n```...```" blocks the server added;
 *               cut the content at the first "\n\nFile " when the message has files)
 *   assistant: <div class="ai">ORB<div class="content"><div class="bub">md.render(content)</div>
 *              <div class="meta2">TINY<span>metaText(tokens, tps)</span><button data-copy>Copy</button>
 *              <button data-retry>Retry</button></div></div></div>
 *              (the stats line is always there, under every answer)
 *   summary:   <div class="note summary">Earlier messages summarised</div>
 * The Copy button copies the raw content with navigator.clipboard.writeText.
 */
function drawMessage(m) {
  const $messages = $("#messages");
  if (m.role === "summary") {
    $messages.insertAdjacentHTML("beforeend", '<div class="note summary">Earlier messages summarised</div>');
    return;
  }
  if (m.role === "user") {
    let text = m.content;
    if (m.files && m.files.length) {
      text = text.split("\n\nFile ")[0];
    }
    let chips = "";
    if (m.files && m.files.length) {
      chips = m.files.map((f) => `<span class="file">${esc(f.name)}</span>`).join("");
    }
    $messages.insertAdjacentHTML("beforeend",
      `<div class="u">${chips}<div class="msg user">${esc(text)}</div></div>`
    );
    return;
  }
  const rendered = md.render(m.content);
  const stats = metaText(m.tokens, m.tps);
  const $msg = document.createElement("div");
  $msg.className = "ai";
  $msg.innerHTML =
    ORB +
    `<div class="content"><div class="bub">${rendered}</div>` +
    `<div class="meta2">${TINY}<span>${stats}</span>` +
    `<button data-copy>Copy</button><button data-retry>Retry</button></div></div>`;
  $msg.querySelector("button[data-copy]").addEventListener("click", () => {
    navigator.clipboard.writeText(m.content);
  });
  $messages.appendChild($msg);
}

/**
 * TODO (task S21). Send #input's text (and state.files ids) to POST /api/chats/ID/messages with
 * {content, mode, files}. mode = the checked input[name=mode] value. If there is no open chat,
 * first POST /api/chats {profile: state.profile} and use its id. Clear #input and the file chips.
 * Draw the user message, then <div class="ai">ORB<div class="content"><div class="bub"></div>
 * <div class="meta2"><span class="phase-label"></span></div></div></div>. Keep `orb` = that ORB element,
 * `bub`, `meta`, and a tiny drop element `tiny` (from TINY). Read the response with readEvents(res, onEvent):
 *   phase -> orb.dataset.phase = data.phase; the .phase-label text = data.label;
 *            for "writing" remember writeStart = performance.now()
 *   delta -> typewriter.push(data.text), where (create it once, before reading):
 *            const typewriter = new Typewriter((shown, pieces) => {
 *              renderFade(bub, md.render(shown), pieces);
 *              placeTail(bub, tiny);
 *              const t = (performance.now() - writeStart) / 1000;
 *              orb.style.setProperty("--fill", (0.82 * (1 - Math.exp(-t / 2.6))).toFixed(3));
 *              meta.innerHTML = `<span class="live">${Math.round(shown.length / 4)} tokens</span>`;
 *            });
 *   done  -> await typewriter.finish(); tiny.remove(); orb.dataset.phase = "idle" (the water drains);
 *            meta.innerHTML = TINY + `<span>${metaText(data.tokens, data.tps)}</span>` + the Copy and
 *            Retry buttons (as drawMessage); drawContext(data.context); loadChats()
 *   error -> orb.dataset.phase = "idle"; tiny.remove(); append <div class="note error">MESSAGE</div> to .content
 * Keep state.busy true while streaming (the send button is disabled meanwhile). Scroll #thread to
 * the bottom as text arrives, unless the user scrolled up.
 */
async function send() {
  const text = $("#input").value.trim();
  if (!text || state.busy) return;
  state.busy = true;

  let chatId = state.chatId;
  if (!chatId) {
    const res = await api("/api/chats", { method: "POST", json: { profile: state.profile } });
    chatId = (await res.json()).id;
    state.chatId = chatId;
  }

  const modeEl = document.querySelector('input[name="mode"]:checked');
  const mode = modeEl ? modeEl.value : "think";

  const $messages = $("#messages");
  const welcome = $messages.querySelector(".welcome");
  if (welcome) welcome.remove();

  // Draw user message
  const fileIds = state.files.map((f) => f.id);
  let textHtml = esc(text);
  if (fileIds.length) {
    textHtml = fileIds.map((id) => {
      const f = state.files.find((ff) => ff.id === id);
      return f ? `<span class="file">${esc(f.name)}</span>` : "";
    }).join("") + textHtml;
  }
  $messages.insertAdjacentHTML("beforeend", `<div class="u">${textHtml}</div>`);

  // Draw assistant placeholder
  const aiId = "ai-" + Date.now();
  $messages.insertAdjacentHTML("beforeend",
    `<div class="ai" id="${aiId}">${ORB}<div class="content"><div class="bub"></div><div class="meta2"><span class="phase-label"></span></div></div></div>`
  );
  const $ai = $(`#${aiId}`);
  const bub = $ai.querySelector(".bub");
  const meta = $ai.querySelector(".meta2");
  const orb = $ai.querySelector(".orb");
  const tiny = document.createElement("span");
  tiny.className = "tiny";
  tiny.textContent = " ";

  // Clear input and file chips
  $("#input").value = "";
  $("#file-chips").innerHTML = "";
  state.files = [];

  const writeStart = performance.now();

  const typewriter = new Typewriter((shown, pieces) => {
    renderFade(bub, md.render(shown), pieces);
    placeTail(bub, tiny);
    const t = (performance.now() - writeStart) / 1000;
    orb.style.setProperty("--fill", (0.82 * (1 - Math.exp(-t / 2.6))).toFixed(3));
    meta.innerHTML = `<span class="live">${Math.round(shown.length / 4)} tokens</span>`;
  });

  const streamRes = await api(`/api/chats/${encodeURIComponent(chatId)}/messages`, {
    method: "POST",
    json: { content: text, mode, files: fileIds }
  });

  await readEvents(streamRes, (event, data) => {
    if (event === "phase") {
      orb.dataset.phase = data.phase;
      meta.innerHTML = `<span class="phase-label">${esc(data.label)}</span>`;
      if (data.phase === "writing") writeStart = performance.now();
    } else if (event === "delta") {
      typewriter.push(data.text);
    } else if (event === "done") {
      typewriter.finish().then(() => {
        tiny.remove();
        orb.dataset.phase = "idle";
        meta.innerHTML = TINY + `<span>${metaText(data.tokens, data.tps)}</span>` +
          `<button data-copy>Copy</button><button data-retry>Retry</button>`;
        $ai.querySelector("button[data-copy]").addEventListener("click", () => {
          navigator.clipboard.writeText(data.content);
        });
        $ai.querySelector("button[data-retry]").addEventListener("click", () => {
          $("#input").value = text;
          send();
        });
        drawContext(data.context);
        loadChats().then(() => { state.busy = false; });
      });
    } else if (event === "error") {
      typewriter.finish().then(() => {
        tiny.remove();
        orb.dataset.phase = "idle";
        bub.insertAdjacentHTML("beforeend", `<div class="note error">${esc(data.message)}</div>`);
        state.busy = false;
      });
    }
  });

  // Scroll #thread to bottom
  const $thread = $("#thread");
  if ($thread) {
    const shouldScroll = $thread.scrollHeight - $thread.scrollTop - $thread.clientHeight < 200;
    if (shouldScroll) $thread.scrollTop = $thread.scrollHeight;
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
 * TODO (task S23). The file input #file-input: on change, POST each file as FormData field "file" to
 * /api/files (use api() with body: formData and no json), push {id, name, size} to state.files and
 * draw a chip <span class="file">NAME <button data-remove="ID">×</button></span> in #file-chips.
 * 413 -> alert "File too big (max 200 KB)", 415 -> alert "Only text files". The × removes it again.
 */
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
      $("#file-chips").insertAdjacentHTML("beforeend",
        `<span class="file">${esc(f.name)} <button data-remove="${f.id}">×</button></span>`
      );
    }
    $("#file-chips").onclick = (e) => {
      const btn = e.target.closest("button[data-remove]");
      if (!btn) return;
      const id = Number(btn.dataset.remove);
      state.files = state.files.filter((ff) => ff.id !== id);
      btn.closest("span.file").remove();
    };
    $("#file-input").value = "";
  });
}

/**
 * TODO (task S24). Wire everything once at start:
 *   #composer submit and Enter (without Shift) in #input -> send(); Shift+Enter = new line
 *   #new-chat -> state.chatId = null, clear #messages, draw the welcome screen (as openChat does)
 *   #convs click on .conv -> openChat(id); double-click #chat-title -> prompt for a new title,
 *     PATCH /api/chats/ID {title}, then loadChats(); #search input -> loadChats()
 *   #profile click -> toggle #pmenu's "hidden" class; .opt click -> state.profile = its
 *     data-profile, #profile-name text, start a new chat
 *   the theme radios input[name=theme]: checked = the current document.documentElement.dataset.themePref,
 *     change -> window.setTheme(value)
 *   the mode radios: remember the choice in localStorage "coder-mode" and restore it
 *   #logout -> POST /api/logout then location.href = "/login"
 *   then setupFiles(), await loadChats(), and openChat(state.chats[0].id) if there is one,
 *   else the welcome screen.
 */
async function start() {
  $("#composer").addEventListener("submit", (e) => {
    e.preventDefault();
    send();
  });
  $("#input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  $("#new-chat").addEventListener("click", () => {
    state.chatId = null;
    $("#messages").innerHTML = "";
    drawWelcome();
  });
  $("#convs").addEventListener("click", (e) => {
    const conv = e.target.closest(".conv");
    if (!conv) return;
    openChat(Number(conv.dataset.id));
  });
  $("#chat-title").addEventListener("dblclick", async () => {
    const newTitle = prompt("Rename chat:");
    if (!newTitle || !state.chatId) return;
    await api(`/api/chats/${encodeURIComponent(state.chatId)}`, { method: "PATCH", json: { title: newTitle } });
    await loadChats();
  });
  $("#search").addEventListener("input", () => loadChats());
  $("#profile").addEventListener("click", () => {
    $("#pmenu").classList.toggle("hidden");
  });
  $("#pmenu").querySelectorAll(".opt").forEach((opt) => {
    opt.addEventListener("click", () => {
      state.profile = opt.dataset.profile;
      $("#profile-name").textContent = opt.querySelector("b").textContent;
      $("#profile").dataset.profile = state.profile;
      state.chatId = null;
      $("#messages").innerHTML = "";
      drawWelcome();
    });
  });
  document.querySelectorAll('input[name="theme"]').forEach((radio) => {
    if (radio.value === document.documentElement.dataset.themePref) {
      radio.checked = true;
    }
    radio.addEventListener("input", () => window.setTheme(radio.value));
  });
  const savedMode = localStorage.getItem("coder-mode");
  if (savedMode) {
    const el = document.querySelector(`input[name="mode"][value="${savedMode}"]`);
    if (el) el.checked = true;
  }
  document.querySelectorAll('input[name="mode"]').forEach((radio) => {
    radio.addEventListener("input", () => {
      localStorage.setItem("coder-mode", radio.value);
      if (!state.chatId) drawWelcome();
    });
  });
  $("#logout").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST" });
    location.href = "/login";
  });
  setupFiles();
  await loadChats();
  if (state.chats.length > 0) {
    await openChat(state.chats[0].id);
  } else {
    drawWelcome();
  }
}

start();
