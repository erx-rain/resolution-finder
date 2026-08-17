# resolution_finder/peer_market.py
import json
import logging
import re
from datetime import date, datetime
from typing import Optional
from urllib.parse import quote_plus
import requests
from sentence_transformers import util
from resolution_finder.models import Market, Verdict
from resolution_finder.query_builder import extract_entities
from resolution_finder.relevance_ranker import _get_model
from resolution_finder.config import PEER_MARKET_SIMILARITY_THRESHOLD

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

# Two markets can name the same subject over different resolution windows --
# "signed into law by Dec 31 2025" vs "...by Dec 31 2026" is the SAME bill but
# a different question, and neither direction of mismatch is safe: an earlier
# peer deadline resolving NO says nothing about a later one, and a later peer
# deadline resolving YES may reflect an event that only happened after our own
# deadline had passed. Verified live: our real clarity-act-2026 market matched
# a Polymarket market on the identical bill (H.R.3633) whose deadline was a
# full year earlier and which resolved NO for missing that earlier deadline.
RESOLUTION_WINDOW_TOLERANCE_DAYS = 7

# Numeric thresholds ("$200,000", "5%") are subject-identifying in exactly the
# way bill numbers are, and neither of the other checks sees them. Verified
# live: "Will Bitcoin reach $200,000 in 2026?" matched "Will Bitcoin reach
# $80,000 by December 31, 2026?" at 0.65 similarity -- same subject, same
# window, 2.5x different strike price, confidently wrong YES.
MONEY_PATTERN = re.compile(
    r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?(bn|mm|[KMB]|thousand|million|billion)?\b",
    re.IGNORECASE,
)
PERCENT_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s?%")
_MAGNITUDES = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
}

# Below this length, the permissive substring match in _has_conflicting_entity
# does more harm than good: a short term like "CLARITY" matches ANY peer
# entity containing "clarity" (verified -- it let the real "Guidance Clarity
# Act" false match through as a same-subject candidate). Short terms must
# match a peer entity exactly instead of by substring.
MIN_SUBSTRING_MATCH_LENGTH = 8


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


def _terms_match(our_term: str, peer_entity: str) -> bool:
    """Whether one of our subject terms accounts for a peer entity.

    Substring matching is only allowed for terms long enough to be
    distinctive. A short term like "clarity" is a substring of both
    "Digital Asset Market Clarity Act" and the unrelated "Guidance Clarity
    Act", so allowing it to match by substring silently defeats the whole
    check; short terms must match exactly instead.
    """
    if our_term == peer_entity:
        return True
    if len(our_term) >= MIN_SUBSTRING_MATCH_LENGTH and our_term in peer_entity:
        return True
    if len(peer_entity) >= MIN_SUBSTRING_MATCH_LENGTH and peer_entity in our_term:
        return True
    return False


def _has_conflicting_entity(our_terms: list[str], peer_text: str) -> bool:
    if not our_terms:
        return False
    our_lower = [t.lower() for t in our_terms]
    peer_entities = extract_entities(peer_text) + ACRONYM_PATTERN.findall(peer_text)
    for entity in peer_entities:
        entity_lower = entity.lower()
        if not any(_terms_match(s, entity_lower) for s in our_lower):
            return True
    return False


def _extract_numeric_thresholds(text: str) -> tuple[set[float], set[float]]:
    """Dollar amounts and percentages named in `text`, normalized to plain
    numbers ("$200K" -> 200000.0). Returned separately so a price is never
    compared against a percentage.
    """
    money: set[float] = set()
    for raw, suffix in MONEY_PATTERN.findall(text):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if suffix:
            value *= _MAGNITUDES.get(suffix.lower(), 1)
        money.add(value)

    percents: set[float] = set()
    for raw in PERCENT_PATTERN.findall(text):
        try:
            percents.add(float(raw))
        except ValueError:
            continue
    return money, percents


def _values_conflict(ours: set[float], theirs: set[float], tolerance: float = 0.01) -> bool:
    """True when both sides name values but none of them agree.

    Mirrors the bill-number check's disjointness logic, with a small relative
    tolerance so "$200,000" and "$200K" are the same number.
    """
    if not ours or not theirs:
        return False
    for our_value in ours:
        for their_value in theirs:
            largest = max(abs(our_value), abs(their_value), 1e-9)
            if abs(our_value - their_value) / largest <= tolerance:
                return False
    return True


def _has_conflicting_threshold(our_text: str, peer_text: str) -> bool:
    """Hard-reject a peer market naming a different numeric threshold.

    Verified live: "Will Bitcoin reach $200,000 in 2026?" matched "Will
    Bitcoin reach $80,000 by December 31, 2026?" at 0.65 similarity. Same
    asset, same year, 2.5x different strike price -- invisible to both the
    bill-number and entity checks, and (at the time) above the shared
    similarity threshold this check used to reuse before it got its own,
    stricter PEER_MARKET_SIMILARITY_THRESHOLD.
    """
    our_money, our_percents = _extract_numeric_thresholds(our_text)
    peer_money, peer_percents = _extract_numeric_thresholds(peer_text)
    if _values_conflict(our_money, peer_money):
        return True
    return _values_conflict(our_percents, peer_percents)


