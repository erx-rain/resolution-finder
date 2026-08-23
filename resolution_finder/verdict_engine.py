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
DEFAULT_OUTCOME_PATTERN = re.compile(
    r"(?:not met|has not|have not|not officially|not been|not known)[^.]{0,150}?"
    r"resolve[s]?\s+to\s+\"?([A-Za-z][A-Za-z0-9 .&'-]*?)\"?[.\n]",
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
]

# "winner of" and the contract-renewal/stay phrases were added after a real
# dry run: BBC Pidgin's "INEC declare Ademola Adeleke winner of the ..."
# matched none of the original phrases ("winner is" != "winner of"), and
# NYTimes/The Athletic describing Vinicius Junior staying at Real Madrid as
# "reached an agreement to renew his contract" isn't an "announcement"
# phrase at all in the original list, which was written for prize/award
# language only. NOTE: both real examples actually named the entity
# partially ("at Madrid", "Govnor Adeleke") rather than by the full option
# string ("Real Madrid", "Ademola Adeleke") -- that partial-name gap is NOT
# fixed here (see Task 19's design note): a bare surname can be genuinely
# ambiguous between two listed options (e.g. osun-state-governor-2026 has
# both "Ademola Adeleke" and "Taofeek Adeleke"), so this only matches the
# full option string, same as before.
ANNOUNCEMENT_KEYWORDS = [
    "awarded to", "wins", "winner is", "winner of", "named recipient", "recipient is",
    "signed a new contract", "reached an agreement to renew", "contract extension",
    "renewed his contract", "renewed her contract", "extended his contract", "extended her contract",
]

# A specific option confirmed to have LOST resolves that option alone to No,
# independently of whether the overall market winner is known yet (e.g. a
# team eliminated partway through a tournament that's still ongoing).
ELIMINATION_KEYWORDS = ["eliminated", "eliminated from", "knocked out", "lost to", "out of the tournament"]

# Directional head-to-head phrases ("Lakers defeated the Rockets", "Lakers'
# victory over the Rockets") -- unlike ANNOUNCEMENT_KEYWORDS/
# ELIMINATION_KEYWORDS (checked via _match_option_keyword's simple
# keyword-near-option proximity, which is fine for one-sided phrases like
# "winner of X"), these phrases put BOTH team names within the same
# proximity window as the verb, so proximity alone can't tell winner from
# loser -- only word ORDER relative to the verb/phrase does. Real bug
# found live (2026-08-23): a real market's top evidence was "The Lakers'
# ... victory over the Houston Rockets" -- both team names sat within the
# existing 40-char window, so naively adding "victory over" to
# ANNOUNCEMENT_KEYWORDS would have let list ORDER (not the actual winner)
# decide, e.g. wrongly crowning Rockets if market.options happened to list
# it first. _match_head_to_head_winner below is directional: it only
# matches a (candidate_winner, candidate_loser) PAIR in that specific
# order, tried for every ordered pair of the market's own options.
HEAD_TO_HEAD_WIN_VERBS = [
    "defeated", "defeats", "beat", "beats", "topped", "downed", "routed", "outlasted",
]
# Noun-phrase form ("[team]'s victory over [team]") rather than a
# transitive verb between two names -- matched the same directional way.
HEAD_TO_HEAD_WIN_NOUN_PHRASES = ["victory over", "win over", "triumph over"]


