# run_scan.py
import logging
from resolution_finder.market_provider import JsonFileMarketProvider
from resolution_finder.pipeline import run_pipeline
from resolution_finder.config import DB_PATH, MARKETS_JSON_PATH, PEER_MARKET_ENABLED


def _no_peer_check(market):
    return None


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    provider = JsonFileMarketProvider(MARKETS_JSON_PATH)
    # The rule-based verdict engine is the default; run_pipeline accepts a
    # `verdict_engine=` callable so an AI-based one can be swapped in here
    # without touching the pipeline.
    kwargs = {}
    if not PEER_MARKET_ENABLED:
        kwargs["peer_checker"] = _no_peer_check
    run_pipeline(provider, DB_PATH, **kwargs)


if __name__ == "__main__":
    main()
