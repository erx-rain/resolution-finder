# Handoff: Congress/Senate official-record resolver (2026-09-17)

**Status: designed at outline level, NOT built, no spec or implementation
plan written.** User's instruction 2026-09-17: record the design as-is
for whoever continues the project; don't go deeper now. The user will get
the API key later.

Why this is next (from 2026-09-17-remaining-work-triage.md): 6 real
markets with known answers resolve 0/6 through the news path today; the
data is public-domain government records and mostly deterministic.

Standing priority: wrong verdicts are the hard constraint. Anything that
can't be checked cleanly ends in UNCLEAR.

## Real markets it targets (all in data/markets.json)

| Market | Truth | Shape |
|---|---|---|
| will-50-senators-vote-yea-for-todd-blanche-as-attorney-general-20260608212508880 | Yes | Nomination (vote count) |
| will-the-senate-confirm-ed-martin-before-july | No | Nomination |
| congress-passes-iran-war-powers-resolution-by-june-30 | Yes | Both chambers pass a measure on a topic |
| congress-passes-epstein-disclosure-billresolution-in-2025 | Yes | Both chambers pass a measure on a topic |
| joe-biden-impeached-before-2024-election | No | House impeachment |
| judge-mcconnell-impeached-before-april | No | House impeachment |
| clarity-act-2026 | (none yet) | Named bill becomes law |

Things found when reading these descriptions in full:
- The Blanche market is really a vote-count question: it "will resolve to
  the number of senators who vote Yea", with brackets, although its
  `options` list is empty in data/markets.json.
- The Epstein bill (H.R. 4405) passed the Senate by unanimous consent, with
  no roll-call vote. Roll-call records alone can't see it; bill action
  histories can.
- Three markets resolve No because something didn't happen. Proving a
  negative needs a complete official record, not "found nothing".

## Data sources (checked live 2026-09-17)

- **Congress.gov API v3** (`https://api.congress.gov/v3/...`): has
  nominations with full action text, bill lists per Congress and type,
  bill action histories with machine-readable action codes, and public
  laws. Public-domain government data.
  - **Needs a free api.data.gov key** for real use (signup at
    api.data.gov/signup; the user must do this -- Claude can't create
    accounts). 5,000 requests/hour with a key.
  - The public `DEMO_KEY` works but is limited to **10 requests/hour**
    (measured via `X-Ratelimit-Limit`, lower than the documented 30).
  - Send the key as the `X-Api-Key` header, never as the `api_key` query
    parameter, so it can't leak into logs or the eval cache (which stores
    fetched URLs).
  - No title/full-text search endpoint. `congress.gov/search` is
    disallowed by robots.txt. Candidates must come from paging list
    endpoints (`limit=250`, `offset`).
- **House Clerk roll-call XML** (`https://clerk.house.gov/evs/<year>/roll<N>.xml`):
  keyless and works (returned the Epstein bill's House vote: H R 4405,
  "On Motion to Suspend the Rules and Pass", Passed, 18-Nov-2025). Covers
  House votes only.
- **senate.gov**: blocks automated access -- robots.txt and the roll-call
  XML both return 403 from its Akamai CDN for this project's User-Agent.
  Don't work around the block (see memory: scraping-tos-risk-practice).
  Senate vote counts are available through Congress.gov nomination/bill
  action text instead.

Real API responses captured during the check (untracked, may be deleted;
recapture if needed): `scratchpad/noms1.json` (nominations, 119th Congress,
page 1 of 9, 2,208 total), `scratchpad/bills_sjres.json`,
`scratchpad/bills_hconres.json`, `scratchpad/hconres86.json`.

Verified end to end on real records:
- **Blanche:** nomination PN1078, "Todd Blanche, of Florida, to be Attorney
  General, vice Pamela Bondi." -- latest action 2026-08-08 "Confirmed by
  the Senate by Yea-Nay Vote. 50 - 49. Record Vote Number: 230." -> 50 Yea
  -> Yes.
- **Iran war powers:** H.Con.Res. 86 actions include action code **8000**
  "Passed/agreed to in House ... 215 - 208 (Roll no. 199)" on 2026-06-03
  and action code **17000** "Passed/agreed to in Senate: Resolution agreed
  to in Senate without amendment by Yea-Nay Vote. 50 - 48" on 2026-06-23
  -> both chambers, identical text, before June 30 -> Yes.
- **Epstein:** H.R. 4405 actions include "Became Public Law No: 119-38."
  (action code 36000) and "Signed by President." (E30000) on 2025-11-19
  -> Yes.
- Ed Martin, Biden and McConnell records were NOT fetched (out of demo-key
  budget). Verify before building.

## Design (outline, as presented to and accepted by the user)

**Plug-in point:** a second structured resolver in
`resolution_finder/structured_resolvers.py`, next to the Fed resolver.
Same contract: `None` = not my market type (normal news path); a list of
verdicts = mine (never falls through to peer-check/news). **When
`CONGRESS_API_KEY` is not set, the resolver is not registered at all**, so
production behavior is unchanged until the key exists.

**Four market shapes**, each recognized by strict wording checks.
Anything that doesn't clearly match isn't claimed and goes through the
normal news path, as today:

