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
language, never an actual vote-count comparison. The market's own
description also describes multiple resolution BRACKETS ("if the
nomination passes unanimously... resolve to highest bracket"), meaning
the real Polymarket question likely has richer structure (a bracketed
vote-count range) than a single Yes/No threshold at all — worth
re-checking whether `options` should be non-empty for this market
(currently `[]` in `data/markets.json`) before designing a fix.

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

## Not yet checked

The remaining 14 markets in `data/markets.json` were not individually
re-audited for shape-mismatch during this pass — this list reflects what
surfaced during today's test-find-fix-retest loop, not an exhaustive
audit. Worth a dedicated pass once the current loop concludes.
