// Browser tests for Dewdrop: drive a real headless Chromium against the app (chat/tests/browser/serve.py).
// Protected (human-owned). Run: make chat-browser-test   (CHROME_BIN picks the browser)
//   smoke:   sign in with the password, send a message, wait for the finished answer, no console errors
//   round 2: phases, code blocks + download, scroll freeze + "Latest", stop, folders (chat/SPEC.md §11)
//   passkey: add a passkey on /account (Chromium's virtual authenticator), sign out, sign in with it
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const PORT = 8000 + Math.floor(Math.random() * 900);
const BASE = `http://localhost:${PORT}`;
const PY = process.env.PYTHON || join(HERE, "..", "..", ".venv", "bin", "python");
const CHROME = process.env.CHROME_BIN || ["/usr/bin/chromium", "/usr/bin/google-chrome", "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find(existsSync);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const failures = [];
const data = mkdtempSync(join(tmpdir(), "dewdrop-bt-"));
const profile = mkdtempSync(join(tmpdir(), "dewdrop-chrome-"));
const server = spawn(PY, [join(HERE, "serve.py"), String(PORT), data], { stdio: "inherit" });
let chrome;

async function waitFor(fn, ms, what) {
  const end = Date.now() + ms;
  for (;;) {
    try { const v = await fn(); if (v) return v; } catch { /* not yet */ }
    if (Date.now() > end) throw new Error("timed out waiting for " + what);
    await sleep(150);
  }
}

async function main() {
  if (!CHROME) throw new Error("no Chromium found; set CHROME_BIN");
  await waitFor(async () => (await fetch(BASE + "/healthz")).ok, 30000, "the app to start");
  const dport = 9400 + Math.floor(Math.random() * 500);
  chrome = spawn(CHROME, ["--headless=new", "--no-sandbox", "--disable-gpu", `--remote-debugging-port=${dport}`,
    `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });
  const target = await waitFor(async () => (await (await fetch(`http://127.0.0.1:${dport}/json`)).json())
    .find((t) => t.type === "page"), 20000, "Chromium");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0; const pend = {}; const errors = [];
  ws.onmessage = (m) => {
    const j = JSON.parse(m.data);
    if (j.id && pend[j.id]) { pend[j.id](j); delete pend[j.id]; }
    if (j.method === "Runtime.exceptionThrown") errors.push(j.params.exceptionDetails.exception?.description || j.params.exceptionDetails.text);
    if (j.method === "Runtime.consoleAPICalled" && j.params.type === "error") errors.push(j.params.args.map((a) => a.value ?? a.description).join(" "));
    if (j.method === "Page.javascriptDialogOpening") {         // prompt() gets "Work", confirm() is accepted
      ws.send(JSON.stringify({ id: ++id, method: "Page.handleJavaScriptDialog", params: { accept: true, promptText: "Work" } }));
    }
  };
  const cmd = (method, params = {}) => new Promise((r) => { pend[++id] = r; ws.send(JSON.stringify({ id, method, params })); })
    .then((j) => { if (j.error) throw new Error(method + ": " + j.error.message); return j.result; });
  const ev = async (expr) => (await cmd("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result.value;
  const go = async (path) => { await cmd("Page.navigate", { url: BASE + path }); await sleep(700); };
  await cmd("Runtime.enable"); await cmd("Page.enable"); await cmd("Network.enable");

  async function check(name, fn) {
    errors.length = 0;
    try { await fn(); if (errors.length) throw new Error("console errors: " + errors.join(" | ")); console.log("ok   " + name); }
    catch (e) { failures.push(name); console.log("FAIL " + name + ": " + e.message); }
  }

  await check("smoke: password sign-in, send a message, answer finishes", async () => {
    await go("/login");
    await ev(`(()=>{const f=document.getElementById('login-form');f.username.value='cedric';f.password.value='browser-test-password';f.requestSubmit()})()`);
    await waitFor(() => ev(`location.pathname==='/' && !!document.getElementById('composer')`), 10000, "the chat page");
    await sleep(800);
    await ev(`(()=>{document.getElementById('input').value='And for an autocomplete?';document.getElementById('composer').requestSubmit()})()`);
    await waitFor(() => ev(`!!document.querySelector('.u .msg.user')`), 5000, "your message bubble (.u .msg.user)");
    await waitFor(() => ev(`[...document.querySelectorAll('.ai .meta2')].some(m=>/tokens/.test(m.textContent)&&/Copy/.test(m.textContent))`), 30000, "the finished answer's stats line");
    const text = await ev(`document.querySelector('.ai:last-of-type .bub')?.textContent||''`);
    if (!/150–200 ms/.test(text)) throw new Error("answer text missing: " + text.slice(0, 80));
    if (!(await ev(`!!document.querySelector('.ai .bub pre code')`))) throw new Error("markdown code block not rendered");
    if (await ev(`!!document.querySelector('.ai .bub .orb.tiny')`)) throw new Error("the tiny writing drop should be gone when done");
    if (!(await ev(`document.querySelectorAll('#convs .conv').length>=1`))) throw new Error("the chat is missing in the sidebar");
    if (!/autocomplete/.test(await ev(`document.getElementById('chat-title').textContent`))) throw new Error("the title at the top is not the chat's title");
    const gap = await ev(`document.getElementById('composer').getBoundingClientRect().right-document.getElementById('send').getBoundingClientRect().right`);
    if (gap > 30) throw new Error("the send button is not at the right end of the composer (" + Math.round(gap) + "px from it)");
  });

  const last = (sel) => `[...document.querySelectorAll('.ai')].pop()?.querySelector('${sel}')`;
  const sendText = (t) => ev(`(()=>{document.getElementById('input').value=${JSON.stringify(t)};document.getElementById('composer').requestSubmit()})()`);
  const idle = () => waitFor(() => ev(`!document.getElementById('send').classList.contains('stop')`), 40000, "the answer to finish");

  await check("round 2: reading phase shimmers, the tiny drop fills while writing", async () => {
    await sendText("hello again");
    await waitFor(() => ev(`${last(".orb")}?.dataset.phase==='reading'||${last(".orb")}?.dataset.phase==='thinking'`), 3000, "the reading phase on the orb");
    const label = await ev(`${last(".phase-label")}?.textContent||''`);
    if (!/Reading|Thinking/.test(label)) throw new Error("phase label: " + label);
    if ((await ev(`getComputedStyle(${last(".phase-label")}).animationName`)) !== "sweep") throw new Error("the phase label does not shimmer");
    await waitFor(() => ev(`${last(".orb.tiny")}?.dataset.phase==='writing'`), 8000, "the tiny drop filling (data-phase=writing)");
    await idle();
  });

  await check("round 2: code is coloured and downloads as its file", async () => {
    const dir = mkdtempSync(join(tmpdir(), "dewdrop-dl-"));
    await cmd("Page.setDownloadBehavior", { behavior: "allow", downloadPath: dir });
    await sendText("make me an html page");
    await idle();
    if ((await ev(`${last(".code-head .lang")}?.textContent`)) !== "page.html") throw new Error("the code block does not name page.html");
    if (!(await ev(`!!${last(".code .hljs-tag, .code .hljs-name")}`))) throw new Error("the code is not coloured (no .hljs-tag)");
    await ev(`${last("[data-download]")}.click()`);
    const file = join(dir, "page.html");
    await waitFor(() => existsSync(file), 5000, "page.html in the downloads");
    const got = (await import("node:fs")).readFileSync(file, "utf8");
    if (!got.includes('<p class="x">Hello</p>')) throw new Error("downloaded file: " + got.slice(0, 80));
    rmSync(dir, { recursive: true, force: true });
  });

  await check("round 2: scrolling up keeps your place; Latest and sending go back down", async () => {
    await sendText("a long one please");
    await waitFor(() => ev(`(()=>{const t=document.getElementById('thread');return t.scrollHeight>t.clientHeight*2})()`), 15000, "a long answer");
    const box = await ev(`(()=>{const r=document.getElementById('thread').getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})()`);
    await cmd("Input.dispatchMouseEvent", { type: "mouseWheel", x: box[0], y: box[1], deltaX: 0, deltaY: -40 });
    await sleep(300);
    const a = await ev(`document.getElementById('thread').scrollTop`);
    await sleep(1500);
    const b = await ev(`document.getElementById('thread').scrollTop`);
    if (Math.abs(b - a) > 2) throw new Error(`the page kept scrolling while you read (${a} -> ${b})`);
    if (await ev(`document.getElementById('jump').hidden`)) throw new Error("#jump (↓ Latest) is not shown");
    await ev(`document.getElementById('jump').click()`);
    await sleep(400);
    const gap = await ev(`(()=>{const t=document.getElementById('thread');return t.scrollHeight-t.scrollTop-t.clientHeight})()`);
    if (gap > 60) throw new Error("Latest did not go to the bottom (" + gap + "px left)");
    await idle();
    await ev(`document.getElementById('thread').scrollTop=0`);
    await sleep(200);
    await sendText("hi");
    await sleep(500);
    const gap2 = await ev(`(()=>{const t=document.getElementById('thread');return t.scrollHeight-t.scrollTop-t.clientHeight})()`);
    if (gap2 > 60) throw new Error("after sending, the page did not scroll to your message (" + gap2 + "px from the bottom)");
    await idle();
  });

  await check("round 2: stop ends the answer and keeps what was written", async () => {
    await sendText("another long one");
    await waitFor(() => ev(`(${last(".bub")}?.textContent||'').length>200`), 15000, "some text");
    if (!(await ev(`document.getElementById('send').classList.contains('stop')`))) throw new Error("#send is not a stop button while writing");
    await ev(`document.getElementById('send').click()`);
    await waitFor(() => ev(`!!${last(".note.stopped")}`), 6000, "the Stopped note");
    await idle();
    const chats = await ev(`fetch('/api/chats').then(r=>r.json())`);
    const chat = await ev(`fetch('/api/chats/${chats[0].id}').then(r=>r.json())`);
    const m = chat.messages.filter((x) => x.role === "assistant").pop();
    if (!m.stopped || m.content.length < 50 || m.content.length > 2000) throw new Error("stored answer: stopped=" + m.stopped + ", " + m.content.length + " chars");
  });

  await check("round 2: folders: new folder, move a chat with the menu, delete a chat", async () => {
    await ev(`document.getElementById('new-folder').click()`);
    await waitFor(() => ev(`[...document.querySelectorAll('.folder summary span')].some(s=>s.textContent==='Work')`), 5000, "the folder Work");
    const before = await ev(`document.querySelectorAll('#convs .conv').length`);
    await ev(`document.querySelector('#convs .conv [data-chat-menu]').click()`);
    await waitFor(() => ev(`!document.getElementById('cmenu').hidden`), 3000, "the ⋯ menu");
    await ev(`document.querySelector('#cmenu button[data-act=move][data-folder]:not([data-folder=""])').click()`);
    await waitFor(() => ev(`!!document.querySelector('.folder .conv')`), 5000, "the chat inside the folder");
    await ev(`document.querySelector('.folder .conv [data-chat-menu]').click()`);
    await waitFor(() => ev(`!document.getElementById('cmenu').hidden`), 3000, "the ⋯ menu");
    await ev(`document.querySelector('#cmenu button[data-act=delete]').click()`);
    await waitFor(() => ev(`document.querySelectorAll('#convs .conv').length===${before - 1}`), 5000, "the chat deleted");
  });

  await check("passkey: add one on /account, sign out, sign in with it", async () => {
    await cmd("WebAuthn.enable");
    await cmd("WebAuthn.addVirtualAuthenticator", { options: { protocol: "ctap2", transport: "internal",
      hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
    await go("/account");
    await waitFor(() => ev(`!!document.getElementById('passkey-list') && !document.getElementById('passkey-add').hidden`), 5000, "the account page");
    await ev(`document.getElementById('passkey-add').click()`);
    await waitFor(() => ev(`/added/i.test(document.getElementById('passkey-msg').textContent)`), 10000, "'Passkey added.'");
    await waitFor(() => ev(`document.querySelectorAll('#passkey-list li [data-remove]').length===1`), 5000, "the passkey in the list");
    await ev(`fetch('/api/logout',{method:'POST',headers:{'X-Coder':'1'}})`);
    await cmd("Network.clearBrowserCookies");
    await go("/login");
    await waitFor(() => ev(`!document.getElementById('passkey-signin').hidden`), 5000, "the passkey button");
    await ev(`document.getElementById('passkey-signin').click()`);
    await waitFor(() => ev(`location.pathname==='/'`), 10000, "being signed in");
    const me = await ev(`fetch('/api/me').then(r=>r.json())`);
    if (me.username !== "cedric") throw new Error("signed in as " + JSON.stringify(me));
  });
  ws.close();
}

main().catch((e) => { failures.push("setup"); console.log("FAIL setup: " + e.message); }).finally(async () => {
  const gone = (p) => (p && p.exitCode === null ? new Promise((r) => { p.once("exit", r); setTimeout(r, 3000); }) : null);
  chrome?.kill(); server.kill();
  await Promise.all([gone(chrome), gone(server)]);
  for (const d of [data, profile]) { try { rmSync(d, { recursive: true, force: true }); } catch { /* left in tmp */ } }
  console.log(failures.length ? `${failures.length} failed` : "all browser tests passed");
  process.exit(failures.length ? 1 : 0);
});
