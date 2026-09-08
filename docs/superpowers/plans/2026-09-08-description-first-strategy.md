# Strategy: description-first resolution (2026-09-08)

Written on Opus after two things landed on the same day: the Priority 1
measurement (which showed the verdict layer is no longer the binding
constraint), and the first real look at the user's OWN markets on
rain.trade -- the actual target population, as opposed to the
Polymarket-sourced eval batch everything has been calibrated against.

This document argues the current architecture is aimed slightly wrong,
and says what to build instead. It supersedes the ordering in
`2026-09-07-opus-plan.md`, though not its individual findings.

## What the Priority 1 measurement actually proved

Scoped eval, the 10 binary markets whose titles parse into the new
NO-path shape (5 truth-No, 5 truth-Yes):

    correct=1  wrong=0  unresolved=9

Binary-No moved 0 -> 1 (the Canadian Stanley Cup market), with no
regressions on the Yes side. The mechanism works. But the Djokovic
market -- the case verified end-to-end against clean evidence -- came
back UNCLEAR on real retrieval, and the reason is the finding:

The NO path fired correctly. It found *"Jannik Sinner ends Novak
Djokovic's hunt for 11th title"*, verified it, and died on
*"Only 1 independent source confirms this."* All seven of its candidates
were bare headlines.

## Root cause: the engine reasons over headlines, not articles

| Source | Results/query | URL fetchable? | What the engine sees |
|---|---|---|---|
| Google News RSS | ~100 | No (JS wrapper) | ~15-word headline |
| Bing News RSS | ~1-2 | Yes (decodes to publisher) | full article text |

Bing was returning **zero** results because feedparser sends no
User-Agent and Bing answers that with an empty HTTP 200 (fixed, commit
`6b9d05c`). But Bing's index is thin and recency-biased -- it returns
2026 articles for a 2024 event -- so that fix is a marginal gain, not
the unlock.

The consequence is systemic: every threshold, hedge word, and
corroboration rule in `verdict_engine.py` was calibrated against
headline-shaped input. The documented wire-duplicate bug (two genuinely
independent sources measuring 0.978 similarity) is a direct symptom --
a 15-word headline has no room to differ from another 15-word headline,
so 2-domain corroboration is close to unsatisfiable by construction.

**Implication: re-baseline before any further verdict-layer tuning.**
The root-cause table in the previous plan (NO_MATCH 61%, corroboration-
blocked, conflict) describes behavior on crippled input.

## The bigger finding: we are building the backup path

Three real rain.trade markets, three different verticals:

**"World Cup: Nation of Top Goalscorer"** (resolved -> France)
- options are NATIONS; the underlying fact is about a PLAYER
- tiebreak cascade: FIFA official leader -> fewer penalty goals ->
  alphabetically by last name
- deadline rule: no official leader by Aug 2 2026 -> resolves "No"

**"Will a Canadian Team Win the 2027 Stanley Cup?"**
- description **enumerates the membership set explicitly**: Flames,
  Oilers, Canadiens, Senators, Maple Leafs, Canucks, Jets
- named primary source: *"The NHL's official information"*, with
  *"credible reporting consensus as backup"*
- deadline rule: no champion declared by Jun 30 2027 -> "No"

**"Will Donald Trump visit Canada in 2026?"**
- precise condition: physically enters terrestrial or maritime territory
- explicit carve-out: *"Entering Canadian airspace alone will not
  qualify"*
- named primary sources: official US government information, official
  statements, verified social media; *consensus of credible reporting if
  necessary*
- stated default: "No"

Every one of them ships a **machine-actionable resolution specification**
in its description: the condition, the entity set, the tiebreak cascade,
the named primary source, the deadline, and the default outcome.

The engine currently ignores nearly all of it. It takes the *title*,
builds news queries from it, and tries to entail the title from open-web
prose. Note what the descriptions themselves say: credible reporting
consensus is the **backup**. We have spent the entire project building
the backup path and running it as the primary.

Three consequences fall straight out:

