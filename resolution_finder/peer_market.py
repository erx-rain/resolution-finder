# resolution_finder/peer_market.py
import json
import logging
import re
from typing import Optional
from urllib.parse import quote_plus
import requests
from sentence_transformers import util
from resolution_finder.models import Market, Verdict
from resolution_finder.query_builder import extract_entities
from resolution_finder.relevance_ranker import _get_model
from resolution_finder.config import SIMILARITY_THRESHOLD

logger = logging.getLogger(__name__)

POLYMARKET_SEARCH = "https://gamma-api.polymarket.com/public-search?q={query}&limit_per_type=5"

# Legislative bill numbers are the most reliable disambiguator between two
# markets that sound alike but are about different things -- verified
# necessary empirically: embedding similarity alone scored 0.71 between our
# real CLARITY Act (H.R. 3633) market and an unrelated "Guidance Clarity
# Act (S.81)" bill found via live Polymarket search.
BILL_NUMBER_PATTERN = re.compile(
    r"\b(?:H\.R\.|H\.Res\.|H\.Con\.Res\.|H\.J\.Res\.|S\.Res\.|S\.Con\.Res\.|S\.J\.Res\.|S\.)\s?\d+\b",
    re.IGNORECASE,
)
ACRONYM_PATTERN = re.compile(r"\b[A-Z]{2,}\b")


def _extract_bill_numbers(text: str) -> set[str]:
    return {m.strip().upper().replace(" ", "") for m in BILL_NUMBER_PATTERN.findall(text)}


def _has_conflicting_bill_number(our_text: str, peer_text: str) -> bool:
    our_bills = _extract_bill_numbers(our_text)
    peer_bills = _extract_bill_numbers(peer_text)
    if not our_bills or not peer_bills:
        return False
    return our_bills.isdisjoint(peer_bills)


def _our_identifying_terms(market: Market) -> list[str]:
    combined = f"{market.title} {market.description}"
    terms = list(extract_entities(combined))
    for word in ACRONYM_PATTERN.findall(market.title):
        if word not in terms:
            terms.append(word)
    return terms


def _has_conflicting_entity(our_terms: list[str], peer_text: str) -> bool:
    if not our_terms:
        return False
    our_lower = [t.lower() for t in our_terms]
    peer_entities = extract_entities(peer_text) + ACRONYM_PATTERN.findall(peer_text)
    for entity in peer_entities:
        entity_lower = entity.lower()
        if not any(entity_lower in s or s in entity_lower for s in our_lower):
            return True
    return False


def _search_polymarket_events(query: str) -> list[dict]:
    url = POLYMARKET_SEARCH.format(query=quote_plus(query))
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except requests.RequestException:
        logger.warning("Polymarket search failed for query %r", query)
        return []
    return response.json().get("events", [])


def _resolved_outcome(peer_market: dict) -> Optional[str]:
    try:
        outcomes = json.loads(peer_market.get("outcomes", "[]"))
        prices = json.loads(peer_market.get("outcomePrices", "[]"))
    except (ValueError, TypeError):
        return None
    if not outcomes or len(outcomes) != len(prices):
        return None
    best_idx = max(range(len(prices)), key=lambda i: float(prices[i]))
    if float(prices[best_idx]) < 0.9:
        return None
    return outcomes[best_idx]


def find_polymarket_match(market: Market) -> Optional[Verdict]:
    """Best-effort: check whether a similar, already-resolved binary market
    exists on Polymarket, and if so, surface its outcome as a proposed
    verdict. Binary (Yes/No) markets only -- mapping option lists across two
    platforms' own outcome structures is a harder problem, deferred.

    Every match is still labeled for mandatory human verification on the
    dashboard regardless of how confident it looks: text similarity across
    platforms can be fooled by two different markets that happen to be
    worded alike (verified empirically during planning), so this is
    corroborating evidence, never treated as confirmed on its own. The two
    conflict checks below exist specifically because similarity alone
    already proved unsafe.
    """
    if market.options:
        return None

    events = _search_polymarket_events(market.title)
    if not events:
        return None

    our_terms = _our_identifying_terms(market)
    model = _get_model()
    query_text = f"{market.title} {market.description}"
    query_embedding = model.encode(query_text, convert_to_tensor=True)

    best_match = None
    best_similarity = 0.0

    for event in events:
        for peer_market in event.get("markets", []):
            if not peer_market.get("closed"):
                continue
            if peer_market.get("umaResolutionStatus") != "resolved":
                continue

            peer_question = peer_market.get("question", "")
            peer_description = peer_market.get("description", "")
            peer_text = f"{peer_question} {peer_description}"
            if not peer_text.strip():
                continue

            # Bill numbers are checked against the FULL peer text (question +
            # description): a bill number anywhere is subject-identifying, so
            # widening the text here only ever adds rejection power. This is
            # the check that rejects the real "Guidance Clarity Act (S.81)"
            # false match -- verified against live Polymarket data, where it
            # is the ONLY check that catches that one.
            if _has_conflicting_bill_number(market.description, peer_text):
                continue
            # The entity check, by contrast, runs against the peer QUESTION
            # only. Polymarket descriptions are near-identical legal
            # boilerplate ("...signed into law by the President of the United
            # States by December 31, 2025, 11:59 PM ET..."), out of which the
            # heuristic entity matcher pulls non-subject junk terms like
            # "United States" and "PM ET. Otherwise". Those match no market's
            # own subject terms, so including the description made this check
            # reject 100% of live candidates -- genuine matches included.
            # Verified against live data: question-only still rejects all 5
            # real bad candidates found during planning (the three unrelated
            # athlete-transfer markets on their entities, the two wrong bills
            # on their bill numbers) while letting a true same-bill match
            # through. The check is not relaxed, only pointed at the text
            # that actually identifies the subject.
            if _has_conflicting_entity(our_terms, peer_question):
                continue

            peer_embedding = model.encode(peer_text, convert_to_tensor=True)
            similarity = float(util.cos_sim(query_embedding, peer_embedding)[0][0])
            if similarity >= SIMILARITY_THRESHOLD and similarity > best_similarity:
                best_similarity = similarity
                best_match = (event, peer_market, peer_question)

    if best_match is None:
        return None

    event, peer_market, peer_question = best_match
    outcome = _resolved_outcome(peer_market)
    if outcome is None:
        return None

    outcome_lower = outcome.strip().lower()
    if outcome_lower == "yes":
        mapped_outcome = "YES"
    elif outcome_lower == "no":
        mapped_outcome = "NO"
    else:
        return None

    slug = event.get("slug") or peer_market.get("slug", "")
    url = f"https://polymarket.com/event/{slug}" if slug else None

    return Verdict(
        outcome=mapped_outcome,
        confidence=min(best_similarity, 0.9),
        evidence_snippet=f"Resolved \"{outcome}\" on Polymarket for a similar question: \"{peer_question}\"",
        source_url=url,
        source_type="peer_market",
    )
