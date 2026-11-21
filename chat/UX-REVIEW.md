# Dewdrop UX review: findings to implement

Hands-on review of the live app (`https://dewdrop.cedricschippers.dev`), done in a headless browser
(Playwright, desktop 1280×800 and iPhone 13) with the test account `claudetest`, on 2026-11-21.
Code references are against `main` at `3d5261c` ("Stop button is a dewdrop with an animated phase label").

Three rounds:
1. **General chat**: sending, streaming, rendering, sidebar, settings, themes, mobile.
2. **Clear Source**: a medical question end to end, then the profile and sources page.
3. **Persona test**: a third-year Dutch medicine student ("Noor") uses Clear Source to study. Eight real study
   questions, each answer checked against the guidelines, plus editing the source list.

## How to use this file

- Items are ranked **P0 (wrong or broken) → P3 (polish)**. Each has an ID (`CS-n` = Clear Source,
  `CH-n` = chat, `PR-n` = profiles page, `UI-n` = look and feel) so you can work on and reference them one by one.
- **Root cause** is marked **confirmed** (I read the code) or **suspected** (inferred from behaviour; verify
  first).
- **Protected paths.** `.agent-protected` lists `chat/static/css/`, `chat/coder/templates/`,
  `chat/static/js/stream.js`, `chat/SPEC.md` and several tests as human-owned. The autonomous agent loop may not
  touch them. In an interactive session with the owner that's fine, but call it out when a fix lands there.
  Items touching protected paths are marked 🔒.
- Run `make chat-test` (or `make chat-test K="..."`) and `node --test chat/tests/js/stream.test.mjs` after changes.
  The repo rule is `make lint && make test` before committing.

---

## P0: wrong answers or broken features

### CS-1 The "not relevant" step discards the best sources
**Symptom.** Clear Source marks the most relevant sources "not relevant", then answers from weak ones.
- **Vitamin D vs fractures in older adults:** all 16 PubMed hits were "not relevant", including *"Vitamin D and bone
  health in adults: a systematic review"* and *"Effect of High-Dose Vitamin D Supplementation…"*. The answer was
  built from a Thuisarts patient leaflet.
- **Apixaban at eGFR 25 for AF:** the Richtlijnendatabase page *"Atriumfibrilleren en chronische nierschade"* and
  NICE *"Atrial fibrillation: recommendations"* were both "not relevant". The user got no usable dose.
- **The reverse also happens:** for "Summarise this file" (non-medical, file attached), quotes from a breastfeeding
  guideline, a cholesterol guideline and a sleep-apnea paper were accepted.

**Root cause (confirmed, partly).** "Not relevant" does **not** mean the model judged the source irrelevant.
`read_one` (`chat/coder/research.py:497-528`) sets `status = "used" if found else "not relevant"`, where
`found` holds only the quotes that pass `verify.find_quote` (`chat/coder/verify.py:26`) as a word-for-word
substring of `src["text"]`. Any of these collapses into "not relevant":
- the model returned `{"quotes": []}`;
- the model returned quotes with tiny changes (whitespace, typographic quotes, `I²` → `I`, dropped
  parentheses, a translated word), so every quote failed verification;
- the JSON could not be parsed (`parse_quotes` returns `[]`);
- `src["text"]` is thin. For PubMed it is probably title plus abstract only (verify); for sites, a trimmed page.

**Suspected.** Many "not relevant" verdicts are failed verifications of near-exact quotes, not real
irrelevance. The quote `"…(RR 1.66, 95% CI 1.16-2.38; I These results suggest…"` in one answer shows that
abstract text loses characters (`I²=…`), which also breaks exact matching.

**Fix ideas.**
1. **Separate statuses** in the trail: `no passages`, `quotes failed check`, `parse error`, `used`. Log the
   counts per answer so the real cause is visible.
2. **Fuzzy-align before rejecting.** When a returned quote fails `find_quote`, find the best-matching span in the
   source (e.g. `difflib.SequenceMatcher` over sentence windows, ratio ≥ 0.9) and **replace the quote with the
   exact source span**. The guarantee stays ("every quote is word-for-word"), but far fewer good sources get thrown
   away.
3. **Keep guideline pages.** A page from an enabled Dutch guideline or medicines site that the search itself
   returned for the question should not be dropped silently. Retry the extract once with a stricter prompt, or
   keep it as "read but no quote" so the user can open it.
