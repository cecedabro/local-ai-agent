// Browser tests for Dewdrop: drive a real headless Chromium against the app (chat/tests/browser/serve.py).
// Protected (human-owned). Run: make chat-browser-test   (CHROME_BIN picks the browser)
//   smoke:   sign in with the password, send a message, wait for the finished answer, no console errors
//   round 2: phases, code blocks + download, scroll freeze + "Latest", stop, folders (chat/SPEC.md §11)
//   round 3: the profiles page (notes, filters, a website, a document, the budget bar), sources under answers
//   round 4: Clear Source's trail, thinking and checked quotes; PubMed strictness (C); the app icon picker
//   round 5: MeSH chips in the trail, study-type badges, the Dutch guideline sources and #add-defaults
//   round 6: files in and out: attach (chip with tokens, a refused file), a file the model made, "Download as" (§16)
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
    .find((t) => t.type === "page"), 60000, "Chromium");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0; const pend = {}; const errors = [];
  ws.onmessage = (m) => {
    const j = JSON.parse(m.data);
    if (j.id && pend[j.id]) { pend[j.id](j); delete pend[j.id]; }
    if (j.method === "Runtime.exceptionThrown") errors.push(j.params.exceptionDetails.exception?.description || j.params.exceptionDetails.text);
    if (j.method === "Runtime.consoleAPICalled" && j.params.type === "error") errors.push(j.params.args.map((a) => a.value ?? a.description).join(" "));
    if (j.method === "Page.javascriptDialogOpening") {         // prompt() gets "Work", confirm() is accepted
      const promptText = /Website/.test(j.params.message) ? "nvk.nl" : "Work";
      ws.send(JSON.stringify({ id: ++id, method: "Page.handleJavaScriptDialog", params: { accept: true, promptText } }));
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

  await check("round 3: profiles page: note, filter, website, document, budget", async () => {
    await go("/profiles");
    await waitFor(() => ev(`document.querySelectorAll('.pf-tab').length===2`), 5000, "two profile tabs");
    await ev(`document.querySelector('.pf-tab[data-profile=evidence]').click()`);
    await waitFor(() => ev(`document.querySelectorAll('.src-card').length===17`), 5000, "the seeded PubMed and 16 guideline, medicine and patient cards");
    if (!(await ev(`document.querySelector('.pf-tab[data-profile=evidence]').getAttribute('aria-selected')==='true'`))) throw new Error("the open tab is not aria-selected");
    if (!(await ev(`document.querySelectorAll('#budget-bar i').length>=1 && /tokens/.test(document.getElementById('budget-text').textContent)`))) throw new Error("no budget bar");
    await ev(`(()=>{const t=document.querySelector('.src-card[data-kind=pubmed] [data-note]');t.value='Alleen RCTs en reviews';t.dispatchEvent(new Event('input',{bubbles:true}))})()`);
    await ev(`(()=>{const c=document.querySelector('.src-card[data-kind=pubmed] [data-type=review]');c.click()})()`);
    await sleep(1500);
    const p = await ev(`fetch('/api/profiles/evidence').then(r=>r.json())`);
    const pub = p.sources.find((s) => s.kind === "pubmed");
    if (pub.note !== "Alleen RCTs en reviews") throw new Error("the note was not saved: " + JSON.stringify(pub.note));
    if (!pub.filters.types.includes("review")) throw new Error("the filter was not saved: " + JSON.stringify(pub.filters));
    await ev(`document.getElementById('add-website').click()`);
    await waitFor(() => ev(`[...document.querySelectorAll('.src-card b')].some(b=>b.textContent==='nvk.nl')`), 5000, "the nvk.nl card");
    const doc = join(data, "richtlijn.md");
    (await import("node:fs")).writeFileSync(doc, "# Richtlijn\n" + "Vitamine D advies. ".repeat(300));
    const node = await cmd("DOM.querySelector", { nodeId: (await cmd("DOM.getDocument")).root.nodeId, selector: "#add-doc" });
    await cmd("DOM.setFileInputFiles", { nodeId: node.nodeId, files: [doc] });
    await waitFor(() => ev(`!!document.querySelector('.src-card[data-kind=file]')`), 8000, "the document card");
    await waitFor(() => ev(`/richtlijn\.md/.test(document.getElementById('budget-legend').textContent)`), 5000, "the document in the budget legend");
    // a profile document is read first in every Clear Source answer (SPEC §15.4): take it out again for the later rounds
    await ev(`fetch('/api/profiles/evidence').then(r=>r.json()).then(p=>Promise.all(p.sources.filter(s=>s.kind==='file')
      .map(s=>fetch('/api/profiles/evidence/sources/'+s.id,{method:'DELETE',headers:{'X-Coder':'1'}}))))`);
  });

  await check("round 3: a searched answer shows numbered sources and citation links", async () => {
    await go("/");
    await waitFor(() => ev(`!!document.getElementById('composer')`), 5000, "the chat page");
    await ev(`document.getElementById('new-chat').click()`);
    await sendText("search: do I need a flu shot?");
    await idle();
    if (!(await ev(`${last(".sources details.src .src-open")}?.href==='https://www.rivm.nl/griepprik'`))) throw new Error("no source card under the answer");
    if (!(await ev(`${last("a.cite")}?.textContent==='1'`))) throw new Error("[1] is not a citation link");
    await ev(`${last("a.cite")}.click()`);
    await waitFor(() => ev(`${last("details.src")}?.open===true`), 3000, "the source card to open when [1] is tapped");
    if (!/Wie krijgt hem/.test(await ev(`${last("details.src blockquote")}?.textContent||''`))) throw new Error("the source card has no quote");
  });

  await check("round 4: Clear Source: search trail, thinking, quotes checked against the source", async () => {
    await go("/");
    await waitFor(() => ev(`!!document.getElementById('composer')`), 5000, "the chat page");
    await ev(`document.getElementById('profile').click()`);
    await ev(`document.querySelector('#pmenu .opt[data-profile=evidence]').click()`);
    await waitFor(() => ev(`document.documentElement.dataset.profile==='evidence'`), 3000, "html[data-profile=evidence]");
    if ((await ev(`document.getElementById('input').placeholder`)) !== "Ask Clear Source…") throw new Error("placeholder is not 'Ask Clear Source…'");
    await sendText("Is apixaban veilig bij een eGFR onder 30?");
    await waitFor(() => ev(`!!${last(".trail .step.search")}`), 15000, "a live trail step while researching");
    await idle();
    const sum = await ev(`${last(".trail summary")}?.textContent||''`);
    if (!/^Researched · \d+ search/.test(sum)) throw new Error("trail summary: " + sum);
    if ((await ev(`${last(".trail")}.querySelectorAll('li.stage>.orb').length`)) !== 4) throw new Error("the trail has no four stages with a drop (§15.6)");
    if (!(await ev(`!!${last(".bub .short .lbl")}`))) throw new Error("the opening summary is not in its box");
    if (!(await ev(`!!${last("blockquote.q-ok p .q-mark")}`))) throw new Error("the ✓ is not right after the quote");
    if (!(await ev(`!!${last('.trail a[href^="https://pubmed.ncbi.nlm.nih.gov/?term="]')}`))) throw new Error("the PubMed search is not a link");
    if (!/Thought/.test(await ev(`${last(".thought summary")}?.textContent||''`))) throw new Error("no thinking block");
    if (!(await ev(`!!${last("blockquote.q-ok .q-mark")}`))) throw new Error("the real quote has no ✓");
    if (await ev(`!!${last("blockquote.q-bad")}`)) throw new Error("the made-up quote is still in the answer (SPEC §15.5 tidy)");
    const note = await ev(`${last(".verify-note summary")}?.textContent||''`);
    if (!/no source/.test(note)) throw new Error("no note about sentences without a source");
    if (!/removed/.test(note)) throw new Error("no note about the removed quote: " + note);
  });

  await check("round 14: no Quick/Think in Clear Source, the quote opens its source, the send drop, a draft stays", async () => {
    if (await ev(`getComputedStyle(document.querySelector('.composer .seg[aria-label="Answer mode"]')).display!=='none'`)) throw new Error("Quick/Think shows in Clear Source");
    if (!(await ev(`!!${last("blockquote.q-ok.q-link[data-qn]")}`))) throw new Error("the checked quote is not a link to its source");
    await ev(`${last("blockquote.q-ok.q-link")}.click()`);
    await waitFor(() => ev(`!!${last("details.src[open]")}`), 3000, "the quote opens its source card");
    await sendText("Helpt paracetamol bij hoofdpijn?");
    await waitFor(() => ev(`!!document.querySelector('#send.has-drop .sd-orb') && !!document.querySelector('.cbar .sd-label b')`), 5000, "the stop button is a drop with a phase label");
    await idle();
    await waitFor(() => ev(`!document.querySelector('#send .sd-orb') && !document.querySelector('.cbar .sd-label')`), 3000, "the drop and label go when the answer is done");
    await ev(`(()=>{const i=document.getElementById('input');i.value='a draft for later';i.dispatchEvent(new Event('input'))})()`);
    await go("/");
    await waitFor(() => ev(`document.getElementById('input').value==='a draft for later'`), 5000, "the draft is back after a reload");
    await ev(`(()=>{const i=document.getElementById('input');i.value='';i.dispatchEvent(new Event('input'))})()`);
  });

  await check("round 14: the style guide and the Clear Source explainer draw with the app's own code", async () => {
    await go("/static/styleguide.html");
    await waitFor(() => ev(`document.querySelectorAll('#sg-cs details.src').length===5 && !!document.querySelector('#sg-cs .q-link') && document.querySelectorAll('#sg-drops .orb').length===7`), 5000, "the guide's samples");
    await go("/static/clear-source.html");
    await waitFor(() => ev(`document.querySelectorAll('.fl-ribbon .orb.live').length===6 && document.querySelectorAll('.fl-step').length===6`), 5000, "the explainer's steps");
    await go("/");
    await waitFor(() => ev(`!!document.getElementById('composer')`), 5000, "back to the chat");
  });

  await check("round 5: Clear Source: MeSH headings in the trail, study types on the sources", async () => {
    const chips = await ev(`[...(${last(".trail")}?.querySelectorAll('.mesh i')||[])].map(i=>i.textContent)`);
    if (JSON.stringify(chips) !== '["Renal Insufficiency, Chronic"]') throw new Error("MeSH chips: " + JSON.stringify(chips));
    const href = await ev(`${last('.trail a[href^="https://pubmed.ncbi.nlm.nih.gov/?term="]')}?.href||''`);
    if (!href.includes("MeSH")) throw new Error("the PubMed link is not the exact search: " + href);
    const badge = await ev(`${last(".sources details.src .stype")}?.textContent||''`);
    if (badge !== "Meta-analysis") throw new Error("study type badge: " + JSON.stringify(badge));
  });

  await check("round 5: the Dutch guideline sources and the add-defaults button", async () => {
    await go("/profiles");
    await waitFor(() => ev(`document.querySelectorAll('.pf-tab').length===2`), 5000, "profile tabs");
    await ev(`document.querySelector('.pf-tab[data-profile=evidence]').click()`);
    await waitFor(() => ev(`!!document.querySelector('.src-card[data-kind=website]')`), 5000, "the website cards");
    const p = await ev(`fetch('/api/profiles/evidence').then(r=>r.json())`);
    const lareb = p.sources.find((x) => x.domain === "lareb.nl");
    if (!lareb || !p.sources.some((x) => x.domain === "richtlijnendatabase.nl")) throw new Error("the Dutch guideline sites are not there by default");
    const groups = await ev(`[...document.querySelectorAll('.pf-group')].map(g=>g.dataset.group)`);
    if (JSON.stringify(groups.slice(0, 3)) !== '["guidelines","medicines","research"]') throw new Error("source groups: " + JSON.stringify(groups));
    if ((await ev(`document.querySelector('.pf-group[data-group=medicines] .src-card b')?.textContent`)) !== "Farmacotherapeutisch Kompas") throw new Error("the sources do not show their names");
    await ev(`fetch('/api/profiles/evidence/sources/${lareb.id}',{method:'DELETE',headers:{'X-Coder':'1'}})`);
    await ev(`document.getElementById('add-defaults').click()`);
    await waitFor(() => ev(`/Added 1 source/.test(document.getElementById('pf-msg').textContent)`), 5000, "'Added 1 source.'");
    await waitFor(async () => (await ev(`fetch('/api/profiles/evidence').then(r=>r.json())`)).sources.some((x) => x.domain === "lareb.nl"), 5000, "lareb.nl back");
    await ev(`document.getElementById('add-defaults').click()`);
    await waitFor(() => ev(`/already have/.test(document.getElementById('pf-msg').textContent)`), 5000, "'You already have all the default sources.'");
  });

  await check("round 4: PubMed strictness (design C) and the app icon picker", async () => {
    await go("/profiles");
    await waitFor(() => ev(`document.querySelectorAll('.pf-tab').length===2`), 5000, "profile tabs");
    await ev(`document.querySelector('.pf-tab[data-profile=evidence]').click()`);
    await waitFor(() => ev(`!!document.querySelector('.src-card[data-kind=pubmed] .preset[data-preset=strict]')`), 5000, "the Strict choice");
    await ev(`document.querySelector('.src-card[data-kind=pubmed] .preset[data-preset=strict]').click()`);
    await waitFor(async () => (await ev(`fetch('/api/profiles/evidence').then(r=>r.json())`)).sources.find((x) => x.kind === "pubmed").filters.preset === "strict", 5000, "the preset saved");
    await waitFor(() => ev(`document.querySelector('.src-card[data-kind=pubmed] .preset.on')?.dataset.preset==='strict'`), 5000, "Strict marked as chosen");
    await ev(`document.querySelector('.src-card[data-kind=website] [data-enabled]').click()`);
    await waitFor(async () => (await ev(`fetch('/api/profiles/evidence').then(r=>r.json())`)).sources.find((x) => x.kind === "website").enabled === false, 5000, "the on/off switch saved");
    await go("/account");
    await waitFor(() => ev(`!!document.querySelector('#icon-pick button.on')`), 5000, "the current icon marked");
    await ev(`document.querySelector('#icon-pick button[data-icon=blossom]').click()`);
    await waitFor(() => ev(`/home screen/.test(document.getElementById('icon-msg').textContent)`), 5000, "the saved message");
    if ((await ev(`fetch('/api/me').then(r=>r.json())`)).icon !== "blossom") throw new Error("icon not saved");
  });

  await check("round 6: attach files, a file the model made, download an answer (SPEC §16)", async () => {
    await go("/");
    await waitFor(() => ev(`!!document.getElementById('composer')`), 5000, "the chat page");
    await ev(`document.getElementById('profile').click()`);
    await ev(`document.querySelector('#pmenu .opt[data-profile=general]').click()`);
    await waitFor(() => ev(`document.documentElement.dataset.profile==='general'`), 3000, "the general profile");
    await ev(`document.getElementById('new-chat').click()`);
    await sleep(400);
    const accept = await ev(`document.getElementById('file-input').accept`);
    if (!/\.pdf/.test(accept) || !/\.docx/.test(accept)) throw new Error("#file-input accept: " + accept);
    const pick = (name, body) => ev(`(()=>{const dt=new DataTransfer();dt.items.add(new File([${JSON.stringify(body)}],${JSON.stringify(name)}));` +
      `const i=document.getElementById('file-input');i.files=dt.files;i.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    await pick("notes.md", "# Notes\nBring water and a towel to every practice.");
    await waitFor(() => ev(`!!document.querySelector('#file-chips .file:not(.err)')`), 5000, "the file chip");
    const chip = await ev(`document.querySelector('#file-chips .file:not(.err)').textContent`);
    if (!/notes\.md/.test(chip) || !/token/.test(chip)) throw new Error("the chip should show the name and its tokens: " + chip);
    if (await ev(`document.querySelector('#file-chips .file:not(.err)').dataset.kind`) !== "text") throw new Error("chip data-kind");
    await pick("old.doc", "x");
    await waitFor(() => ev(`!!document.querySelector('#file-chips .file.err')`), 5000, "a chip for the refused file (.file.err)");
    if (!/docx/.test(await ev(`document.querySelector('#file-chips .file.err').textContent`))) throw new Error("the refused chip has no reason");
    await waitFor(() => ev(`!document.querySelector('#file-chips .file.err')`), 9000, "the refused chip to go away after 6 s");
    await sendText("make a word file <b>please</b>");
    await idle();
    if (!(await ev(`!![...document.querySelectorAll('.u')].pop()?.querySelector('.file')`))) throw new Error("no file chip on your message");
    const card = last(".files-out a.file-card");
    await waitFor(() => ev(`!!${card}`), 5000, "the download card (.files-out a.file-card)");
    if ((await ev(`${card}.dataset.kind`)) !== "docx" || !(await ev(`${card}.hasAttribute('download')`))) throw new Error("card data-kind/download");
    if (!/^\/api\/files\/\d+$/.test(await ev(`${card}.getAttribute('href')`))) throw new Error("card href");
    if (!/Weekplan\.docx/.test(await ev(`${card}.textContent`))) throw new Error("card name");
    if ((await ev(`${card}.querySelector('.fi')?.textContent.trim().toLowerCase()`)) !== "docx") throw new Error("card .fi");
    if (!/KB|B$/.test(await ev(`${card}.querySelector('small')?.textContent||''`))) throw new Error("card size");
    const ct = await ev(`fetch(${card}.getAttribute('href')).then(r=>r.headers.get('content-type'))`);
    if (!/wordprocessingml/.test(ct)) throw new Error("download type " + ct);
    await ev(`${last(".meta2 .dl")}.click()`);
    await waitFor(() => ev(`!${last(".dl-menu")}?.hidden`), 3000, "the Download as menu (.dl-menu)");
    const links = await ev(`[...${last(".dl-menu")}.querySelectorAll('a')].map(a=>a.getAttribute('href'))`);
    if (links.length !== 4 || !links.every((h) => /^\/api\/messages\/\d+\/export\?format=(md|docx|pdf|html)$/.test(h)))
      throw new Error("menu links: " + JSON.stringify(links));
    const pdf = await ev(`fetch(${last(".dl-menu")}.querySelector('a[href$="pdf"]').getAttribute('href')).then(r=>r.headers.get('content-type'))`);
    if (pdf !== "application/pdf") throw new Error("export pdf type " + pdf);
    await cmd("Page.reload"); await sleep(1200);                          // the newest chat opens again
    await waitFor(() => ev(`!!document.querySelector('.ai .files-out a.file-card')`), 6000, "the card again after a reload");
    if (await ev(`!!document.querySelector('.u .msg.user b')`)) throw new Error("your own text is not escaped");
    if (!/<b>please<\/b>/.test(await ev(`[...document.querySelectorAll('.u .msg.user')].pop().textContent`))) throw new Error("typed text lost");
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
