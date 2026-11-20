# How Clear Source works

Clear Source is Dewdrop's research profile (internal id `evidence`; it was called EvidenceHunt until 2026-11-19). It
answers medical questions **only from sources it found and read**, and checks every quote word for word. The rules
are in `chat/SPEC.md` §13–§15 (and §16.2 for attached files); the code is `chat/coder/research.py` (the flow),
`chat/coder/verify.py` (the checker) and `chat/coder/tools.py` (PubMed, MeSH, websites).

## The idea: strict by construction

A language model left alone will mix sources with what it remembers. Clear Source doesn't trust the model to be
strict; the app enforces it:

1. The **app** runs the searches, not the model.
2. The model reads one source at a time and may only copy passages from it. The app keeps a passage only when it
   appears **word for word** in that source (`verify.find_quote`).
3. The answer is written from those passages only.
4. The checker compares every quote in the answer with the sources again and flags anything that doesn't match, and
   every claim without a `[n]`.
5. When nothing useful is found, the app answers "no reliable evidence found" itself, without asking the model.

## The flow (what you see in the trail)

```
question ──► 1 Understand ──► 2 Search ──► 3 Read one by one ──► (too little? new words, round 2) ──► 4 Write ──► check
              (plan call)      (app)        (one call per source)                                       (answer call)
```

1. **Understand** (thinking): one model call (`PLAN_PROMPT`) turns the question into a plan: a one-line summary and
   up to 7 searches: 2–4 PubMed searches in English with MeSH headings, and 1–3 Dutch sites in Dutch. Medicine
   questions always include the Farmacotherapeutisch Kompas.
2. **Search** (searching): the app runs the plan.
   - PubMed: the MeSH headings are first checked against PubMed's MeSH database (made-up ones are dropped), then
     the search runs with the profile's filters (study types, years, languages). Fewer than 3 hits → the same search
     once more without filters and MeSH ("broader").
   - Sites: a site-restricted search through SearXNG, then the top 2 pages are opened. PubMed Central links are
     read as full text. Bot-check pages ("Just a moment…") are refused, never bypassed.
   - All PubMed / MeSH / PMC calls go through `tools.ncbi_get`: at most ~3 per second (10 with an `NCBI_API_KEY`),
     retried on "429 Too Many Requests".
3. **Read** (reading): every new source gets its own model call (`EXTRACT_PROMPT`): "copy up to 4 passages that help
   answer the question". Only verbatim passages count; a source without one is "not relevant".
4. **Round 2**: fewer than 3 useful sources → one more model call (`FOLLOWUP_PROMPT`) plans new searches with
   other words (synonyms, Dutch and English, broader terms, other sites); steps 2–3 run again.
5. **Write** (writing): the useful sources (max 10, guidelines first, then meta-analyses / reviews, trials, the
   rest) and their passages go to the answer call (`RULES`): a long answer in the question's language under fixed
   headings (Kort antwoord, Nederlandse richtlijnen, Onderzoek, Dosering en bijzondere groepen, Sterkte van het
   bewijs), built from quotes as `> "exact words" [n]` with an explanation under each.
6. **Check**: `verify.check` marks each quote ✓ or ⚠ and lists uncited claims. The short answer at the top, lead-ins
   ending in ":" and sentences about what the sources don't cover are not counted as claims.

## Sources

Default sources (`profiles.SEED`, restorable with "↺ Restore Dutch defaults" on the sources page): PubMed,
Richtlijnendatabase, Farmacotherapeutisch Kompas, Kinderformularium, RIVM, Lareb, Geneesmiddelenbulletin,
JGZ-richtlijnen (youth health care), Palliaweb (palliative care) and Thuisarts. This follows what EvidenceHunt
searches (FMS guidelines, NHG, the Kompas, NICE, PubMed) as far as the sites can be read: NHG's standards sit behind
a bot check (we don't get around that) and NICE and GGZ-standaarden load their text with JavaScript, so those three
are not included. Thuisarts is the readable route to NHG advice.
Users can add their own websites and documents; files attached to a message are read first, as sources (§16.2).

## Limits worth knowing

- **Time**: an answer is ~10 model calls and takes 1–3 minutes with an idle GPU; much longer while an agent loop runs
  (every call queues behind the agent). It isn't stuck.
- **Search quality decides the answer**: on topics where PubMed's best matches are off-topic (for example "seed oils"
  returns dry-eye and skin-cream studies) most sources are correctly rejected and the answer is short or "no evidence".
- **Abstracts, not full papers**: PubMed gives abstracts; only PubMed Central articles are read in full, and
  each source is cut at 8 000 characters.
- **The checker is literal**: a correct paraphrase without quote marks and `[n]` is flagged as uncited, and a quote
  translated into Dutch is flagged as changed. The prompt tells the model to quote in the source's language.

## Tuning knobs (`research.py`)

`MAX_SEARCHES` 7 · `PUBMED_N` 8 hits per PubMed search · `PAGES_PER_SEARCH` 2 · `MAX_SOURCES` 20 candidates ·
`MIN_FOUND` 3 (else broaden) · `MIN_USED` 3 (else round 2) · `MAX_ROUNDS` 2 · `MAX_USED` 10 · `MAX_PASSAGES` 4 ·
`SOURCE_CHARS` 8000. The prompts (`PLAN_PROMPT`, `FOLLOWUP_PROMPT`, `EXTRACT_PROMPT`, `RULES`) sit at the top of the file.

## Testing it for real

The tests use a fake model. To try the real one without the browser, run the flow inside the Dewdrop pod with a
throwaway database (nothing is stored in the app):

```
kubectl --context homelab -n dewdrop exec -i deploy/dewdrop -- python3 - < probe.py
```

where `probe.py` builds `profiles.config` on a temp `db.DB(...)`, runs `research.investigate(...)` and prints the
trail, the used sources and `verify.check` of the answer.
