# run_scan.py
from resolution_finder.market_provider import JsonFileMarketProvider
from resolution_finder.pipeline import run_pipeline
from resolution_finder.config import DB_PATH, MARKETS_JSON_PATH


def main():
    provider = JsonFileMarketProvider(MARKETS_JSON_PATH)
    run_pipeline(provider, DB_PATH)


if __name__ == "__main__":
    main()