def _peer_end_date(peer_market: dict) -> Optional[date]:
    """The peer market's resolution deadline, if the payload states one."""
    for key in ("endDateIso", "endDate"):
        raw = peer_market.get(key)
        if not raw or not isinstance(raw, str):
            continue
        text = raw.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            try:
                return datetime.strptime(text[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
    return None


def _has_conflicting_resolution_window(our_close: date, peer_end: Optional[date]) -> bool:
    """Hard-reject a peer market whose deadline lands meaningfully apart from
    ours, in EITHER direction.

    Two markets can name the same subject over different windows, and neither
    direction of mismatch is safe. Both were reproduced live against the same
    real Polymarket event:

    * Peer deadline earlier than ours -- "signed into law by Dec 31 2025" vs
      our "...by Dec 31 2026". The peer resolved NO for missing ITS earlier
      deadline, which says nothing about ours; proposing NO for our
      still-open market was simply wrong.
    * Peer deadline later than ours -- our "...by Jan 31 2025" vs the peer's
      "...by Dec 31 2025", which resolved YES because the bill was signed in
      the intervening months. The true answer for our market is NO, but the
      peer's YES was proposed with 0.74 confidence. This direction is the
      realistically common one: a resolution finder's normal input is markets
      whose deadline has often already passed.

    A small symmetric tolerance keeps genuinely-the-same-event markets that
    differ by a day or two; every genuine match observed live so far has a
    window delta of exactly 0 days.
    """
    if peer_end is None or our_close is None:
        return False
    return abs((our_close - peer_end).days) > RESOLUTION_WINDOW_TOLERANCE_DAYS


def _search_polymarket_events(query: str) -> list[dict]:
    """Best-effort: any network failure or malformed response degrades to no
    candidates found, never an uncaught exception. A single bad response must
    not propagate up through `_scan_market` and cause `run_pipeline` to skip
    the whole market -- verified: a 200 response with a non-JSON body raises
    `ValueError` from `response.json()`, and a JSON top-level list (instead of
    the expected dict) raises `AttributeError` on `.get("events")`, both of
    which must be caught here rather than left to propagate.
    """
    url = POLYMARKET_SEARCH.format(query=quote_plus(query))
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        logger.warning("Polymarket search failed for query %r", query)
        return []
    if not isinstance(payload, dict):
        logger.warning("Polymarket search returned unexpected payload shape for query %r", query)
        return []
    return payload.get("events", [])


def _resolved_outcome(peer_market: dict) -> Optional[str]:
    """Best-effort: a malformed `outcomePrices` string (e.g. `'["",""]'`)
    must degrade to `None`, not raise. `float()` on the parsed prices is kept
    inside the same guarded step as the `json.loads` calls above it, since
    both can fail on data this function does not control.
    """
    try:
        outcomes = json.loads(peer_market.get("outcomes", "[]"))
        raw_prices = json.loads(peer_market.get("outcomePrices", "[]"))
    except (ValueError, TypeError):
        return None
    if not outcomes or len(outcomes) != len(raw_prices):
        return None
    try:
        prices = [float(p) for p in raw_prices]
    except (ValueError, TypeError):
        return None
    best_idx = max(range(len(prices)), key=lambda i: prices[i])
    if prices[best_idx] < 0.9:
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
    our_full_text = f"{market.title} {market.description}"
    model = _get_model()
    query_embedding = model.encode(our_full_text, convert_to_tensor=True)

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

            # A peer market whose deadline lands meaningfully before OR after
            # our own close date is answering a different question about the
            # same subject, so this is checked first -- it is the cheapest
            # check and it caught a real wrong-answer match on our own
            # production market.
            if _has_conflicting_resolution_window(market.close_date, _peer_end_date(peer_market)):
                continue

            # Bill numbers are checked against the FULL peer text (question +
            # description): a bill number anywhere is subject-identifying, so
            # widening the text here only ever adds rejection power. This is
            # the check that rejects the real "Guidance Clarity Act (S.81)"
            # false match -- verified against live Polymarket data, where it
            # is the ONLY check that catches that one. Our side scopes over
            # title + description, matching _our_identifying_terms.
            if _has_conflicting_bill_number(our_full_text, peer_text):
                continue

            # Numeric thresholds are subject-identifying the same way bill
            # numbers are, and no other check sees them.
            if _has_conflicting_threshold(our_full_text, peer_text):
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
            if similarity >= PEER_MARKET_SIMILARITY_THRESHOLD and similarity > best_similarity:
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
