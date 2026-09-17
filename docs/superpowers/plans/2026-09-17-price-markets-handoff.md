# Handoff: crypto / currency / gold / stock price markets (2026-09-17)

**Status: deliberately NOT built.** Skipped by the user's decision on
2026-09-17 because no price-data source was found that is both free and
licensed for commercial use. This file is the handoff for whoever builds
it later: what the markets actually ask, what was checked, what the
build needs, and the rules that keep it from adding wrong verdicts.

Standing priority that governs every choice below: **wrong verdicts are
the hard constraint.** Maximize correct and resolved, but never at the
cost of wrong going up. When in doubt, the answer is UNCLEAR.

## 1. Why it's blocked: data licensing (checked live 2026-09-17)

The markets name Binance (crypto) or Pyth (gold, stocks) as their
resolution source. Every candidate source checked:

| Source | Works technically? | Commercial use for settling markets? |
|---|---|---|
| Binance public API (`data-api.binance.vision/api/v3/klines`, keyless) | Yes -- returned real 1-minute candles. `api.binance.com` returned an empty 200 from this network. | **No.** Binance Terms of Use prohibit commercial use of Binance market data without written consent. |
| Pyth (`pythdata.app`, Benchmarks/Hermes API) | Old TradingView-history shim now 404. Since the Pyth Core upgrade (2026-08-26 16:00 UTC) every request needs `Authorization: Bearer $PYTH_API_KEY`. | **Paid.** Data plans start around $500/month. |
| Coinbase Exchange public candles (`api.exchange.coinbase.com/products/BTC-USDT/candles`) | Yes -- keyless, 1-minute history works. | **No.** Market Data Terms (updated 2026-08-07) restrict use to personal or research purposes, and explicitly bar using the data to determine amounts payable under a financial product -- i.e. settlement. |
| CoinGecko | Not tested. | **No** on the free Demo plan (no commercial license); paid plans only. |
| Kraken public OHLC (`api.kraken.com/0/public/OHLC`) | Partly: the REST endpoint only returns the most recent ~720 candles (about 12 hours at 1-minute), `since` is ignored beyond that. Bulk historical CSVs exist separately. | **No** (read 2026-09-17, Kraken Global Terms of Service). Kraken content may be used "only for your own benefit"; commercially exploiting it or making it available to third parties is prohibited; any other use needs permission via marketdata@kraken.com. |
| Chainlink on-chain price feeds (historical rounds via `getRoundData`) | Not tested. | **Ambiguous** (read 2026-09-17, Chainlink Foundation Terms of Service v6.0, effective 2026-08-18). Grants a limited license to use the Services "in accordance with their intended uses and using their designated public interfaces"; feeds are published for consumption through the on-chain aggregator interface. No explicit ban on commercial consumption of feed data, but no explicit grant either, and it bars breaching third-party data-provider terms. A legal call for the user, not an engineering one. Precision caveat: feeds update on a deviation threshold (about 0.5% for major pairs on Ethereum mainnet) or a heartbeat, so a price at an exact minute is only known to within that band -- vs. real bracket widths of ~2-2.6%. |
| Gold / US stock prices from any free commercial source | None found. | -- |

**User decision 2026-09-17: leave blocked for now.**

Ways to unblock, in the order worth trying:
1. The user's own legal review approves Chainlink -> it becomes a proxy
   source (section 4), with the margin sized above its deviation band.
2. Ask for written permission: Kraken (marketdata@kraken.com), Coinbase
   (marketdata@coinbase.com), or Binance.
3. Budget for a paid plan (Pyth covers crypto + gold + stocks, which is
   every market shape in this file).

Recheck this table before building -- terms change.

## 2. The market shapes (all real, all in `data/markets.json` with ground truth)

**A. Point-in-time close, binary threshold.**
`bitcoin-above-64k-on-august-17-2026` ("above $64,000", truth Yes),
`will-the-price-of-ethereum-be-less-than-1400-on-august-17-2026`
("less than $1,400", truth No). Resolves on ONE Binance 1-minute candle's
Close, at 12:00 ET on the title's date.

**B. Point-in-time close, price brackets (multi-outcome).**
13 markets: `bitcoin-price-on-*`, `ethereum-price-on-*`,
`bitcoin-price-july-15-5pm-et`. Same single candle, but the answer is
which bracket the Close falls in. Real option formats the parser must
handle, verbatim:
- `<70,000`, `70,000-72,000`, `>88,000` (comma thousands)
- `<103k`, `103-105k`, `>109k` (k suffix on the upper bound only)
- `>90k`, `90-88k`, `84-82k`, `<80k` (**descending** ranges)
- `2400–2500` (en dash, not hyphen)
- Description rule: *"If the reported value falls exactly between two
  brackets, then this market will resolve to the higher range bracket."*

**C. Window touch, HIGH and/or LOW.**
`will-spcx-reach-145-in-august-2026` (SpaceX stock, "(HIGH)"),
`will-xauusd-reach-4400-in-august-2026` (gold). YES if ANY 1-minute
candle in the window has High >= the price (or Low <= the price). The
gold description defines the arrows the Polymarket UI shows: *above for
↑ High Prices, below for ↓ Low Prices*. So ↑ = "touched it from below",
↓ = "dipped to it".

Window details the descriptions specify and a builder must honor:
- Window starts "after market creation", not necessarily the 1st of
  the month.
- Stocks: regular trading hours only (9:30-16:00 ET); pre-market and
  after-hours don't count; split-adjusted prices, and the target price
  is adjusted for splits too.