4. **Add a cheap topicality pre-check** (question terms or MeSH vs title and abstract) so off-topic PubMed hits
   (Vibrio cholerae, acne, Klebsiella in wildlife, Stenotrophomonas for a UTI question) are dropped *before* the
   model reads them. That also saves time.

**Acceptance.** For the vitamin D, apixaban/eGFR and SGLT2/HFpEF questions, at least the clearly on-topic
guideline page or systematic review shows "used". The trail tells "nothing useful" apart from "quotes failed
the check".

### CS-2 User-added sources are never used (NHG website, uploaded documents)
**Symptom.** I added `richtlijnen.nhg.org` (with the note "Gebruik dit eerst bij vragen over de eerste lijn")
and uploaded `collegeaantekeningen-UWI.txt` as a profile document. Across 8 Clear Source questions, NHG was
searched **0 times** and the document read **0 times**. That includes the cystitis question that the notes answer
directly. The explainer page (`/static/clear-source.html`) promises attached files are "Read first and treated as
sources, before anything is searched".

**Root cause (confirmed, websites).** `plan_messages` (`research.py:123-131`) passes only bare `site:<domain>`
strings. **Source notes are not sent to the planner.** `PLAN_PROMPT` (`research.py:30-45`) hard-codes which
built-in site fits which question, so the model has no reason to choose an unknown domain.

**Root cause (suspected, documents).** Profile documents (`kind == "file"`) are counted in the profile's context
(`profiles.py:233-301`, `system_prompt`). But `investigate(..., files=...)` (`research.py:~681`) appears to receive
only **message attachments**, through `file_sources()`. A profile document never becomes a numbered source in the
Clear Source flow. Verify at the `investigate(` call site in `answer.py`.

**Fix ideas.**
- Pass each enabled source to the planner as `site:<domain> — <title>: <note>`, and tell `PLAN_PROMPT` to prefer
  sources whose note matches.
- Always include enabled profile documents as sources, like message attachments (`file_sources`). Read them
  first, as the explainer says.
- Consider shipping NHG (`richtlijnen.nhg.org`, "NHG-Standaarden, eerste lijn") as a default guideline card.
  For Dutch primary care and for students it is *the* source.

**Acceptance.** With NHG enabled, a primary-care question searches `richtlijnen.nhg.org`. With a profile document
enabled, it appears in the trail as "Your file" and is read before any search.

### CS-3 Wrong or unusable clinical answers (content QA)
Persona round, each answer checked against the guidelines (as known to the reviewer; double-check the clinical
claims yourself):

| Q | Question | Time | Result |
|---|---|---|---|
| 1 | Eerste keus ongecompliceerde cystitis, niet-zwangere vrouw | 223 s | ✅ Nitrofurantoïne 5 d, 2dd 100 mg MR / 4dd 50 mg. Second choice (fosfomycine) missing. The "Onderzoek" section quotes an irrelevant Russian herbal RCT (Canephron N). |
| 1b | Waarom niet meer trimethoprim? (follow-up) | 293 s | 🟡 Core right (3rd choice, E. coli resistance 21% in 2024). Uses a *Stenotrophomonas* meta-analysis as evidence. 3 "quotes" are paper titles, flagged "not in source"; 7 unsourced sentences. |
| 2 | Ibuprofen in pregnancy | 237 s | 🟠 **Misleading short answer.** The Kompas quote says avoid from week 20, contraindicated 3rd trimester. The short answer says "first 26 weeks with a doctor, forbidden from 27", taken from a Thuisarts page about **reuma** medication. It never says "paracetamol first". |
| 3 | RIVM griepprik 60+ "dit jaar" | 219 s | 🟡 Generic. Found "Griepprik 2026 \| RIVM" but nothing year-specific. Source 1 is the *Pneumokokken* page. |
| 4 | Apixaban dose, AF, eGFR 25 | 299 s | ❌ Says "avoid". To the reviewer's knowledge the EU SmPC gives 2dd 2.5 mg for AF at CrCl 15–29 (verify). No eGFR vs Cockcroft-Gault nuance. The best guideline page was discarded (CS-1). |
| 5 | Keelontsteking, for an exam | 326 s | 🟡 Dutch policy right (paracetamol; antibiotics only when seriously ill or high risk; feneticilline). Mixes in a German point score that NHG doesn't use; cites "NHG-standaard 2007"; a quote pile, not an explanation. |
| 6 | SGLT2i in HFpEF (English) | 255 s | ✅ EMPEROR-Preserved and DELIVER numbers correct. Paraphrase errors: calls PRESERVED-HF "DAPA-HFpEF"; says it lowers CV death (driven by HF hospitalisation); "2.3 to 26.2 **months**" (2.3 is years). |
| 7 | Fictional drug "zorbaxitol" | 58 s | ✅ Honest refusal. 11 searches, one with the typo "zorrbaxitol". |

