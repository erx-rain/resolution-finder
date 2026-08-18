# resolution_finder/verdict_engine.py
import re
from datetime import date, datetime
from typing import Optional
from resolution_finder.models import Market, RankedArticle, Verdict
from resolution_finder.query_builder import extract_entities

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

BINARY_YES_KEYWORDS = ["signed into law", "became law", "enacted", "approved by both"]

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
_THRESHOLD_NUMBER = r"\$?\s?(\d[\d,]*(?:\.\d+)?)\s?(bn|mm|thousand|million|billion|[kmb])?%?"
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
    """The market's own numeric threshold condition, parsed from its title/
    description -- e.g. ("up", 64000.0) for "above $64,000". Returns None
    for a market that isn't phrased as a numeric-threshold question at
    all, which is the common case and must fall through to the existing
    _decide_binary unchanged.
    """
    combined = f"{market.title} {market.description}"
    for direction, pattern in _THRESHOLD_CONDITION_PATTERNS:
        match = pattern.search(combined)
        if match:
            return direction, _parse_threshold_number(match.group(1), match.group(2))
    return None


def _extract_latest_number(sentence: str) -> Optional[float]:
    """The most recently stated number in `sentence` -- real evidence often
    states a prior value before the current one ("up from $61,000 to
    $64,000"), and the current value is conventionally stated last. Known,
    documented heuristic, not perfect for every phrasing (see Task 22
    design note point 4)."""
    matches = list(re.finditer(_THRESHOLD_NUMBER, sentence))
    if not matches:
        return None
    raw, suffix = matches[-1].group(1), matches[-1].group(2)
    return _parse_threshold_number(raw, suffix)


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

# A bare acronym (e.g. "CLARITY") that Task 4's extract_entities won't catch
# on its own, since that regex requires 2+ consecutive capitalized words and
# a title like "Will the CLARITY act..." has a lowercase word right after it.
ACRONYM_PATTERN = re.compile(r"\b[A-Z]{2,}\b")


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
    subject_lower = [t.lower() for t in subject_terms]
    sentence_entities = extract_entities(sentence) + ACRONYM_PATTERN.findall(sentence)
    for entity in sentence_entities:
        entity_lower = entity.lower()
        if not any(entity_lower in s or s in entity_lower for s in subject_lower):
            return True
    return False


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

    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            lowered = sentence.lower()
            for option in market.options:
                option_lower = option.strip().lower()
                if not option_lower:
                    continue

                if winner is None and _match_option_keyword(lowered, option_lower, ANNOUNCEMENT_KEYWORDS):
                    winner = Verdict(
                        outcome="YES", option=option, confidence=item.similarity,
                        evidence_snippet=sentence.strip()[:280],
                        source_url=item.article.url, source_type=item.article.source_type,
                    )

                if option not in eliminated and _match_option_keyword(lowered, option_lower, ELIMINATION_KEYWORDS):
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
