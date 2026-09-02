# resolution_finder/verdict_engine.py
import logging
import re
from datetime import date, datetime
from typing import Optional
from sentence_transformers import util
from resolution_finder.models import Market, RankedArticle, Verdict
from resolution_finder.query_builder import extract_entities
from resolution_finder.relevance_ranker import _get_model

logger = logging.getLogger(__name__)

# Generalized: captures a plain Yes/No default (CLARITY Act, Nobel Prize) OR a
# specific named option default (Vinicius Junior -> "Real Madrid") OR a named
# outcome outside the option list (Osun -> "Other"). The trigger phrases
# anchor on deadline-miss language so this doesn't match an unrelated
# "resolves to X" sentence describing the normal win condition. "not known"
# was added after a real market ("are not known definitively by [date] ...
# resolve to 'Other'") didn't match any of the original trigger phrases.
# "otherwise" was added after finding 9 of 18 real markets in the current
# dataset (2026-08-25) state their default purely as "Otherwise, this
# market will resolve to 'No'." -- no negation word at all, so none of the
# original triggers matched it, silently disabling the deadline-passed
# default for roughly half the dataset. The quote character class also
# now accepts curly quotes (“/”), not just straight ones -- real
# markets.json descriptions use curly quotes around the resolved value,
# which the old \"? literal never matched, so even an "otherwise" trigger
# alone wouldn't have captured the value.
DEFAULT_OUTCOME_PATTERN = re.compile(
    r"(?:not met|has not|have not|not officially|not been|not known|otherwise)[^.]{0,150}?"
    r"resolve[s]?\s+to\s+[\"“]?([A-Za-z][A-Za-z0-9 .&'-]*?)[\"”]?[.\n]",
    re.IGNORECASE | re.DOTALL,
)

# Originally entirely bill-SIGNING vocabulary. Broadened 2026-08-18 (real
# gap confirmed live, first time real evidence reached the verdict engine
# for congress-passes-iran-war-powers-resolution): a market whose Yes
# condition is vote PASSAGE by both chambers (a war powers resolution,
# unlike an ordinary bill, is never signed into law at all) had no matching
# vocabulary even with genuinely confirming evidence in hand. Kept general
# (any bill/resolution/measure, not "Iran" or "war powers" specific) per the
# project's standing rule against overfitting a fix to one event. Distinct
# from "advanced"/"procedural vote"/"moved forward" language, which is NOT
# included here -- those describe a step toward passage, not passage itself,
# and the real evidence that motivated this change was itself an example of
# exactly that weaker, non-confirming phrasing (correctly still NOT a match).
BINARY_YES_KEYWORDS = [
    "signed into law", "became law", "enacted", "approved by both",
    "passed the senate", "senate passed", "passed the house", "house passed",
    "cleared the senate", "cleared the house", "passed both chambers",
    "cleared both chambers", "confirmed by the senate",
    # Real bug found live (2026-08-26): this list was entirely legislative
    # vocabulary -- structurally cannot confirm an FDA drug-approval
    # market, which never gets "signed into law" or "passed" at all. Real
    # evidence ("First approved treatment for thyroid eye disease...")
    # scores 0.908 against the EXISTING, unchanged NLI verification
    # threshold once it's given the chance to be checked -- this was
    # purely a missing keyword, not a verification weakness.
    "fda approves", "fda approved", "fda approval", "approved by the fda",
    "first approved treatment", "receives fda approval", "gains fda approval",
]

# ANNOUNCEMENT_KEYWORDS / ELIMINATION_KEYWORDS / the directional
# head-to-head phrase lists that used to live here were removed
# 2026-08-23: _decide_multi_outcome and _decide_date_thresholds now gate
# candidates on a plain "does this sentence mention the option" check
# instead (see _decide_multi_outcome's own comment for the reasoning --
# any old keyword match already implied the option was mentioned, so the
# new gate strictly subsumes the old one), and let NLI verification
# (_verify_winner_candidate / _verify_head_to_head_candidate /
# _verify_elimination_candidate) decide the outcome direction, not a
# fixed phrase list. See git history for the removed lists and the real
# bugs (BoomBoys' win-loss record, a not-yet-played match, list-order-
# dependent head-to-head crowning) that motivated building them in the
# first place, before this simplification replaced them.

# Trigger phrases confirming a multi-outcome market's date-shaped options
# are CUMULATIVE thresholds ("by August 1" also satisfies "by September 1")
# rather than independent exact-date guesses. Same small-phrase-list style
# as DEFAULT_OUTCOME_PATTERN's trigger list above.
CUMULATIVE_DATE_TRIGGER_PHRASES = [
    "by ", "no later than", "before ", "on or before", "prior to",
]

# Formats real option strings are expected to use. Every option in a
# market must parse fully (the whole string, not a substring) against one
# of these for the market to be treated as date-threshold-shaped -- a
# named option that merely starts with a month name (e.g. "August
# Wilson") must fail every one of these and fall through safely.
_OPTION_DATE_FORMATS = ["%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"]


def _parse_option_as_date(option: str) -> Optional[date]:
    stripped = option.strip()
    for fmt in _OPTION_DATE_FORMATS:
        try:
            return datetime.strptime(stripped, fmt).date()
        except ValueError:
            continue
    return None


def _is_cumulative_date_threshold_market(market: Market) -> bool:
    if not market.options:
        return False
    if any(_parse_option_as_date(opt) is None for opt in market.options):
        return False
    combined = f"{market.title} {market.description}".lower()
    return any(phrase in combined for phrase in CUMULATIVE_DATE_TRIGGER_PHRASES)


# Numeric-threshold binary markets ("Will Bitcoin be above $64,000?"),
# added after live testing found BINARY_YES_KEYWORDS structurally cannot
# resolve these -- it's entirely legislative vocabulary, and no keyword
# list can cover every possible threshold number. This extracts and
# compares real numbers instead. Self-contained here (not imported from
# peer_market.py's near-identical MONEY_PATTERN/PERCENT_PATTERN) so this
# file stays independently swappable per the project's "Verdict Engine
# must be swappable" constraint.
#
# The `(?![a-zA-Z])` after the single-letter `[kmb]` alternative is
# load-bearing -- do NOT remove it. Without it, a number followed by a
# space and an unrelated word starting with k/m/b (e.g. "2023 before",
# "5 minutes", "10 kids") has that word's first letter spuriously
# consumed as a magnitude suffix -- "2023 before" parses as 2023 *
# 1_000_000_000 (misread as billion). Found live (2026-08-22) while
# testing the bare-year fix below; the multi-letter alternatives
# (bn/mm/thousand/million/billion) don't need the same guard since a
# real following word essentially never starts with one of those exact
# letter sequences.
_THRESHOLD_NUMBER = r"\$?\s?(\d[\d,]*(?:\.\d+)?)\s?(bn|mm|thousand|million|billion|[kmb](?![a-zA-Z]))?%?"
_THRESHOLD_MAGNITUDES = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
}

# Only single-threshold direction, not "between X and Y" ranges -- see
# Task 22 design note point 1. Order matters: tried in this sequence, so
# a title using "at least" isn't accidentally caught by a looser pattern.
#
# The last two entries (number BEFORE the trigger word, e.g. "14 or
# more goals") are a separate shape from the first two (trigger word
# BEFORE the number, e.g. "at least 14") -- real bug found live
# (2026-08-23): a real market phrased as "scores 14 or more goals" wasn't
# recognized as numeric-threshold-shaped at all, so it silently fell
# through to _decide_binary instead.
_THRESHOLD_CONDITION_PATTERNS = [
    ("up", re.compile(
        rf"(?:at least|reach(?:es)?|hit|above|over|more than)\s+\(?(?:HIGH\)?\s*)?{_THRESHOLD_NUMBER}",
        re.IGNORECASE,
    )),
    ("down", re.compile(rf"(?:below|under|less than)\s+{_THRESHOLD_NUMBER}", re.IGNORECASE)),
    ("up", re.compile(rf"{_THRESHOLD_NUMBER}\s+or\s+(?:more|greater|higher|above)", re.IGNORECASE)),
    ("down", re.compile(rf"{_THRESHOLD_NUMBER}\s+or\s+(?:less|fewer|lower|below)", re.IGNORECASE)),
]


def _parse_threshold_number(raw: str, suffix: Optional[str]) -> float:
    value = float(raw.replace(",", ""))
    if suffix:
        value *= _THRESHOLD_MAGNITUDES.get(suffix.lower(), 1)
    return value


def _first_sentence(text: str) -> str:
    """The first sentence of `text` that isn't a leading "Note: ..."
    preamble -- a market's description conventionally states its core
    Yes/No condition in its opening sentence, before any exception/
    edge-case elaboration begins, but some real descriptions open with a
    factual caveat ("Note: Current record 13 goals (...).") before that.
    Real bug found live (2026-08-23): a real market's actual condition
    sentence ("...scores 14 or more goals...") was the SECOND sentence,
    after exactly such a "Note:" preamble, so it was invisible to
    threshold detection (which only ever looked at sentences[0])
    entirely."""
    for sentence in _split_sentences(text):
        if not sentence.strip().lower().startswith("note:"):
            return sentence
    return ""


def _extract_threshold_condition(market: Market) -> Optional[tuple[str, float]]:
    """The market's own numeric threshold condition, parsed from its
    title, or -- if the title has no threshold phrase -- the FIRST
    SENTENCE of its description only, e.g. ("up", 64000.0) for "above
    $64,000". Returns None for a market that isn't phrased as a
    numeric-threshold question at all, which is the common case and must
    fall through to the existing _decide_binary unchanged.

    Deliberately title-first, then only the description's opening
    sentence -- NOT the full description: every real numeric-threshold
    market states its own core Yes/No condition directly in the title
    (true of every fixture in this test suite) or, if not, in the
    description's own opening statement, while the REST of a market's
    description often contains extended exception/edge-case language
    that can incidentally contain a threshold-shaped phrase without the
    market actually being a numeric-threshold comparison. Real bug found
    live (2026-08-23): a 5-category typhoon-intensity classification
    market (options=[], title asking specifically about the "Very Strong
    Typhoon" category, no threshold phrasing in the title at all) had an
    unrelated exception clause several sentences into its description --
    "...classifies the system as a tropical depression (below 34 kt)...
    the crossing does not count and the market resolves to 'No
    Qualifying Landfall'" -- describing a DIFFERENT outcome branch, not
    the market's actual question. Searching the full description matched
    "below 34" and wrongly routed the whole market into the numeric-
    threshold decision path, comparing an unrelated number from real
    evidence (a building-damage count, "14,000") against a threshold of
    34 and confidently resolving NO. The market's actual opening sentence
    ("This market resolves to the intensity category...") does not
    contain the false-match phrase, confirmed by re-reading the real
    description, so restricting to it (instead of title-only) still
    excludes this bug while covering a market whose real threshold is
    stated only in its description's first sentence, not its title.
    """
    search_text = f"{market.title} {_first_sentence(market.description)}"
    for direction, pattern in _THRESHOLD_CONDITION_PATTERNS:
        match = pattern.search(search_text)
        if match:
            return direction, _parse_threshold_number(match.group(1), match.group(2))
    return None


# A bare 4-digit number in a plausible calendar-year range. Deliberately
# NOT a blanket "must have $/suffix/%" requirement -- this module also
# needs to support non-dollar numeric-threshold markets (sales counts,
# vote tallies, "reach 1 million users", etc.), where the real threshold
# figure legitimately has no currency symbol or magnitude suffix at all.
# The actual, observed ambiguity is narrower: a bare number that looks
# like a YEAR, sitting in unrelated prose alongside real evidence, gets
# mistaken for the threshold value. A real sales/vote count essentially
# never falls in this exact 4-digit 1900-2099 shape with no other signal.
_BARE_YEAR_PATTERN = re.compile(r"^(?:19|20)\d{2}$")


def _looks_like_bare_year(raw: str, suffix: Optional[str]) -> bool:
    return suffix is None and bool(_BARE_YEAR_PATTERN.match(raw.replace(",", "")))


def _extract_latest_number(sentence: str) -> Optional[float]:
    """The most recently stated number in `sentence` -- real evidence often
    states a prior value before the current one ("up from $61,000 to
    $64,000"), and the current value is conventionally stated last. Known,
    documented heuristic, not perfect for every phrasing (see Task 22
    design note point 4).

    Skips a bare, unadorned number that looks like a calendar year (no "$"
    prefix, no "k"/"m"/"b"/"thousand"/"million"/"billion" suffix, no "%"
    suffix, and shaped like a year) and keeps looking earlier in the
    sentence instead -- real bug found live (2026-08-22): a box-office
    market's ranked evidence was unrelated homepage boilerplate mentioning
    "...making his first return since 2023's Plane", and the bare year
    2023 was picked up as if it were the dollar figure, wrongly resolving
    NO against the market's real $21M threshold. Any OTHER bare number
    (a plain count, not year-shaped) is still accepted as before, so a
    non-dollar threshold market ("reach 1 million users", "get 50,000
    sales") keeps working.
    """
    for match in reversed(list(re.finditer(_THRESHOLD_NUMBER, sentence))):
        raw, suffix = match.group(1), match.group(2)
        if _looks_like_bare_year(raw, suffix):
            continue
        return _parse_threshold_number(raw, suffix)
    return None