Systemic causes are CS-1, CS-2, CS-4, CS-5 and CS-6. Use these questions as a **regression set**: they are
realistic, and the expected answers are known.

### CS-4 The checker verifies quotes, not claims; flagged content stays in the answer
- Errors in the model's **paraphrase** pass unchecked: wrong trial name, wrong units, wrong week threshold, and
  "volgens de Richtlijnendatabase van het Farmacotherapeutisch Kompas".
- **Cut-off quotes get ✓:** "If the risk is low (<3 points), antibiotics are not indi" and
  "In HFpEF patients, SGLT2i is associated w". The prefix exists in the source, so `find_quote` passes. Reject or
  extend quotes that end mid-word.
- **Fake quotes:** the model quotes **paper titles plus metadata** ("…meta-analysis. (Meta-analysis · Journal… · 2023)").
  They are flagged "⚠ not in source" but stay in the answer, so it looks broken (red blocks).
- **Fix ideas:**
  - A post-check that compares numbers, units, percentages, week counts and named entities in each paraphrase
    sentence against its cited quote.
  - Drop flagged blockquotes, or rewrite their sentences, before showing the answer. Keep a collapsed
    "removed: 3 unverified quotes" note.
  - Never accept a source title as a quote.

### CS-5 Fixed five-section template produces filler and leaks into headings
`RULES` (`research.py:81-96`) asks for a "long, detailed answer" under five fixed headings, and writes the heading
*instructions* on the same line as the headings:
```
## Short answer: two or three sentences.
## Dosing and special groups: children, pregnancy, kidney or liver function, the elderly, interactions.
```
- **The model copies them:** "Dosing and special groups: children, pregnancy, … interactions." and
  "Strength of the evidence: the study types, their size and limitations." appeared as headings; "Kort antwoord:"
  and "Nederlandse richtlijnen:" got trailing colons.
- **Filler:** sections get filled with irrelevant material (a herbal RCT under "Onderzoek", Kompas breastfeeding
  advice under "Sterkte van het bewijs", symptom trials under "Dosing").
