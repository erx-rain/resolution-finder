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


def _first_sentence(text: str) -> str:
    """The first sentence of `text` -- a market's description
    conventionally states its core Yes/No condition in its opening
    sentence, before any exception/edge-case elaboration begins."""
    sentences = _split_sentences(text)
    return sentences[0] if sentences else ""


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


def _decide_numeric_threshold(
    market: Market, ranked_evidence: list[RankedArticle], direction: str, threshold: float
) -> Verdict:
    subject_terms = _subject_terms(market)
    distinctive_terms = _distinctive_subject_terms(market)
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms, distinctive_terms):
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
    "analysts say", "some say", "pundits say", "reportedly", "allegedly",
    "is speculated", "some believe", "many believe", "it is believed",
    "rumored", "sources say", "some argue", "experts say",
    "will face", "will play", "will meet", "will take on",
    "is set to", "are set to", "scheduled to",
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


def _verify_winner_candidate(sentence: str, option: str, market: Market) -> bool:
    """True if `sentence` entails `option` has ALREADY won `market` --
    not just that a winner-shaped keyword and the option name both
    appear in it."""
    positive, negative = _winner_hypotheses(option, market)
    return _verify_candidate_semantically(sentence, positive, negative)


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
    distinctive_terms = _distinctive_subject_terms(market)
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms, distinctive_terms):
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
            if _sentence_mentions_other_entity(sentence, subject_terms, distinctive_terms):
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


def _decide_multi_outcome(market: Market, ranked_evidence: list[RankedArticle]) -> list[Verdict]:
    winner: Optional[Verdict] = None
    eliminated: dict[str, Verdict] = {}
    expected_year = _market_expected_year(market)
    market_is_playoff = _market_is_playoff_context(market)

    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            lowered = sentence.lower()
            # Gate winner/elimination matches on this sentence not naming a
            # conflicting event instance -- checked once a phrase has
            # matched, not as a blanket pre-filter (see
            # _sentence_mentions_conflicting_year's docstring for why:
            # incidental years/phase words in ordinary prose must not
            # cause a false rejection). Two independent signals: a
            # different YEAR (a past season's game), or the same year but
            # a different MEETING within it (a regular-season game vs. the
            # market's own playoff series).
            context_conflict = (
                _sentence_mentions_conflicting_year(sentence, expected_year)
                or _sentence_mentions_conflicting_phase(sentence, market_is_playoff)
            )
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
                option_mentioned = _contains_keyword(lowered, option_lower)

                if winner is None and not context_conflict and option_mentioned and _verify_winner_candidate(sentence, option, market):
                    winner = Verdict(
                        outcome="YES", option=option, confidence=item.similarity,
                        evidence_snippet=sentence.strip()[:280],
                        source_url=item.article.url, source_type=item.article.source_type,
                    )

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
                if len(market.options) == 2 and winner is None and not context_conflict and option_mentioned:
                    for other_option in market.options:
                        if other_option == option:
                            continue
                        other_option_lower = other_option.strip().lower()
                        if not other_option_lower:
                            continue
                        if (_contains_keyword(lowered, other_option_lower)
                                and _verify_head_to_head_candidate(sentence, option, other_option)):
                            winner = Verdict(
                                outcome="YES", option=option, confidence=item.similarity,
                                evidence_snippet=sentence.strip()[:280],
                                source_url=item.article.url, source_type=item.article.source_type,
                            )
                            break

                if (option not in eliminated and not context_conflict and option_mentioned
                        and _verify_elimination_candidate(sentence, option, market)):
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
                # Same mention-then-verify gate as _decide_multi_outcome
                # (see its comment for why) -- a mere mention alone is not
                # trusted as the verdict, only NLI verification is.
                option_mentioned = _contains_keyword(lowered, option_lower)
                if option_mentioned and _verify_winner_candidate(sentence, option, market):
                    outcome = "YES"
                elif option_mentioned and _verify_elimination_candidate(sentence, option, market):
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
