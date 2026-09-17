# Unsupported / partially-supported market types — tracking doc

Separate from the main SDD ledger (`.superpowers/sdd/2026-08-10-resolution-finder-scanner/progress.md`)
on purpose, per direct user request: this tracks market *shapes* the
architecture doesn't cleanly handle yet, to review after the current
test-find-fix-retest loop on already-supported types is done. These are
not bugs in the usual sense — they're markets whose real structure
doesn't map onto any of the four `decide()` paths (`_decide_binary`,
`_decide_numeric_threshold`, `_decide_multi_outcome`,
`_decide_date_thresholds`), so no amount of guard-fixing within a path
helps until the market gets routed to the right one, or a new path is
built.

## 1. Bare-number vote-count threshold with no trigger phrase (confirmed real, in current dataset)

`will-50-senators-vote-yea-for-todd-blanche-as-attorney-general` — title
"Will 50 senators vote 'Yea' for Todd Blanche as Attorney General?".
`_extract_threshold_condition`'s patterns all require an explicit trigger
word next to the number ("at least 50", "50 or more") — a bare "50
senators" with no trigger word matches nothing, so this market silently
falls through to `_decide_binary` instead of `_decide_numeric_threshold`.
Confirmed directly: `_extract_threshold_condition` returns `None` for
this market's title.

Real consequence: even if evidence like "the Senate confirmed Blanche
52-46" were found, `_decide_binary` has no way to compare 52 against 50
— it can only match YES via `BINARY_YES_KEYWORDS`/NLI confirmation
language, never an actual vote-count comparison.

**Confirmed (2026-09-02), not just suspected:** re-read this market's
full pulled description directly. It explicitly states "If the
nomination passes unanimously or without a specified vote count, this
market will resolve to the highest bracket. If the nomination is
rejected... resolves to the lowest bracket." This is unambiguous —
the REAL Polymarket market is a bracketed vote-count range (multiple
named brackets, e.g. "45-49", "50-54", "55+"), not a single Yes/No
threshold at all. A bare-number regex fix would be actively wrong here:
it would force this into the single-threshold shape when the real
market has a genuinely different one. `data/markets.json` currently
flattens it to `options: []` with a single `resolved_to: "Yes"` — this
is a `pull_test_batch.py` gap (it doesn't yet know how to represent a
bracketed-range market), not a `verdict_engine.py` one. Needs: (1)
confirm via the live Polymarket API what the real bracket options are
for this specific market, (2) design how `pull_test_batch.py` should
represent a bracketed numeric-range market (a new shape distinct from
both existing multi-outcome-by-name and single-threshold), (3) design
a `_decide_*` path for it. Real sub-project, not a quick regex
addition — re-scoped up from "small fix" after reading the real data.

## 2. "Between X and Y" range markets (documented gap, not yet reproduced)

`_extract_threshold_condition`'s own docstring says: "Only single-
threshold direction, not 'between X and Y' ranges." No market in the
current 18-market set exercises this, so it's undconfirmed against real
data, but it's a named, deliberate gap in the existing code, not
speculation.

## 3. Multi-category classification markets, forced into a binary shape

`will-typhoon-dolphin-be-a-very-strong-typhoon-at-japan-landfall` — the
real classification has 5 categories (Tropical Storm / Severe Tropical
Storm / Typhoon / Very Strong Typhoon / Violent Typhoon), but this pulled
market instance asks about exactly ONE category ("Very Strong Typhoon")
with `options: []`, so it currently resolves via the ordinary binary
path. This works adequately for the single-category question as pulled,
but the architecture has no native concept of "one specific rung of a
labeled multi-category scale" — if a future pulled market asks the full
5-way question in one entry (options = the 5 category names), none of
the four decide functions match that shape (it's not a numeric threshold,
not a recurring winner-take-all competition, not a date).

## 4. Submarket price-history / reopening tracking (already in the main plan doc's Backlog, cross-referenced here)

Real, already-designed-in-outline gap for crypto/finance numeric-
threshold markets: a naive "has the price ever touched $X" check using
incidental news-article mentions doesn't track the market's real
resolution WINDOW, and doesn't handle a submarket that closed once
already touching the threshold, then reopened for a new window that
hasn't. See the main plan doc's "Submarket price-history tracking for
numeric-threshold markets" section for the full writeup (real historical
high/low tracking from a structured price source, reopening semantics) —
not duplicated here, just cross-referenced since it's the same class of
"market type architecture doesn't support" gap as the others on this
page. Deliberately deprioritized behind sports/politics per standing
directive.

