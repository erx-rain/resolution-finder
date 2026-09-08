# Design: Resolution Availability Check (the main goal)

The stated goal, in the user's words: **"check if there is a resolution
for the market"** -- and separately, *"needing a resolution just means it
fits one of the deadlines, or a tournament has ended."*

This document designs that check. It is the primary deliverable; outcome
determination is secondary to it.

## The template is confirmed: 5/5

All five rain.trade markets examined carry a full resolution spec in the
description, across four very different verticals:

| Market | Condition shape | Named primary source | Deadline | Default |
|---|---|---|---|---|
| World Cup top goalscorer | stat + 3-level tiebreak | FIFA | Aug 2 2026 | No |
| Canadian team / Stanley Cup | membership set (7 teams, enumerated) | NHL official | Jun 30 2027 | No |
| Trump visits Canada | physical event + carve-out | US gov / official statements | Dec 31 2026 | No |
| Katy Perry engaged | announcement event | principals / authorized reps | Dec 31 2026 | No |
| AI bubble bursts | **5 numeric conditions, >=3 in a 90-day window** | companies + listing exchanges | Dec 31 2026 | No |

The AI-bubble market is the decisive case. The most subjective-sounding
title in the set is operationalized into precise, machine-checkable
thresholds, and the description **explicitly forbids resolving on news
claims**: *"will not resolve to 'Yes' based solely on reports or claims
that the AI bubble has burst."* The current engine's entire method is
ruled out by the market's own text. Meanwhile every field needed to
resolve it correctly is sitting in the description, unread.

## The core decomposition

Every market in the set becomes resolvable through exactly one of two
paths:

**Path A -- the deadline passed.** A resolution is available, and it is
the stated default (in all five cases, "No"). This is *pure date
arithmetic*. No retrieval, no model, no news. It cannot fail to detect.

**Path B -- the triggering event occurred early.** A resolution became
available before the deadline. This needs evidence, and is where all the
existing machinery applies.

Path A is free and it is currently unused. The engine deliberately
refuses to assert a stated default after the deadline
(`_deadline_default_note`), on the correct reasoning that doing so
asserts an outcome from *our own failure to find evidence*. That
reasoning was right for Polymarket markets with unreliable descriptions.
For markets that state the default explicitly, it forfeits the single
cheapest win available.

## The key insight: availability and outcome have different risk profiles

The reason Path A is safe to act on is that **"a resolution exists" and
"here is the resolution" are different claims with different costs**:

- Once the deadline passes, a resolution unambiguously **exists**. The
  market *can* be closed. This is certain, from a date comparison.
- *What* it resolves to is the default -- but only if we genuinely
  looked for a Path B trigger and found none. If Trump visited Canada in
  March and we missed it, the default is wrong.

So the two get reported separately, and only the second is gated:

```
resolution_available: bool      # certain via Path A; evidence-based via Path B
availability_reason:  "deadline_passed" | "event_occurred"
suggested_outcome:    the default (Path A) or the observed outcome (Path B)
outcome_confidence:   graded by whether the primary source was actually checked
```

A false "available" costs one wasted human glance. A false
`suggested_outcome` is only ever a *suggestion* a human reviews. Neither
carries the cost of an autonomous wrong resolution -- which is why this
can ship at a far lower evidence bar than the verdict engine, without
weakening any existing guard.

## Measured: Path A coverage on the real 97-market batch

Sized before writing any parser (`scratchpad/size_path_a.py`,
`size_path_a2.py` -- read-only, nothing authored):

| | count | share |
|---|---|---|
| has `close_date` | 94 | 96% |
| has a deadline in the description | 43 | 44% |
| **has EITHER -- availability-ready** | **94** | **96%** |
| has a parseable stated default | 35 | 36% |
| **already past `close_date` today** | **87** | **90%** |

Three conclusions:

1. **Availability needs only a deadline, not a default.** "Is a
   resolution available" is answered by the deadline passing; the
   default only affects what we *suggest* it resolved to. Measured that
   way, coverage is 96%, not the 20% that a stricter both-fields test
   reports. Only three markets have no deadline from either source (the
   known `close_date=None` March Madness cases).

2. **87 of 97 markets are already past their close date.** Path A flags
   a resolution as available for every one of them, with zero retrieval
   and zero model calls. That is the entire main goal, for 90% of the
   batch, from a date comparison.