| Shape | YES when | NO when |
|---|---|---|
| **Nomination** (confirm X as Y; N senators vote Yea for X) | The nomination record shows it confirmed before the deadline (and the vote count clears the threshold) | Withdrawn, returned to the President, or rejected; or the deadline passed with the nomination still pending |
| **Both chambers pass a measure on a topic** | One qualifying measure shows House passage (code 8000) and Senate passage (code 17000), or became law (36000), before the deadline, in identical text | **Never.** A missed candidate bill would make NO wrong, so this shape returns UNCLEAR instead |
| **House impeaches a named person** | A matching impeachment resolution shows House passage (8000) inside the window | The deadline passed, at least one resolution to impeach that person was found, and none passed |
| **A named bill becomes law** (bill citation like H.R. 3633 in the description) | "Became Public Law" (36000) before the deadline | The deadline passed without it |

**Safety rules, from traps found in the real data:**
- **Nominee matching:** surname, first initial, and the exact position
  must all match, and exactly one nomination must match. Real traps: the
  same nominations list has "Todd Blanche ... to be **Deputy** Attorney
  General" (PN12-5, confirmed 52 - 46), and "Ed Martin" is filed as
  "Edward R. Martin, Jr." Rule: the nomination's position text (after "to
  be", before ", vice" / " for the term") must be contained in the
  market's text, plus surname and first initial, plus exactly one match.
- **Vote-count threshold:** "Will 50 senators vote Yea" with exactly 50
  Yea is YES under both readings ("exactly 50" and "50 or more"). Above 50
  is ambiguous, so it stays UNCLEAR unless the title says "or more" / "+".
  Below 50 is NO under both readings. Per the Blanche description,
  confirmation without a vote count (voice vote / unanimous consent) counts
  as the highest bracket, and Vice President tie-break votes don't count.
- **Topic matching:** the measure's title must contain a salient proper
  noun from the description's criterion (e.g. "Iran", "Epstein"), then the
  NLI model must confirm its title entails the criterion, built from the
  description ("This legislation seeks to limit U.S. armed forces military
  action in the recent US/Israel-Iran conflict."). Needed because the real
  119th-Congress lists contain 40+ Iran war-powers measures, and about a
  dozen Senate votes on them were **rejected** (e.g. S.J.Res. 59, 104, 118:
  "Motion to discharge ... rejected by Yea-Nay Vote. 47 - 53").
  Unmeasured: whether the NLI model verifies terse short titles like
  "Epstein Files Transparency Act". If it doesn't, the market stays
  UNCLEAR (safe).
- **Identical text:** a measure only counts if it became law, or if the
  second chamber's passage says "without amendment" with no later
  amendment actions. Otherwise UNCLEAR.
- **Impeachment NO needs a positive control:** at least one House
  resolution whose title contains "impeach" and the person's surname must
  be found (proves the name search works) before "none passed" counts as
  NO. If none is found at all, UNCLEAR.
- **NO needs the deadline passed** (inject `today` for tests). YES can
  resolve early.
- **Congress number from dates:** congress = (year - 1789) // 2 + 1
  (January 1-2 of odd years still belong to the previous Congress). If a
  market's window spans two Congresses and the description doesn't pin
  one, UNCLEAR.

**Where candidates come from** (paged to completion):
- Nominations: `/v3/nomination/{congress}` (119th: 2,208 entries, 9
  pages), then `/v3/nomination/{congress}/{number}/actions` for the match.
- Topic measures: `/v3/law/{congress}` (only bills that became law) plus
  `/v3/bill/{congress}/hjres`, `sjres`, `hconres`, `sconres` (119th:
  216 S.J.Res., 118 H.Con.Res.). Plain H.R./S. lists are too large to page
  (thousands of bills), so an H.R./S. bill that passed both chambers but
  hasn't become law is missed -> UNCLEAR, never wrong.
  `hres`/`sres` are single-chamber and never qualify.
- Impeachment: `/v3/bill/{congress}/hres` (~5-7 pages per Congress), then
  actions for each title match.
- Named bill: `/v3/bill/{congress}/{type}/{number}/actions`.

**Verdict output:** binary markets get one verdict, source_type
"primary", `source_url` = the human congress.gov page (e.g.
`https://www.congress.gov/nomination/119th-congress/1078`,
`https://www.congress.gov/bill/119th-congress/house-concurrent-resolution/86/all-actions`),
`evidence_snippet` = the deciding action text verbatim.

**Tests and fixtures:** real API responses saved verbatim under
`tests/fixtures/congress/`, roughly 45 calls. With `DEMO_KEY` (10/hour)
that's ~5 hours of rate-limited capture in the background; with a real key,
about a minute. Negative cases mutate a real fixture inside the test, as
the Fed tests do. Then an eval on the 6 markets with known answers; ship
bar **wrong = 0**.

## Before building

1. Get the api.data.gov key (user).
2. Fetch the Ed Martin, Biden-impeachment and McConnell-impeachment records
   and confirm the rules above actually produce the known answers (No, No,
   No) -- not yet verified.
3. Measure whether NLI verifies the real Iran and Epstein titles.
4. Then write the spec and implementation plan (brainstorming ->
   writing-plans), same flow as the Fed resolver.