**Scope correction (2026-09-02, found during the 14-market audit):**
this item was tracked as covering only the 2 markets with an explicit
"(HIGH)"/"(LOW)" title marker (SPCX, XAUUSD -- window high/low over a
date range). Re-reading `will-the-price-of-ethereum-be-less-than-1400`'s
real description shows the SAME underlying problem on a market with NO
such marker: it resolves on one exact Binance 1-minute-candle close
price at a specific timestamp, not a window -- but that's just as
unanswerable from ordinary news-article text as the window case is; a
news article reporting "ETH trading around $1,150" is not the same
number as "Binance's exact ETH/USDT close at 12:00 ET on this date."
`bitcoin-above-64k-on-august-17-2026` is the same shape. Both currently
get NO guard at all and fall through to ordinary numeric-threshold text
search, which can only ever be coincidentally right, not reliably so.
The real fix (a historical-price API) covers all 4 of these markets,
not just the 2 with the marker -- worth re-scoping the eventual design
to "any market whose resolution source is a named exchange/price feed",
not "any market with a HIGH/LOW window."

## 5. Box-office weekend-gross tracking (confirmed real, in current dataset)

`will-the-odyssey-5th-weekend-box-office-be-at-least-21m` (title asks
about a SPECIFIC weekend's gross for a SPECIFIC film) -- same structural
shape as item 4 (price-history tracking): the real resolution source is
a structured data page (the-numbers.com's own weekend-gross table for
that title), not ordinary news search. Confirmed wrong live
(2026-09-02, corroboration re-verification run): retrieval ranked a
the-numbers.com WEEKEND-PROJECTIONS page highly (0.593 similarity) that
covers OTHER films entirely ("Spider-Man lands fifth straight weekend
win... Coyote vs. Acme won't end up the winner...") and never names
"Odyssey" anywhere in the extracted snippet at all -- the wrong-subject
veto didn't fire because the sentence doesn't name any OTHER
2+-word-capitalized entity either (a projections page reads more like a
list/table than prose), so the number-extraction loop had nothing to
veto on and used a projection number for the wrong film entirely.
Tried-and-rejected in the moment: a general "sentence must positively
name the subject, not just avoid naming a different one" gate for
numeric-threshold markets -- too broad a change to make safely without
checking it against every other numeric-threshold market's legitimate
pronoun/short-reference evidence (the CLARITY-Act-style "no repeated
subject name" case this file already protects elsewhere). Needs the
same real-data-source treatment as item 4, not a text-search guard.

## 6. Multi-team "qualifies" markets, forced into a single-winner shape

`epl-team-to-qualify-for-uefa-champions-league` (real ground truth:
Manchester United) -- title reads like a single-winner market
(`_decide_multi_outcome`'s shape: one option crowned, others
eliminated), but UEFA Champions League qualification from a domestic
league is a multi-team outcome (top 4-5 EPL clubs ALL qualify most
seasons) -- there is no single "the team that qualifies." Confirmed
wrong live (2026-09-02): real evidence about Arsenal's 2026-27
Champions League DRAW fixtures (a team that HAS qualified, just not
necessarily the market's real intended answer) got crowned winner,
eliminating every other option including the real ground truth
(Manchester United) outright. Needs the market's real Polymarket
resolution criteria pulled and read before guessing at a fix -- this
may be a market whose real question is narrower than its title suggests
(e.g. "which team qualifies HIGHEST" or a specific bracket/qualifying
round), which no amount of multi-outcome guard-tuning can address
without knowing the actual rule.

## Audit pass (2026-09-02)

Ran a heuristic text-pattern sweep (bracket language, between-X-and-Y,
named price-feed sources, classification/category resolution, multi-
team-qualify language, cumulative-date shape) over all 42 current
markets (the original 30 minus the 5 already-tracked gaps above, plus
the 12 newly pulled). Two hits beyond the already-tracked gaps:

- `world-series-champion-2025` matched "bracket" -- false positive, its
  description says "playoff bracket" (baseball elimination format), not
  a resolution bracket. No new gap.
- `will-the-price-of-ethereum-be-less-than-1400...` matched "bracket"
  and a named price source -- real finding, folded into item 4 above
  (scope correction) rather than listed as a separate new item.

Everything else the heuristic flagged (mostly "cumulative/multi-date-
option shape" and "qualify" hits) was pattern noise from ordinary
deadline phrasing ("by June 30") or unrelated uses of "qualify" in
prose, not real shape mismatches -- checked by reading each flagged
market's actual description, not just trusting the regex.

This was a heuristic pattern sweep, not a from-scratch structural
re-read of every market's actual resolution logic -- it can only catch
gaps that show up in recognizable phrasing. Worth treating as a first
pass, not a guarantee nothing else is hiding, especially for markets
whose real resolution criteria diverge from their title's plain
English (the same way epl-team-to-qualify's and Todd Blanche's real
shapes only became clear once their full descriptions were read
directly).

## 7. Fed interest-rate decision markets (confirmed real, root cause diagnosed 2026-09-17)

14 real markets pulled from Polymarket (fed-decision-in-january,
fed-decision-in-december, fed-interest-rates-january-2025, etc.),
options like "No change" / "25 bps decrease" / "25 bps increase".
Scored 0/14 (all UNCLEAR) on the full fresh-batch eval run
(2026-09-17) -- not a retrieval problem: for fed-decision-in-december
(truth: 25 bps decrease), the real confirming headline was RETRIEVED
and RANKED (4th, sim=0.579): "Federal Reserve cuts interest rates by
25 basis points, signals 1 cut ahead - Yahoo Finance". It just never
verified.

**Root cause, confirmed empirically, not guessed:**
`_winner_hypotheses` builds `f"{option} has won {market.title}."` --
for this market that's literally *"25 bps decrease has won Fed decision
in December?."*, which is grammatically broken and semantically
nonsensical (an outcome description can't "win" an event the way a
named competitor can -- same class of bug this file's own history
already documents for "is still advancing in {market.title}" against
"The International 2026 Champion"). Scored the real confirming headline
above against this exact hypothesis: **0.337**, far below
`NLI_VERIFICATION_THRESHOLD = 0.85`. The negative hypothesis scored
0.663 instead -- the model isn't confused about the FACT, it's being
asked a malformed question about it.

This generalizes to any multi-outcome market whose options are OUTCOME
DESCRIPTIONS rather than named competitors -- crypto price brackets
share the same grammatical shape ("64,000-66,000" can't "win" a market
either). **Checked, not assumed**: read bitcoin-price-on-june-21-2026's
real cached evidence to see if the SAME mechanism blocks it too -- it
does not, or at least isn't the binding constraint there. Its top-
ranked candidates are page titles like "BTC price on Jun 21, 2026 at
11am EDT - Robinhood" that never state the actual price number in
retrievable text at all, so there is no confirming sentence for a
hypothesis (broken or not) to even be tested against -- that market is
still blocked by the already-tracked "needs a structured price feed"
problem (gap item 4), not by this one. The two gaps are real and
distinct: Fed decisions have plenty of real prose confirming the
outcome, and fail on VERIFYING it (a hypothesis-wording fix, no new
data source needed); crypto brackets fail on FINDING the outcome
in the first place (a data-source fix, no amount of hypothesis
rewording helps). Worth fixing the Fed-decision hypothesis wording on
its own merits, but don't expect it to move crypto brackets too.

## User input captured 2026-09-16, for Opus to design (not designed here)

Two additions to already-tracked gaps, captured verbatim in intent per
the user's explicit instruction to defer planning to the next Opus
session -- nothing below is a design decision, just requirements.

**On item 4 (structured price-feed markets, crypto/gold/currencies):**
the system needs to determine the resolution instrument and threshold
ITSELF -- "get what he should be taking as measurement" -- by reading
BOTH the description and the title, with the user's specific note that
the TITLE may be the more useful field for this market type
specifically ("maybe title because it's most worth it"). Worth flagging
explicitly: this is a narrower, market-type-scoped exception to the
project's general rule that the description is authoritative over the
title (see memory: description-stronger-than-title) -- crypto/price
titles tend to state the ticker and threshold plainly ("Will Bitcoin be
above $64,000 on August 17?"), while the description mostly adds
mechanism detail (which exchange, which candle). Opus should decide
whether/how to special-case this market type rather than applying the
general rule uniformly.

**New: structured sports-data source, sports-market-scoped.** ESPN
proposed by the user as a scrape target specifically for yellow
cards, goals, and match times -- i.e. the same class of gap as the
"Structured sports data adapters" work already outlined in
docs/superpowers/plans/2026-09-08-description-first-strategy.md
(Ordering step 4) and the resolution-availability design doc, now with
a concrete first candidate source. Per-sport schema design (soccer's
yellow/red cards don't apply to the NBA, etc.), ToS/scraping-risk
verification (see memory: scraping-tos-risk-practice -- the existing
discipline throughout source_config.py of a live robots.txt + real
fetch check before adding any source applies here too), and how this
plugs into resolution_spec.py are all open, for Opus.
