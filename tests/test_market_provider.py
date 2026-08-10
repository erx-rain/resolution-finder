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