- **Fix:**
  - Put the headings and their descriptions on separate lines, or in a bulleted spec ("Heading → what goes in
    it"), and strip trailing `:` and the instruction text from headings server-side.
  - Make empty sections truly optional: "omit a heading when no quote fits it; never fill it with general
    statements".
  - Consider "concise" over "long, detailed".

### CS-6 Sources contradict each other and the answer silently mixes them
Ibuprofen: Kompas says 20 weeks and contraindicated in the 3rd trimester; the Thuisarts reuma page says 27 weeks.
The short answer used the less relevant one. Detect numeric or threshold conflicts between quotes on the same
topic, and show a "⚠ Bronnen verschillen" box naming both. Prefer guideline and Kompas sources over patient
leaflets for the short answer. Rank the source types: guideline > medicines > studies > patient info.

---

## P1: bugs in flows and controls

### CH-1 "+ New chat" race: the message lands in the previous chat
**Repro.** Load `/`, click "+ New chat" immediately (before the chat list loads), send a message. It is appended
to the most recent existing chat. I hit this for real: 8 test prompts landed in "Prime check".
**Root cause (confirmed).** The startup code `chat/static/js/app.js:1050-1052` runs
`await loadChats(); if (state.chats.length > 0) await openChat(state.chats[0].id);` after the user may already
have pressed New chat (`app.js:1000-1008`, which sets `state.chatId = null`).
**Fix.** A `state.userPicked` flag (set by New chat, by picking a profile, or by clicking a chat) checked before
the startup `openChat`. Or start the click handlers only after init.

### CH-2 Retry on an older answer appends a duplicate turn
`doneMeta` (`app.js:186`) wires Retry to `$("#input").value = retryText(); send();`. On the last answer it
looks like a regenerate. On an **older** answer it re-sends that old prompt at the bottom, so the question appears
twice. Either regenerate in place (server: delete that assistant message, re-run on the history up to it), or show
Retry only on the last answer.

### PR-1 🔒 Note / "Strictness and note" / "Customise" are invisible on desktop
**Root cause (confirmed): class name collision.** `profiles.js:118` renders
`<details class="more">` for source cards. `.more` is also the sidebar "⋯" button (`app.js:80,110`), styled in
`chat/static/css/chat.css:287-291`: `width:24px; height:24px; opacity:0`, shown only on `.conv:hover` and
similar. Mobile shows it only through `@media (hover:none){.more{opacity:1}}`.
So on desktop every source note, the PubMed strictness presets and the Customise filters can be clicked but not
seen. Saving still works (PATCH 200) but nothing shows.
**Fix.** Rename the profile class (e.g. `details.src-more`) in `profiles.js:118` and `chat.css:628-638` 🔒. Add a
"Saved" confirmation on note blur (the Instructions box already shows "Saved").

### PR-2 Added websites: no name, no category, wrong group
`richtlijnen.nhg.org` lands under "Other websites", last in the list, titled with its bare domain. The page says
"Guidelines come first in every answer", but users can't put a site under Guidelines. `profiles.py:133`
(`group()`) only knows the seeded domains. Add name and group fields when adding a site (a small form, not
`prompt()`), and make the group editable later.

### PR-3 PubMed preset text vs controls disagree
"Balanced" says "+ randomised trials · last 15 years · humans", but Customise shows **10** selected and has no
15 option (`5 / 10 / 20 / any`). Align them.

### CH-3 CSP blocks the markdown table styles
`style-src 'self'` rejects the `style="text-align:left"` that markdown-it writes on `<th>/<td>` when rendering
tables (`app.js:8` `markdownit(...)`, rendered at `app.js:310`). There are 9 console errors per page load, and
alignment is lost. Fix by overriding the table renderer to output a class (`.al-left` and so on), or strip the
style attribute.

### CH-4 Wrong tok/s (300–700 tok/s on Clear Source and Think answers)
`answer.py:204` sets `t0` when the first **content** token arrives, but `count` and `tokens` include all tokens
(thinking too). So `tps = tokens / (clock() - t0)` (`answer.py:317`) is inflated. Use content tokens over the
content time, or all tokens over the total time.

### CS-7 Literal `\n` in quotes
A Thuisarts quote rendered `"Niet vanaf 27 weken zwanger\n\nDeze pijnstillers…\n\nibuprofen\n\nnaproxen…"` with
visible backslash-n. Suspected: newlines escaped twice (page text or model JSON). Normalise `\\n` → newline or
space in extracted quotes before verification and display.

### CS-8 Clear Source ignores attachments and runs medical search on anything
"Summarise this file in one sentence" with a `.txt` attached, in Clear Source: the file was ignored, PubMed was
searched, and the answer summarised random guidelines as "the provided document". Related to CS-2. Also, "+ New
chat" keeps the last profile, so everyday questions silently go through Clear Source. Ideas: when a file is
attached and the question is about the file, answer from the file only. Show a small hint when a non-medical
question is asked in Clear Source ("Dit lijkt geen medische vraag. Overschakelen naar General?").

---

## P2: clarity and feedback

### CS-9 The long silent "Writing" phase (biggest UX issue in Clear Source)
Per question: understand 5–10 s, search 5–15 s, read 30–80 s (great, visible per source). Then
**130–215 s of "Writing the answer from N sources…"** with no change. That's 60–75% of the total wait, and the
context meter shows `0 / 49.2k` the whole time. The model is thinking (we see "Thought for 131–214 s" afterwards).
Ideas:
- Stream the short answer first.
- Show sub-steps ("Bronnen vergelijken… / Kort antwoord… / Citaten controleren 7/12").
- Show a short live summary of the thinking.
- At minimum, an elapsed timer and an estimate ("meestal 2–4 min").

### CS-10 Mislabelled sources and sections
- **Wrong headings:** the source list heading "Guidelines" contains items tagged "Drug information"
  (Farmacotherapeutisch Kompas) and "Patient information" (Thuisarts).
- **RIVM:** information pages are tagged "Guideline".
- **"Studies" section:** in one answer it contained the Thuisarts leaflet.
- **Mixed-up names:** the text mixes up source names ("volgens de Richtlijnendatabase van het Farmacotherapeutisch
  Kompas").
- **Fix:** group the source list by the actual type (Richtlijnen / Geneesmiddelen / Studies / Patiënteninformatie /
  Jouw documenten).

### CS-11 No way to judge recency
No revision or publication date on guideline and site sources. A cited "NHG-standaard 2007" looks the same as a
2026 page, and "dit jaar" isn't handled. Show "herzien <jaar>" where available, and warn about sources older than
~5 years. PubMed cards already show the year; add the trial acronym and n where known.

### CS-12 Language mixing
Dutch answers carry English chrome: "summary of the sources below" (`stream.js:557` 🔒), "Guidelines", "Studies",
"Drug information", "Patient information", "Other sources". Localise the labels to the answer language
(`answer_language()` already exists in `research.py`).

### CH-5 Web search in General: misleading status and source list
- **Wrong status:** Quick mode shows "Thinking…" while it is actually searching. Show "Searching the web…".
- **Wrong label:** the list is titled **"Other sources"** but includes the cited source [3].
- **Counts don't match:** "Searched 1 time · read 1 page" sits next to 5 listed sources.
- **Context cost:** one search answer took the context from 1.7k to 6.1k tokens.

### CH-6 Silent actions
- **Copy:** works, but never says "Copied ✓".
- **Compress now:** works (844 → 647) with no visible confirmation.
- **Escape:** doesn't stop a stream.
- **New chats appear late:** a new chat appears in the sidebar and header only after its first answer finishes.

### CH-7 LaTeX not rendered
`$1.05 + 0.05 = 1.10$`, `$17 \times 23 = 391$` show raw. Either add KaTeX (vendored) or tell the model in the
system prompt not to use `$…$` math. Plain-text formulas ("A = πr²") render fine.

### CH-8 Prose wrapped in code fences renders as a code block
"Write a 600-word story" and a markdown demo came back inside ```` ```markdown ```` fences and rendered as
`code.md` blocks with horizontal scroll. Unwrap `markdown`/`md` fences whose content is prose, or render
markdown-language fences as markdown.

### CH-9 Suggestion chips send immediately
"Plan my week" sends that bare prompt and creates a chat with that name. "Summarise a file" sends with no file.
Better: fill the input (cursor at the end) so the user can add details. For "Summarise a file", open the file
picker.

---

## P3: consistency and polish (look and feel)

- **UI-1 Internal names leak into the UI.** The sidebar badge "EH" on Clear Source chats (`app.js:71-80`) and
  "chat · evidence" under the username. Use "Clear Source" (or a teal drop icon) and hide profile slugs.
- **UI-2 Native `prompt()` and `confirm()`** for new, rename and delete of folders and chats (`app.js:781,859,871,882,887`)
  and for "+ Website" (`profiles.js`). Everything else uses custom glass menus. Replace them with inline rename
  and a small glass dialog, and add undo for delete.
- **UI-3 The "↓ Latest" pill** overlaps answer text and the Copy/Retry/Download row, on desktop and mobile. Hide
  it when within ~80 px of the bottom, and give it a backdrop.
- **UI-4 Thinking state shows three indicators at once** on mobile: the "Thought" pill, "Thinking… 5 s" and a
  "Thinking" chip by the stop button. Keep one.
- **UI-5 Profiles page is visually heavy.**
  - 17 bright red "Remove" links → a quiet trash icon or a ⋯ menu.
  - Rows give no description of the source → show the note as a subtitle (also fixes PR-1's empty band).
  - Thuisarts (patient leaflets) sits under "Guidelines".
  - The context card says "Instructions 761" while the instructions box is empty. It's the built-in prompt; label
    it "Clear Source rules".
- **UI-6 🔑 icon opens Settings** (icon, passkeys, docs). Use a gear.
- **UI-7 Two different "how it works" links.** The footer goes to GitHub, Settings goes to the in-app explainer.
- **UI-8 The style guide page** has no "← Chat" back button (other sub-pages do), and is slightly wider than the
  screen on mobile (scrollWidth 415 vs 390).
- **UI-9 Theme toggle "auto" icon** renders as a tiny dot.
- **UI-10 Signup:** after "Create my account" the form stays active above the passkey prompt (double submit
  possible), with a large gap between them.
- **UI-11 Mobile research trail:** long query lines overflow the right edge.
- **UI-12 Clear Source suggestion chips** are Dutch only while the UI is English (pick one, or follow the UI
  language).
- **UI-13 The Cloudflare analytics beacon** is blocked by the CSP: one console error on every page. Remove it or
  allow it.
- **UI-14 Mobile footer:** truncates to "Dewdrop · 558 toke…".

---

## What already works well (keep it)

- **Glass aesthetic:** consistent across login, chat, settings, profiles and all four themes (Aero, light, dark,
  Blossom). The profile dropdown is now glass too.
- **Streaming survives a closed tab:** reload shows the finished answer.
- **Rendering:** code blocks with language labels, Copy and Download; tables (cell wrapping fixed).
- **Downloads:** per answer, as MD, DOCX, PDF and HTML.
- **Clear Source trail:** the MeSH terms, per-site search lines, "Reading 6 of 14: …" with a verdict per source.
  Transparent and educational.
- **Citations:** clicking a bubble expands the source card with the exact quote and "Open on <site> ↗".
- **Honesty:** the refusal for an unknown drug, the "⚠ 1 sentence has no source" warnings, the Clear Source
  explainer page (excellent).
- **General chat:** fast (4–8 s, 30–50 tok/s), remembers context, answers in the question's language, reads
  attachments correctly, keeps your scroll position while streaming.
- **Profile documents:** uploading gives a confirmation, a token count and a live context meter.
- **Already fixed** since the first round: the Profiles JS error, the grey profile dropdown, and table cells
  wrapping mid-word.

---

## Ideas backlog (new features)

1. **Study mode for exams.** Answers in exam structure (definitie → anamnese → LO → beleid → valkuilen) and ends
   with "Overhoor me": 3 quiz questions, plus an Anki/flashcard export.
2. **"Gebruik deze toch".** Click a "not relevant" source in the trail to include it, then the answer is
   rewritten. A human-in-the-loop fix for CS-1 that also teaches source appraisal.
3. **Two speeds.** A quick answer from Dutch guidelines and the Kompas in ~30 s, then a "Verdiep met PubMed"
   button for the slow deep search.
4. **Source chips above the composer** ("Zoek in: NHG · FK · PubMed") to steer a single question.
5. **Built-in NHG preset**, ranked first for primary-care questions.
6. **Conflict box** when sources disagree (see CS-6).
7. **Case mode:** type a patient case, get the clinical reasoning steps in ward-rotation format.
8. **Compare view:** NHG vs NICE side by side.
9. **Study folders and reference export:** Vancouver/APA reference list for papers.
10. **Trial badges** in source cards: "EMPEROR-Preserved · RCT · n=5988 · NEJM 2021".
11. **Confidence per section** ("Sterk: 2 richtlijnen eens" / "Zwak: alleen patiëntinformatie").
12. **Push notification (PWA)** when a 4-minute answer is ready.

---

## Regression set (rerun after changes)

Run in a **new** Clear Source chat each, after the page has fully loaded (CH-1):
1. `Wat is de eerste keus behandeling bij een ongecompliceerde blaasontsteking bij een niet-zwangere vrouw? Graag met dosering en duur.`
   Follow-up in the same chat: `Waarom is trimethoprim niet meer de eerste keus?`
2. `Mag een zwangere vrouw ibuprofen gebruiken tegen hoofdpijn?`
3. `Wat zegt het RIVM over de griepprik voor 60-plussers dit jaar?`
4. `Welke dosis apixaban geef je bij atriumfibrilleren bij een patiënt met een eGFR van 25?`
5. `Leg uit hoe een huisarts een keelontsteking beoordeelt en wanneer er antibiotica worden gegeven. Ik leer dit voor mijn tentamen.`
6. `What is the evidence for SGLT2 inhibitors in heart failure with preserved ejection fraction?`
7. `Wat is de standaarddosering van zorbaxitol bij volwassenen?` (must refuse)
8. `Does vitamin D supplementation reduce the risk of fractures in older adults?`
9. In Clear Source, attach a `.txt` with meeting notes: `Summarise this file in one sentence.`

What to record per question: total time, time in "Writing", searches / read / used, which sources were used,
correctness of the short answer, the number of ⚠ flags, and whether NHG and profile documents were used.

## Test data on the live account (`claudetest`)

Clear Source profile: added source `richtlijnen.nhg.org` (with a note) and document `collegeaantekeningen-UWI.txt`.
About 15 test chats, a folder "QA folder" holding the chat "Prime check", and one deleted chat. The app icon was set
back to Aero, and the instructions were cleared after testing. Delete the account, or clean this up, for a fresh
demo.
