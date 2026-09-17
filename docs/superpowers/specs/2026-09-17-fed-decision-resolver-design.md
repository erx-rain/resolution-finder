# Fed decision resolver: design (2026-09-17)

Approved by the user 2026-09-17. Implementation plan:
`docs/superpowers/plans/2026-09-17-fed-decision-resolver.md`.

## Problem

14 real Fed-decision markets in `data/markets.json` score 0/14 through
the news pipeline (fresh-batch eval, 2026-09-17). Root cause is
documented in `docs/superpowers/plans/2026-08-25-unsupported-market-types.md`
item 7: the confirming headline is retrieved, but `_winner_hypotheses`
asks the NLI model a malformed question ("25 bps decrease has won Fed
decision in December?."), scoring 0.337 against the 0.85 bar.

These markets don't need news at all. Their own descriptions name the
resolution source -- the FOMC statement, and the Fed's rate table --
both public pages on federalreserve.gov (US government work, free for
any use, including commercial). A feasibility probe (throwaway script,
not committed) read those pages and got **14/14 correct** against
ground truth.

Goal priority (user, standing): maximize correct and resolved; wrong is
a hard constraint that must not grow. Every rule below that can't be
satisfied cleanly ends in UNCLEAR, never a guess.

## What the real markets say (all 14 read in full)

Two description shapes:

**Full spec (11 markets)**, wording identical apart from "fund"/"funds":
- "defined in this market by the upper bound of the target federal
  funds range"
- "resolve to the amount of basis points the upper bound ... is changed
  by versus the level it was prior to the Federal Reserve's December
  2025 meeting"
- "the change will be rounded up to the nearest 25 ... (e.g. if there's
  a cut/increase of 12.5 bps it will be considered to be 25 bps)"
- "the FOMC's statement after its meeting scheduled for December 9 - 10,
  2025" (also written "April 28-29, 2026") -- explicit meeting dates
- rate table at federalreserve.gov/monetarypolicy/openmarket.htm named
  as a second source
- "If no statement is released by the end date of the next scheduled
  meeting, this market will resolve to the 'No change' bracket."

**One-liners (3 markets)**, no rules: "This is a market on predictions
for the Federal Reserve's interest rates in January 2025." /
"...decision for December 2024."

Real option sets (verbatim):
- `50+ bps decrease | 25 bps decrease | No change | 25+ bps increase`
- `50+ bps decrease | 25 bps decrease | No change | 25 bps increase | 50+ bps increase`
- `75+ bps decrease | 50 bps decrease | 25 bps decrease | No Change | 25+ bps increase`
- `75+ bps decrease | 50 bps decrease | 25 bps decrease | No Change | 25+ bps increase | Other`

Real titles: "Fed decision in December?", "Fed Decision in June?".
Not in scope: "How many dissent at the next Fed meeting?" (options
`0..4+`) -- same statement answers it, noted as a follow-up.

## What the real Fed pages look like (checked live)

- **Calendar** `monetarypolicy/fomccalendars.htm`: one panel per year
  (currently 2021-2027), `<h4>...YYYY FOMC Meetings</h4>`. Each meeting
  row has `fomc-meeting__month` (e.g. `December`, can be two months like
  `April/May`), `fomc-meeting__date` (`9-10*`, `28-29`, or non-meeting
  entries like `22 (notation vote)`), and -- only once released -- a
  statement link `/newsevents/pressreleases/monetaryYYYYMMDDa.htm`
  (date = decision day). Years before 2021 live on
  `fomchistoricalYYYY.htm` (out of scope; those markets -> UNCLEAR).
- **Statement**: the decision sentence, verbatim examples:
  - "the Committee decided to lower the target range for the federal
    funds rate by 1/4 percentage point to 3-1/2 to 3‑3/4 percent"
    (note: non-breaking hyphen U+2011 inside `3‑3/4`)
  - "decided to maintain the target range for the federal funds rate at
    ..."
  - March 15 2020 emergency cut: "decided to lower the target range for
    the federal funds rate to 0 to 1/4 percent" -- **no "by X"**. The
    same statement's dissent says a member "preferred to reduce the
    target range for the federal funds rate to 1/2 to 3/4 percent" --
    must never be read as the decision.
