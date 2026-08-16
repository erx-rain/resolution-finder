# resolution_finder/verdict_engine.py
import re
from datetime import date
from typing import Optional
from resolution_finder.models import Market, RankedArticle, Verdict
from resolution_finder.query_builder import extract_entities

# Generalized: captures a plain Yes/No default (CLARITY Act, Nobel Prize) OR a
# specific named option default (Vinicius Junior -> "Real Madrid"). The
# trigger phrases anchor on deadline-miss language so this doesn't match an
# unrelated "resolves to X" sentence describing the normal win condition.
DEFAULT_OUTCOME_PATTERN = re.compile(
    r"(?:not met|has not|have not|not officially|not been)[^.]{0,150}?"
    r"resolve[s]?\s+to\s+\"?([A-Za-z][A-Za-z0-9 .&'-]*?)\"?[.\n]",
    re.IGNORECASE | re.DOTALL,
)

BINARY_YES_KEYWORDS = ["signed into law", "became law", "enacted", "approved by both"]
ANNOUNCEMENT_KEYWORDS = ["awarded to", "wins", "winner is", "named recipient", "recipient is"]

SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")

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


def _decide_multi_outcome(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict:
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            lowered = sentence.lower()
            for option in market.options:
                option_lower = option.strip().lower()
                if not option_lower:
                    continue
                for keyword in ANNOUNCEMENT_KEYWORDS:
                    option_re = _word_boundary(option_lower)
                    keyword_re = _word_boundary(keyword)
                    pattern = re.compile(
                        rf"{option_re}.{{0,40}}{keyword_re}|"
                        rf"{keyword_re}.{{0,40}}{option_re}"
                    )
                    if pattern.search(lowered):
                        return Verdict(
                            outcome=option,
                            confidence=item.similarity,
                            evidence_snippet=sentence.strip()[:280],
                            source_url=item.article.url,
                            source_type=item.article.source_type,
                        )

    default_outcome = _extract_default_outcome(market.description)
    if default_outcome and date.today() > market.close_date:
        if default_outcome == "NO":
            return Verdict(
                outcome="NO",
                confidence=0.5,
                evidence_snippet=(
                    "Deadline passed with no matching evidence; applying "
                    "stated default (no listed option resolves Yes)."
                ),
                source_url=None,
                source_type=None,
            )
        matching_option = next(
            (opt for opt in market.options if opt.lower() == default_outcome.lower()),
            None,
        )
        if matching_option:
            return Verdict(
                outcome=matching_option,
                confidence=0.5,
                evidence_snippet="Deadline passed with no matching evidence; applying stated default option.",
                source_url=None,
                source_type=None,
            )

    if ranked_evidence:
        top = ranked_evidence[0]
        return Verdict(outcome="UNCLEAR", confidence=top.similarity,
                        evidence_snippet=top.text[:280], source_url=top.article.url,
                        source_type=top.article.source_type)

    return Verdict(outcome="NO_EVIDENCE", confidence=0.0, evidence_snippet=None,
                    source_url=None, source_type=None)


def decide(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict:
    if market.options:
        return _decide_multi_outcome(market, ranked_evidence)
    return _decide_binary(market, ranked_evidence)
