import json
import os
import tempfile
from resolution_finder.market_provider import JsonFileMarketProvider


def make_temp_markets_file(markets):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(markets, f)
    return path


def test_loads_binary_market():
    path = make_temp_markets_file([
        {
            "id": "m1",
            "title": "Will X happen?",
            "description": "Resolves Yes if X happens by 2026-12-31.",
            "options": [],
            "close_date": "2026-12-31",
        }
    ])
    provider = JsonFileMarketProvider(path)
    markets = provider.get_unresolved_markets()
    assert len(markets) == 1
    assert markets[0].id == "m1"
    assert markets[0].options == []
    os.remove(path)


def test_loads_multi_outcome_market():
    path = make_temp_markets_file([
        {
            "id": "m2",
            "title": "Who will win?",
            "description": "Resolves based on winner.",
            "options": ["A", "B"],
            "close_date": "2027-01-01",
        }
    ])
    provider = JsonFileMarketProvider(path)
    markets = provider.get_unresolved_markets()
    assert markets[0].options == ["A", "B"]
    os.remove(path)


def test_loads_market_with_null_close_date():
    # Real bug found live (2026-09-16): a real market can genuinely have
    # no close_date in its source data (some pulled March Madness
    # markets -- verified against the real data/markets.json, which
    # stores this as an explicit `"close_date": null`, not a missing
    # key). This loader called date.fromisoformat() unconditionally, so
    # the real production entrypoint (run_scan.py) crashed on the very
    # FIRST get_unresolved_markets() call against the current real data
    # -- before the pipeline's own per-market try/except ever ran,
    # taking down every market in the batch, not just this one.
    # run_eval.py's loader got the equivalent fix on 2026-09-07 (commit
    # ff6369d); this production loader was missed.
    path = make_temp_markets_file([
        {
            "id": "womens-march-madness-iowa-vs-lsu",
            "title": "Women's March Madness: Iowa vs. LSU",
            "description": "scheduled for April 1 at 7:15 PM ET",
            "options": ["Iowa", "LSU"],
            "close_date": None,
        }
    ])
    provider = JsonFileMarketProvider(path)
    markets = provider.get_unresolved_markets()
    assert markets[0].close_date is None
    os.remove(path)