# Calibrated empirically (2026-08-23) against real evidence
# ("Just Fontaine's incredible record that still stands") plus several
# constructed cases covering both directions of the failure mode. Real
# TRUE-should-be-YES cases scored 0.002-0.007 on the negative label --
# comfortably below this threshold, so it can't false-trigger on a
# genuine confirmation. The one real motivating NO case scored 0.788.
THRESHOLD_NOT_MET_VERIFICATION_THRESHOLD = 0.7


def _threshold_not_met_hypotheses() -> tuple[str, str]:
    positive = "The record or threshold has been broken or exceeded."
    negative = "The record or threshold remains unbroken, intact, or has not been reached."
    return positive, negative


def _verify_threshold_not_met(sentence: str) -> bool:
    """True if `sentence` entails the market's threshold/record was NOT
    met -- deliberately one-directional (see _decide_numeric_threshold's
    comment for why): only ever used to confirm NO, never YES."""
    positive, negative = _threshold_not_met_hypotheses()
    scores = _classify_scores(sentence, [positive, negative])
    return scores[negative] >= THRESHOLD_NOT_MET_VERIFICATION_THRESHOLD


def _decide_numeric_threshold(
    market: Market, ranked_evidence: list[RankedArticle], direction: str, threshold: float
) -> Verdict:
    # Real bug found live (2026-09-02): the real will-spcx-reach-145-in-
    # august-2026 market (title marker "(HIGH)", truth: Yes) wrongly
    # resolved NO off "SpaceX Stock Price Prediction: SPCX Sinks 35%,
    # Eyes August Earnings" -- a snapshot headline, not the market's real
    # condition ("at any point during August 2026, any 1-minute candle
    # ... has a final High price equal to or above the listed price",
    # per its real description; resolution source is Pyth's own 1-minute
    # candle data, not news coverage at all). No amount of incidental
    # news-headline text can answer a "did the price touch X at ANY
    # point in a window" question -- that needs the real historical
    # price-history data source, which is explicitly deferred (see
    # docs/superpowers/plans/2026-08-10-...: "Submarket price-history
    # tracking for numeric-threshold markets"). The archive pass made
    # this WORSE, not better: it now finds a misleading snapshot where
    # it previously found nothing, converting unresolved into wrong.
    # "(HIGH)"/"(LOW)" in the title is the real, already-used marker for
    # this exact market shape (verified: matches both real price-window
    # markets in the current dataset, no others) -- refuse to assert
    # anything from ordinary evidence for these until the real price
    # source is built, rather than guess from a headline that cannot
    # possibly answer the real question.
    if "(HIGH)" in market.title or "(LOW)" in market.title:
        if ranked_evidence:
            top = ranked_evidence[0]
            return Verdict(
                outcome="UNCLEAR", confidence=top.similarity,
                evidence_snippet=(
                    "This market resolves on a price-history window (see title's "
                    "HIGH/LOW marker), which ordinary news search cannot answer -- "
                    "needs the real historical price API (deferred, not yet built)."
                ),
                source_url=top.article.url, source_type=top.article.source_type,
            )
        return Verdict(outcome="NO_EVIDENCE", confidence=0.0, evidence_snippet=None,
                        source_url=None, source_type=None)

    subject_terms = _subject_terms(market)
    distinctive_terms = _distinctive_subject_terms(market)

    # Phase 3 corroboration (2026-09-02): a single extracted number is no
    # longer enough on its own, symmetric for BOTH sides of the threshold
    # -- unlike _decide_binary/_decide_multi_outcome, which only gate the
    # positive (YES/winner) assertion, the SAME risky "did we grab the
    # right number from the right source" mechanism produces both YES and
    # NO here, so both need the same bar. Real bug this catches: the real
    # Odyssey box-office market resolved a confident WRONG "NO" off a
    # the-numbers.com weekend-projections page that never names the
    # market's subject at all (see docs/superpowers/plans/2026-08-25-
    # unsupported-market-types.md item 5) -- a single misattributed
    # number, asserted with no second source to catch it.
    #
    # Collects every (sentence, item, met) confirmation across the whole
    # evidence set (per item: number-extraction tried first, semantic
    # NO-only fallback only for an item that didn't already confirm via a
    # number -- same per-item gating fix as _decide_binary's own
    # confirmations, and for the same reason: an independent source
    # confirming "still stands" in plain prose, with no digit in it,
    # must still count as a corroborating source, not be silently
    # dropped because a DIFFERENT source elsewhere found a number).
    confirmations: list[tuple[str, RankedArticle, bool]] = []
    for item in ranked_evidence:
        item_confirmed = False
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence) or _sentence_is_interrogative(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms, distinctive_terms):
                continue
            if _sentence_is_vague_reference(sentence):
                continue
            if _sentence_describes_a_different_metric(sentence):
                continue
            value = _extract_latest_number(sentence)
            if value is None:
                continue
            met = value >= threshold if direction == "up" else value <= threshold
            confirmations.append((sentence, item, met))
            item_confirmed = True

        if item_confirmed:
            continue
        # Semantic NO-only fallback: only reached for an item whose
        # sentences had no extractable number at all. Real gap found live
        # (2026-08-23): real evidence for a real market ("Just Fontaine's
        # incredible record that still stands") plainly confirms the
        # threshold was NOT met, in plain English with no digit in it
        # anywhere -- the number-extraction loop above has no way to see
        # this.
        #
        # Deliberately asymmetric -- can only ever contribute a NOT-met
        # (met=False) confirmation, never a met=True one. Real calibration
        # (4 wording attempts) found confirming "the record WAS broken"
        # from prose alone is NOT reliably safe with this model: every
        # wording tested had at least one realistic phrasing ("one short
        # of the record", "well below the target") that scored high for
        # "broken" when it should have been low. But the NOT-met/negative
        # score was reliably LOW on every genuine true-positive case
        # tested (0.002-0.007, no false-trigger risk) -- so restricting
        # this fallback to NOT-met-only can only ever fill in a missed
        # UNCLEAR with a correct NO-side confirmation, never introduce a
        # wrong YES-side one. The number-extraction loop above remains
        # the only way this function ever collects a met=True confirmation.
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence) or _sentence_is_interrogative(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms, distinctive_terms):
                continue
            if _verify_threshold_not_met(sentence):
                confirmations.append((sentence, item, False))
                break

    met_true = [(s, i) for s, i, m in confirmations if m]
    met_false = [(s, i) for s, i, m in confirmations if not m]

    # Conflict is judged on CORROBORATED sides only, not on any stray
    # number that happens to land on the other side of the threshold.
    # Real bug found live (2026-09-02, measured across the full 30-market
    # eval): the naive "any met=True plus any met=False -> abstain" rule
    # turned 3 previously-CORRECT answers into unresolved while converting
    # only 1 wrong one, a net-negative trade on real data. The reasoning
    # error: in a NUMERIC market, numbers falling on both sides of the
    # threshold are the NORMAL case, not a sign of genuine dispute -- a
    # price article legitimately cites many prices, and the real
    # world-cup-most-goals market (truth: No) was vetoed by a headline
    # about a DIFFERENT record entirely ("Messi Breaks All-Time World Cup
    # Scoring Record" -- career goals across tournaments, not the single-
    # tournament record this market asks about) contradicting a properly
    # corroborated "Fontaine's record still stands". Requiring BOTH sides
    # to clear the corroboration bar keeps the abstention for real
    # disagreement while stopping one stray number from vetoing a
    # well-sourced answer. The winning side still needs its own 2+
    # domains, so this stays strictly more conservative than the
    # pre-corroboration code.
    true_corroborated = bool(met_true) and _corroborating_domain_count(met_true) >= CORROBORATION_MIN_DOMAINS
    false_corroborated = bool(met_false) and _corroborating_domain_count(met_false) >= CORROBORATION_MIN_DOMAINS

    if true_corroborated and false_corroborated:
        sentence, item = met_true[0]
        return Verdict(
            outcome="UNCLEAR", confidence=item.similarity,
            evidence_snippet=(
                "Conflicting evidence: independently corroborated sources "
                "disagree on whether the threshold was met -- flagged for "
                "review rather than guessed."
            ),
            source_url=item.article.url, source_type=item.article.source_type,
        )

    if true_corroborated or false_corroborated:
        bucket, outcome = (met_true, "YES") if true_corroborated else (met_false, "NO")
        sentence, item = bucket[0]
        return Verdict(
            outcome=outcome, confidence=item.similarity,
            evidence_snippet=sentence.strip()[:280],
            source_url=item.article.url, source_type=item.article.source_type,
        )

    if met_true or met_false:
        bucket = met_true or met_false
        sentence, item = bucket[0]
        return Verdict(
            outcome="UNCLEAR", confidence=item.similarity,
            evidence_snippet=(sentence.strip()[:280] + _uncorroborated_note(bucket)),
            source_url=item.article.url, source_type=item.article.source_type,
        )

    if ranked_evidence:
        top = ranked_evidence[0]
        return Verdict(outcome="UNCLEAR", confidence=top.similarity,
                        evidence_snippet=top.text[:280], source_url=top.article.url,
                        source_type=top.article.source_type)

    return Verdict(outcome="NO_EVIDENCE", confidence=0.0, evidence_snippet=None,
                    source_url=None, source_type=None)


# The `(?<![A-Z]\.)` lookbehind is load-bearing — do NOT "simplify" it away.
# Without it, a period preceded by a single capital letter (the "S." in "U.S.",
# the "R." in "H.R. 3633") counts as a sentence boundary, and the split lands
# BETWEEN a disqualifying signal and the keyword: "The GENIUS Act was passed by
# the U.S. Senate and signed into law in July 2025." becomes "...the U.S." +
# "Senate and signed into law in July 2025." — the second fragment carries the
# keyword with no "GENIUS" left in it, so the wrong-subject veto never sees the
# other entity and the exact production false positive this module exists to
# prevent comes right back. This domain is US legislation, so "U.S. Senate",
# "U.S. House" and "H.R. ####" are everywhere, including in market descriptions.
# Known residual gap (accepted): Title-case abbreviations like "Sen.", "Rep."
# and "Jan." still split, since matching those needs a real abbreviation list.
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<![A-Z]\.)(?<=[.!?])\s+")