- Gold: trading sessions only (Sun 18:00 ET - Fri 17:00 ET, daily break
  17:00-18:00 ET, holiday schedules). "If Gold does not trade at all
  during the listed time frame, this market will resolve to No."
- Fallbacks named in the descriptions: exchange official daily high
  (stocks), COMEX GC futures daily high/low (gold).

**D. Submarket reopening (user requirement, 2026-09-17).**
When a price market's threshold gets crossed and it resolves, Polymarket
can REOPEN that submarket (e.g. price went above, came back below, and
the market opens again). The resolution window for the reopened market
runs from the most recent reopening, not the original creation date --
otherwise a crossing from BEFORE the reopen would wrongly resolve it
YES. **Nothing today can detect a reopen.** How Polymarket represents
it in its Gamma API (a new market id? a changed `startDate`? event
history?) has not been investigated. A builder must work this out
before shipping shape C, or restrict shape C to markets confirmed never
reopened.

## 3. Parsing: what to measure, from title AND description

User requirement: the system must work out by itself what to measure,
from the title and the description, and for this market type the TITLE
is likely the most useful field (a scoped exception to the project's
general description-over-title rule). The two will usually agree. Rules:

- **Instrument.** Title names the asset (Bitcoin, Ethereum, Gold
  (XAUUSD), SpaceX (SPCX)); description names the exact pair and venue
  (`BTC/USDT` on Binance, `Metal.XAU/USD` / `Equity.US.SPCX/USD` on
  Pyth). Build a small explicit alias table (Bitcoin -> BTC, Ethereum ->
  ETH, Gold -> XAU ...). **Title and description must resolve to the
  same instrument, otherwise UNCLEAR.** Never guess an unknown ticker.
- **Threshold and direction.** From the title: "above $64,000", "less
  than $1,400", "hit (HIGH) $145". For brackets, from the options.
  Real trap: `will-the-price-of-ethereum-be-less-than-1400-on-august-17-2026`
  has a binary title but a BRACKET-style description ("falls exactly
  between two brackets"), copied boilerplate. The title is right there.
- **Timestamp.** Date from the title ("on August 17", "July 15, 5PM ET"),
  time from the title if present, else the description ("12:00 in the
  ET timezone (noon)"). Year is not in most titles: take it from
  `close_date`, and require the parsed date to equal `close_date`,
  otherwise UNCLEAR. Convert ET -> UTC with DST handled (use `zoneinfo`,
  `America/New_York`), never a fixed offset.
- **Window.** Month from the title ("in August"), year from
  `close_date`; start = max(month start, market creation).
- Anything that doesn't parse cleanly -> the market is not handled by
  the price path at all (and see rule 5.3 below).

## 4. Proxy source + safety margin (the user's chosen approach)

The user chose: use a legally usable source even if it isn't the exact
named one, and only resolve when the price is clearly away from the line.

- One real measurement so far (the 2025-09-15 16:00 UTC candle):
  Binance BTC/USDT Close 114,834.90 vs Coinbase BTC-USDT
  Close 114,840.74 -- a $5.84 (0.005%) gap. ONE sample; do not size the
  margin from it.
- **Calibrate the margin on many samples** (hundreds of timestamps,
  including volatile minutes) before shipping, and set it well above the
  worst observed gap. For a deviation-threshold source like Chainlink,
  the margin must also exceed that source's own update threshold.
- Resolve only if the proxy price is at least the margin away from the
  threshold (shape A) or from BOTH edges of the bracket it falls in
  (shape B). Inside the margin -> UNCLEAR.
- Shape B: the "exactly between two brackets -> higher bracket" rule
  only matters at the edge, which the margin already excludes.
- Shape C: a window high that clears the threshold by the margin is YES
  (can resolve early, before the window ends). NO needs the whole window
  finished AND the window high below threshold minus margin.
- Cheap first pass for windows: daily candles; only fetch minute data
  near the threshold.

## 5. Safety rules (non-negotiable)

1. Any disagreement (title vs description instrument, parsed date vs
   `close_date`, option that doesn't parse) -> UNCLEAR.
2. Source fetch failure, missing candle, or data gap -> UNCLEAR.
3. **A market recognized as a price market must NOT fall back to the
   news-article numeric path.** That path caused real wrong verdicts:
   a Binance futures/spot volume ratio (7.82) was read as the BTC price
   (`bitcoin-above-64k-on-august-17-2026`, 2026-08-23). Until the price
   path can answer, these markets stay UNCLEAR.
4. For brackets, emit YES for the one bracket and NO for the rest only
   when exactly one bracket matches.

## 6. Where it plugs in

The Fed-decision build (2026-09-17, see
`docs/superpowers/specs/2026-09-17-fed-decision-resolver-design.md`)
adds a structured-resolver step to `pipeline._scan_market` and
`run_eval.py` that runs before news retrieval. A price resolver is a
second entry in that same hook, same contract: return None for "not my
market type", or a list of verdicts (possibly all UNCLEAR) for "mine".
Eval caching of fetched candles is allowed in `run_eval.py` only, never
in the production pipeline (standing rule).

## 7. Eval set ready for it

All 17 markets above have `resolved_to` in `data/markets.json`
(4 threshold/window + 13 bracket). Baseline today: 0 correct, 0 wrong
on the 13 brackets (fresh-batch eval, 2026-09-17). Ship bar: wrong
stays 0.