- **Rate table** `monetarypolicy/openmarket.htm`: one table per year,
  rows `Date | Increase | Decrease | Level (%)`, e.g.
  `December 11 | 0 | 25 | 3.50-3.75`. Only CHANGES are listed (no row
  for a hold), and the row date is the effective date, the day AFTER
  the decision (Dec 9-10 meeting -> Dec 11 row).

## Design

### Contract

New module `resolution_finder/fed_decision.py`:

```python
def resolve_fed_decision(market: Market, fetch: Fetcher) -> Optional[list[Verdict]]
```

- `None` = not a Fed-decision market. Caller continues its normal path;
  behavior for every other market is unchanged.
- A list = this is a Fed market; the caller saves these verdicts and
  **does not** run peer-check or news retrieval for it. The news path
  scored 0/14 and can read forecasts ("expected to cut") as outcomes.

`Fetcher = Callable[[str], str]` returns page HTML or raises
`FetchError`. Injected so tests use saved fixtures and `run_eval.py`
can cache.

New module `resolution_finder/structured_resolvers.py`: an ordered list
of resolvers (just `resolve_fed_decision` today) and
`resolve_structured(market, fetch=http_fetch)` that returns the first
non-None result. This is the plug-in point the price-market handoff
(`docs/superpowers/plans/2026-09-17-price-markets-handoff.md`) refers to.

### Step 1 -- recognize the market (strict)

All must hold, else `None`:
- title matches `^\s*Fed (decision|interest rates?)\b` (case-insensitive)
- description mentions "Federal Reserve" or "FOMC"
- options non-empty, and every option parses as one of:
  `No change` (any case), `N bps decrease|increase`,
  `N+ bps decrease|increase`, or `Other`; at least one non-Other.

### Step 2 -- identify the meeting

In order:
1. Explicit dates in the description: `meeting scheduled for <Month>
   <d1> - <d2>, <YYYY>` (spaces around the dash optional; also handle a
   single day). Gives start and end dates.
2. Else month + year from the description ("...Federal Reserve's
   December 2025 meeting", "...interest rates in January 2025",
   "...decision for December 2024").
3. Else month from the title + year from `close_date`.

Find calendar rows in that year whose month (either half of
`April/May`) matches, whose date text is a day or day range (skip
`notation vote` and other non-meeting entries). If explicit dates were
parsed, the row's days must equal them. Exactly one row must remain,
else UNCLEAR ("could not identify a single FOMC meeting").

If the year isn't on the calendar page -> UNCLEAR.

### Step 3 -- statement published?

Sanity first: the meeting's decision date (last meeting day) must be
within 3 days of `close_date` when `close_date` is set, else UNCLEAR.
Checked before the published check so a wrongly matched meeting can
never hide behind NO_EVIDENCE.

Row has no statement link -> one whole-market verdict
`NO_EVIDENCE` ("FOMC statement not yet published"). Nothing to look at.

Real calendar details (checked 2026-09-17): two-month meetings use
abbreviations (`Apr/May` 30-1, `Jan/Feb` 31-1, `Oct/Nov` 31-1), and the
`22 (notation vote)` entry in August 2025 HAS a statement link -- it is
skipped because its date text isn't a plain day or day range.

The calendar and rate-table pages are served as `text/html` with no
charset, so a plain `requests` decode would fall back to ISO-8859-1 and
mangle the U+2011 hyphens in "3‑3/4". Production fetch decodes as UTF-8
when the header names no charset.

The description's "no statement by the next meeting -> No change"
default is **not** auto-applied: absence of a link is indistinguishable
from a page-layout change we failed to parse. Stays NO_EVIDENCE; a
human handles that (never observed) case.

### Step 4 -- read the decision

Parse only a sentence containing `the Committee decided to` (this is
what excludes dissent text like "preferred to reduce ... to"):
- `decided to (lower|raise) the target range for the federal funds rate
  (by <amount> percentage point(s))? to <low> to <high> percent`
- `decided to maintain the target range for the federal funds rate at
  <low> to <high> percent`

Normalize U+2011 and other dash variants to `-`. Numbers: `3`, `1/4`,
`3-1/2`, `3-3/4`. Exactly one decision sentence must match, else
UNCLEAR. Result: direction (lower/raise/maintain), new upper bound,
optional stated amount.

