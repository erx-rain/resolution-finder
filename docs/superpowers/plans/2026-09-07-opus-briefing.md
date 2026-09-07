# Briefing for Opus: corroboration rollout results, open bugs, structural gaps

## -1. Future-phase item — the eventual target is niche markets with NO Polymarket equivalent

User's explicit statement (2026-09-07, dictated, self-corrected after an
initial garbled version): the eventual real target use case is **niche
markets that are NOT on Polymarket at all** — narrow questions (e.g. "how
many yellow cards will a player get") with no existing Polymarket market
to piggyback resolution off of. Separately, there is already an existing
system that watches Polymarket directly: when a market closes there, the
mirrored market on the user's side closes using THAT signal — this tool
was never meant to compete with resolving markets Polymarket itself
already resolves.

**Sequencing, stated explicitly — this is NOT an urgent correction to
current work.** Broad/mainstream-market testing (everything pulled and
evaluated this session — the original 30, +12, +55) is the CORRECT
current phase. Niche-market testing is an explicit LATER phase: "after
we get to a point where we say 'alright, this is good' [on broad
coverage], we should try and see how our model does with more niche
subjects." (An earlier draft of this section overstated this as an
urgent blocker undercutting the whole eval methodology — corrected here
after the user clarified; don't read anything below this note as
implying the current mainstream-market eval work should stop or is
wrong.)

**What this means for later:** once broad-market performance is judged
solid, deliberately pull a batch of narrow/niche markets with no
Polymarket equivalent — harder to source via `pull_test_batch.py`
specifically, since it's built on Polymarket's own search API, so a
different sourcing approach will likely be needed for genuinely
non-Polymarket niche markets — and re-measure specifically against
those. Retrieval breadth and the corroboration bar were both tuned
against mainstream-market coverage density this session and may behave
differently (likely harder to satisfy 2+ independent domains) on a
low-coverage niche market; worth keeping in mind as a known unknown,
not something to solve now.

**User-acknowledged difficulty:** "it'll be hard to actually measure
niche-ness and actually come up with markets" — there's no simple
metric; low Polymarket volume/liquidity is only a rough proxy, not the
real signal (the real target has no Polymarket listing at all).
Concrete example patterns given: (1) player-level individual stat props
("this player will get a yellow card"), and (2) narrower cross-entity
subset framings of a mainstream event ("will a CANADIAN team win the
Stanley Cup" rather than "who wins the Stanley Cup"). Pattern (2) is
already represented once in the current eval set —
`will-a-canadian-team-win-nhl-stanley-cup-782` — a real anchor example
for sourcing more like it later.

**Reframe, same conversation — this is the actual operative strategy:**
NOT "go hunt for niche markets specifically" — instead, "cover all
market TYPES/shapes comprehensively, and niche markets fall into place
naturally as a side effect," since a niche market is usually a
mainstream shape with less news coverage, not a fundamentally different
kind of question. Concrete edge case the user raised, illustrating why
comprehensive-shape coverage is genuinely hard: a "will a Canadian team
win X" market gets ambiguous when a Canadian-born player plays for a
DIFFERENT country's national team — is that player's team "Canadian"
for the market's purposes? Current entity-matching has no concept of
"represents nation X" vs. "is nationality X but plays for someone
else" — it would just pattern-match the literal word "Canadian" in
evidence text. **This is explicitly why the user wants AI/LLM reasoning
available in some form** — this class of nuanced semantic judgment is
exactly what keyword/NLI-entailment logic struggles with. This connects
directly to the local-LLM-final-gate idea raised earlier the same
session (deliberately deferred behind corroboration) — treat this
nationality-ambiguity case as a concrete test case for whether/how that
idea should be revisited, not a generic "just add an LLM somewhere" ask.

## 0. HEADLINE FINDING — the embedding model silently truncates long descriptions at 256 tokens

Confirmed live (2026-09-07): `EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"`
(`resolution_finder/config.py`) has `max_seq_length = 256`. `sentence-
transformers` truncates silently past that — no exception, just an
easy-to-miss debug warning. Checked real token counts for `title +
description` across the current dataset: **13 markets exceed 256
tokens**, worst case `will-typhoon-dolphin-be-a-very-strong-typhoon...`
at **736 tokens (only the first ~35% is ever embedded)**. Others over
the limit: both FDA approval markets, SPCX, XAUUSD, several SCOTUS
markets, Todd Blanche, the Odyssey box-office market, a couple of the
newly-pulled tariff/SCOTUS markets.

**Why this matters more than it might look:** this hits
`relevance_ranker.py`'s query embedding (`f"{market.title} {market.
description}"`), which decides which candidate articles even clear
`SIMILARITY_THRESHOLD=0.35` to become candidates at all. It does NOT
affect keyword/regex matching or entity extraction (those read the raw
string, no model involved) — only embedding-based ranking. Given this
session's own repeated finding that a market's REAL resolution criteria
is often in the back half of a long description (the "Note:" preamble
bug, Sanofi/Viridian's exclusion lists, Todd Blanche's bracket language,
the (HIGH)/(LOW) price-window markers) — exactly the content most likely
to sit past token 256 — a genuinely relevant article can plausibly score
BELOW threshold and never surface as a candidate purely because the
query embedding was built from a truncated, less-specific slice. This
is mechanistically real, not yet proven to be the direct cause of any
one specific wrong/unresolved verdict in the log — worth Opus deciding
whether to chase down specific cases or treat this as a structural fix
regardless (options include: chunk the description and take max
similarity across chunks; keep the full description for keyword-based
retrieval and only use a bounded slice for embedding ranking; a longer-
context embedding model at some compute cost). NOT fixed this session.

