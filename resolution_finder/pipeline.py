# resolution_finder/pipeline.py
import time
from datetime import datetime, timezone
from resolution_finder.market_provider import MarketProvider
from resolution_finder.query_builder import build_queries
from resolution_finder.evidence_retriever import retrieve_evidence
from resolution_finder.article_extractor import extract_article_text
from resolution_finder.relevance_ranker import rank_by_relevance
from resolution_finder.verdict_engine import decide
from resolution_finder.storage import init_db, save_finding
from resolution_finder.config import REQUEST_DELAY_SECONDS


def run_pipeline(market_provider: MarketProvider, db_path: str) -> None:
    init_db(db_path)
    run_timestamp = datetime.now(timezone.utc).isoformat()

    for market in market_provider.get_unresolved_markets():
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
            time.sleep(REQUEST_DELAY_SECONDS)

        ranked = rank_by_relevance(market, articles_with_text)
        verdict = decide(market, ranked)
        save_finding(db_path, market.id, run_timestamp, verdict)
