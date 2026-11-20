# Frontend

How Dewdrop's pages are built and why they look and move the way they do. The markup contract the styles rely on
is `chat/SPEC.md` §9; the design history (every round, as shown) is in `docs/design/`.

## Principles

- **Server-rendered pages, small ES modules, no framework, no build step.** Jinja templates
  (`chat/coder/templates/`) give the page its structure; JavaScript adds behaviour. It loads fast on a phone and is
  easy for the coding agent to work on.
- **A strict CSP**: scripts only from the app itself, no inline scripts or styles in HTML. Everything the server
  renders, and every piece of model text, is escaped; Markdown goes through markdown-it with HTML off.
- **The look is human-owned.** `chat/static/css/` and `stream.js` change only through design rounds; the agent
  works behind the markup hooks of SPEC §9 so it can't break the look.
- **Works as an app on the phone**: installable (manifest, app icons per theme), a drawer sidebar, keyboard-safe
  composer, notch-safe headers.

## Pages

| Page | Template | Script | What's on it |
|---|---|---|---|
| Chat | `chat.html` | `app.js` (+ `stream.js`, `mobile.js`) | Sidebar (chats by day, folders, ⋯ menu), top bar (title, profile button), messages, composer (📎, Quick/Think, send/stop), context bar |
| Sign in | `login.html` | `login.js`, `webauthn.js` | Password and passkey sign-in |
| Invite | `invite.html` | `invite.js` | Choose your password from an invite link |
| Account | `account.html` | `webauthn.js`, `icons.js` | Passkeys, the app icon picker |
| Sources | `profiles.html` | `profiles.js` | Per profile: instructions, sources in groups (guidelines, medicines, research, documents, websites) with switches, PubMed strictness (Strict / Balanced / Broad), the context budget |
| Admin | `admin.html` | `admin.js` | Invites and users |

`base.html` loads `theme.js` first (a classic script in `<head>`), so the right theme is applied before the page
paints.

## JavaScript modules

| File | Role |
|---|---|
| `stream.js` | The rendering engine (human-owned, unit tested with `node --test`): splitting the event stream (`splitEvents`, `readEvents`), steady writing with a soft fade (`renderFade`) and the tiny drop that follows the text (`placeTail`), code blocks with highlighting and Download (`codeBlocks`, `codeActions`), the orb (`orbHtml`, `startOrb`, `flyFrom`), the research trail (`trailHtml`, `animateTrail`), sources under an answer (`sourcesHtml`, `citeLinks`), the checker's marks (`markVerify`, `shortBox`) |
| `app.js` | The chat page: loading chats and folders, opening a chat, drawing stored messages, sending and reading the stream, stop, profiles, Quick/Think, files |
| `mobile.js` | Phone layout: the sidebar becomes a drawer, the theme switch and model badge move into it |
| `theme.js` | auto / light / dark / Blossom, stored locally and on the server |
| `profiles.js` | The sources page |
| `login.js`, `invite.js`, `webauthn.js`, `admin.js`, `icons.js` | The small pages |

### Sending a message (`app.js send`)

1. Your bubble flies in from the send button (`flyFrom`); an answer block with a live orb appears.
2. `readEvents` reads the server-sent events: `phase` sets the orb's `data-phase` and the shimmering label;
   `thinking` fills the folded "Thought" box; `trail` adds a step to the research trail; `delta` feeds the writer;
   `file` adds a download card; `done` draws the stats line (tokens, speed, Copy, Retry), sources and citation links.
3. The writer releases text at a steady pace (it measures the stream's speed and smooths it), so fast bursts and
   pauses from the GPU don't make the text jump.
4. While busy the send button is a stop button; scrolling up freezes the view and shows "↓ Latest".

## The look

### Tokens and themes

`tokens.css` defines every colour as a variable per theme: `:root` (light, frosted aqua), `[data-theme=dark]`
(sea glass), `[data-theme=blossom]` (pink). Everything else uses only these variables: `--glass`, `--btn` (the
glossy Aero buttons), `--primary`, `--chip`, `--line`, `--title-a/b`, `--eh` (Clear Source's turquoise; deep rose in
Blossom), `--scene` (the soft background blobs). Adding a theme means adding one block of tokens.

| File | What it styles |
|---|---|
| `tokens.css` | Colours and gradients per theme |
| `base.css` | Page basics, buttons, inputs, cards, sign-in pages |
| `chat.css` | The chat page, sidebar, messages, composer, trail, sources, the sources page, file cards |
| `orb.css` | The dewdrop orb and its phase animations |

### The dewdrop orb

The orb is the app's voice: one element (`<span class="orb live" data-phase="…">` with layers `.clouds`, `.radar`,
`.read`, `.sum`, `.water`), and the phase picks which layer moves:

| Phase | Animation |
|---|---|
| waiting | grey, fading softly (the model is busy or waking) |
| reading | lines drifting up past a light beam |
| thinking | big clouds drifting inside the drop |
| searching | a radar sweep with a dot per source found |
| summarising | particles gathering |
| writing | water filling the drop, waves following the writing speed |
| idle / done | the still dewdrop; it shrinks from 44 px to 28 px and slides into the stats line |

All animations use only `transform` and `opacity` (smooth on phones) and stop under `prefers-reduced-motion`.

### Clear Source in the page

The profile button glows turquoise, the composer says "Ask Clear Source…", and a thin strip marks the chat. An
answer shows the trail in four stages (understand, search, read, write), each with its own small drop; the short
answer in a box; ✓ or ⚠ right after each quote; numbered citations that open the source; sources grouped as
Guidelines, Studies, Other sources (and Your files).

## Testing the frontend

- `node --test chat/tests/js/stream.test.mjs`: the engine's pure functions.
- `make chat-browser-test`: headless Chromium signs in, sends messages against a fake model and checks phases,
  code blocks and download, scroll and stop, folders, the sources page, Clear Source's trail and checks, the icon
  picker, passkeys and (loop D) files. It fails on any console error.
- `node --check` on every module (the agent runs it for each JS task).
