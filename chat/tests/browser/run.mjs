// Browser tests for Dewdrop: drive a real headless Chromium against the app (chat/tests/browser/serve.py).
// Protected (human-owned). Run: make chat-browser-test   (CHROME_BIN picks the browser)
//   smoke:   sign in with the password, send a message, wait for the finished answer, no console errors
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