def _match_head_to_head_winner(lowered_sentence: str, winner_option_lower: str, loser_option_lower: str) -> bool:
    """True if `lowered_sentence` states, in that specific direction, that
    `winner_option_lower` beat `loser_option_lower` -- not just that both
    names and a win-shaped word appear somewhere nearby."""
    winner_re = _word_boundary(winner_option_lower)
    loser_re = _word_boundary(loser_option_lower)
    for phrase in HEAD_TO_HEAD_WIN_VERBS + HEAD_TO_HEAD_WIN_NOUN_PHRASES:
        phrase_re = _word_boundary(phrase)
        if re.search(rf"{winner_re}.{{0,40}}{phrase_re}.{{0,40}}{loser_re}", lowered_sentence):
            return True
    return False

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
_THRESHOLD_CONDITION_PATTERNS = [
    ("up", re.compile(
        rf"(?:at least|reach(?:es)?|hit|above|over|more than)\s+\(?(?:HIGH\)?\s*)?{_THRESHOLD_NUMBER}",
        re.IGNORECASE,
    )),
    ("down", re.compile(rf"(?:below|under|less than)\s+{_THRESHOLD_NUMBER}", re.IGNORECASE)),
]


def _parse_threshold_number(raw: str, suffix: Optional[str]) -> float:
    value = float(raw.replace(",", ""))
    if suffix:
        value *= _THRESHOLD_MAGNITUDES.get(suffix.lower(), 1)
    return value


def _extract_threshold_condition(market: Market) -> Optional[tuple[str, float]]:
    """The market's own numeric threshold condition, parsed from its TITLE
    only (not its description) -- e.g. ("up", 64000.0) for "above
    $64,000". Returns None for a market that isn't phrased as a
    numeric-threshold question at all, which is the common case and must
    fall through to the existing _decide_binary unchanged.

    Deliberately title-only, not title+description: every real numeric-
    threshold market states its own core Yes/No condition directly in the
    title (true of every fixture in this test suite), while a market's
    DESCRIPTION often contains extended exception/edge-case language that
    can incidentally contain a threshold-shaped phrase without the market
    actually being a numeric-threshold comparison. Real bug found live
    (2026-08-23): a 5-category typhoon-intensity classification market
    (options=[], title asking specifically about the "Very Strong
    Typhoon" category, no threshold phrasing in the title at all) had an
    unrelated exception clause in its description -- "...classifies the
    system as a tropical depression (below 34 kt)... the crossing does
    not count and the market resolves to 'No Qualifying Landfall'" --
    describing a DIFFERENT outcome branch, not the market's actual
    question. Searching the full description matched "below 34" and
    wrongly routed the whole market into the numeric-threshold decision
    path, comparing an unrelated number from real evidence (a
    building-damage count, "14,000") against a threshold of 34 and
    confidently resolving NO.
    """
    for direction, pattern in _THRESHOLD_CONDITION_PATTERNS:
        match = pattern.search(market.title)
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


