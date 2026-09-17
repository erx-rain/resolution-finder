# Remaining work triage (2026-09-17, Opus)

After the Fed-decision resolver shipped, the user asked Opus to go over
every remaining planned item. Each was checked against live facts
(licensing texts, measurements on the real batch) before deciding whether
it's worth designing. Standing priority: maximize correct + resolved,
never let wrong grow.

Measurement scripts (untracked, `scratchpad/`): `profile_nli.py`,
`nli_threads.py`, `nli_batch.py`, `triage_measure.py`.

## Summary

| Item | Verdict | Why |
|---|---|---|
| Congress/Senate official-record resolver | **Designed at outline level, not built** -- see 2026-09-17-congress-resolver-handoff.md. Needs a free api.data.gov key (user). | 6 real markets with truth, 0/6 resolved today. Public-domain government data, mostly deterministic (roll-call counts, confirmation passed/failed, bill status). Same shape as the Fed resolver. |
| SCOTUS ruling resolver | **Design after Congress** | 9 real markets with truth, 0/9 resolved today. Public domain, but reading a holding ("upholds the ban") is interpretation, not a number -- harder to make wrong-proof. |
| Fed dissent count | **Small add-on** | 1 market (truth "1"). Same FOMC statement the Fed resolver already fetches ("Voting against this action were ..."). |
| NLI slowness | **Small safe fix only** | Smaller than first reported; only memoization is output-identical. |
| "{option} has won" grammar bug | **Close as superseded** | Outside Fed, 13 of 15 affected markets are crypto brackets blocked on data, 1 is the dissent count (solved above), 1 is TV viewership. Rewording alone resolves ~nothing. |
| Named-source list expansion (`TIER1_DOMAINS`) | **Drop as specified** | `source_type="primary"` is display-only -- verdict_engine never reads it. Expanding the list moves no numbers. The real lever is official-source resolvers (rows above). |
| Crypto / gold / stock price markets | **Blocked -- user decision** | See licensing below. |
| ESPN sports data | **Blocked -- dropped** | Disney Terms of Use (cover ESPN) prohibit commercial use and automated extraction. |

## Licensing, checked in the actual legal texts

- **Kraken** (Global Terms of Service): Kraken content may be used "only
  for your own benefit"; commercially exploiting or making it available to
  third parties is prohibited; other uses need permission via
  marketdata@kraken.com. **No.**
- **Chainlink** (Foundation Terms of Service v6.0, effective 2026-08-18):
  grants a limited license to use the Services "in accordance with their
  intended uses and using their designated public interfaces"; data feeds
  are published for consumption and read through the on-chain aggregator
  interface. No explicit commercial prohibition on consuming feed data, but
  no explicit grant either, and it bars breaching third-party data-provider
  terms. **Ambiguous -- a legal call for the user, not an engineering one.**
  Precision caveat already in the price-markets handoff: mainnet BTC/USD
  updates on a ~0.5% deviation, vs. real bracket widths of ~2-2.6%.
- **ESPN** (Disney Terms of Use): prohibits "any commercial or
  business-related use" and extraction "using a robot, spider, script, or
  other automated means". **No.**
- Previously (see 2026-09-17-price-markets-handoff.md): Binance, Coinbase,
  CoinGecko free tier -- no; Pyth -- paid (~$500/mo).

Crypto options for the user: (a) accept Chainlink after their own legal
review, (b) email for permission (marketdata@kraken.com /
marketdata@coinbase.com), (c) pay for Pyth, (d) leave blocked.

## NLI slowness -- corrected diagnosis

Earlier today this was reported as "38+ CPU-minutes without finishing" on
`international-2026-champion`. That figure is CPU time summed across ~8
threads (roughly 5 minutes of wall time), not a hang. Measured on the
cached snapshot (28 ranked articles, 855 sentences, 8 options):

- Up to 378 (sentence, option) pairs pass the cheap mention gate; with the
  winner + elimination checks that's up to ~750 NLI calls.
- Per call: ~300 ms for a short sentence, ~2.5 s for the longest (1,386
  chars). Thread count isn't the cause (4 threads ≈ 11 threads).
- 12% of calls repeat an identical (sentence, labels) pair.
- Calling the model directly with both hypotheses batched: only ~12%
  faster, and scores differ from the pipeline by up to 0.00095 -- enough
  to flip a threshold edge case. Rejected.

Safe fix: memoize `_classify_scores` on (sentence, labels) -- byte-identical
outputs, ~12% fewer calls. Anything larger (pre-filter gates, truncating
long sentences, quantization) changes scores and would need its own
correct/wrong measurement. Not worth it now: a heavy market costs minutes,
and eval replays are resumable.

## Government-source markets in the batch (latest eval class)

- SCOTUS (9): all unresolved -- supreme-court-vacancy-in-2024 (No),
  supreme-court-unanimous-vote-in-trump-immunity-case (No),
  scotus-bars-counting-mail-ballots-after-election-day (No),
  scotus-strikes-down-trumps-birthright-citizenship-eo (Yes),
  scotus-lets-trump-fire-ftc-commissioners-in-trump-v-slaughter (Yes),
  scotus-upholds-trans-sports-bans (Yes), scotus-rules-in-favor-of-monsanto
  (Yes), will-scotus-let-trump-build-the-white-house-ballroom (Yes),
  will-scotus-let-drug-users-own-guns (Yes).
- Congress/Senate (6 with truth): all unresolved --
  will-50-senators-vote-yea-for-todd-blanche (Yes),
  congress-passes-iran-war-powers-resolution-by-june-30 (Yes),
  congress-passes-epstein-disclosure-billresolution-in-2025 (Yes),
  will-the-senate-confirm-ed-martin-before-july (No),
  joe-biden-impeached-before-2024-election (No),
  judge-mcconnell-impeached-before-april (No). Plus clarity-act-2026 (no
  truth yet).
- FDA approvals (2): already correct via news.
- Tariffs (2): 1 correct, 1 unresolved.
