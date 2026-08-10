# resolution_finder/verdict_engine.py
import re
from datetime import date
from typing import Optional
from resolution_finder.models import Market, RankedArticle, Verdict

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
    for item in ranked_evidence:
        lowered = item.text.lower()
        if any(keyword in lowered for keyword in BINARY_YES_KEYWORDS):
            return Verdict(
                outcome="YES",
                confidence=item.similarity,
                evidence_snippet=item.text[:280],
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
        lowered = item.text.lower()
        for option in market.options:
            option_lower = option.lower()
            for keyword in ANNOUNCEMENT_KEYWORDS:
                pattern = re.compile(
                    rf"{re.escape(option_lower)}.{{0,40}}{re.escape(keyword)}|"
                    rf"{re.escape(keyword)}.{{0,40}}{re.escape(option_lower)}"
                )
                if pattern.search(lowered):
                    return Verdict(
                        outcome=option,
                        confidence=item.similarity,
                        evidence_snippet=item.text[:280],
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
