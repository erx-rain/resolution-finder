# Opus session agenda (2026-09-16)

Written on Sonnet at the user's explicit request: flag open questions,
flag the plan(s) that need writing, and record the priority reaffirmed
this session before the next Opus planning switch. Nothing below is
designed here -- captured for Opus to design.

## Standing priority, reaffirmed and sharpened (2026-09-16)

User's own words: *"what we want to maximize is correctness and
resolved... I don't know if we could compromise on wrongs... we
shouldn't touch wrong, just try to minimize it whatsoever, shouldn't
risk making it bigger."*

This sharpens, not changes, the priority stated at the start of this
project: **wrong is the hard constraint, never a trade-off variable.**
Correct and resolved (now including the availability/closeability
signal shipped 2026-09-08/09) are the things to maximize, but never at
the cost of increasing wrong -- not even a little, not even to catch
more real cases. Every future design (the crypto adapter, the ESPN
source, named-source-first retrieval, anything else) should be
evaluated against this explicitly: does it risk wrong going up? If yes,
it needs its own corroboration/safety gate before it ships, the same
pattern the 2026-09-08 elimination-corroboration fix just followed
(corroboration first, recall second, measured separately).

Also worth Opus's attention: the user's own framing that a "wrong"
verdict is worse than it looks, because it can mean the system
"took bullshit" -- i.e. confidently asserted from low-quality evidence
instead of correctly abstaining. This argues for treating any FUTURE
wrong finding as a signal to tighten a gate, not just an unlucky miss,
consistent with how every "wrong" case already found this project
(Cleveland Guardians, Iowa/LSU, the Ipswich Town conflict-veto case,
etc.) has been handled.

## Plans that need writing (not yet designed, flagged explicitly)

### 1. Crypto/currency/gold market resolution -- title-first parsing

Captured as a requirement in
docs/superpowers/plans/2026-08-25-unsupported-market-types.md's
"User input captured 2026-09-16" section, but needs an actual written
design, not just a requirements note. Core question: the system should
determine its own resolution instrument and threshold from BOTH title
and description, with the user's specific instruction that title may be
the more useful field for THIS market type -- a narrower exception to
the general description-over-title rule (memory:
description-stronger-than-title), not a replacement for it.

This is the same underlying gap as item 4 in the unsupported-market-
types doc (structured price-feed markets -- Binance/exchange-sourced
exact-candle-close or window-high/low questions) and the crypto price-
lookup design brainstormed earlier this project
(docs/superpowers/plans/2026-08-10-resolution-finder-scanner.md,
"Submarket price-history tracking for numeric-threshold markets").
Real markets to design against: bitcoin-above-64k-on-august-17-2026,
will-spcx-reach-145-in-august-2026, will-xauusd-reach-4400-in-
august-2026, will-the-price-of-ethereum-be-less-than-1400-on-
august-17-2026 -- all in data/markets.json now.

### 2. Structured sports-data source (ESPN proposed)

Also captured 2026-09-16 in the same doc. Needs a design covering:
per-sport schema (yellow/red cards for soccer, not the NBA), the
already-user-specified operational rules from earlier this session
(concluded games only, 30-day rolling window, no live polling, cache
once fetched, manual hard-refresh override -- see git history around
commit 8c0804e/its revert 3e20445 for the exact original phrasing,
reverted only because it was posted to the wrong conversation that day,
not because the requirements were wrong), and a live ToS/robots.txt
verification pass on espn.com specifically (same discipline as every
existing source_config.py addition -- see memory:
scraping-tos-risk-practice) before anything is added.

## Open questions for Opus

**Q1 -- should availability get a formal eval metric?** Right now
`check_availability`'s recall (96%, measured 2026-09-08 via a one-off
scratchpad script) is not part of run_eval.py's own scorecard --
correct/wrong/unresolved still only scores the OUTCOME. Given
availability is now the stated primary product goal, is it worth adding
a first-class availability-precision/recall column to the eval harness
itself, rather than re-deriving it ad hoc each time?

**Q2 -- named-source-first retrieval, scope.** `source_config.py`
already has a working mechanism (`resolve_named_source`, wired into
`evidence_retriever.retrieve_evidence`) that promotes a Bing result to
"primary" when its domain matches a known named source -- but
`TIER1_DOMAINS` only has 3 entries (congress.gov, nobelprize.org,
sec.gov) against real descriptions naming many more (NHL, FDA, Binance,
foreign election commissions, MLB, NCAA, ...). Expanding this one
verified domain at a time matches the file's own established discipline
(every existing entry required a live robots.txt + fetch check first)
and needs no new design -- but is it worth first measuring, across the
full real batch, how many DISTINCT named sources actually appear and
how often `resolve_named_source` currently returns None despite a
source clearly being named, to prioritize which ones are worth adding
first, rather than guessing?

**Q3 -- structural gap priority, now that Bing/headline fix has
landed.** The original Priority 3 (NO_MATCH long tail, 45 cases) from
the 2026-09-07 Opus plan was sized against headline-only evidence --
stale now that real article text is flowing. Worth re-measuring NO_MATCH
specifically before deciding whether it's still the next priority, or
whether the 6 tracked structural gaps (unsupported-market-types.md) or
the crypto/ESPN work above should come first.

