# resolution_finder/pipeline.py
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional
from resolution_finder.market_provider import MarketProvider
from resolution_finder.models import Market, RankedArticle, Verdict
from resolution_finder.query_builder import build_queries
from resolution_finder.evidence_retriever import retrieve_evidence
from resolution_finder.article_extractor import extract_article_text, is_known_unresolvable_url
from resolution_finder.relevance_ranker import rank_by_relevance
from resolution_finder.verdict_engine import decide
from resolution_finder.peer_market import find_polymarket_match
from resolution_finder.storage import init_db, save_finding
from resolution_finder.config import REQUEST_DELAY_SECONDS, PEER_MARKET_ENABLED

logger = logging.getLogger(__name__)

VerdictEngine = Callable[[Market, list[RankedArticle]], Verdict]
PeerChecker = Callable[[Market], Optional[Verdict]]


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
) -> None:
    """Scan every unresolved market and store a proposed verdict for review.

    `verdict_engine` is injected the same way `market_provider` is, so the
    rule-based engine can be swapped for an AI-based one with no changes here
    or further upstream. `peer_checker` is injected the same way, so the
    Polymarket cross-check can be swapped out (or stubbed in tests) without
    editing the pipeline. The default already honours `PEER_MARKET_ENABLED`
    (see `_default_peer_checker`).
    """
    init_db(db_path)
    run_timestamp = datetime.now(timezone.utc).isoformat()

    for market in market_provider.get_unresolved_markets():
        try:
            _scan_market(market, db_path, run_timestamp, verdict_engine, peer_checker)
        except Exception as exc:  # noqa: BLE001 - one bad market must not abort the run
            logger.exception("Skipping market %s after error: %s", market.id, exc)


def _scan_market(
    market: Market,
    db_path: str,
    run_timestamp: str,
    verdict_engine: VerdictEngine,
    peer_checker: PeerChecker,
) -> None:
    peer_verdict = peer_checker(market)
    if peer_verdict is not None:
        save_finding(db_path, market.id, run_timestamp, peer_verdict)
        return

    queries = build_queries(market)
    candidate_refs = retrieve_evidence(market, queries)

    articles_with_text = []
    for ref in candidate_refs:
        if ref.source_type == "official_social":
            # Never fetch the actual X page — only use the search-result
            # snippet already captured by the retriever. The reviewer
            # checks the real post by hand via the dashboard link.
            text = ref.summary or ref.title
            if text:
                articles_with_text.append((ref, text))
            continue

        text = extract_article_text(ref.url)
        if text:
            articles_with_text.append((ref, text))
        # No request was made for a known-unresolvable host (see
        # article_extractor.py) -- nothing to rate-limit, so skip the sleep.
        if not is_known_unresolvable_url(ref.url):
            time.sleep(REQUEST_DELAY_SECONDS)

    ranked = rank_by_relevance(market, articles_with_text)
    verdicts = verdict_engine(market, ranked)
    if not isinstance(verdicts, list):
        verdicts = [verdicts]
    for verdict in verdicts:
        save_finding(db_path, market.id, run_timestamp, verdict)
