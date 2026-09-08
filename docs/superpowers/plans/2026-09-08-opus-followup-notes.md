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

## Status of Priority 1 measurement

Full cached ~97-market eval re-run (measuring binary-No's 0%-of-19
baseline against the new path) was started but not yet confirmed
complete when this note was written -- results to follow in a separate
update once the run finishes and is checked for `wrong` not rising, per
the Opus plan's own stated success criterion.