# Words/phrases that turn a sentence hypothetical or negated, e.g. "if
# enacted" or "has not been signed" — a keyword match inside one of these
# doesn't describe something that actually happened.
#
# The "attribution"/"future" phrases below were added 2026-08-23 after the
# user asked directly: what stops an opinion or a not-yet-decided event
# from being read as a confirmed fact just because it's phrased assertively?
# Two real, live-confirmed cases:
#   - Attribution/opinion (constructed, then verified): "Analysts say Team
#     Spirit is basically eliminated from the tournament..." matched
#     "eliminated from" and was wrongly treated as a confirmed elimination
#     -- it's someone's assessment, not a reported fact.
#   - Future/scheduling (found live, real search results, same session):
#     "...where they will face the winner of the lower bracket semifinals
#     between Team Spirit and BoomBoys..." matched "winner of" near "Team
#     Spirit" and was wrongly treated as a confirmed tournament win --
#     the match described hasn't been played yet, and neither name is a
#     "winner" of anything at the point this sentence describes.
# Both are the same underlying gap: grammatically assertive phrasing does
# not mean an already-settled fact. Kept as small, general phrase lists
# (not exhaustive) in the same style as the rest of this list.
NEGATION_HEDGE_WORDS = [
    "not ", "n't ", "never ", "without ", "fails to", "failed to",
    "yet to", "has yet", "remains uncertain", "uncertain", "unclear",
    "unlikely", "pending", "awaiting", "no vote", "not scheduled",
    "if ", "unless ", "would be", "could be", "might be",
    # Real bug found live (2026-09-02): "may " -- despite "might be"/
    # "could be"/"would be" already being covered above -- was missing
    # entirely. Real evidence, a vague multi-sport summary headline
    # ("Famous records that may soon be broken"), never got hedge-
    # blocked at all, letting an unrelated number ("800m landmarks", an
    # athletics reference) reach number-extraction and wrongly confirm a
    # numeric-threshold market.
    #
    # TRIED AND REJECTED: bare "may " -- regressed 3 existing tests. The
    # real Lakers/Rockets true-positive uses "may have accomplished more
    # than..." -- a RHETORICAL hedge about the SIGNIFICANCE of an
    # already-confirmed victory ("playoff victory over the Houston
    # Rockets" is stated as settled fact), not genuine uncertainty about
    # whether it happened. "may soon"/"may be broken" (future tense,
    # genuine uncertainty about occurrence) is a different grammatical
    # pattern than "may have" (present perfect, rhetorical) -- narrowed
    # to the phrases the real sentence actually needs instead of bare
    # "may " catching both.
    "may soon", "may be broken", "may break",
    "analysts say", "some say", "pundits say", "reportedly", "allegedly",
    "is speculated", "some believe", "many believe", "it is believed",
    "rumored", "sources say", "some argue", "experts say",
    "will face", "will play", "will meet", "will take on",
    "is set to", "are set to", "scheduled to",
    # Real bug found live (2026-08-23): a real pre-tournament preview
    # sentence ("Defending champions Team Falcons are raring to retain
    # the Aegis at Dota 2 TI 2026, but they face a stacked field...")
    # scored 0.926 for "has won the tournament" -- well above threshold.
    # "Defending champions" (their PAST title) plus "raring to retain"
    # (future intent) reads as strong topical/lexical confirmation to
    # the model, but describes an outcome not yet decided. None of the
    # existing future-tense hedges ("will face", "is set to") catch this
    # present-tense aspiration phrasing.
    "raring to", "hoping to", "hope to", "aiming to", "aim to",
    "looking to", "look to", "bidding to", "bid to", "gunning for",
    "eyeing a", "eyeing the",
    # Real bug found live (2026-08-25): real evidence for the 2026 Nobel
    # Peace Prize -- "In addition to UNRWA, the ICJ was also nominated
    # for its seeming contributions to peace..." -- wrongly confirmed
    # UNRWA as the WINNER. Being a candidate/nominee is a different claim
    # than having won; a market with many listed candidates will surface
    # nomination-stage news for options that never win, same failure
    # shape as "raring to" (describes standing/eligibility, not a
    # decided outcome).
    #
    # Qualified with "for" ("nominated FOR", "a nominee FOR"), not bare
    # "nominated"/"nomination"/"nominee": real regression found live
    # (2026-08-26) -- for a NOMINATION-CONTEST market (a party primary),
    # unlike an AWARD market (the Nobel Prize), "becomes the nominee" /
    # "receives the nomination" IS the win condition, not mere candidacy.
    # Bare "nominee" wrongly hedged the real evidence "It's official:
    # Kamala Harris becomes Democrats' 2024 presidential nominee - NPR"
    # (which direct calibration confirmed scores 0.997 on the winner-
    # verification NLI check once it's even given the chance), and the
    # market wrongly crowned a different option off weaker evidence
    # instead. "nominated for [an award]" / "a nominee for [a prize]" is
    # unambiguous candidacy language in a way bare "nominee" is not.
    "nominated for", "nomination for", "a nominee for", "shortlisted for",
    # Real bug found live (2026-08-26): real evidence for the 2026 NHL
    # Stanley Cup Champion market -- "The Pittsburgh Penguins returned to
    # the Stanley Cup Playoffs in 2026." -- wrongly confirmed the
    # Penguins as CHAMPION. Reaching the playoffs is eligibility to
    # compete for a championship, not the outcome itself -- same
    # "describes standing, not a decided outcome" shape as nomination
    # above, different domain (any single-elimination tournament/playoff
    # market with many listed teams will surface "made the playoffs"
    # news for teams that don't go on to win it all).
    # NEGATION_HEDGE_WORDS is a plain substring match (see
    # _sentence_has_hedge), not a fuzzy one -- "returned to the playoffs"
    # alone did NOT match the real sentence, which says "returned to the
    # Stanley Cup Playoffs" (an extra name in between), so the specific
    # phrase is listed alongside the generic one.
    "returned to the playoffs", "returned to the stanley cup playoffs",
    "made the playoffs", "reached the playoffs",
    "qualified for the playoffs", "advanced to the playoffs",
    "made the postseason", "reached the postseason", "clinched a playoff spot",
    # Real bug found live (2026-09-02, corroboration re-verification run):
    # real evidence for the World Cup most-goals-record market -- "Watch
    # Out, Messi: Mbappe Scores 18th World Cup Goal, One Shy Of All-Time
    # Record" -- wrongly confirmed the record as BROKEN. "One shy of"/
    # "one short of"/"one behind" plainly states the record was NOT met --
    # a near-miss is reported as sports news precisely because it's
    # notable that it DIDN'T happen, the same shape of gap as this file's
    # own numeric-threshold NO-only fallback already documented ("one
    # short of the record" scores high for "broken" on the model, hence
    # that path being restricted to NO-only) -- generalizable phrasing,
    # not specific to this one tournament or sport.
    "one shy of", "shy of the record", "one short of", "short of the record",
    "one behind the record", "one behind the all-time record",
]

# Generic quantifier+"other" hedges of SPECIFICITY -- e.g. "numerous other
# records have been broken" -- distinct from NEGATION_HEDGE_WORDS (hedges
# on certainty) and _sentence_mentions_other_entity (hedges on subject
# identity, by naming a different capitalized entity). This is a third
# failure mode: a sentence that stays on the market's own topic (no other
# named entity to catch) but never actually confirms the SPECIFIC claim in
# question, only gestures at other/unspecified instances of it. Confirmed
# live bug (2026-08-18 retest): the semantic fallback resolved a World Cup
# "most goals by a single player" market YES on "Since then, numerous other
# records have been broken by both individual players and national squads."
# -- topically on-subject, ground truth NO. Kept domain-general (not
# hardcoded to "records") since the semantic fallback runs across many
# market domains (legislative, sports, drug approvals, etc.).
#
# "records were broken"/"records broken" added 2026-08-23 after a second,
# real live bug in the same family, this time in _decide_numeric_threshold's
# number-extraction loop (not the semantic fallback): "Several records were
# broken at the 2026 World Cup... a record 48 teams were invited to
# participate" -- doesn't contain "other", so the original phrase list
# missed it, and _extract_latest_number grabbed the unrelated "48" (team
# invite count) and wrongly compared it against the market's real 13-goal
# threshold.
VAGUE_REFERENCE_HEDGE_WORDS = [
    "other records", "numerous other", "various other", "several other",
    "many other", "other such", "records were broken", "records broken",
]