1. **Set membership is not an NLP problem.** The Canadian Stanley Cup
   market *lists the seven teams*. There is nothing to infer. Parsed
   from the description, "will a Canadian team win" reduces to the
   already-working multi-option winner detection over seven named
   options. The binary-NO path shipped today parses "a Canadian team"
   out of the *title* as an opaque string -- strictly worse than the
   answer sitting in the description. (Consistent with the standing
   note that the description is authoritative and the title is often
   underspecified.)

2. **Option-level abstraction mismatch.** "Nation of Top Goalscorer"
   lists nations, but no article will ever write "France won the nation
   of top goalscorer." Articles say "Mbappe won the Golden Boot with 8
   goals." Resolving it requires a JOIN -- find the top scorer, map
   player -> nation, match against options -- that no amount of NLI on
   the option name can produce. This generalizes: "team of the MVP",
   "conference of the champion", "nation of the winner". The user's own
   earlier worry about a Canadian-born player representing another
   country is exactly this join being genuinely hard.

3. **Deadline + stated default is a large, unused lever.** Both sports
   markets resolve "No" if no champion is declared by a stated date. The
   engine deliberately refuses to assert a stated default after the
   deadline, on the grounds that it would assert an outcome from our own
   failure to find evidence. That was the right call for Polymarket
   markets with unreliable descriptions. For markets whose description
   states the default explicitly, and once the named primary source has
   actually been checked, the default becomes assertable. The
   distinction is *"we looked at the authoritative source and it shows
   nothing"* versus *"our news search came up empty"* -- currently these
   are indistinguishable to the engine.

## Closeability, per the user's own definition

The user defined it directly: a market needs a resolution when *"it fits
one of the deadlines, or a tournament has ended."*

That is a date/schedule question, not a prose-entailment question. For a
large share of markets it is pure date arithmetic against the stated
deadline, plus (for tournaments) a schedule/fixture check. It needs no
NLI and no news at all.

This is the strongest argument yet for splitting the output in two
(see `2026-09-08-opus-followup-notes.md` #2): closeability is cheap,
separately computable, and is the thing the user actually asked for.

## Recommended architecture: description-first

Replace *title -> news search -> entail the title* with:

1. **Parse the description into a resolution spec**: condition, entity/
   membership set, tiebreak cascade, named primary sources, deadline,
   stated default, explicit carve-outs.
2. **Evaluate closeability**: has the deadline passed, has the event
   concluded? Mostly date math. Emit this independently of any outcome.
3. **Query the named primary source first** (`source_config.py` already
   has `resolve_named_source`); fall back to news consensus only as the
   descriptions themselves prescribe.
4. **Apply the stated rule**, including tiebreaks, carve-outs, and the
   default -- rather than pattern-matching prose for a winner.

The existing news/NLI/corroboration machinery is not wasted: it stays as
the backup tier the descriptions explicitly call for, and it is what
handles markets whose primary source is silent.

## Ordering

1. **Re-baseline the eval on full-article input.** Everything else is
   measured against this. (User has chosen alternative sources over
   decoding Google News wrappers -- so add feeds that return real
   article URLs, e.g. GDELT's free documented API, publisher RSS.)
2. **Build the description parser + closeability signal.** Highest
   leverage, cheapest, and it is literally the product spec.
3. **Named-primary-source retrieval**, ahead of broad news search.
4. **Structured sports data adapters** (stats, fixtures, standings) --
   required for the top-goalscorer join class and for the genuinely
   niche stat markets that are the stated long-term target.
5. **Then** the verdict-layer backlog: elimination corroboration before
   gate broadening (see followup-notes #1), NO_MATCH long tail.

## Caveats stated honestly

- Three markets is a small sample. The template consistency across
  sports/politics/celebrity is strong and looks platform-standard, but
  it should be confirmed against more of the user's own markets before
  the parser is designed around it.
- Four of the five markets shared are still open (2026-2027 dates), so
  they are a **negative** test set: the correct behavior is to flag none
  of them as closeable. That is genuinely useful -- it tests the
  expensive failure direction -- but it is not a substitute for resolved
  markets with known outcomes.
- Description parsing is itself an NLP problem, and a brittle one if
  done with regexes. It is, however, a far more constrained one than
  open-web entailment: the text is short, structured, written to a house
  template, and authored by the platform rather than scraped.
