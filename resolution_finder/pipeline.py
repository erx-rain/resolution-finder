# resolution_finder/pipeline.py
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional
from resolution_finder.market_provider import MarketProvider
from resolution_finder.models import Market, RankedArticle, Verdict
from resolution_finder.query_builder import build_queries, fallback_queries
from resolution_finder.evidence_retriever import CONTEXT_SOURCE_TYPE, retrieve_evidence
from resolution_finder.article_extractor import extract_article_text, is_known_unresolvable_url
from resolution_finder.relevance_ranker import rank_by_relevance, best_below_threshold
from resolution_finder.verdict_engine import decide
from resolution_finder.peer_market import find_polymarket_match
from resolution_finder.resolution_spec import check_availability
from resolution_finder.storage import init_db, save_finding
from resolution_finder.structured_resolvers import resolve_structured
from resolution_finder.config import REQUEST_DELAY_SECONDS, PEER_MARKET_ENABLED

logger = logging.getLogger(__name__)

VerdictEngine = Callable[[Market, list[RankedArticle]], Verdict]
PeerChecker = Callable[[Market], Optional[Verdict]]
StructuredResolver = Callable[[Market], Optional[list[Verdict]]]


def _promote_best_below_threshold(
    verdicts: list[Verdict], market: Market, articles_with_text: list
) -> list[Verdict]:
    """When every proposed verdict is NO_EVIDENCE but real article text was
    fetched (it just fell below relevance_ranker.SIMILARITY_THRESHOLD),
    surface the single closest article as UNCLEAR instead of silently
    discarding it -- so a human reviewer sees the closest real evidence
    found rather than nothing. This does NOT feed the article back into
    verdict_engine's keyword/semantic matching (rank_by_relevance's real
    threshold gate, called above, is unchanged), so it can't introduce a
    new false-positive surface the way lowering SIMILARITY_THRESHOLD itself
    would -- see the 2026-08-18 world-cup-highest-scoring-match finding in
    the ledger, where real relevant evidence was silently dropped.
    """
    if not articles_with_text or not all(v.outcome == "NO_EVIDENCE" for v in verdicts):
        return verdicts
    if any(v.option is not None for v in verdicts):
        # Per-option rows (a market with listed options, 2026-10-13): the
        # closest article is about the market, not about any one option,
        # and copying it onto every option's row would present the same
        # unrelated text as if it were evidence about each of them.
        return verdicts
    best = best_below_threshold(market, articles_with_text)
    if best is None:
        return verdicts
    return [
        Verdict(
            outcome="UNCLEAR", confidence=best.similarity,
            evidence_snippet=best.text[:280],
            source_url=best.article.url, source_type=best.article.source_type,
            option=v.option,
        )
        for v in verdicts
    ]


def _attach_context(verdicts: list[Verdict], ranked: list[RankedArticle]) -> list[Verdict]:
    """A finding that found nothing it may rest a verdict on still shows the
    most relevant context headline, marked as context (see
    evidence_retriever.CONTEXT_SOURCE_TYPE). Only for a single, option-less
    NO_EVIDENCE finding -- the outcome becomes UNCLEAR ("something was
    found, it decides nothing"), never YES or NO."""
    context = [item for item in ranked if item.article.source_type == CONTEXT_SOURCE_TYPE]
    if len(verdicts) != 1 or verdicts[0].outcome != "NO_EVIDENCE" or not context:
        return verdicts
    best = context[0]
    dated = f" ({best.article.published_date.isoformat()})" if best.article.published_date else ""
    return [Verdict(
        outcome="UNCLEAR", confidence=best.similarity,
        evidence_snippet=(
            f"{best.text.strip()[:240]} — {best.article.source_domain}{dated}"
            " [Context only: a headline from a publisher outside the verified-source list."
            " Shown for review; no verdict rests on it.]"
        ),
        source_url=best.article.url, source_type=CONTEXT_SOURCE_TYPE,
        option=verdicts[0].option,
    )]


def _default_peer_checker(market: Market) -> Optional[Verdict]:
    """The default `peer_checker`, which respects PEER_MARKET_ENABLED.

    Reading the config flag here -- inside the shared mechanism -- rather
    than only at whichever caller happens to exist today (previously just
    run_scan.py) means EVERY caller of run_pipeline() honours the flag by
    default, including any future one that doesn't know to check it itself.
    A caller that explicitly passes its own `peer_checker=` still overrides
    this entirely, same as before.
    """
    if not PEER_MARKET_ENABLED:
        return None
    return find_polymarket_match(market)


def run_pipeline(
    market_provider: MarketProvider,
    db_path: str,
    verdict_engine: VerdictEngine = decide,
    peer_checker: PeerChecker = _default_peer_checker,
    structured_resolver: StructuredResolver = resolve_structured,
) -> None:
    """Scan every unresolved market and store a proposed verdict for review.

    `verdict_engine` is injected the same way `market_provider` is, so the
    rule-based engine can be swapped for an AI-based one with no changes here
    or further upstream. `peer_checker` is injected the same way, so the
    Polymarket cross-check can be swapped out (or stubbed in tests) without
    editing the pipeline. The default already honours `PEER_MARKET_ENABLED`
    (see `_default_peer_checker`).

    `structured_resolver` runs first and is injected the same way: a market
    it claims (e.g. a Fed rate decision, answered from federalreserve.gov)
    is saved from its verdicts and never reaches peer-check or news.
    """
    init_db(db_path)
    run_timestamp = datetime.now(timezone.utc).isoformat()

    for market in market_provider.get_unresolved_markets():
        try:
            _scan_market(market, db_path, run_timestamp, verdict_engine, peer_checker, structured_resolver)
        except Exception as exc:  # noqa: BLE001 - one bad market must not abort the run
            logger.exception("Skipping market %s after error: %s", market.id, exc)