def _decide_numeric_threshold(
    market: Market, ranked_evidence: list[RankedArticle], direction: str, threshold: float
) -> Verdict:
    subject_terms = _subject_terms(market)
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms):
                continue
            value = _extract_latest_number(sentence)
            if value is None:
                continue
            met = value >= threshold if direction == "up" else value <= threshold
            return Verdict(
                outcome="YES" if met else "NO",
                confidence=item.similarity,
                evidence_snippet=sentence.strip()[:280],
                source_url=item.article.url,
                source_type=item.article.source_type,
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
NEGATION_HEDGE_WORDS = [
    "not ", "n't ", "never ", "without ", "fails to", "failed to",
    "yet to", "has yet", "remains uncertain", "uncertain", "unclear",
    "unlikely", "pending", "awaiting", "no vote", "not scheduled",
    "if ", "unless ", "would be", "could be", "might be",
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
VAGUE_REFERENCE_HEDGE_WORDS = [
    "other records", "numerous other", "various other", "several other",
    "many other", "other such",
]


def _sentence_is_vague_reference(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(phrase in lowered for phrase in VAGUE_REFERENCE_HEDGE_WORDS)


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


def _split_sentences(text: str) -> list[str]:
    return [s for s in SENTENCE_SPLIT_PATTERN.split(text) if s.strip()]


def _sentence_has_hedge(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(word in lowered for word in NEGATION_HEDGE_WORDS)


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


def _sentence_mentions_other_entity(sentence: str, subject_terms: list[str]) -> bool:
    """True if the sentence names a capitalized entity/acronym that isn't
    (even partially) one of this market's own subject terms — a signal the
    sentence is about something else. Empty subject_terms means we have no
    way to tell our own subject apart, so never reject on this basis alone.
    """
    if not subject_terms:
        return False
    subject_lower = [_normalize_entity(t) for t in subject_terms]
    sentence_entities = extract_entities(sentence) + ACRONYM_PATTERN.findall(sentence)
    for entity in sentence_entities:
        entity_lower = _normalize_entity(entity)
        if not entity_lower:
            continue
        if not any(entity_lower in s or s in entity_lower for s in subject_lower):
            return True
    return False


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


def _decide_binary(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict:
    subject_terms = _subject_terms(market)
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms):
                continue
            if any(_contains_keyword(sentence.lower(), keyword) for keyword in BINARY_YES_KEYWORDS):
                return Verdict(
                    outcome="YES",
                    confidence=item.similarity,
                    evidence_snippet=sentence.strip()[:280],
                    source_url=item.article.url,
                    source_type=item.article.source_type,
                )

    # Semantic fallback: only reached when keyword matching found nothing
    # at all above. Keyword matching stays primary/more precise.
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms):
                continue
            if _sentence_is_vague_reference(sentence):
                continue
            semantic_score = _semantic_yes_signal(sentence, market)
            if semantic_score is not None:
                return Verdict(
                    outcome="YES",
                    confidence=min(item.similarity, semantic_score),
                    evidence_snippet=sentence.strip()[:280],
                    source_url=item.article.url,
                    source_type=item.article.source_type,
                )

    default_outcome = _extract_default_outcome(market.description)
    if default_outcome in ("YES", "NO") and date.today() > market.close_date:
        return Verdict(
            outcome=default_outcome,
            confidence=0.5,
            evidence_snippet="Deadline passed with no matching evidence; applying stated default.",
            source_url=None,
            source_type=None,
        )

    if ranked_evidence:
        top = ranked_evidence[0]
        return Verdict(
            outcome="UNCLEAR",
            confidence=top.similarity,
            evidence_snippet=top.text[:280],
            source_url=top.article.url,
            source_type=top.article.source_type,
        )

    return Verdict(outcome="NO_EVIDENCE", confidence=0.0, evidence_snippet=None,
                    source_url=None, source_type=None)


def _match_option_keyword(lowered_sentence: str, option_lower: str, keywords: list[str]) -> bool:
    option_re = _word_boundary(option_lower)
    for keyword in keywords:
        keyword_re = _word_boundary(keyword)
        pattern = re.compile(rf"{option_re}.{{0,40}}{keyword_re}|{keyword_re}.{{0,40}}{option_re}")
        if pattern.search(lowered_sentence):
            return True
    return False


def _decide_multi_outcome(market: Market, ranked_evidence: list[RankedArticle]) -> list[Verdict]:
    winner: Optional[Verdict] = None
    eliminated: dict[str, Verdict] = {}
    expected_year = _market_expected_year(market)

    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            lowered = sentence.lower()
            # Gate winner/elimination matches on this sentence not stating
            # a conflicting year -- checked once a phrase has matched, not
            # as a blanket pre-filter (see _sentence_mentions_conflicting_year
            # docstring for why: incidental years in ordinary prose must
            # not cause a false rejection).
            year_conflict = _sentence_mentions_conflicting_year(sentence, expected_year)
            for option in market.options:
                option_lower = option.strip().lower()
                if not option_lower:
                    continue

                if (winner is None and not year_conflict
                        and _match_option_keyword(lowered, option_lower, ANNOUNCEMENT_KEYWORDS)):
                    winner = Verdict(
                        outcome="YES", option=option, confidence=item.similarity,
                        evidence_snippet=sentence.strip()[:280],
                        source_url=item.article.url, source_type=item.article.source_type,
                    )

                if winner is None and not year_conflict:
                    for other_option in market.options:
                        if other_option == option:
                            continue
                        other_option_lower = other_option.strip().lower()
                        if not other_option_lower:
                            continue
                        if _match_head_to_head_winner(lowered, option_lower, other_option_lower):
                            winner = Verdict(
                                outcome="YES", option=option, confidence=item.similarity,
                                evidence_snippet=sentence.strip()[:280],
                                source_url=item.article.url, source_type=item.article.source_type,
                            )
                            break

                if (option not in eliminated and not year_conflict
                        and _match_option_keyword(lowered, option_lower, ELIMINATION_KEYWORDS)):
                    eliminated[option] = Verdict(
                        outcome="NO", option=option, confidence=item.similarity,
                        evidence_snippet=sentence.strip()[:280],
                        source_url=item.article.url, source_type=item.article.source_type,
                    )

    if winner is not None:
        # Overall winner confirmed: every option gets an explicit verdict.
        results = [winner]
        for option in market.options:
            if option == winner.option:
                continue
            if option in eliminated:
                results.append(eliminated[option])
            else:
                results.append(Verdict(
                    outcome="NO", option=option, confidence=winner.confidence,
                    evidence_snippet=winner.evidence_snippet,
                    source_url=winner.source_url, source_type=winner.source_type,
                ))
        return results

    if eliminated:
        # Partial resolution: only the options confirmed lost so far. The
        # rest of the market stays unreported (still genuinely pending) --
        # not spammed with an UNCLEAR row for every remaining option.
        return list(eliminated.values())

    default_outcome = _extract_default_outcome(market.description)
    if default_outcome and date.today() > market.close_date:
        matching_option = (
            next((opt for opt in market.options if opt.lower() == default_outcome.lower()), None)
            if default_outcome != "NO" else None
        )
        snippet = "Deadline passed with no matching evidence; applying stated default."
        if matching_option:
            results = [Verdict(outcome="YES", option=matching_option, confidence=0.5,
                                evidence_snippet=snippet, source_url=None, source_type=None)]
            results += [
                Verdict(outcome="NO", option=opt, confidence=0.5, evidence_snippet=snippet,
                        source_url=None, source_type=None)
                for opt in market.options if opt != matching_option
            ]
            return results
        # Either an explicit "No" default, or a named default that matches
        # none of the listed options (e.g. Osun's "Other") -- both mean the
        # same thing for a per-option verdict: nothing on the list wins.
        return [
            Verdict(outcome="NO", option=opt, confidence=0.5, evidence_snippet=snippet,
                    source_url=None, source_type=None)
            for opt in market.options
        ]

    if ranked_evidence:
        top = ranked_evidence[0]
        return [Verdict(outcome="UNCLEAR", option=None, confidence=top.similarity,
                         evidence_snippet=top.text[:280], source_url=top.article.url,
                         source_type=top.article.source_type)]

    return [Verdict(outcome="NO_EVIDENCE", option=None, confidence=0.0, evidence_snippet=None,
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
    evidence_verdicts: dict[str, Verdict] = {}
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            lowered = sentence.lower()
            for option in market.options:
                if option in evidence_verdicts:
                    continue
                option_lower = option.strip().lower()
                outcome = None
                if _match_option_keyword(lowered, option_lower, ANNOUNCEMENT_KEYWORDS):
                    outcome = "YES"
                elif _match_option_keyword(lowered, option_lower, ELIMINATION_KEYWORDS):
                    outcome = "NO"
                if outcome:
                    evidence_verdicts[option] = Verdict(
                        outcome=outcome, option=option, confidence=item.similarity,
                        evidence_snippet=sentence.strip()[:280],
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
            results.append(Verdict(
                outcome="NO", option=option, confidence=0.5,
                evidence_snippet=(
                    f"Deadline ({option}) passed with no matching evidence; "
                    "this date's threshold was not met."
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