def _sentence_is_vague_reference(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(phrase in lowered for phrase in VAGUE_REFERENCE_HEDGE_WORDS)


# Real bug found live (2026-08-23), same session as the two guards above but
# a different failure shape: not a vague/unspecified reference, but a
# genuine, specific number that measures a DIFFERENT metric than the
# market's own threshold. Real evidence for the real Bitcoin $64,000 PRICE
# market: "Binance Bitcoin volume ratio hits record as futures outweigh
# spot eight times over ... The ratio now stands at 7.82..." --
# _extract_latest_number grabbed the 7.82 (a futures-to-spot VOLUME RATIO)
# and compared it directly against the $64,000 price threshold, wrongly
# resolving NO. This is the same root problem the plan doc's "Submarket
# price-history tracking" backlog item describes (no way to verify an
# extracted number matches the market's real metric) -- but unlike that
# item's general case (which needs a real price-history data source, not
# a quick fix), THIS specific failure has one clear, catchable, reusable
# signal: the sentence explicitly names the number as a "ratio", not a
# price/count. A narrow, targeted guard, not a substitute for that larger
# feature.
_DIFFERENT_METRIC_HEDGE_WORDS = [
    "ratio", "multiple of", "times over", "times higher", "times outweigh",
]


def _sentence_describes_a_different_metric(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(phrase in lowered for phrase in _DIFFERENT_METRIC_HEDGE_WORDS)


# A bare acronym (e.g. "CLARITY") that Task 4's extract_entities won't catch
# on its own, since that regex requires 2+ consecutive capitalized words and
# a title like "Will the CLARITY act..." has a lowercase word right after it.
ACRONYM_PATTERN = re.compile(r"\b[A-Z]{2,}\b")

# ENTITY_PATTERN requires a capitalized word to start an entity match, so a
# sentence-initial "The"/"A"/"An" -- capitalized only because it's the first
# word of the sentence, not because it's part of a proper noun -- gets swept
# into the match (e.g. "The Senate passed..." extracts as "The Senate").
# Real bug found live (2026-08-19): a market's subject term for this same
# institution comes from mid-sentence text in the market's own description
# ("...the U.S. Senate pass..."), so it never carries a "The" prefix -- "the
# senate" and "u.s. senate" are then neither a substring of the other, and
# the wrong-subject veto incorrectly rejects a genuinely on-topic sentence.
# Stripping the leading article before comparison is domain-general (not
# specific to any institution/event) and safe-direction: it can only make
# the veto fire less, never introduce a new false "same entity" match, since
# it never merges two otherwise-distinct entity strings together.
_LEADING_DETERMINER_PATTERN = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)


def _normalize_entity(entity: str) -> str:
    return _LEADING_DETERMINER_PATTERN.sub("", entity).strip().lower()


def _word_boundary(phrase: str) -> str:
    r"""Regex source matching `phrase` only as whole words.

    Keywords are matched on word boundaries rather than as bare substrings so
    that e.g. "wins" does not fire on "Winston" — a real risk now that market
    options include short common words like "Arsenal".

    `\b` is only added on an edge that is actually a word character; a phrase
    ending in punctuation (an option like "Acme Inc.") would otherwise produce
    a pattern that can never match.
    """
    escaped = re.escape(phrase)
    prefix = r"\b" if phrase[:1].isalnum() or phrase[:1] == "_" else ""
    suffix = r"\b" if phrase[-1:].isalnum() or phrase[-1:] == "_" else ""
    return prefix + escaped + suffix


def _contains_keyword(text: str, keyword: str) -> bool:
    return re.search(_word_boundary(keyword), text) is not None


# Real gap found live (2026-08-26): the real epl-team-to-qualify-for-
# uefa-champions-league market's real confirming evidence -- "Arsenal,
# Man Utd, Liverpool, Man City and Aston Villa will all take part in the
# 2026/27 Champions League" -- never even became a CANDIDATE for
# "Manchester United", because British football journalism commonly
# abbreviates club names, and the option-mention gate only checks
# whether the full option string appears verbatim in the sentence (not
# the reverse -- a shorthand mention doesn't satisfy it either). The
# market correctly fell through to a safe UNCLEAR rather than a wrong
# answer, but the real, findable confirmation was sitting right there.
#
# Deliberately a SMALL, conservative list: only nicknames that are
# unambiguous in ordinary English text (never mistaken for something
# else). "Man Utd"/"Man City"/"Spurs"/"Wolves" essentially only ever
# appear in a football context. Common but AMBIGUOUS nicknames --
# "Forest" (Nottingham Forest), "Palace" (Crystal Palace), "Villa"
# (Aston Villa), bare "Brighton" -- are deliberately left OUT: they're
# ordinary English words/place names with real false-trigger risk, and
# missing a match here means a safe UNCLEAR, not a wrong answer, which
# is the acceptable failure mode.
TEAM_NICKNAME_ALIASES: dict[str, list[str]] = {
    "Manchester United": ["man utd", "man united"],
    "Manchester City": ["man city"],
    "Tottenham Hotspur": ["spurs"],
    "Wolverhampton Wanderers": ["wolves"],
}


def _option_mentioned(lowered_sentence: str, option: str) -> bool:
    """True if `lowered_sentence` names `option`, either by its own full
    name or one of its known, unambiguous nicknames."""
    option_lower = option.strip().lower()
    if _contains_keyword(lowered_sentence, option_lower):
        return True
    return any(
        _contains_keyword(lowered_sentence, alias)
        for alias in TEAM_NICKNAME_ALIASES.get(option, [])
    )


def _split_sentences(text: str) -> list[str]:
    return [s for s in SENTENCE_SPLIT_PATTERN.split(text) if s.strip()]


def _sentence_has_hedge(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(word in lowered for word in NEGATION_HEDGE_WORDS)


def _sentence_is_interrogative(sentence: str) -> bool:
    """True if `sentence` IS a question, not merely mentions one -- an
    interrogative headline speculates ("Can Edmonton Oilers end it?"), it
    doesn't report a result. Real regressions found live (2026-08-26)
    after date-scoped archive retrieval started supplying HEADLINES as
    evidence, which made this failure mode far more common than full
    article prose ever did: "Canada's Stanley Cup drought is decades
    long: Can Edmonton Oilers end it?" wrongly confirmed a Canadian team
    winning; "Who could replace Joe Biden as the 2024 Democratic
    nominee?" wrongly confirmed a nominee. `_split_sentences` splits on
    "?" followed by whitespace, so the real question sentence keeps its
    own trailing "?" once split from any trailing attribution ("... end
    it? - usatoday.com" splits into the question and a separate,
    keyword-free "- usatoday.com" fragment) -- checking the END of the
    sentence, not just whether it CONTAINS "?" anywhere, avoids rejecting
    a sentence that merely quotes a question mark mid-clause."""
    return sentence.rstrip().endswith("?")


def _subject_terms(market: Market) -> list[str]:
    """Distinctive terms identifying this market's own subject, so a keyword
    match about a different named entity mentioned elsewhere in the same
    article (e.g. a comparable law cited for context) isn't mistaken for
    evidence about this market."""
    combined = f"{market.title} {market.description}"
    terms = list(extract_entities(combined))
    for word in ACRONYM_PATTERN.findall(market.title):
        if word not in terms:
            terms.append(word)
    return terms


# Connector words the entity regex itself treats as glue (see
# query_builder.ENTITY_PATTERN) -- stripped back out here so they never
# count as the "shared word" that proves two entity phrases are the same
# subject.
_ENTITY_CONNECTOR_WORDS = {"of", "the", "for", "and"}


def _distinctive_subject_terms(market: Market) -> list[str]:
    """Narrower than _subject_terms: entities/acronyms drawn from the
    market's TITLE only, not its description.

    Used only to decide whether a sentence that also names some OTHER
    entity is nonetheless clearly still about this market. The full
    description is too generic for that job -- e.g. nearly every US
    legislation market's description separately mentions "the U.S.
    Senate" as part of standard passage-requirement boilerplate, so its
    presence in a sentence proves nothing about which specific bill that
    sentence is about. The title, by contrast, IS the thing that makes
    this market distinguishable from every other market in the same
    domain, so a real overlap with it is a real signal.
    """
    terms = list(extract_entities(market.title))
    for word in ACRONYM_PATTERN.findall(market.title):
        if word not in terms:
            terms.append(word)
    # Real bug found live (2026-08-26): extract_entities' pattern requires
    # TWO OR MORE consecutive capitalized words, so a market whose only
    # distinctive title word is a single proper noun ("Congress passes
    # Epstein disclosure bill/resolution in 2025?" -- "Epstein" has no
    # adjacent capitalized word) extracts NOTHING at all here, silently
    # disabling this escape hatch for that market entirely: ANY sentence
    # mentioning an unrelated entity gets vetoed from then on, even one
    # that ALSO genuinely names the market's real subject in the same
    # breath. Restricted to non-title-initial words -- the first word is
    # ambiguous (capitalized purely by sentence-start convention, not
    # necessarily a proper noun), but a capitalized word LATER in the
    # title is a much more reliable name signal. "Congress" (title-
    # initial) is deliberately NOT picked up this way, matching the
    # existing "generic reused domain term" reasoning already applied to
    # "U.S. Senate" boilerplate above. All-caps words are excluded (an
    # acronym, already handled by ACRONYM_PATTERN just above).
    for word in market.title.split()[1:]:
        cleaned = word.strip(".,;:!?()[]\"'")
        if len(cleaned) >= 4 and cleaned[0].isupper() and cleaned[1:].islower() and cleaned not in terms:
            terms.append(cleaned)
    return terms


def _entity_words(entity: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", entity.lower())
    return {w for w in words if w not in _ENTITY_CONNECTOR_WORDS and len(w) >= 3}


def _sentence_mentions_other_entity(
    sentence: str, subject_terms: list[str], distinctive_terms: Optional[list[str]] = None
) -> bool:
    """True if the sentence names a capitalized entity/acronym that isn't
    (even partially) one of this market's own subject terms, AND the
    sentence doesn't ALSO clearly name the market's own DISTINCTIVE
    subject (title-derived terms, matched word-by-word) — a signal the
    sentence is about something else entirely. Empty subject_terms means
    we have no way to tell our own subject apart, so never reject on this
    basis alone.

    Real bug found live (2026-08-23): a sentence can legitimately name
    both the market's subject AND an unrelated entity in the same breath,
    e.g. real evidence "Sanofi's subcutaneous Sarclisa Escena approved in
    the US" -- "Sarclisa Escena" (a specific product-name variant) doesn't
    substring-match the subject term "Subcutaneous Sarclisa" as a whole
    phrase, but they share the distinctive word "Sarclisa". The old logic
    vetoed the whole sentence as soon as it hit ANY non-matching entity,
    even when the sentence's own subject was also named -- wrongly
    discarding real confirming evidence.

    Word-overlap against `distinctive_terms` (title-only), not the full
    `subject_terms` (title+description), is deliberate: an earlier version
    of this fix checked for ANY subject-term overlap and reopened the
    original production false positive this veto exists to catch --
    "The GENIUS Act was passed by the U.S. Senate..." wrongly matched
    CLARITY_MARKET because "U.S. Senate" is generic boilerplate present in
    CLARITY's own description too, even though the sentence is about a
    completely different bill. Title terms don't have that problem: they
    ARE the thing that distinguishes this market from every other one.

    An article-lead-sentence fallback (mirroring the year-conflict guard's
    fix) was tried and REJECTED (2026-08-26): it reopened this exact
    GENIUS/CLARITY false positive -- the CLARITY test's lead sentence
    happens to mention CLARITY, which then let a LATER sentence entirely
    about the unrelated GENIUS Act through. Unlike the year-conflict case,
    a real subject mention elsewhere in the article does NOT reliably mean
    a later sentence naming a different entity is still on-topic; it can
    just as easily be a genuine comparison to something else, which is
    exactly what this guard exists to catch. Left as a known gap (real
    evidence for the Viridian FDA-approval market, mentioning a royalty
    partner without repeating "Viridian", still gets vetoed) rather than
    risk reopening a confirmed real bug for an unconfirmed narrower one.
    """
    if not subject_terms:
        return False
    subject_lower = [_normalize_entity(t) for t in subject_terms]
    distinctive_words: set[str] = set()
    for term in distinctive_terms or []:
        distinctive_words |= _entity_words(term)
    sentence_entities = extract_entities(sentence) + ACRONYM_PATTERN.findall(sentence)
    subject_mentioned = False
    other_entity_found = False
    for entity in sentence_entities:
        entity_lower = _normalize_entity(entity)
        if not entity_lower:
            continue
        if not any(entity_lower in s or s in entity_lower for s in subject_lower):
            other_entity_found = True
        if distinctive_words and _entity_words(entity) & distinctive_words:
            subject_mentioned = True
    return other_entity_found and not subject_mentioned


# Same-entity-name-different-calendar-instance problem: for a RECURRING
# event (the same two teams playing every season, an annual award, etc.),
# _sentence_mentions_other_entity can't help -- the named entities (team
# names) are IDENTICAL across different years, only the year itself
# distinguishes which instance a sentence is about. Real bug found live
# (2026-08-23): a market about the 2026 Lakers-Rockets playoff series
# wrongly resolved YES on real historical evidence about their 2009
# series ("The Lakers defeated the Rockets ... during the 2009 Western
# Conference Semifinals") -- same team names, completely different year,
# and nothing in the pipeline checked.
#
# Deliberately scoped narrow: only applied in _decide_multi_outcome, and
# only as a gate on a sentence that ALREADY matched a winner/elimination
# phrase -- NOT a blanket "reject any sentence with a non-matching year"
# filter. That broader version was tried first and rejected: ordinary
# prose legitimately mentions incidental years for context ("the game has
# sold 1.2M copies since its 2023 launch") without being about a
# different event instance, and blanket-rejecting on any year mismatch
# would have broken real numeric-threshold evidence that already worked.
_YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")


def _market_expected_year(market: Market) -> Optional[int]:
    """The single calendar year this market's own event is understood to
    be about, read from its title/description if stated explicitly (e.g.
    "2026 NBA Playoffs First Round", "The International 2026") --  falls
    back to the market's close_date year if no year is stated anywhere.
    """
    combined = f"{market.title} {market.description}"
    match = _YEAR_PATTERN.search(combined)
    if match:
        return int(match.group())
    return market.close_date.year if market.close_date else None


def _sentence_mentions_conflicting_year(sentence: str, expected_year: Optional[int]) -> bool:
    """True if `sentence` states at least one year and NONE of them match
    `expected_year` -- a signal this specific sentence narrates a
    different calendar instance of a recurring event. A sentence with no
    year at all is not rejected on this basis (same fail-safe-only-on-a-
    -positive-signal principle as _sentence_mentions_other_entity)."""
    if expected_year is None:
        return False
    years = {int(y) for y in _YEAR_PATTERN.findall(sentence)}
    return bool(years) and expected_year not in years


# Sibling gap to the year check above: it only catches an EXPLICIT year
# number, but a sentence can narrate a recurring, multi-instance pattern
# with no year at all ("the past two years", "in recent seasons",
# "historically") -- the same "different calendar instance" problem in
# different clothing. Real bug found live (2026-08-23), reproduced against
# the actual production decide() (not just this helper in isolation): a
# real NBA market ("...Lakers vs. Rockets") wrongly resolved BOTH options
# eliminated on a sentence about an unrelated player's free-agency history
# ("...those are the two West rivals that knocked the Rockets out of the
# playoffs the past two years") -- no explicit year, so
# _sentence_mentions_conflicting_year couldn't help, and NLI verification
# confidently (0.998-0.999) read "knocked ... out of the playoffs" as a
# clean elimination regardless of when.
#
# An NLI-based fix (a 3-way "different/past occurrence" hypothesis, the
# same pattern that fixed the head-to-head order-dependence bug) was tried
# and rejected: real calibration showed it did NOT fix the actual bug
# (the real sentence still scored "eliminated" at 0.951, above threshold)
# AND broke a previously-correct case (a genuinely CURRENT, ongoing-series
# sentence phrased in ordinary past tense -- "BoomBoys lost ... in Game 2,
# but lead the series 2-1" -- got mistaken for "different occurrence" at
# 0.549, since virtually all real evidence is past-tense by the time it's
# reported, and the model latched onto tense rather than the real signal).
# The real distinguishing feature is a RELATIVE, recurring-range phrase,
# which is a surface/syntactic pattern, not a semantic one -- a fixed,
# closed phrase list is the right tool here, not NLI.
_RELATIVE_RECENCY_PATTERN = re.compile(
    r"\b(?:the\s+)?(?:past|last)\s+(?:\d+|few|couple(?:\s+of)?|two|three|four|five|several)\s+(?:years?|seasons?)\b",
    re.IGNORECASE,
)
_RELATIVE_RECENCY_PHRASES = [
    "in recent years", "in recent seasons", "historically",
    "in previous years", "in previous seasons", "in past seasons",
    "over the years", "in years past",
]


def _sentence_mentions_relative_recency(sentence: str) -> bool:
    """True if `sentence` uses a relative, multi-instance time reference
    ("the past two years", "historically") instead of describing a single
    specific occurrence. Deliberately a closed, fixed phrase set, not a
    general "is this about the past" judgment -- ordinary single-event
    past-tense reporting ("The Lakers defeated the Rockets last night")
    must NOT be caught by this; only an explicit recurring-range phrase
    does."""
    lowered = sentence.lower()
    if _RELATIVE_RECENCY_PATTERN.search(lowered):
        return True
    return any(phrase in lowered for phrase in _RELATIVE_RECENCY_PHRASES)


# Sibling gap to both checks above: "last season"/"this season" is neither
# an explicit year (the year-conflict check needs one stated in the
# sentence) nor a vague recurring-range reference (deliberately excluded
# from _sentence_mentions_relative_recency -- see its own docstring: a
# single specific past occurrence like "last night" must NOT be treated
# as vague). It's a real, single-instance reference, just stated RELATIVE
# to when the article was published rather than by an explicit year. Real
# bug found live (2026-08-26): real evidence for premier-league-winner-
# 24-25 (asking about the already-decided 2024-25 season) read "Winning
# the Premier League last season finally answered..." -- Arsenal's
# 2025-26 title, from an article published in August 2026 -- wrongly
# crowned Arsenal. Resolving what "last season" numerically means needs
# the article's own publish date, which evidence_retriever.py now
# populates from the RSS feed's own <pubDate> (previously declared on
# ArticleRef but never actually read). Fail-safe: no signal at all
# (returns False) if that date isn't available -- never guesses.
_RELATIVE_SEASON_PHRASES = ["last season", "this season"]

# Northern-hemisphere annual leagues (soccer, NHL, NBA) all roughly follow
# a July/August season start -- a reasonable, if imperfect, general cutover
# for resolving "this season" from an arbitrary publish date without
# needing a per-sport calendar.
_SEASON_START_MONTH = 7


def _implied_season_start_year(published: date, relative_phrase: str) -> int:
    current_season_start = published.year if published.month >= _SEASON_START_MONTH else published.year - 1
    if relative_phrase == "last season":
        return current_season_start - 1
    return current_season_start


def _sentence_mentions_conflicting_relative_season(
    sentence: str, expected_year: Optional[int], published_date: Optional[date],
) -> bool:
    if expected_year is None or published_date is None:
        return False
    lowered = sentence.lower()
    for phrase in _RELATIVE_SEASON_PHRASES:
        if phrase in lowered:
            return _implied_season_start_year(published_date, phrase) != expected_year
    return False


# Different failure shape again: not a wrong TIME, a wrong COMPETITION.
# Real bug found live (2026-08-26): the real epl-team-to-qualify-for-uefa-
# champions-league market (real winner Manchester United) wrongly crowned
# Crystal Palace off a sentence entirely about the CONFERENCE LEAGUE -- a
# different, lower-tier UEFA club competition Crystal Palace actually won
# and is playing in this season, not the Champions League the market
# asks about. The three competitions share enough surface vocabulary
# ("champions", "the draw", "qualified", team names) that ordinary
# keyword/option matching can't tell them apart -- this needs an explicit
# same-competition check, the same shape as the year-conflict guard but
# for a different kind of "different instance of a recurring thing".
_UEFA_COMPETITION_NAMES = ["champions league", "europa league", "conference league"]


def _market_named_competition(market: Market) -> Optional[str]:
    combined = f"{market.title} {market.description}".lower()
    for name in _UEFA_COMPETITION_NAMES:
        if name in combined:
            return name
    return None


def _sentence_mentions_conflicting_competition(sentence: str, expected_competition: Optional[str]) -> bool:
    if expected_competition is None:
        return False
    lowered = sentence.lower()
    mentioned = [name for name in _UEFA_COMPETITION_NAMES if name in lowered]
    return bool(mentioned) and expected_competition not in mentioned


# Same-year, different-MEETING problem: two teams can play each other more
# than once within a single year -- a regular-season game and a separate
# playoff series, or twice in a round-robin-then-knockout tournament. The
# year-conflict check above can't catch this since the year is identical
# either way. Real bug found live (2026-08-23), by construction (not yet
# observed from real search results, but realistic and directly analogous
# to the year bug): a market specifically about a 2026 NBA PLAYOFF series
# wrongly resolved YES on evidence about a same-year REGULAR SEASON game
# between the same two teams.
#
# Deliberately scoped to the single clearest, most common real-world
# distinction (playoff/postseason vs. regular season) rather than a full
# round-name taxonomy (First Round/Semifinals/Group Stage/Round of 16/...
# varies too much by sport and competition to generalize safely without a
# real case to design each one against -- see the plan doc Backlog section
# for this as a documented, narrower-still residual gap).
_PLAYOFF_PHRASES = ["playoff", "playoffs", "postseason"]
_REGULAR_SEASON_PHRASES = ["regular season"]


def _market_is_playoff_context(market: Market) -> Optional[bool]:
    """True if the market's own title/description explicitly states a
    playoff/postseason context, False if it explicitly states a regular-
    season context, None if neither is stated (the common case -- most
    tournament/award/legislative markets have no such ambiguity at all,
    and are never checked against this)."""
    combined = f"{market.title} {market.description}".lower()
    is_playoff = any(p in combined for p in _PLAYOFF_PHRASES)
    is_regular = any(p in combined for p in _REGULAR_SEASON_PHRASES)
    if is_playoff and not is_regular:
        return True
    if is_regular and not is_playoff:
        return False
    return None


def _sentence_mentions_conflicting_phase(sentence: str, market_is_playoff: Optional[bool]) -> bool:
    """True if `sentence` explicitly states the OPPOSITE phase from the
    market's own (playoff evidence for a regular-season market, or vice
    versa) and does not also mention the market's own phase. Same
    fail-safe-only-on-a-positive-signal principle as the year check --
    market_is_playoff is None (most markets) never rejects anything."""
    if market_is_playoff is None:
        return False
    lowered = sentence.lower()
    if market_is_playoff and any(p in lowered for p in _REGULAR_SEASON_PHRASES) and not any(
        p in lowered for p in _PLAYOFF_PHRASES
    ):
        return True
    if market_is_playoff is False and any(p in lowered for p in _PLAYOFF_PHRASES) and not any(
        p in lowered for p in _REGULAR_SEASON_PHRASES
    ):
        return True
    return False


# Calibrated empirically against real evidence sentences (Task 23 Step 3)
# -- do not change these without re-running that calibration. Real run:
# expected=True  positive_sim=0.750 negative_sim=0.750 margin=-0.000  (Sanofi/FDA)
# expected=True  positive_sim=0.765 negative_sim=0.784 margin=-0.020  (World Cup record)
# expected=False positive_sim=0.425 negative_sim=0.430 margin=-0.005  (Sanofi, "expected to review")
# expected=False positive_sim=0.514 negative_sim=0.529 margin=-0.015  (World Cup, "could be broken")
# The positive/negative templates share the market's own title as a long
# common prefix, so the margin between them turns out to be a weak signal
# in practice (near zero, sometimes slightly negative, even for genuinely
# confirmed cases) -- the threshold on positive_sim alone is what actually
# separates the True cases (0.750/0.765) from the False cases (0.425/0.514)
# here. THRESHOLD is set just below the lowest True positive_sim, well
# above the highest False positive_sim. MARGIN is set just below the
# lowest True margin so it doesn't reject the real motivating cases, and
# still exists as a guard against a sentence that scores high similarity
# to BOTH templates (genuinely ambiguous), per design decision 3.
#
# NOTE (Task 23 self-review finding): an attempt to lower
# SEMANTIC_CONFIRMATION_THRESHOLD to 0.65 -- to also cover an alternate
# real phrasing of the World Cup case used in Step 5's re-verification
# script ("The all-time tournament scoring record was broken on Tuesday
# when the striker netted his 16th goal...", positive_sim=0.668) --
# caused a regression: an existing safety-test sentence ("The CLARITY Act
# remains stalled in the Senate.") scored positive_sim=0.656, margin
# -0.014, close enough to slip past both a 0.65 threshold and the -0.03
# margin gate and produce a false YES. That false-positive case's margin
# (-0.014) is actually LESS negative (more "confident") than the genuine
# World Cup Step 5 case's margin (-0.019), so margin cannot separate them
# either. The two sit only ~0.012 apart in cosine similarity -- reportable
# as a genuinely inseparable pair at this resolution, not a threshold that
# was merely picked wrong. THRESHOLD was kept at 0.72 (the value that
# produces zero regressions across the full test suite) rather than
# narrowed to a razor-thin, overfit gap between one specific accept/reject
# example pair. See task-23-report.md for the full writeup.
SEMANTIC_CONFIRMATION_THRESHOLD = 0.72
SEMANTIC_MARGIN = -0.03


def _semantic_yes_signal(sentence: str, market: Market) -> Optional[float]:
    """A confidence score if `sentence` semantically confirms `market`'s
    subject has been resolved true, or None otherwise. Fallback net for
    when BINARY_YES_KEYWORDS finds nothing -- reuses the same free local
    embedding model already running for relevance ranking, not a new
    dependency. The market's own title is embedded into BOTH templates so
    the comparison reflects subject-plus-confirmation together, not
    confirmation-tone alone (see Task 23 design note point 3)."""
    model = _get_model()
    positive_template = f"{market.title} This has been confirmed and has already happened."
    negative_template = f"{market.title} This has not happened yet and remains unconfirmed or uncertain."
    sentence_emb = model.encode(sentence, convert_to_tensor=True)
    positive_emb = model.encode(positive_template, convert_to_tensor=True)
    negative_emb = model.encode(negative_template, convert_to_tensor=True)
    positive_sim = float(util.cos_sim(sentence_emb, positive_emb)[0][0])
    negative_sim = float(util.cos_sim(sentence_emb, negative_emb)[0][0])
    if positive_sim >= SEMANTIC_CONFIRMATION_THRESHOLD and (positive_sim - negative_sim) >= SEMANTIC_MARGIN:
        logger.info(
            "Semantic fallback fired for market %r: sentence=%r positive_sim=%.3f margin=%.3f",
            market.id, sentence, positive_sim, positive_sim - negative_sim,
        )
        return positive_sim
    return None


# Multi-outcome candidate verification -- architecture change, not another
# phrase-list patch. Added 2026-08-23 after live testing found repeated
# keyword false positives (BoomBoys crowned champion off a losing win-loss
# record; a not-yet-played match treated as decided; an analyst's opinion
# treated as a confirmed elimination), and the user pushed back directly:
# treating a keyword match as the verdict itself, then patching each new
# false positive with more keywords, is not scalable. From here, a
# keyword/phrase match in _decide_multi_outcome is only a CANDIDATE (kept
# cheap, for efficiency, so the classifier model isn't run against every
# sentence in every article) -- it must also pass this verification check
# before being trusted.
#
# Runs on a dedicated NLI (Natural Language Inference) zero-shot
# entailment model, NOT the general-purpose sentence-embedding model used
# for relevance_ranker.py's article filtering -- a different job needs a
# different model. Relevance filtering is a genuine similarity question
# ("is this article even about this market"); this is a classification
# question ("does this evidence entail this specific outcome being true"),
# which cosine similarity between two independently-encoded texts answers
# poorly. First built with a cosine-similarity-margin approach (see git
# history), then REPLACED after a real head-to-head calibration against
# the same 8 real/realistic test sentences:
#   old (MiniLM cosine-similarity margin): TRUE margins +0.038 to +0.094,
#     FALSE margins -0.058 to +0.120 -- only a ~0.027 real gap to set a
#     threshold in, and the false "opinion" sentence scored a HIGHER raw
#     similarity (0.800) than the weakest true confirmation (0.744).
#   new (NLI zero-shot entailment probability): TRUE scores 0.986-0.999,
#     FALSE scores 0.226-0.751 -- a ~0.235 real gap, an order of magnitude
#     more robust separation on the exact same sentences.
# Threshold set at 0.85: comfortably below every real TRUE score (>=0.986,
# 0.136 of margin) and comfortably above every real FALSE score (<=0.751,
# 0.099 of margin) -- biased toward the stricter/higher side deliberately,
# per the user's own stated design principle: not resolving is fine, a
# given answer must be correct, so a borderline case should fall through
# to UNCLEAR rather than risk a wrong verdict.
NLI_VERIFICATION_THRESHOLD = 0.85

_nli_classifier = None


def _get_nli_classifier():
    global _nli_classifier
    if _nli_classifier is None:
        from transformers import pipeline
        from resolution_finder.config import NLI_VERIFICATION_MODEL_NAME
        _nli_classifier = pipeline("zero-shot-classification", model=NLI_VERIFICATION_MODEL_NAME)
    return _nli_classifier


def _classify_scores(sentence: str, labels: list[str]) -> dict[str, float]:
    """Raw NLI entailment score per candidate label, straight from the
    real production classifier/model. Factored out so the verify_*
    functions below and nli_report.py (a diagnostic script for inspecting
    this layer's actual behavior) see identical scores from identical
    code, instead of the report duplicating hypothesis-construction logic
    that could silently drift out of sync with production."""
    classifier = _get_nli_classifier()
    result = classifier(sentence, candidate_labels=labels)
    return dict(zip(result["labels"], result["scores"]))


def _verify_candidate_semantically(sentence: str, positive_hypothesis: str, negative_hypothesis: str) -> bool:
    scores = _classify_scores(sentence, [positive_hypothesis, negative_hypothesis])
    return scores[positive_hypothesis] >= NLI_VERIFICATION_THRESHOLD


def _winner_hypotheses(option: str, market: Market) -> tuple[str, str]:
    positive = f"{option} has won {market.title}."
    negative = f"{option} has not won {market.title}, or it has not been decided yet."
    return positive, negative


def _verify_winner_candidate(
    sentence: str, option: str, market: Market,
    other_mentioned_options: Optional[list[str]] = None,
) -> bool:
    """True if `sentence` entails `option` has ALREADY won `market` --
    not just that a winner-shaped keyword and the option name both
    appear in it. When the same sentence also names other listed
    options (`other_mentioned_options`), their own "has won" claims are
    thrown in as extra competing labels so the check is genuinely
    relative, not just an independent threshold test per option.

    Real bug found live (2026-08-25): a schedule-recap sentence --
    "TEAM VISION 2-3 Team Spirit ... Team Spirit are the champions of
    TI 2026." -- explicitly names Team Spirit (a different listed
    option) as the actual champion, yet independently scored "Team
    Vision has won" at 0.991 against the old vague negative ("has not
    won ..., or it has not been decided yet"). Same failure mode
    already found and fixed for head-to-head (see
    HEAD_TO_HEAD_VERIFICATION_THRESHOLD's comment): a vague negative
    gives the model no real contrastive alternative, so it just detects
    topical relevance, not direction. Throwing the other mentioned
    option's own positive claim in as a competing label fixes it here
    too: real re-test, same sentence, Team Vision drops to 0.496 (below
    threshold) and Team Spirit's own claim similarly drops to 0.499 --
    both correctly fall through instead of crowning the loser. A real,
    unambiguous sentence naming only one option is untouched by this
    (skips straight to the single-pair check below) and still scores
    0.94-0.99 on the real true-positive cases tested (Nobel, Vinicius,
    Osun)."""
    positive, negative = _winner_hypotheses(option, market)
    if not other_mentioned_options:
        return _verify_candidate_semantically(sentence, positive, negative)
    competing_labels = [_winner_hypotheses(other, market)[0] for other in other_mentioned_options]
    scores = _classify_scores(sentence, [positive, negative] + competing_labels)
    return scores[positive] >= NLI_VERIFICATION_THRESHOLD and scores[positive] == max(scores.values())


# Directional bug found live (2026-08-23), the exact failure mode the
# original cosine-similarity version was built to prevent: pitting the
# claim against a vague negative ("has not played them yet, or has not
# defeated them") does NOT reliably discriminate WHICH side won -- real
# diagnostic against the actual reversed-option-list test failure showed
# BOTH "Lakers defeated Rockets" (0.989) AND the wrong "Rockets defeated
# Lakers" (0.962) scored extremely high against that vague negative on
# the exact same real sentence. The model was really just detecting "is
# this text about a Lakers-Rockets result" (topical match), not judging
# direction. Pitting the claim directly against the SPECIFIC OPPOSITE
# claim instead fixes this: real re-test, same sentence, correct
# direction 0.789 vs wrong direction 0.211 -- the model discriminates
# direction well when given a real contrastive alternative, just not a
# vague one. Uses 3 candidate labels (positive / opposite / unresolved)
# so a genuinely not-yet-decided match still has somewhere to go instead
# of being forced into a coin flip between two real teams -- real
# calibration: TRUE cases score 0.704/0.988 and are the max of the 3;
# the reversed-direction case correctly scores lowest (0.186, opposite
# wins at 0.695); the future/unplayed-match case scores lower still
# (0.481) despite technically "winning" the 3-way (still below threshold).
HEAD_TO_HEAD_VERIFICATION_THRESHOLD = 0.65


def _head_to_head_hypotheses(option: str, other_option: str) -> tuple[str, str, str]:
    positive = f"{option} defeated {other_option}."
    opposite = f"{other_option} defeated {option}."
    unresolved = f"It is not yet known whether {option} or {other_option} won."
    return positive, opposite, unresolved


def _verify_head_to_head_candidate(sentence: str, option: str, other_option: str) -> bool:
    """True if `sentence` entails `option` has ALREADY beaten
    `other_option` specifically -- not just that a win-shaped phrase and
    both names appear in it, and not just because the sentence is ABOUT
    a result between them (direction matters)."""
    positive, opposite, unresolved = _head_to_head_hypotheses(option, other_option)
    scores = _classify_scores(sentence, [positive, opposite, unresolved])
    return scores[positive] >= HEAD_TO_HEAD_VERIFICATION_THRESHOLD and scores[positive] == max(scores.values())


# Real bug found live (2026-08-23): the original wordy negative ("is
# still competing in {title} and has not been eliminated") scored a
# genuine single-game-loss-within-a-series-they're-leading sentence
# ("BoomBoys lost to Team Falcons in Game 2, but lead the series 2-1.")
# at 0.952 for "eliminated" -- confidently wrong, apparently over-
# weighting the literal "lost to" phrase over the contradicting "but
# lead the series" clause. A cleaner, grammatically PARALLEL opposite
# ("is still advancing" vs. "has been eliminated" -- same sentence
# shape, not a compound "X and not Y") fixed it: real re-calibration,
# same sentence, dropped to 0.669 (below threshold) while the two real
# TRUE elimination cases climbed to 0.998-0.999 (better separation, not
# worse) -- no threshold change needed, the wording was the problem.
def _elimination_hypotheses(option: str) -> tuple[str, str]:
    positive = f"{option} has been eliminated from the competition."
    negative = f"{option} is still advancing in the competition."
    return positive, negative


def _verify_elimination_candidate(sentence: str, option: str, market: Market) -> bool:
    """True if `sentence` entails `option` has ALREADY been eliminated --
    not just that an elimination-shaped keyword and the option name both
    appear in it.

    Deliberately does NOT interpolate market.title here, unlike
    _verify_winner_candidate -- real bug found live (2026-08-23): this
    market's title is "The International 2026 Champion" (phrased as the
    answer to the market's question, not an event name), so a template
    like "is still advancing in {market.title}" produces a grammatically
    broken hypothesis ("advancing in ... Champion" doesn't parse), which
    the classifier handled unpredictably -- every option, including ones
    never even mentioned in the sentence, scored as eliminated. A
    generic "eliminated from the competition" template avoids assuming
    market.title is grammatically an event name, and is exactly what was
    calibrated: real re-test, same sentence, correctly scored the false
    case at 0.669 (below threshold) and real eliminations at 0.998-0.999.
    """
    positive, negative = _elimination_hypotheses(option)
    return _verify_candidate_semantically(sentence, positive, negative)


def _deadline_default_note(market: Market) -> str:
    """Reviewer-facing CONTEXT for a market whose close date has passed
    with nothing confirmed -- never a verdict. See _decide_binary's own
    comment for why the stated default is no longer emitted as an answer
    (it was a measured coin flip: 2 correct, 2 wrong, on the real eval).
    Returns "" when there's nothing useful to say."""
    if not market.close_date or date.today() <= market.close_date:
        return ""
    stated_default = _extract_default_outcome(market.description)
    if not stated_default:
        return ""
    return (
        f" [Close date passed and no confirming evidence was found. "
        f"This market's own stated default is {stated_default} -- shown "
        f"as context for review, NOT asserted as a verdict.]"
    )


def _extract_default_outcome(description: str) -> Optional[str]:
    match = DEFAULT_OUTCOME_PATTERN.search(description)
    if not match:
        return None
    candidate = match.group(1).strip()
    if candidate.lower() == "yes":
        return "YES"
    if candidate.lower() == "no":
        return "NO"
    return candidate


# Real bug found live (2026-08-23): a BINARY_YES_KEYWORDS match used to
# decide YES immediately, with no verification at all -- unlike every
# other market type, where a keyword match is only a CANDIDATE that NLI
# must confirm. "The bill would need to be signed into law by the
# president to take effect..." contains the exact phrase "signed into
# law" but is purely hypothetical/explanatory, not a report that it
# happened -- and evades the hedge guard too (NEGATION_HEDGE_WORDS has
# the exact phrase "would be", not "would need to be", so the substring
# check misses it). Confirmed to reproduce against real production
# decide() (YES, confidence 0.8) before this fix.
#
# Deliberately does NOT interpolate market.title into the hypothesis,
# unlike _semantic_yes_signal's older cosine-similarity version --
# calibrated head-to-head against real cases: the title-interpolated
# version ("Will the CLARITY act...? This has been confirmed...") badly
# under-scored a genuine true-positive sentence (0.262, would have
# wrongly REJECTED it), apparently because concatenating a question onto
# a confirmation statement reads as an ungrammatical, hard-to-judge
# hypothesis -- the same title-interpolation fragility already
# documented for _verify_elimination_candidate. The plain version scored
# real TRUE cases at 0.986-0.992 and the real FALSE case at 0.193 --
# by the time this runs, _sentence_mentions_other_entity has already
# ruled out the sentence being about some OTHER subject, so the
# hypothesis doesn't need to restate the subject itself.
def _binary_yes_hypotheses() -> tuple[str, str]:
    positive = "This has been confirmed and has already happened."
    negative = "This has not happened yet and remains unconfirmed or uncertain."
    return positive, negative


def _verify_binary_yes_candidate(sentence: str) -> bool:
    """True if `sentence` entails the market's YES condition has ALREADY
    happened -- not just that a YES-shaped keyword phrase appears in it."""
    positive, negative = _binary_yes_hypotheses()
    return _verify_candidate_semantically(sentence, positive, negative)


# Narrower, separately-calibrated companion to BINARY_YES_KEYWORDS: real
# bugs found live (2026-08-26), election/inauguration and legislative-
# consequence language that doesn't fit BINARY_YES_KEYWORDS' shape at all
# ("was sworn in for his third term", "voted... to force the release of"
# files) but genuinely confirms a YES outcome. The subject-agnostic NLI
# check (_verify_binary_yes_candidate, same hypotheses as above) DOES
# score these correctly relative to real noise -- real calibration:
# Egypt 0.683, Epstein 0.730, vs FALSE cases like "the committee
# discussed the bill's implications" (0.860) and "have emerged as the
# frontrunners" (0.909) scoring HIGHER. That backwards ordering is why
# the shared NLI_VERIFICATION_THRESHOLD (0.85) can't just be lowered
# globally -- it would let those false cases through too. This narrower
# keyword list is gated separately with its own, lower threshold
# instead: false-trigger risk stays contained by (a) the phrase's own
# specificity, (b) the existing hedge guard, (c) the existing wrong-
# subject-entity veto, all still required to pass first, unchanged.
CONSEQUENCE_YES_KEYWORDS = ["sworn in", "to force the release of"]
CONSEQUENCE_VERIFICATION_THRESHOLD = 0.6

# Real bug found live (2026-08-26): the real Man City market (real
# answer: Yes) had NO BINARY_YES_KEYWORDS or CONSEQUENCE_YES_KEYWORDS
# match despite retrieval finding the exact right evidence: "Inside
# Manchester City's history-making fourth Premier League title in a row
# - The Athletic - The New York Times" (scores 0.801 on the winner-
# verification NLI check -- above CONSEQUENCE_VERIFICATION_THRESHOLD).
# TRIED AND REJECTED: adding "history-making"/"back-to-back"/"in a row"/
# "record-breaking" as CONSEQUENCE_YES_KEYWORDS. Calibration against
# plausible PREVIEW sentences ("City's bid for a fourth title in a row
# continues...", "A fourth title in a row is within reach...") scored
# 0.803-0.905 -- INDISTINGUISHABLE from the real confirmed case. These
# words are adjectival descriptors of a STORYLINE, not markers of
# completion -- they describe an in-progress pursuit exactly as readily
# as an achieved one, so no threshold can separate them. Shipping this
# would trade one wrong answer for new, unpredictable ones elsewhere.
# Left as a known, unfixed gap -- correctly resolves UNCLEAR rather than
# a guessed wrong answer.


def _verify_consequence_yes_candidate(sentence: str) -> bool:
    positive, _ = _binary_yes_hypotheses()
    scores = _classify_scores(sentence, list(_binary_yes_hypotheses()))
    return scores[positive] >= CONSEQUENCE_VERIFICATION_THRESHOLD


# --- Corroboration: require 2+ independent sources before committing --------
#
# Real design decision, not another guard fix: every wrong verdict this
# session traced back to a SINGLE sentence in a SINGLE article deciding a
# market (Team Vision crowned over Team Spirit, UNRWA's nomination,
# the Canadian-NHL question headline, the Michelle Obama false
# positive...). Committing on the strength of one source is a bet that
# source is both found AND correct. Requiring a SECOND, INDEPENDENT
# source to agree converts "got unlucky once" from a wrong verdict into
# a safe abstention instead -- which is the whole point under a
# minimize-wrong objective (user, 2026-08-27: "we're trying to minimize
# wrong, not to minimize unresolved").
#
# "Independent" means a different DOMAIN, with wire-service duplication
# collapsed: two different domains republishing the same underlying
# report (identical or near-identical confirming sentence, e.g. AP wire
# copy picked up by both) are NOT two sources, they're one story
# appearing twice -- counting them as corroboration would be WORSE than
# no corroboration check at all (false confidence). Sentence embedding
# similarity detects this, reusing the same free local model already
# running for relevance ranking -- no new dependency.
#
# Known, accepted cost (flagged before building this): markets that only
# ever retrieve 1-2 real candidate articles (several FDA-approval
# markets did, this session) will now surface as unresolved even when
# their single source was genuinely correct. That is the intended trade,
# not an oversight.
CORROBORATION_MIN_DOMAINS = 2
CORROBORATION_WIRE_DUPLICATE_SIMILARITY = 0.95

# _decide_multi_outcome phase 2: bounds how many confirming (sentence,
# item) pairs get collected per option before verification stops for
# that option. Keeps the added NLI-call cost small on markets with many
# options/many articles (e.g. a 30-team World Series market) -- once an
# option has enough confirmations to settle corroboration either way, a
# few extra genuinely wouldn't change the answer, so there is no value
# in exhaustively verifying every remaining sentence against it too.
MULTI_OUTCOME_MAX_CONFIRMATIONS_PER_OPTION = 3


def _confirmation_domain(item: RankedArticle) -> str:
    """A stable identity for 'which source' a confirmation came from --
    the real publisher domain when known, falling back to the URL itself
    (still correctly treats two different unknown-domain URLs as two
    distinct sources, never silently merges them)."""
    return item.article.source_domain or item.article.url


def _corroborating_domain_count(confirmations: list[tuple[str, RankedArticle]]) -> int:
    """How many genuinely INDEPENDENT sources support this set of
    confirming (sentence, item) pairs, after collapsing wire-service
    duplicates. First groups by domain (multiple confirming sentences
    from the SAME outlet are still just one source's editorial
    judgment, not two); then merges different domains whose ARTICLES
    (full item.text, not just the one matched sentence) are near-
    identical (the same underlying report syndicated, not independent
    corroboration).
    #
    # Real bug found building this: comparing just the matched SENTENCE
    # instead of the full article was too aggressive. Two genuinely
    # independent outlets confirming the same simple fact in one
    # sentence each ("The FDA approved Viridian's Veligrotug..." vs
    # "...announced FDA approval of Veligrotug...") measured
    # sim=0.978 -- indistinguishable from real wire duplication at the
    # single-sentence level, since a short factual sentence has very
    # little room to phrase the same fact differently. Comparing the
    # surrounding full article text instead (which differs in quotes,
    # structure, and extra detail between independently written
    # pieces even when their core confirming sentence is near-
    # identical) separates the two cases correctly.
    """
    if not confirmations:
        return 0
    by_domain: dict[str, RankedArticle] = {}
    for sentence, item in confirmations:
        domain = _confirmation_domain(item)
        if domain not in by_domain:
            by_domain[domain] = item
    if len(by_domain) < 2:
        return len(by_domain)

    model = _get_model()
    domains = list(by_domain.keys())
    embeddings = [model.encode(by_domain[d].text, convert_to_tensor=True) for d in domains]

    parent = list(range(len(domains)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(domains)):
        for j in range(i + 1, len(domains)):
            sim = float(util.cos_sim(embeddings[i], embeddings[j])[0][0])
            if sim > CORROBORATION_WIRE_DUPLICATE_SIMILARITY:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    return len({find(i) for i in range(len(domains))})


def _uncorroborated_note(confirmations: list[tuple[str, RankedArticle]]) -> str:
    domain_count = _corroborating_domain_count(confirmations)
    return (
        f" [Only {domain_count} independent source{'s' if domain_count != 1 else ''} "
        f"confirm{'s' if domain_count == 1 else ''} this -- needs "
        f"{CORROBORATION_MIN_DOMAINS} to assert a verdict, flagged for review instead.]"
    )


def _decide_binary(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict:
    subject_terms = _subject_terms(market)
    distinctive_terms = _distinctive_subject_terms(market)

    # Collects every confirming (sentence, item) pair instead of
    # returning on the first match -- see the corroboration comment
    # above _decide_binary's own definition for why: committing on one
    # source is a bet that source is both found AND correct.
    # Semantic fallback is gated PER ITEM, not on the confirmations list as
    # a whole. Real bug found building this: corroboration needs 2+
    # INDEPENDENT sources, and independent articles routinely describe the
    # same event in different words -- one might literally say "signed
    # into law" (keyword match) while another says "enacting the
    # legislation" (no keyword match, but a clear semantic match). Gating
    # the fallback on "did keyword matching find ANYTHING across the whole
    # evidence set" meant that as soon as ONE source keyword-matched, every
    # OTHER source that didn't happen to share its exact phrasing was
    # silently dropped -- making 2-domain corroboration nearly impossible
    # to satisfy for realistic differently-worded coverage. Falling back to
    # semantic per un-matched item (keyword stays tried first/primary for
    # each item) fixes that while still never running the model on an item
    # that a keyword already confirmed.
    confirmations: list[tuple[str, RankedArticle]] = []
    for item in ranked_evidence:
        item_confirmed = False
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence) or _sentence_is_interrogative(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms, distinctive_terms):
                continue
            if (any(_contains_keyword(sentence.lower(), keyword) for keyword in BINARY_YES_KEYWORDS)
                    and _verify_binary_yes_candidate(sentence)):
                confirmations.append((sentence, item))
                item_confirmed = True
                continue
            if (any(_contains_keyword(sentence.lower(), keyword) for keyword in CONSEQUENCE_YES_KEYWORDS)
                    and _verify_consequence_yes_candidate(sentence)):
                confirmations.append((sentence, item))
                item_confirmed = True

        if item_confirmed:
            continue
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence) or _sentence_is_interrogative(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms, distinctive_terms):
                continue
            if _sentence_is_vague_reference(sentence):
                continue
            semantic_score = _semantic_yes_signal(sentence, market)
            if semantic_score is not None:
                confirmations.append((sentence, item))

    if confirmations:
        sentence, item = confirmations[0]
        if _corroborating_domain_count(confirmations) >= CORROBORATION_MIN_DOMAINS:
            return Verdict(
                outcome="YES",
                confidence=item.similarity,
                evidence_snippet=sentence.strip()[:280],
                source_url=item.article.url,
                source_type=item.article.source_type,
            )
        return Verdict(
            outcome="UNCLEAR",
            confidence=item.similarity,
            evidence_snippet=(sentence.strip()[:280] + _uncorroborated_note(confirmations)),
            source_url=item.article.url,
            source_type=item.article.source_type,
        )

    # A market's description usually states a fallback ("Otherwise, this
    # market will resolve to 'No'"). That used to be EMITTED AS A VERDICT
    # once the close date passed with no confirming evidence found.
    #
    # Removed 2026-08-26: it conflates "our retrieval found nothing" with
    # "the event did not happen". Those are the same claim only if our
    # search is exhaustive, and it demonstrably isn't -- re-running the
    # real Egypt market 3x against the live search endpoint surfaced its
    # confirming article only 1 of 3 times. Measured on the real
    # 30-market eval, this path produced 4 verdicts: 2 correct (Powell,
    # Canadian-NHL) and 2 WRONG (Egypt and Man City -- both truth=Yes,
    # both asserted NO purely because the confirming article didn't
    # surface on that run). A coin flip that emits confident verdicts is
    # strictly negative value when a wrong answer is the expensive
    # failure mode and an unresolved one is cheap.
    #
    # The stated default is still surfaced to the human reviewer as
    # CONTEXT on the unresolved verdict -- just never as the answer.
    deadline_note = _deadline_default_note(market)

    if ranked_evidence:
        top = ranked_evidence[0]
        return Verdict(
            outcome="UNCLEAR",
            confidence=top.similarity,
            evidence_snippet=(top.text[:280] + deadline_note) or None,
            source_url=top.article.url,
            source_type=top.article.source_type,
        )

    return Verdict(outcome="NO_EVIDENCE", confidence=0.0,
                    evidence_snippet=deadline_note or None,
                    source_url=None, source_type=None)


def _decide_multi_outcome(market: Market, ranked_evidence: list[RankedArticle]) -> list[Verdict]:
    # Real bug found live (2026-08-27): the loop used to stop checking
    # for a winner entirely once the FIRST option verified ("winner is
    # None" gated every check below) -- so whichever option's confirming
    # sentence happened to be ranked/ordered first WON, even when a
    # DIFFERENT option ALSO independently verifies from other real
    # evidence. Confirmed concretely: for the real democratic-nominee-
    # 2024 market, both "It's official: Kamala Harris becomes Democrats'
    # 2024 presidential nominee - NPR" (true) AND "Ready to go: Barack
    # and Michelle Obama electrify the 2024 Democratic National
    # Convention..." (false positive -- merely describes her speaking)
    # independently verify as winners. The market currently resolves
    # correctly ONLY because Harris's article happens to rank higher and
    # gets checked first -- a coin flip on retrieval order, not a real
    # decision. Now collects EVERY option that verifies across all
    # evidence; if more than one distinct option does, that is
    # unresolved disagreement, not a market with two winners, so the
    # market abstains rather than betting on whichever was found first.
    #
    # Phase 2 corroboration (2026-09-02): even a SINGLE option verifying
    # as winner is no longer enough on its own -- same
    # CORROBORATION_MIN_DOMAINS bar as _decide_binary (see its own
    # comment). Real bug this catches: the real world-series-champion-
    # 2025 market (truth: Los Angeles Dodgers) wrongly crowned Cleveland
    # Guardians off a single MLB.com historical reference page ("Every
    # World Series champion since 1943 | ... | Cleveland Guardians") --
    # a list/table page, not a real 2025 result, and the ONLY source
    # that ever said so. Collects every (sentence, item) confirmation
    # per option (capped at MULTI_OUTCOME_MAX_CONFIRMATIONS_PER_OPTION,
    # not unbounded, to keep the added NLI-call cost small -- once an
    # option has enough confirmations to settle corroboration either
    # way, further matching sentences for that option stop being
    # verified) instead of stopping at the first.
    winner_confirmations: dict[str, list[tuple[str, RankedArticle]]] = {}
    eliminated: dict[str, Verdict] = {}
    expected_year = _market_expected_year(market)
    market_is_playoff = _market_is_playoff_context(market)
    expected_competition = _market_named_competition(market)

    for item in ranked_evidence:
        # An article's OPENING sentence conventionally establishes which
        # calendar instance of a recurring event the whole piece is about
        # (journalistic lead-paragraph convention) -- computed once per
        # article, not per sentence, so a LATER sentence that matches an
        # option but states no year of its own can still inherit that
        # context. Real bug found live (2026-08-25): premier-league-
        # winner-24-25 (asking about the already-decided 2024-25 season)
        # wrongly crowned Arsenal off a headline-style sentence ("Arsenal
        # tipped to win Premier League title by supercomputer... the
        # overwhelming favourites") with no year in it at all, while the
        # SAME article's opening sentence explicitly said "ahead of the
        # 2026-27 campaign" -- a different season entirely. The per-
        # sentence-only check (deliberate design, see
        # _sentence_mentions_conflicting_year's own docstring) had nothing
        # to compare against on that specific sentence.
        article_lead_sentence = _first_sentence(item.text)
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence) or _sentence_is_interrogative(sentence):
                continue
            lowered = sentence.lower()
            # Gate winner/elimination matches on this sentence not naming a
            # conflicting event instance -- checked once a phrase has
            # matched, not as a blanket pre-filter (see
            # _sentence_mentions_conflicting_year's docstring for why:
            # incidental years/phase words in ordinary prose must not
            # cause a false rejection). Four independent signals: a
            # different YEAR (a past season's game), the same signal
            # inherited from the article's own lead sentence when THIS
            # sentence states no year of its own, the same year but a
            # different MEETING within it (a regular-season game vs. the
            # market's own playoff series), or a RELATIVE recurring-range
            # reference with no explicit year at all ("the past two
            # years") -- see _sentence_mentions_relative_recency's own
            # comment for the real bug this catches.
            context_conflict = (
                _sentence_mentions_conflicting_year(sentence, expected_year)
                or _sentence_mentions_conflicting_year(article_lead_sentence, expected_year)
                or _sentence_mentions_conflicting_relative_season(
                    sentence, expected_year, item.article.published_date)
                or _sentence_mentions_conflicting_phase(sentence, market_is_playoff)
                or _sentence_mentions_relative_recency(sentence)
                or _sentence_mentions_conflicting_competition(sentence, expected_competition)
            )
            # All listed options this sentence actually names, computed
            # once up front so the winner check below can compare a
            # candidate against every OTHER option the sentence also
            # mentions, not just check it in isolation -- see
            # _verify_winner_candidate's docstring for the real bug this
            # prevents (a sentence naming two options, where the loser
            # was checked first and scored high against a vague negative
            # alone).
            mentioned_options = [
                opt for opt in market.options
                if opt.strip() and _option_mentioned(lowered, opt)
            ]
            for option in market.options:
                option_lower = option.strip().lower()
                if not option_lower:
                    continue

                # Candidate gate: the sentence must at least MENTION this
                # option -- cheap (a plain word-boundary check, no model
                # call), kept for efficiency so the real NLI verification
                # call below only runs on sentences that could plausibly
                # be about this option at all, not every sentence in every
                # article. Deliberately broader than the old fixed
                # ANNOUNCEMENT_KEYWORDS/ELIMINATION_KEYWORDS phrase-list
                # gate it replaces (2026-08-23, user-requested): any
                # keyword-matched sentence necessarily already mentions
                # the option too (the keyword check required proximity to
                # it), so this gate strictly subsumes the old one --
                # simplification, not just a broadening. The keyword lists
                # no longer decide the OUTCOME direction either (winner vs.
                # eliminated) -- that judgment now belongs entirely to the
                # NLI verification call, which is what actually reads the
                # sentence's meaning.
                option_mentioned = _option_mentioned(lowered, option)

                other_mentioned_options = [opt for opt in mentioned_options if opt != option]
                if (len(winner_confirmations.get(option, [])) < MULTI_OUTCOME_MAX_CONFIRMATIONS_PER_OPTION
                        and not context_conflict and option_mentioned
                        and _verify_winner_candidate(sentence, option, market, other_mentioned_options)):
                    winner_confirmations.setdefault(option, []).append((sentence, item))

                # Head-to-head winner-crowning is only valid for a true
                # 2-option (one-on-one) market -- beating ONE opponent in
                # a market with MORE options doesn't decide the whole
                # thing. Real bug found live (2026-08-23): in the real
                # 8-team International 2026 market, a sentence describing
                # BoomBoys losing individual matches to several other
                # LISTED teams (a normal group-stage record, not the
                # tournament outcome) got a genuine, correctly-verified
                # "Team Falcons defeated BoomBoys" head-to-head result
                # (0.699, a real true fact at the single-match level) --
                # and then wrongly crowned Team Falcons the overall
                # CHAMPION off that one match. The NLI check wasn't wrong;
                # applying "won one head-to-head" as "won the market" was.
                if (len(market.options) == 2
                        and len(winner_confirmations.get(option, [])) < MULTI_OUTCOME_MAX_CONFIRMATIONS_PER_OPTION
                        and not context_conflict and option_mentioned):
                    for other_option in market.options:
                        if other_option == option:
                            continue
                        other_option_lower = other_option.strip().lower()
                        if not other_option_lower:
                            continue
                        if (_contains_keyword(lowered, other_option_lower)
                                and _verify_head_to_head_candidate(sentence, option, other_option)):
                            winner_confirmations.setdefault(option, []).append((sentence, item))
                            break

                if (option not in eliminated and not context_conflict and option_mentioned
                        and _verify_elimination_candidate(sentence, option, market)):
                    eliminated[option] = Verdict(
                        outcome="NO", option=option, confidence=item.similarity,
                        evidence_snippet=sentence.strip()[:280],
                        source_url=item.article.url, source_type=item.article.source_type,
                    )

    # Conflict is judged on CORROBORATED options only, not on any option
    # that merely matched once. Real bug found live (2026-09-02, measured
    # across the full 30-market eval): treating any two matching options
    # as "conflict" made this the single largest cause of unresolved
    # markets -- 8 of 21 -- and in every one of those the TRUE answer was
    # sitting in the conflicting set, vetoed by an obvious stray. The real
    # premier-league-winner-24-25 market (truth: Liverpool) abstained
    # because Arsenal, Brighton, IPSWICH TOWN and Liverpool all
    # "independently appeared to have won"; the real world-series-winner
    # market (truth: Dodgers) abstained on Blue Jays vs Dodgers. Letting a
    # one-source false positive veto a well-corroborated answer applies
    # corroboration inconsistently -- it demands 2+ domains to ASSERT a
    # winner, then lets a single unverified domain BLOCK one. Requiring
    # both sides to clear the same bar is strictly more conservative than
    # the pre-corroboration code (the crowned winner still needs 2+
    # independent domains), so this cannot reopen the single-source
    # failures phase 2 was built to stop.
    corroborated = {
        option: confirmations for option, confirmations in winner_confirmations.items()
        if _corroborating_domain_count(confirmations) >= CORROBORATION_MIN_DOMAINS
    }

    if len(corroborated) > 1:
        # Genuine disagreement: more than one option is independently
        # CORROBORATED as the winner. Committing to whichever was found
        # first is a coin flip on article ranking/order, not a real
        # decision -- see this function's own opening comment. Abstain.
        names = ", ".join(sorted(corroborated))
        return [Verdict(
            outcome="UNCLEAR", option=None, confidence=0.0,
            evidence_snippet=(
                f"Conflicting evidence: {len(corroborated)} options "
                f"({names}) are each independently corroborated as having "
                "won -- flagged for review rather than guessed."
            ),
            source_url=None, source_type=None,
        )]

    if not corroborated and winner_confirmations:
        # Options matched, but none reached the corroboration bar -- see
        # this function's own phase-2 comment for the real Cleveland
        # Guardians case this catches (a single MLB.com list page).
        option, confirmations = next(iter(winner_confirmations.items()))
        sentence, item = confirmations[0]
        return [Verdict(
            outcome="UNCLEAR", option=None, confidence=item.similarity,
            evidence_snippet=(
                f"{option}: " + sentence.strip()[:280] + _uncorroborated_note(confirmations)
            ),
            source_url=item.article.url, source_type=item.article.source_type,
        )]

    if corroborated:
        # Overall winner confirmed: every option gets an explicit verdict.
        option, confirmations = next(iter(corroborated.items()))
        sentence, item = confirmations[0]
        winner = Verdict(
            outcome="YES", option=option, confidence=item.similarity,
            evidence_snippet=sentence.strip()[:280],
            source_url=item.article.url, source_type=item.article.source_type,
        )
        results = [winner]
        for other_option in market.options:
            if other_option == winner.option:
                continue
            if other_option in eliminated:
                results.append(eliminated[other_option])
            else:
                results.append(Verdict(
                    outcome="NO", option=other_option, confidence=winner.confidence,
                    evidence_snippet=winner.evidence_snippet,
                    source_url=winner.source_url, source_type=winner.source_type,
                ))
        return results

    if eliminated:
        # Partial resolution: only the options confirmed lost so far. The
        # rest of the market stays unreported (still genuinely pending) --
        # not spammed with an UNCLEAR row for every remaining option.
        return list(eliminated.values())

    # The stated default is no longer emitted as a verdict here either --
    # same reasoning as _decide_binary (see its comment): it asserts an
    # outcome purely from OUR failure to find evidence. The multi-outcome
    # version was arguably worse, since a named default (e.g. Vinicius ->
    # "Real Madrid") would crown a specific option YES on no evidence at
    # all.
    deadline_note = _deadline_default_note(market)

    if ranked_evidence:
        top = ranked_evidence[0]
        return [Verdict(outcome="UNCLEAR", option=None, confidence=top.similarity,
                         evidence_snippet=(top.text[:280] + deadline_note) or None,
                         source_url=top.article.url,
                         source_type=top.article.source_type)]

    return [Verdict(outcome="NO_EVIDENCE", option=None, confidence=0.0,
                     evidence_snippet=deadline_note or None,
                     source_url=None, source_type=None)]


def _decide_date_thresholds(market: Market, ranked_evidence: list[RankedArticle]) -> list[Verdict]:
    """Cumulative date-threshold options only (see Task 21 design note).

    Only the elapsed-deadline half is implemented here: an option whose
    own date has passed, with no evidence resolving it via the existing
    keyword-matching path, resolves NO; one whose date hasn't arrived yet
    stays UNCLEAR. Evidence-confirmed cross-option YES cascading is
    deliberately NOT implemented here -- see the Task 21 design note for
    why, and Task 19's `_decide_multi_outcome` for the winner/elimination
    matching this still uses first, per option, before falling back to the
    date-elapsed default below.
    """
    # Reuse the exact same evidence-based matching Task 19 already has,
    # so real evidence always wins over a date-elapsed guess (Task 21
    # precision point 2). This mirrors _decide_multi_outcome's own
    # winner/elimination scan but keyed per date-option instead of
    # collecting a single market-wide winner.
    #
    # Phase 4 corroboration (2026-09-02): a single YES confirmation is no
    # longer enough on its own -- same CORROBORATION_MIN_DOMAINS bar as
    # the other three decide() paths. NO stays single-source verified,
    # matching _decide_multi_outcome phase 2's own scope decision (see
    # its comment): only the positive assertion is gated here, not
    # elimination, for the same consistency reason across this file's
    # near-identical winner/elimination NLI mechanism.
    no_verdicts: dict[str, Verdict] = {}
    yes_confirmations: dict[str, list[tuple[str, RankedArticle]]] = {}
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence) or _sentence_is_interrogative(sentence):
                continue
            lowered = sentence.lower()
            for option in market.options:
                if option in no_verdicts:
                    continue
                option_lower = option.strip().lower()
                if not option_lower:
                    continue
                # Same mention-then-verify gate as _decide_multi_outcome
                # (see its comment for why) -- a mere mention alone is not
                # trusted as the verdict, only NLI verification is.
                if not _option_mentioned(lowered, option):
                    continue
                if (len(yes_confirmations.get(option, [])) < MULTI_OUTCOME_MAX_CONFIRMATIONS_PER_OPTION
                        and _verify_winner_candidate(sentence, option, market)):
                    yes_confirmations.setdefault(option, []).append((sentence, item))
                    continue
                if _verify_elimination_candidate(sentence, option, market):
                    no_verdicts[option] = Verdict(
                        outcome="NO", option=option, confidence=item.similarity,
                        evidence_snippet=sentence.strip()[:280],
                        source_url=item.article.url, source_type=item.article.source_type,
                    )

    evidence_verdicts: dict[str, Verdict] = {}
    for option in market.options:
        if option in no_verdicts:
            if option in yes_confirmations:
                # Genuine disagreement for this SAME option: some evidence
                # confirms it, other evidence eliminates it -- same shape
                # as the other phases' conflict abstention. Flag for
                # review rather than guess which source is right.
                sentence, item = yes_confirmations[option][0]
                evidence_verdicts[option] = Verdict(
                    outcome="UNCLEAR", option=option, confidence=item.similarity,
                    evidence_snippet=(
                        "Conflicting evidence for this option -- some "
                        "sources confirm it, others eliminate it -- "
                        "flagged for review rather than guessed."
                    ),
                    source_url=item.article.url, source_type=item.article.source_type,
                )
            else:
                evidence_verdicts[option] = no_verdicts[option]
        elif option in yes_confirmations:
            confirmations = yes_confirmations[option]
            sentence, item = confirmations[0]
            if _corroborating_domain_count(confirmations) >= CORROBORATION_MIN_DOMAINS:
                evidence_verdicts[option] = Verdict(
                    outcome="YES", option=option, confidence=item.similarity,
                    evidence_snippet=sentence.strip()[:280],
                    source_url=item.article.url, source_type=item.article.source_type,
                )
            else:
                evidence_verdicts[option] = Verdict(
                    outcome="UNCLEAR", option=option, confidence=item.similarity,
                    evidence_snippet=(sentence.strip()[:280] + _uncorroborated_note(confirmations)),
                    source_url=item.article.url, source_type=item.article.source_type,
                )

    today = date.today()
    results: list[Verdict] = []
    for option in market.options:
        if option in evidence_verdicts:
            results.append(evidence_verdicts[option])
            continue
        option_date = _parse_option_as_date(option)
        if today > option_date:
            # Was "NO" until 2026-08-26 -- same absence-as-proof fallacy
            # as the stated-default path in _decide_binary (see its
            # comment): an elapsed date only tells us the deadline is
            # behind us, never that the event failed to occur. We have no
            # ground-truth data measuring this specific path (the one
            # date-threshold market in the eval set is NO_GROUND_TRUTH),
            # so this is changed for consistency of reasoning rather than
            # on measured error -- but the reasoning is identical, and
            # under a minimize-wrong objective an unmeasured confident
            # assertion is exactly the thing to stop emitting.
            results.append(Verdict(
                outcome="UNCLEAR", option=option, confidence=0.0,
                evidence_snippet=(
                    f"Deadline ({option}) passed and no confirming evidence "
                    "was found -- unresolved, NOT asserted as not-met."
                ),
                source_url=None, source_type=None,
            ))
        else:
            results.append(Verdict(
                outcome="UNCLEAR", option=option, confidence=0.0,
                evidence_snippet=None, source_url=None, source_type=None,
            ))
    return results


def decide(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict | list[Verdict]:
    if market.options:
        if _is_cumulative_date_threshold_market(market):
            return _decide_date_thresholds(market, ranked_evidence)
        return _decide_multi_outcome(market, ranked_evidence)
    threshold_condition = _extract_threshold_condition(market)
    if threshold_condition is not None:
        direction, value = threshold_condition
        return _decide_numeric_threshold(market, ranked_evidence, direction, value)
    return _decide_binary(market, ranked_evidence)
