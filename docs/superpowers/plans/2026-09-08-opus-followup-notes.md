# Follow-up notes for Opus (2026-09-08)

Written on Sonnet immediately after shipping Opus plan Priority 1
(binary-NO path, commit `a7840eb`). Two items below need Opus's
judgment before more implementation work continues -- not urgent
blockers, but real design questions, logged per the user's request
rather than decided unilaterally on Sonnet.

## 1. Hedge-word list is polarity-specific; the multi-outcome elimination path likely has the same bug the binary-NO path just had

While calibrating Priority 1, the NO-confirmation loop initially reused
`_sentence_has_hedge` verbatim to gate candidate sentences before the
NLI elimination check. That looked like a phrasing-calibration problem
at first (natural negative sentences scoring anywhere from 0.5 to 0.99
on the elimination hypothesis depending on exact wording -- see the
commit's own comments for the measured numbers), but the real cause was
structural: `NEGATION_HEDGE_WORDS` (`"not "`, `"n't "`, `"never "`,
`"without "`, `"fails to"`, `"failed to"`, `"yet to"`, `"has yet"`, plus
genuine-uncertainty phrases like `"might be"`/`"reportedly"`) exists to
keep negation OUT of a YES/winner confirmation -- correct there. Reused
verbatim to gate a NO/elimination candidate, it filters out almost every
natural way of stating a negative outcome ("X has not won Y", "X never
advanced"), since those sentences are built entirely out of the words
the list is designed to catch.

Fixed narrowly for the new binary-NO loop only: added
`BINARY_NO_HEDGE_WORDS` (the same list minus the eight literal-negation-
of-the-verb words above) and `_sentence_has_hedge_for_negative_claim`,
used only in `_decide_binary`'s new NO loop.

**Not yet fixed**: `_decide_multi_outcome`'s existing, already-shipped
elimination path (`verdict_engine.py` around the sentence loop that
feeds both `_verify_winner_candidate` at ~line 2064 and
`_verify_elimination_candidate` at ~line 2079) shares ONE upstream
hedge gate for both branches, using the full negation-inclusive list.
That means the same class of sentence -- "Team X has not won a game
since 1993", "Team X never advanced past the quarterfinals" -- is
almost certainly being silently dropped as an elimination candidate
there too, today, in code that has already been calibrated and shipped
and is part of the 53% head-to-head / ~12% multi-outcome hit rates in
the Opus plan's own root-cause table.

This is very likely a pure recall gap, not a correctness regression --
dropping a candidate sentence can only ever produce more UNCLEAR, never
a wrong verdict, consistent with "minimize wrong, not unresolved." But
it means elimination-shaped multi-outcome markets may currently be
under-performing for a reason that has nothing to do with the model or
the threshold, purely a string-gate mismatch. I deliberately did NOT
touch `_decide_multi_outcome` in this pass: the fix requires splitting
one shared pre-filter that currently feeds two opposite-polarity checks
into two separate gates without disturbing the winner-detection branch,
which is calibrated code with a lot of accumulated real-bug history
(see its own comments) -- exactly the kind of change worth a deliberate
look rather than a fast unilateral edit on Sonnet.

**Ask for Opus**: decide whether/how to split that shared gate (same
`BINARY_NO_HEDGE_WORDS` approach, applied to the elimination branch
only, leaving the winner branch on the strict list), and whether it's
worth re-measuring the eval batch before and after to size the actual
recall gain before committing to touching calibrated code.

## 2. "Maybe I designed this wrong" -- oracle-agent framing

User's own words: "we have our own oracle of ai agent to close each
market, this one will be to search if a resolution is in place... it'll
just flag to the user if it needs to be closed, how confident he is and
what he thinks the resolution is," followed by "hmm to be honest idk if
we really need accuracy we just need to know when to close, maybe i
designed this wrong."

This is not really a from-scratch redesign question -- the current
system already outputs an outcome, a confidence float, an evidence
snippet, and a source for every market, which is most of "what it
thinks + how confident." The "minimize wrong, not unresolved" priority
that has driven every corroboration decision this session already
matches "just need to know WHEN to close, not force an answer."

The one real, concrete tension worth Opus's attention: `CORROBORATION_
MIN_DOMAINS = 2` is currently a hard gate on asserting ANYTHING --
a single-source match that scores very high on the NLI check still
collapses all the way down to a generic UNCLEAR verdict, indistinguishable
from a market with zero evidence at all. If the actual product is "flag
for a human, with a confidence level and a best guess," that's throwing
away real signal: a single very-confident source and zero evidence are
not the same thing, but they currently produce the same shape of output.

Worth Opus deciding whether the verdict contract should split into two
independent things instead of one gated one:
  - a best-guess outcome + confidence + corroboration count, always
    populated when ANY evidence was found (even just 1 source), and
  - a separate "auto-assert-safe" boolean gated on the existing
    corroboration bar,
so the human-facing flag ("this looks closeable, X% confident, here's
why") doesn't require the same bar as full autonomous assertion. This
doesn't necessarily mean lowering the bar for what the pipeline commits
to on its own -- it may just mean surfacing more of what's already being
computed internally (single-source confirmations, UNCLEAR's uncorroborated
note, the actual similarity score) instead of collapsing it to one of
four coarse outcome labels.

---

# Opus answers (2026-09-08)

## Answer to #1: do NOT just broaden the gate -- elimination has no corroboration at all

The note's risk assessment is backwards, and the reason only shows up
downstream of the loop it describes.

`_decide_multi_outcome` writes elimination verdicts directly:

```
if (option not in eliminated and not context_conflict and option_mentioned
        and _verify_elimination_candidate(sentence, option, market)):
    eliminated[option] = Verdict(outcome="NO", ...)
```

and then, if no winner was corroborated:

```
if eliminated:
    return list(eliminated.values())
```

There is **no corroboration check on that path**. One sentence, from one
source, clearing 0.85 on the NLI elimination check, becomes a committed
`NO` verdict for that option. Winner detection accumulates
`winner_confirmations` and requires `CORROBORATION_MIN_DOMAINS`;
elimination never got the same treatment when corroboration was rolled
out in phase 2.

So the note's claim -- "dropping a candidate sentence can only ever
produce more UNCLEAR, never a wrong verdict" -- describes the *current*
state correctly but inverts when applied to the proposed fix. ADDING
elimination candidates feeds more single-source matches into an
uncorroborated assert path. Broadening the hedge gate on its own would
directly increase wrong-NO risk, which is the expensive failure mode
this whole effort exists to minimize.

The code's own comment (at the winner-conflict block) already names this
exact inconsistency in a neighbouring context: *"it demands 2+ domains
to ASSERT a winner, then lets a single unverified domain BLOCK one."*
The standalone elimination path is the same asymmetry, one step over.

**Mitigating factor, stated fairly**: an elimination claim is
intrinsically safer than a winner claim. In an N-option market, N-1
options genuinely did not win, so base rates favour NO being right, and
a wrong NO on one option is narrower than wrongly crowning a champion.
That is probably why it shipped uncorroborated and has not obviously
burned us. It is a reason the risk is moderate, not a reason it is
absent -- a market closed on "Option X: NO" when X actually won is a
real, expensive error on the user's platform.

**Recommended order (the ordering is the point):**

1. **First, add corroboration to the standalone elimination path.**
   Change `eliminated: dict[str, Verdict]` to accumulate
   `elimination_confirmations: dict[str, list[tuple[str, RankedArticle]]]`
   exactly as `winner_confirmations` already does, then gate the
   `if eliminated:` return on `_corroborating_domain_count(...) >=
   CORROBORATION_MIN_DOMAINS`, falling back to UNCLEAR with
   `_uncorroborated_note` otherwise. This is a pure safety tightening:
   it can only convert currently-asserted verdicts into UNCLEAR, never
   the reverse. Expect the measured `correct` count to DIP on markets
   that were previously resolved off a single elimination source -- that
   dip is the point, not a regression.
   Note the derived-NO path (when a winner IS corroborated, every other
   option gets NO) is untouched and stays safe -- it inherits the
   winner's corroboration.

2. **Then broaden the hedge gate for the elimination branch**, using the
   `BINARY_NO_HEDGE_WORDS` / `_sentence_has_hedge_for_negative_claim`
   pair already built for `_decide_binary`. The refactor is cleaner than
   the note feared and is provably behaviour-preserving for winner
   detection: move the strict `_sentence_has_hedge` check off the shared
   top-of-loop gate and onto the winner and head-to-head branches
   directly, leaving the shared gate on the negation-permissive list.
   Everything computed in between (`lowered`, `context_conflict`,
   `mentioned_options`, `option_mentioned`, `other_mentioned_options`)
   is pure per-sentence computation with no side effects, so the winner
   branches see an identical sentence set to today. Recall added here is
   now backstopped by step 1's corroboration requirement.

3. **Watch the cost.** Elimination verification runs per
   (sentence x option) and, unlike winner confirmations, has no
   per-option cap beyond `option not in eliminated`. Broadening the gate
   on a 30-option market measurably increases NLI calls. If it bites,
   add a `MULTI_OUTCOME_MAX_ELIMINATION_CONFIRMATIONS_PER_OPTION` mirroring
   the winner-side cap.

Steps 1 and 2 should be measured separately on the eval batch, because
they push the numbers in opposite directions and a combined run would
hide both effects.

## Answer to #2: the user's instinct is right, and the fix is a second signal, not a looser one

The note frames this as "surface more of what's already computed."
That undersells it. The real structural gap:

**"Has this been decided yet?" and "what was the outcome?" are separable
questions, and the system can currently only answer the first as a
byproduct of answering the second.**

Every path in `verdict_engine.py` reaches "this market is closeable"
only by successfully resolving the outcome with corroboration. There is
no code path that concludes "the event is plainly over, I just cannot
tell you who won." Yet that state is extremely common in the measured
data -- the Opus plan's own root-cause table puts NO_MATCH at 45/74
(61%): markets where retrieval found real articles and nothing verified.
A large share of those are events that visibly concluded.

The conflict-abstain case makes the gap vivid. When two options are each
independently corroborated as having won, that is the single strongest
"this event definitely finished" signal the system ever produces -- and
it is emitted as `outcome="UNCLEAR", confidence=0.0`. Maximum evidence
that something happened, reported as minimum confidence, because
confidence currently means "confidence in the outcome" and nothing
tracks "confidence that it's over."

**Why a lower bar is legitimate here (this is the part that does not
contradict the last month of work):** the two signals have completely
different costs of being wrong.

- A wrong OUTCOME resolves a market incorrectly. Expensive. Keep
  `CORROBORATION_MIN_DOMAINS = 2` and every hedge guard exactly as
  strict as they are.
- A wrong CLOSEABILITY flag costs one wasted human glance. Cheap.

So closeability can run at a much lower evidence bar without weakening
anything, precisely because a human is the backstop -- which is exactly
what the user described. This is not "lower the standards"; it is
"stop forcing a cheap question to clear an expensive question's bar."

**Shape of the change:** `Verdict` gains a second, independent dimension
-- something like `event_concluded: bool` plus its own confidence --
computed from signals that do not require resolving the outcome:

  - past-tense result language in the retrieved evidence ("defeated",
    "final score", "was awarded", "took office", "lifted the trophy")
  - candidate articles whose `published_date` falls after the market's
    close date / expected event date
  - multiple independent domains publishing about the market's subject
    after that date (coverage volume, no NLI needed)
  - state the engine already computes and currently discards: sentences
    that cleared every hedge/entity gate and scored high on NLI but
    failed corroboration; and the conflict-abstain set above

None of these need a generative model, and the first three need no model
at all. This is also the honest answer to the user's "maybe I designed
this wrong": the retrieval, ranking, hedging and corroboration work all
stay -- they are what makes the OUTCOME half trustworthy. What is
missing is a second, cheaper detector answering the question they
actually care about.

**Suggested sequencing**: this is worth doing before Priority 3 (the
45-market NO_MATCH long tail), because it changes what "recovering" a
NO_MATCH market even means -- many of them may not need their outcome
resolved at all, only to be flagged. Design it once the Priority 1
measurement lands, so the closeability detector can be calibrated
against markets whose outcome the engine provably could not reach.

## Status of Priority 1 measurement

Full cached ~97-market eval re-run (measuring binary-No's 0%-of-19
baseline against the new path) was started but not yet confirmed
complete when this note was written -- results to follow in a separate
update once the run finishes and is checked for `wrong` not rising, per
the Opus plan's own stated success criterion.