# Fewer candidates than this from the first queries and fallback_queries
# runs too. Found 2026-10-14 at 0: two queries, no result, "nothing found".
THIN_RESULTS = 3


def _scan_market(
    market: Market,
    db_path: str,
    run_timestamp: str,
    verdict_engine: VerdictEngine,
    peer_checker: PeerChecker,
    structured_resolver: StructuredResolver,
    trace: Optional[dict] = None,
) -> None:
    """`trace`, when given, is filled with what this scan searched, what it
    found and read, and what it decided -- the record a reviewer needs to
    judge "nothing found" (owner request 2026-10-14: a run that searched
    nothing looked the same as one that read twenty articles)."""
    if trace is None:
        trace = {}
    # Computed once per market, independent of everything below -- pure
    # date arithmetic against the market's close_date and its own
    # description (see resolution_spec.py), no retrieval or model calls.
    # Deliberately NOT a gate on any of the retrieval/verdict work that
    # follows: an early triggering event (Path B in the design doc) is
    # exactly what that work exists to catch BEFORE the deadline, so
    # availability=False must never skip it. It is attached to every
    # finding this market produces below as independent context for a
    # reviewer -- "is this worth a look" is a different question from
    # "what does the evidence say", per the 2026-09-08 availability/
    # outcome split (docs/superpowers/plans/2026-09-08-resolution-
    # availability-design.md): a false "available" costs one wasted
    # glance, a wrong outcome resolves a market incorrectly, so this can
    # ship at a much lower bar without touching verdict_engine's own.
    availability = check_availability(market)

    # A market answered from its own official structured source (Fed rate
    # decisions today) never falls through to peer-check or news -- for
    # these types the news path is where wrong answers come from. See
    # structured_resolvers.py.
    structured_verdicts = structured_resolver(market)
    if structured_verdicts is not None:
        trace["path"] = "official source"
        for verdict in structured_verdicts:
            save_finding(db_path, market.id, run_timestamp, verdict, availability)
        return

    peer_verdict = peer_checker(market)
    if peer_verdict is not None:
        trace["path"] = "peer market"
        save_finding(db_path, market.id, run_timestamp, peer_verdict, availability)
        return

    queries = build_queries(market)
    candidate_refs = retrieve_evidence(market, queries)
    retried = []
    if len(candidate_refs) < THIN_RESULTS:
        retried = fallback_queries(market, already=queries)
        if retried:
            seen = {ref.url for ref in candidate_refs}
            for ref in retrieve_evidence(market, retried):
                if ref.url not in seen:
                    seen.add(ref.url)
                    candidate_refs.append(ref)
    trace["path"] = "news"
    trace["queries"] = queries
    trace["retry_queries"] = retried
    read_status: dict[str, str] = {}

    articles_with_text = []
    for ref in candidate_refs:
        if (ref.source_type in ("official_social", CONTEXT_SOURCE_TYPE)
                or (ref.summary and is_known_unresolvable_url(ref.url))):
            # Never fetch the actual X page — only use the search-result
            # snippet already captured by the retriever. The reviewer
            # checks the real post by hand via the dashboard link.
            #
            # The same rule now covers the date-scoped archive pass: those
            # are news.google.com JS-redirect wrappers (an UNRESOLVABLE_HOST
            # that returns 200 with no content no matter how the request is
            # made), so the headline carried in `summary` is the only text
            # that will ever exist for them.
            text = ref.summary or ref.title
            if text:
                articles_with_text.append((ref, text))
            read_status[ref.url] = "headline only"
            continue

        text = extract_article_text(ref.url)
        if text:
            articles_with_text.append((ref, text))
        read_status[ref.url] = "read" if text else "could not be read"
        # No request was made for a known-unresolvable host (see
        # article_extractor.py) -- nothing to rate-limit, so skip the sleep.
        if not is_known_unresolvable_url(ref.url):
            time.sleep(REQUEST_DELAY_SECONDS)

    ranked = rank_by_relevance(market, articles_with_text)
    relevance = {item.article.url: round(item.similarity, 3) for item in ranked}
    trace["articles"] = [
        {
            "domain": ref.source_domain, "title": ref.title, "url": ref.url,
            "source_type": ref.source_type,
            "published": ref.published_date.isoformat() if ref.published_date else None,
            "status": read_status.get(ref.url, "not read"),
            "relevance": relevance.get(ref.url),
        }
        for ref in candidate_refs
    ]
    verdicts = verdict_engine(market, ranked)
    if not isinstance(verdicts, list):
        verdicts = [verdicts]
    verdicts = _attach_context(verdicts, ranked)
    verdicts = _promote_best_below_threshold(verdicts, market, articles_with_text)
    for verdict in verdicts:
        save_finding(db_path, market.id, run_timestamp, verdict, availability)
