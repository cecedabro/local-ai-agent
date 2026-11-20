# Design history

**The living style guide** is `chat/static/styleguide.html` (open `/static/styleguide.html` on the running app). It loads
the app's real stylesheets and draws the components with the app's own code, in every theme, so it is always current.
Change a component in `chat/static/css/` and it changes there too; add an example when you add a component.
**How Clear Source works**, explained visually: `chat/static/clear-source.html` (`/static/clear-source.html`).

The rounds below are the history: how each choice was made.

Every design round, as it was shown, so the choices can be traced later. Each file is a single,
self-contained HTML page: download it and open it in a browser (most have a light / dark / Blossom
switch at the top right and animate live).

## Status page (agent-status), 2026-11-16 to 17

| Page | What it explored | Chosen |
|---|---|---|
| `status/…-glass-light.html`, `…-glass-dark.html` | Liquid-glass look in both themes | the mix below |
| `status/…-aero.html` | Frutiger Aero gloss: candy buttons, tinted tiles | the mix below |
| `status/…-mix-light.html`, `…-mix-dark.html` | Glass cards + Aero buttons and bars | **this one** |
| `status/…-options.html` | Quick/Think switch, token bar, chat bubbles | Quick/Think + token bar |
| `status/…-bubbles2.html` | Your messages: frosted aqua (light) vs sea glass (dark) | **A in light, D in dark** |
| `status/…-loading.html` | Loading orbs and shimmer text | the spinning, breathing orb |
| `status/…-progress.html`, `…-progress-final.html` | Animated progress bar while a loop runs | **D, flowing left to right, slower** |

Later status tweaks (sonar "model ready", play orb, foldable Recent, the "Start an agent loop" card)
were decided in chat and built directly; round 6 below has the loop-card ideas.

## Dewdrop (the chat app), 2026-11-17 to 20

| Round | Page | What it explored | Chosen |
|---|---|---|---|
| 1 | `chat/2026-11-17-r1-orbs-phases-replies.html` | "Model ready" pulse, five orbs, phase animations, reply styles, the footer line | **S1 halo** (thin ring); the footer line |
| 2 | `chat/2026-11-17-r2-softer-orbs.html` | Softer marbles, waiting styles, radar with dots, water | **W3** waiting (the orb asleep) |
| 3 | `chat/2026-11-17-r3-twelve-orbs.html` | Twelve orb directions, app icon, welcome screen, smooth phases | **Dewdrop** (name and logo); cloud marble on the welcome screen |
| 4 | `chat/2026-11-18-r4-layouts.html` | Six chat layouts (how ChatGPT, Claude, Gemini and Perplexity do it) | **L1 open page** for chat, **L3 turn cards** for EvidenceHunt |
| 5 | `chat/2026-11-18-r5-open-page.html` | Styles for your own messages | **U1 frosted aqua** (sea glass stays in dark) |
| 6 | `chat/2026-11-18-r6-writing-and-loop-card.html` | Four ways to write the answer; three "start a loop" cards | loop card **A** |
| 7 | `chat/2026-11-18-r7-writing-v2.html` | Writing rebuilt like the big apps (measured 120 fps) | **V2** steady letters + soft fade |
| 8 | `chat/2026-11-18-r8-drop-details.html` | Tiny drop at the end of the text, Blossom drops, water | **E2** (the drop slides into the stats), **P1 rose quartz** for Blossom |
| 9 | `chat/2026-11-18-r9-water.html` | Realistic filling water | **F4 without the gloss band** |
| 10 | `chat/2026-11-19-r10-animations.html` | Message appear, profile indicator, phase animations on the drop | **M3** (your message flies in from the send button) |
| 11 | `chat/2026-11-19-r11-chat-preview.html` | The whole chat with the round-3 phases on the dewdrop; app icons | icons **r4-aero** and **r4-blossom**; drop 44 px while busy → 28 px when done |
| 12 | `chat/2026-11-19-r12-chat-preview.html` | Round-3 phases exactly, no breathing, livelier clouds, the glossy stop button, Clear Source's colour | **turquoise** Clear Source (deep rose in Blossom); stop = glossy Aero ball |
| 13 | `chat/2026-11-20-r13-clear-source.html` | Clear Source's research trail in four stages, the short-answer box, ✓ after quotes, grouped sources, the sources page as rows | **13b**: the app's own tokens, plain readable citation numbers, grouped lists (needs a local web server: it loads ES modules) |

The chosen look lives in `chat/static/css/` (`tokens.css`, `base.css`, `chat.css`, `orb.css`) and the
writing engine in `chat/static/js/stream.js`; `chat/SPEC.md` §9 lists the markup hooks they rely on.
The first full mockup is `chat/2026-11-17-chat-mockup.html` (on the chat branch).