**Q4 -- fresh batch, pulled and merged 2026-09-16.** Pulled from
Polymarket (deliberately NOT rain.trade -- everything currently open
there is mirrored on Polymarket already, so a rain.trade pull right now
would not add real diversity over a Polymarket one). 239 real resolved
markets came back from broad sports/politics/currency queries; merging
all of them would have mostly duplicated the existing batch's single-
game-winner shape (80 of the 239 were individual NFL games alone).
Curated a diverse 68-market subset instead (script:
scratchpad/curate_fresh_batch.py, verbatim entries only, nothing
authored) -- `data/markets.json` is now 165 markets (97 -> 165).

Genuinely new shapes this surfaced, not yet run through the engine:

- **Crypto price-BRACKET markets** (Bitcoin/Ethereum, ~19 added) --
  options like `"64,000-66,000"`, `">109k"`, `"84-82k"`. This is the
  SAME shape as gap item 1 (bracketed vote-count ranges,
  will-50-senators-vote-yea), just for crypto prices instead of votes --
  real confirmation the bracketed-numeric-range gap generalizes across
  domains, not a one-off. Directly relevant to Plan #1 above (crypto
  title/description parsing) -- these need designing together, not as
  two separate problems.
- **Fed interest-rate decision markets** (14 added) -- multi-outcome,
  options like "No change"/"25 bps decrease". A market type not
  previously in the batch at all.
- **A real multi-WINNER market**: `premier-league-top-4-finishers`
  (12 options, truth "Arsenal" -- but 4 teams actually qualify each
  season). Same structural shape as the already-tracked
  epl-team-to-qualify-for-uefa-champions-league gap (item 6) -- a
  second real instance, not a one-off.
- **`electoral-college-margin-of-victory...`** and
  `margin-in-argentina-presidential-election` -- a THIRD variant of the
  bracket-range shape (election-margin brackets), reinforcing the same
  point.
- Large-field races (20-31 options: Grand Prix winners, golf) to
  stress-test winner-crowning/conflict logic on option counts bigger
  than the current batch's largest (32, 2026-nhl-stanley-cup-champion).
- International elections (Taiwan, Argentina, Indonesia, Slovakia,
  Senegal) -- new named-source targets (foreign election commissions),
  relevant to Q2 above.
- `vail-resorts-mtn-up-or-down-after-earnings` -- a new "stock move
  after earnings" binary shape.

**Update 2026-09-17 -- scored.** Ran the full 68-market fresh batch:

    correct=12  wrong=0  unresolved=56

(after a real bug fix below -- see the wrong=2 note). Confirms, on
real data: all 14 Fed-decision markets unresolved (0%), all 13
Bitcoin/Ethereum bracket markets unresolved (0%) -- exactly the
predicted gaps, no surprises. International elections: 5/10 correct,
0 wrong -- solid on a genuinely new geography. No crash, no new
structural surprises beyond what was already flagged above.

## New real bug found and fixed (2026-09-17): sentence-splitter breaks on "vs."

Running the fresh batch surfaced 2 wrong verdicts (nfl-wsh-bal-2023-08-21,
nfl-lac-no-2023-08-20), both resolving to the LOSING team. Root cause:
`SENTENCE_SPLIT_PATTERN` split headlines shaped "Team A vs. Team B -
Final Score - ..." right at "vs." (lowercase, so the existing `[A-Z]\.`
guard built for "U.S."/"H.R." never covered it) -- the resulting
fragment named only ONE team, and the semantic fallback misread it as a
specific winner claim for a headline that never actually states a
winner at all. Fixed (commit 602d3cd): added a case-insensitive,
scoped lookbehind excluding "vs." specifically. 3 new tests (both real
headlines verbatim, a check that real sentence boundaries near "vs."
text still split, and an end-to-end decide() reproduction). Re-ran both
markets: wrong -> UNCLEAR (honestly correct -- neither headline states
a winner). Full suite: 310 passed. This is why the reported fresh-batch
total above is wrong=0, not wrong=2.

**Open question this raises for Opus (Q5):** the SAME pattern's own
comment already documents an "accepted" residual gap for OTHER
lowercase-abbreviation-like sentence-enders -- specifically "Sen.",
"Rep.", "Jan." (Title-case, so technically different from "vs.", but
the same failure MECHANISM: an abbreviation containing a period gets
misread as a sentence boundary). "vs." was escalated from theoretical
to a confirmed real wrong verdict. Worth deciding: audit for other
live abbreviation-split risks proactively (a systematic pass), or wait
for the next one to surface live and fix reactively as this project has
consistently done so far? Given the standing priority that wrong should
never be allowed to grow, a proactive audit may be worth the small
upfront cost -- but "vs." was uniquely dangerous because it's the
single most common word in this project's own sports-headline
vertical; "Sen."/"Rep."/"Jan." may not carry the same real-world
frequency, worth measuring before deciding to invest here.

## Not open questions (already decided, noted so Opus doesn't re-litigate)

- Google News wrapper decoding vs. alternative sources: user chose
  "prefer alternative sources" (GDELT, publisher RSS) over decoding
  Google's wrapper format, 2026-09-08.
- Elimination corroboration ordering (corroborate first, broaden gate
  second): implemented and measured 2026-09-16, wrong=0 on the same
  46-market subset it was 1 before, correct improved 12->15. Real
  caveat: the Bing UA fix landed in the same window, so this comparison
  reflects everything shipped since 09-07, not an isolated A/B on the
  elimination fix alone.