3. **The outcome half is genuinely thinner** (36% with a parseable
   default), which is exactly why availability and outcome must be
   reported separately. Gating the cheap signal on the expensive one
   would discard most of the coverage.

### `close_date` and the description deadline are different things

In 8 markets the two disagree on the year, and the pattern is
consistent -- the description deadline is *later*:

    world-series-champion-2025   close=2025-10-31  desc=February 28, 2026
    f1-constructors-champion-2025 close=2025-12-07 desc=February 28, 2026
    mlb-2025-will-a-1-seed...    close=2025-10-31  desc=February 28, 2026

They are not competing values for one field. `close_date` is when
trading stops, around when the event is expected; the description
deadline is the **backstop** after which the stated default applies.
That yields a natural two-tier signal:

- **past `close_date`** -> resolution *probably* available, go and look
  (flag for review)
- **past the description backstop** -> resolution *definitely*
  available, and the stated default applies if nothing was found

Use `close_date` as the availability trigger and the description
deadline as the default-assertion trigger. Do not collapse them.

## What to build

### 1. Description -> spec parser

Split by regularity, and use a model only where the text actually
demands it:

**Deterministic (no model) -- and these are the two that power Path A:**
- `deadline`: the phrasing is highly regular ("December 31, 2026,
  11:59 PM ET"). Parse from the *description text*, not `close_date` --
  they differ, typically by a day (Katy Perry: condition deadline
  Dec 31, close date Jan 1).
- `default_outcome`: also highly regular ("Otherwise, this market will
  resolve to 'No'").
- `named_primary_sources`: regular phrasing ("The NHL's official
  information", "Official information from the relevant companies and
  listing exchanges").

**Model-assisted (the genuinely variable parts):**
- `conditions`: the AI-bubble market's five thresholds and 90-day
  window; the Trump market's carve-out ("airspace alone will not
  qualify")
- `membership_set`: when enumerated (the seven Canadian teams) -- note
  this makes set-membership a *parsing* problem, not an NLP one
- `tiebreak_cascade`: FIFA leader -> fewer penalty goals -> alphabetical

Ship the deterministic half first. It alone unlocks Path A for every
market, which is most of the goal.

### 2. Availability check

```
spec = parse_description(market)

if now > spec.deadline:
    available = True
    reason    = "deadline_passed"
    suggested = spec.default_outcome
    outcome_confidence = high if primary source was checked and silent
                         else low  ("default, unverified")
else:
    evidence  = query(spec.named_primary_sources) or news_backup(...)
    available = condition_met(evidence, spec)
    reason    = "event_occurred"
    suggested = observed outcome
```

Note the ordering inside Path B: **named primary source first, news as
backup** -- which is what the descriptions themselves prescribe. The
existing news/NLI/corroboration stack becomes the backup tier it was
always meant to be, not the primary.

### 3. Structured data adapters

Path B for several of these markets is not answerable from prose at all:

- AI bubble: equity prices and all-time highs (NVDA, SOXX, TSM, ASML,
  AVGO, ANET, SMCI), plus H100 rental pricing
- Top goalscorer: tournament stat tables, plus a player -> nation join
- Stanley Cup / tournaments: fixtures and official champion declaration

This unifies with the already-backlogged crypto/price work: one gap,
several domains. A price series and a stat table are the same shape of
problem.

## Ordering

1. **Deterministic spec parser (deadline, default, named sources)** --
   small, and it unlocks Path A immediately.
2. **Availability output** as a separate signal from the outcome verdict.
3. **Named-primary-source-first retrieval** ahead of broad news search.
4. **Model-assisted parsing** of conditions / membership / tiebreaks.
5. **Structured adapters** (prices, stats, fixtures).

Everything the project has built so far survives as step 3's backup
tier. Nothing is thrown away; it is demoted to the role the market
descriptions always assigned it.

## Open questions

- **Is there a rain.trade API** to enumerate open markets and pull
  descriptions verbatim? Production needs this regardless -- the scanner
  must iterate the user's live markets -- and test data must be pulled
  verbatim rather than transcribed from web pages.
- **Does the template hold for markets not authored in this house
  style?** All five look platform-authored. If markets can be user-
  created with free-form descriptions, the parser needs a confidence
  measure and a graceful fallback to the existing title-driven path.
- **How should a market with no parseable deadline behave?** Almost
  certainly: fall back to the current engine, never invent a deadline.
