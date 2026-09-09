# Requirements: structured game-data adapter (2026-09-09)

Captured on Sonnet from the user's own description, verbatim in intent,
for Opus to design. This is NOT a design doc -- it deliberately makes no
architectural decisions (data source, per-sport schema shape, how it
plugs into resolution_spec.py/verdict_engine.py). It exists so the
requirements aren't lost between now and the next Opus design session.

## Why this exists

Flagged as a structural gap in
`docs/superpowers/plans/2026-09-08-description-first-strategy.md`
("Structured data adapters"): several real market types are not
answerable from news prose at all -- a box score, a stat table, a
tournament bracket is not a news article. This is the concrete next
step toward the niche-market target ([[target-use-case-niche-markets]]
in memory): yellow-card counts, individual-player stats, and similar
markets need structured data, not NLI over search results.

## Requirements, as stated

1. **A structured game-data fetcher, separate from the news/NLI
   pipeline.** Pulls actual results/stats for a game -- not articles
   about it.

2. **Window: concluded games only, rolling 30 days.** From `now` back
   to `now - 30 days`. Explicitly NOT live/in-progress games -- no
   live-score polling loop. A simple "this game is live right now"
   status indicator is fine (cheap, derivable from schedule/fixture
   data alone), but no live-data fetching.

3. **Cache the fetched data; do not refetch automatically.** A
   concluded game's stats are immutable, so once fetched there is
   nothing new to find on a re-fetch -- refetching only costs time. See
   memory [[structured-game-data-caching-is-safe]] for why this does
   NOT conflict with the standing "no caching in production" rule
   (that rule is about the news corpus changing over time; a finished
   game's box score does not change).

4. **Manual "hard refresh" override**, for the rare case a refetch is
   actually needed (a correction, a data-source error). Direct
   precedent already in this codebase: `run_eval.py --live` -- cache-
   first by default, one flag forces a fresh fetch and overwrites the
   cache. The same shape should work here.

5. **The data schema must be sport-specific, not universal.** A soccer
   game has yellow/red cards; the NBA does not. Different sports need
   different field sets -- decided per sport (or per sport-family), not
   one fixed shape imposed across all of them.

## Explicitly open (Opus's to design, not decided here)

- Which sport(s) first -- should follow from mining real market
  descriptions (the user's own "give Opus a lot of descriptions to find
  things to fixate on" idea from earlier this session) rather than
  being guessed.
- Where the data actually comes from per sport (a stats API, official
  league site, box-score aggregator) -- ToS/access considerations apply
  the same way they do for existing named sources (see
  [[scraping-tos-risk-practice]] in memory).
- The cache's storage shape and where it lives (a new table, a file
  store, something modeled after `eval_cache.py`'s pattern but for
  production use this time).
- How a per-sport schema plugs into the resolution-spec / verdict-
  engine layers already built (2026-09-08 commits `532b825`, `9106bcf`)
  -- e.g. does a market's parsed ResolutionSpec eventually carry a
  reference to "which structured adapter answers this", or does this
  stay a fully separate lookup path.
- How the 30-day window interacts with a market's own close_date /
  backstop deadline (already parsed by resolution_spec.py) -- are they
  the same window, or independent.