### Step 5 -- cross-check against the rate table (corroboration)

Parse the rate table into dated rows (year from each table's heading).
- `prior_upper` = upper bound of the latest row dated **before** the
  meeting start date. (An intermeeting change is correctly "the level
  prior to the meeting".)
- Change = `(statement new upper - prior_upper)` in bps. This is the
  description's own definition (upper bound vs prior level) and works
  without a stated "by X".
- Consistency, all required, else UNCLEAR ("Fed statement and rate
  table disagree"):
  - direction matches sign of change (maintain <-> 0)
  - if the statement states an amount, it equals |change|
  - change != 0: a table row dated decision day .. decision day + 2
    exists, its upper bound equals the statement's new upper, and its
    Increase/Decrease column equals |change|
  - change == 0: no table row dated in that window

A table not yet updated after a change -> UNCLEAR until it is (safe
direction).

### Step 6 -- map to an option

- Full-spec markets (description contains "rounded up to the nearest
  25"): round |change| up to the nearest 25 (description rule).
- One-liner markets (no rounding rule stated): |change| must already be
  a multiple of 25, else UNCLEAR.
- Match: `No change` <-> 0; `N bps <dir>` <-> exactly N; `N+ bps <dir>`
  <-> >= N; direction must match. `Other` is never selected.
- Exactly one option must match, else UNCLEAR.

### Output

Matched option -> `YES`; every other option -> `NO` (same convention as
`_decide_multi_outcome`'s confirmed-winner branch). All share:
`confidence=1.0`, `source_type="primary"`, `source_url` = statement URL,
`evidence_snippet` = the decision sentence verbatim (<=280 chars).

UNCLEAR / NO_EVIDENCE verdicts are single whole-market verdicts
(`option=None`) with the reason in `evidence_snippet`.

`FetchError` on any page -> UNCLEAR ("could not fetch <url>").

### Wiring

- `pipeline.run_pipeline(..., structured_resolver=resolve_structured)`,
  injected like `peer_checker`. In `_scan_market`, after
  `check_availability` and before `peer_checker`: if it returns a list,
  save each verdict with `availability` and return.
- Production fetch: `requests.get`, 20s timeout, project User-Agent
  (same string as `evidence_retriever.FEED_USER_AGENT`), no caching
  (standing rule: caching only in run_eval.py).
- `run_eval.py`: before retrieval, call
  `resolve_structured(market, fetch=<cached fetcher>)`. If it returns a
  list, score those verdicts and record `trace.structured_resolver` +
  fetched URLs; skip retrieval. Fetched pages are cached in
  `data/eval_cache.json` under a reserved top-level key
  `"__structured_pages__"` (url -> html); `--live` refetches.

## Testing

Fixtures: real pages saved verbatim under `tests/fixtures/fed/`
(calendar, rate table, statements for Dec 2025 cut with dissents,
Jan 2026 hold, Dec 2024 cut, Mar 15 2020 cut with no "by X"). Negative
cases (table disagreeing, missing link) mutate a real fixture in the
test, with a comment saying so -- no invented pages.

Unit tests cover: recognition (Fed yes; dissent market, CLARITY market,
bad option -> None), meeting identification (explicit dates, month-
year, title+close_date, two-month row, notation-vote skip), decision
parsing (all four real wordings; dissent sentence ignored), table
parsing and cross-check (change, hold, disagreement, not-yet-updated),
option mapping (exact, plus, rounding, one-liner non-multiple, Other,
zero/two matches), outputs (YES/NO set, NO_EVIDENCE, UNCLEAR, fetch
error), pipeline hook (Fed market short-circuits news path; non-Fed
market untouched), eval cache helpers.

Acceptance: full test suite passes; `run_eval` on the 14 Fed markets
gives **correct=14 wrong=0** (probe already did); full-batch eval shows
wrong not increased.

## Out of scope (tracked)

- "How many dissent" markets: parse "Voting against this action
  were/was ..." from the same statement. Cheap follow-up.
- Meetings before 2021 (historical calendar pages).
- Auto-applying the "no statement -> No change" default.
- `_winner_hypotheses` grammar for other outcome-description markets
  (unsupported-market-types.md item 7 stays open for non-Fed markets).
- Crypto/currency/gold/stock markets: see the price-markets handoff.