**Scope is bigger than the market side alone (found in follow-up,
2026-09-07, prompted by a direct user question — worth checking for
this class of bug elsewhere before assuming it's contained):**
`grep -rn "\.encode(" resolution_finder/*.py` surfaces the SAME
256-token limit hit on:
- **`relevance_ranker.py` also embeds the full ARTICLE text**, not just
  the market query, on both `rank_by_relevance` and `best_below_
  threshold`. 256 tokens ≈ 192 words (confirmed via the real tokenizer)
  — a typical news article runs 400-1000+ words, so this plausibly
  truncates the MAJORITY of articles processed all session, not a
  13-market edge case. This is likely the bigger version of the same
  problem, not a separate minor one.
- **This session's OWN new corroboration wire-duplicate check**
  (`verdict_engine.py`'s `_corroborating_domain_count`, `model.encode
  (by_domain[d].text, ...)`) embeds full article text with the same
  limit — for long articles, the duplicate-vs-independent judgment
  only ever sees the first ~192 words of each side. Could merge two
  genuinely different long articles that happen to share a lead-
  paragraph structure, or fail to merge two truly duplicate long
  articles with different framing before the same wire text.
- `peer_market.py` (`our_full_text`, `peer_text`) has the identical
  `.encode()` pattern — flagged, NOT investigated this session (this
  module was never touched in this session's work at all).
- `_semantic_yes_signal` (verdict_engine.py) is NOT at meaningful risk
  — it embeds one sentence plus a short template, essentially always
  under 256 tokens.

**On predicted impact — explicitly do NOT treat this as measured.**
Asked directly and answered honestly in-session: this most directly
affects which articles clear `SIMILARITY_THRESHOLD` at all, so the more
predictable effect is on the UNRESOLVED count (a real match scores just
below threshold and never becomes a candidate — correctly abstains,
doesn't assert wrong). But it is NOT guaranteed wrong-answer-neutral: a
truncated, less-specific embedding on either side could also make an
irrelevant article look relevant, potentially feeding a keyword match
into a confident wrong verdict downstream. No number should be quoted
for "how much this would help" without an actual before/after
re-ranking measurement, which was not done this session (would have
competed for CPU with a time-boxed eval batch the user was waiting on).

Compiled 2026-09-07 on Sonnet, per explicit user request, ahead of an Opus
planning session to sequence remaining work against a 2-week deadline. Every
claim below is either a git commit, a real eval-log trace, or a live
diagnostic run in this session — nothing here is invented or extrapolated
without a citation.

## 1. What shipped this session (all committed, all tested)

The corroboration rule (2+ independent domains required before a definitive
verdict) was built across all four `decide()` paths, then a follow-on
root-cause pass fixed real bugs the corroboration rollout itself surfaced.
Commit order:

1. `8b74acf` — `_decide_binary` requires 2+ independent domains (phase 1)
2. `5037729` — fix: "one shy of"/"one short of" near-miss phrasing
3. `98f5934` — `_decide_multi_outcome`, same bar (phase 2)
4. `c2b6713` — real-data measurement after phase 2
5. `8e20d2a` — `_decide_numeric_threshold`, SYMMETRIC (both YES and NO gated) (phase 3)
6. `933b38e` — `_decide_date_thresholds` (phase 4, final)
7. `fe142d9` — fix: conflict abstention now requires BOTH sides corroborated
   (root cause: was blocking 8/21 unresolved markets on single-source noise)
8. `8b15d96` — docs: 42-market structural audit; scope-corrected the crypto price gap
9. `9334233` — fix: aggregate field-wide stats + pre-election polling misreads
10. `708be7f` — fix: "passed by Congress" bicameral-bill phrasing
11. `6fa873d` — fix: vote-share GROWTH language wrongly read as a win

267 tests pass as of the last commit. Full test suite: `pytest tests/ -q`.

## 2. Eval history trend (real runs, `data/eval_history.jsonl`)

| timestamp (UTC) | correct | wrong | unresolved | no_gt | note |
|---|---|---|---|---|---|
| 09-02 09:38 | 11 | 3 | 12 | 4 | pre-corroboration baseline (30 markets) |
| 09-02 11:32 | 6 | 4 | 16 | 4 | phase 1+2 landed, not yet root-caused |
| 09-02 13:31 | 9 | 1 | 16 | 4 | phase 2 fixed (Guardians, EPL misreads) |
| 09-02 14:25 | 5 | **0** | 21 | 4 | all 4 phases landed (30 markets) |
| 09-07 08:29 | 8 | 2 | 28 | 4 | **42 markets** (12 new merged); conflict fix landed; 2 NEW bugs found |
| 09-07 09:24 | 7 | 1 | 30 | 4 | fixed bug #1 (aggregate stat); bug #2 (polling) still open |
| 09-07 09:31 | — | — | — | — | targeted Germany-only re-check after fixing bug #2 + a 3rd bug (vote-share growth) found on the SAME market: now UNCLEAR, not wrong |

**Read on this trend:** `wrong` is the metric the user has explicitly said
matters most ("minimize wrong, not unresolved — a human reviews the
monitor"). It went 3/4 → 0 → 2 (new markets + new bugs) → 1 → 0 (confirmed
by targeted re-check, not yet by a full re-run). `unresolved` has grown
substantially (12 → 30) as a direct, accepted consequence of the
corroboration bar. **A fresh full 42-market run has NOT been done since the
last 2 fixes landed** — the next full-batch number is unverified, only the
single-market Germany re-check is confirmed post-fix.

## 3. Real bugs found and FIXED this session (all with root cause + test)

See commits 2, 7, 9, 10, 11 above for full narrative. Short form:
- Numeric "one shy of the record" near-miss phrasing wrongly read as YES.
- Conflict abstention fired on ANY contradicting match, even single-source
  noise (Ipswich Town veto-ing a corroborated Liverpool). Fixed to require
  both sides corroborated.
- Aggregate tournament-wide stat ("1,039 players... score 308 goals")
  misread as a single player's tally.
- Pre-election MRP polling projection misread as the actual result.
- "AfD doubled its share of votes" (true, but describes growth not victory)
  misread as a win.
- "Bill Passed by Congress" (no chamber named) didn't match any existing
  keyword phrase.

## 4. Confirmed NOT bugs — correctly cautious (live-diagnosed this session)

Using a new diagnostic (`scratchpad/full_text_probe.py`, still in the repo)
that fetches REAL live evidence and prints full untruncated text + gate
results per sentence — this matters because `run_eval.py`'s own console log
only prints a 160-char snippet, which caused at least one wasted diagnostic
pass earlier in this session (see appendix).

- **`nba-playoffs-who-will-win-series-lakers-vs-rockets`**: the CORRECT
  evidence is found and correctly verified (`_verify_winner_candidate`
  returns True for Lakers, elimination True for Rockets) — but both
  confirming sentences come from the SAME domain (Sky Sports), so
  corroboration correctly withholds a verdict. **This is a retrieval-
  breadth gap, not a decide()-logic bug** — the fix would be finding a
  SECOND independent source, not changing any verdict logic.
- **`will-man-city-win-the-premier-league`**: every retrieved sentence is
  pre-season preview/odds-market language (hedge-flagged correctly), no
  actual season-result evidence surfaced this run. Correctly UNCLEAR.

## 5a. Significant NEW finding — election-type confusion (needs Opus judgment, not a quick fix)

**`which-party-wins-the-most-seats-in-french-election`** (a French
PARLIAMENTARY/legislative election market): the top-ranked retrieved
evidence is a real article about a completely DIFFERENT election — the EU
Parliament election (covering Rima Hassan's controversy) — not the
national parliamentary election this market actually asks about. From
that ONE wrong-election article, the live probe shows `_verify_winner_
candidate` independently returning `won=True` for BOTH "La France
Insoumise" ("secure a notable portion of the vote") AND "National Rally"
("made substantial gains") — neither claim is even about the right
election, let alone about winning the most SEATS (a proportional EU vote
share and a "made gains" framing are both weaker/different claims than
"won the most seats nationally"). This run happened to still end UNCLEAR
(not wrong) because neither reached 2-domain corroboration, but the
underlying evidence-relevance failure is real and would not be caught by
adding more sources of the SAME confusion.

This is the same SHAPE of problem the existing `_sentence_mentions_
conflicting_competition` guard already solves for sports (a market about
one tournament shouldn't accept evidence about a different one), but
**no equivalent guard exists for political elections** — a country can
hold multiple distinct elections (national parliamentary, EU parliament,
municipal, presidential) in overlapping timeframes, and nothing currently
distinguishes them. Worth Opus's judgment: is this worth a general
"election-type" guard (generalizes across many political markets, real
architecture work), or narrower per-market disambiguation? Not attempted
this session — flagged, not fixed.

## 5b. NOT YET diagnosed (ran out of scope for this pass, listed for Opus/Sonnet to pick up)

Still in the `NO_MATCH` bucket from the last full run, not yet probed with
the live full-text tool:
- `will-messi-win-ballon-dor-23`
- `trump-found-guilty-in-hush-money-case-before-election-day`
- `egypt-presidential-election-will-abdel-fattah-el-sisi-win` (a prior
  diagnosis attempt on this one was INVALIDATED — see appendix, it was
  built on a truncated/misremembered sentence, not real run data)
- `congress-passes-epstein-disclosure-billresolution-in-2025` (same
  invalidated-diagnosis caveat)

Confirmed correctly-cautious, not bugs, no action needed:
- `supreme-court-vacancy-in-2024`, `will-a-canadian-team-win-nhl-stanley-cup-782`,
  `will-the-senate-confirm-ed-martin-before-july`,
  `will-trump-try-to-fire-powell-as-fed-board-member...` — all confirmed by
  their log snippets to be genuine absence-of-evidence or off-topic
  retrieval, not a matching failure.

**A genuinely different, unresolved kind of gap:** `will-aston-martin-beat-mercedes-in-the-2023-f1-season`'s
top evidence is a markdown TABLE of F1 standings embedded in article text,
not prose. No sentence-based guard can read a table. This needs actual
table parsing — a different kind of fix than a keyword/hedge addition, not
attempted this session. Worth Opus's judgment on whether it's worth
building for one market or logging as a known content-shape limitation.

## 6. Structural gaps (full detail: `docs/superpowers/plans/2026-08-25-unsupported-market-types.md`)

Six tracked, re-scoped this session:
1. **Bracketed vote-count markets** (Todd Blanche) — CONFIRMED via direct
   API pull the real market has options like discrete vote-count brackets.
   Real sub-project: new market representation + new decide path.
2. "Between X and Y" range markets — documented gap, still unconfirmed
   against a real market.
3. Multi-category classification markets — undiagnosed.
4. **Crypto/finance price-API gap — RE-SCOPED this session.** Not just the
   2 window-shaped markets (SPCX, XAUUSD) — Bitcoin and Ethereum ALSO
   resolve on one exact Binance candle close, exactly as unanswerable from
   news text, just currently unguarded. Real sub-project; Binance has a
   free public `klines` REST endpoint that's literally the market's own
   named resolution source for BTC/ETH. XAUUSD is Pyth-sourced, a
   different integration.
5. Box-office weekend-gross (Odyssey) — same shape as #4, no public API
   for the-numbers.com, likely needs scraping (see ToS-risk memory).
6. Multi-team "qualifies" markets (EPL Champions League) — needs the real
   Polymarket resolution rule read before any fix is designable.

**Audit sweep (`8b15d96`)** ran a heuristic pattern check over all 42
current markets — found nothing new beyond the above 6; two flags were
false positives (checked by reading the real description, not just the
regex hit).

**This round's market search (see §7) also found nothing that looks like
a genuinely new market TYPE** — margin-of-victory brackets are the same
shape as gap #1; "what will Trump say/post" multi-option word-guessing
markets are the same excluded shape already filtered out (not resolvable
from news reporting at all, by design).

## 6a. Explicit user request: review the retrieval/networking layer for anomalies

User asked directly for Opus to review `evidence_retriever.py` /
`article_extractor.py` (the HTTP-fetching layer) for anomalies and
whether any steps should change — not investigated in depth this
session, but one concrete, recurring anomaly was already visible in
every eval log all session and is worth starting from:

**Bing News RSS fails its own fetch-health check on 17-24% of markets,
consistently, across every run this session** (7/33 in the in-progress
97-market run so far, 10/42 and 7/42 in two earlier runs — a stable
rate, not a fluke). The warning is `Bing News RSS fetch may have failed
... (status=None, bozo=True)`, raised by `_warn_if_feed_fetch_failed`
in `evidence_retriever.py`. **Plausible root cause, not yet confirmed:**
`search_bing_news_rss` (evidence_retriever.py:153) calls `feedparser.
parse(url)` with no custom headers, unlike other fetches in this same
file that explicitly spoof a browser User-Agent
(`headers={"User-Agent": "Mozilla/5.0"}`) — `status=None` is consistent
with Bing soft-blocking or rate-limiting feedparser's default UA rather
than a genuine network failure. Worth Opus deciding: (a) confirm the
UA-spoofing theory directly, (b) decide whether Bing Tier 2 is
contributing enough real evidence to be worth keeping at all given this
failure rate, since it exists specifically as a Google-News-RSS
`site:`-scoping workaround (see the function's own docstring) — if it's
frequently dead weight, that's retrieval latency being spent for
nothing on a fifth to a quarter of all markets.

## 7. New market candidates found, NOT yet merged (per user instruction: verify before merging)

126 additional real, resolved markets pulled verbatim from Polymarket's
public API this session (`scratchpad/supported_candidates.json`), already
shape-filtered to exclude known-unsupported types. Categories represented:
2024 presidential debate events (including some novelty "bingo"-style
behavioral markets — lower substantive value, flagged for the user to
decide whether to include), multiple 2024 special elections (House/Senate),
several 2025-26 SCOTUS rulings, 2 impeachment markets. Liquidity/volume was
NOT used as a filter criterion in this pass (would need a per-market API
call not currently in `pull_test_batch.py`) — the API does expose `volume`/
`liquidity` fields (confirmed live, see appendix) if that's worth adding
before merging.

**Not merged into `data/markets.json` yet** — per user instruction, new
market types get flagged for review first, and per standing convention
`data/markets.json` changes get a deliberate merge step, not an automatic
one.

## Appendix: process notes worth knowing before continuing this work

- `run_eval.py`'s console log truncates article text to 160 characters
  (`run_eval.py:164`). Diagnosing a `NO_MATCH` from that log alone risks
  testing against a misremembered "complete" sentence instead of the real
  one — this actually happened once this session (an Epstein-disclosure
  diagnosis had to be thrown out). `scratchpad/full_text_probe.py` (live,
  read-only, reuses the real pipeline) is the correct tool for this now.
- Corroboration's wire-duplicate-collapse check compares FULL article text
  between two same-option sources, not just the matched sentence — this
  matters if extending corroboration logic further; comparing only the
  matched sentence was tried first and was too aggressive (two independent
  outlets confirming the same simple fact in one sentence each measured
  0.978 similarity, indistinguishable from real syndication).

## 8. Final 97-market batch result (completed after this doc was mostly written)

`correct=18 wrong=1 unresolved=74 no_ground_truth=4`. The single wrong,
`womens-march-madness-iowa-vs-lsu` (truth=Iowa, got=YES for LSU) — **now
investigated and fixed** (commit `0eb9a2f`, 268 tests pass): a "last
year's title game" reference (LSU won the PRIOR year's championship)
was misread as confirming LSU won the CURRENT Elite Eight game. New
guard `_sentence_mentions_last_year_reference`, deliberately
unconditional on year/close_date info being available (this market had
neither) — see the commit for full reasoning. Re-running this specific
market after the fix would be the natural verification step, not done
in this session (time-boxed). `unresolved`
is very high (74/93 with ground truth) on this batch — consistent with
most of the +55 newly-merged markets being narrow single-game/tournament
questions with thinner news coverage than the original 30, which is
itself a small, accidental preview of the niche-market coverage
question in section -1 above, worth Opus noting as a data point even
though it wasn't deliberately designed as a niche-market test.

## 9. Post-fix live re-verification (Iowa-LSU)

Re-ran the specific market against live retrieval after the "last
year's" fix: now correctly `UNRESOLVED` (was `WRONG`), blocked by
corroboration (single source). One residual soft signal noted for
completeness, not chased further: the neutral, no-stated-winner
sentence "Iowa and LSU meet again, this time in Elite Eight" alone
still gets read as a weak LSU-win candidate by `_verify_winner_
candidate` (unclear why LSU specifically and not Iowa -- possibly
second-named-team bias in this specific sentence structure, not
investigated). This never surfaces as a wrong answer on its own since
corroboration requires a second independent domain, which this signal
is unlikely to reliably get -- but it's a softer version of the same
underlying pattern (matchup-announcement language misread as a result
claim) worth Opus knowing about if a similar case surfaces elsewhere.
