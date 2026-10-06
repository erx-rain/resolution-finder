import json
from datetime import date

import pytest

from resolution_finder.gist_market_provider import (
    GistMarketProvider,
    GistProviderError,
    parse_gist_markets,
)


def test_parse_list_shape_matches_markets_json():
    payload = [
        {
            "id": "m-1",
            "title": "Will X happen by June 2026?",
            "description": "Full resolution rules verbatim.",
            "options": [],
            "close_date": "2026-06-30",
        }
    ]
    markets = parse_gist_markets(payload)
    assert len(markets) == 1
    m = markets[0]
    assert m.id == "m-1"
    assert m.description == "Full resolution rules verbatim."
    assert m.close_date == date(2026, 6, 30)
    assert m.options == []


def test_parse_keyed_object_shape_with_aliases():
    payload = {
        "pool-abc": {
            "question": "Who wins the final?",
            "description": "Settles on the official result.",
            "endDate": "2026-07-01T18:00:00Z",
            "options": [{"optionName": "Team A"}, {"optionName": "Team B"}],
        }
    }
    markets = parse_gist_markets(payload)
    assert len(markets) == 1
    m = markets[0]
    assert m.id == "pool-abc"
    assert m.title == "Who wins the final?"
    assert m.close_date == date(2026, 7, 1)
    assert m.options == ["Team A", "Team B"]


def test_missing_close_date_is_none_never_guessed():
    markets = parse_gist_markets([
        {"id": "m-2", "title": "T", "description": "D", "options": []}
    ])
    assert markets[0].close_date is None


def test_missing_description_is_empty_string_market_still_listed():
    markets = parse_gist_markets([{"id": "m-3", "title": "T"}])
    assert markets[0].description == ""


def test_titleless_record_skipped():
    markets = parse_gist_markets([{"id": "m-4", "description": "D"}])
    assert markets == []


def test_invalid_top_level_shape_raises():
    with pytest.raises(GistProviderError):
        parse_gist_markets("not a list or dict")


def test_provider_uses_injected_fetch_no_network():
    payload = [{"id": "m-5", "title": "T", "description": "D", "options": []}]

    def fake_fetch(gist_id, file_name, token):
        assert gist_id == "g123"
        assert file_name == "rf_markets.json"
        return json.dumps(payload)

    provider = GistMarketProvider(gist_id="g123", file_name="rf_markets.json", token=None, fetch=fake_fetch)
    markets = provider.get_unresolved_markets()
    assert [m.id for m in markets] == ["m-5"]
    assert provider.get_market("m-5").title == "T"
    assert provider.get_market("nope") is None


def test_provider_requires_gist_id(monkeypatch):
    monkeypatch.delenv("RF_GIST_ID", raising=False)
    with pytest.raises(GistProviderError):
        GistMarketProvider(gist_id=None, fetch=lambda *a: "[]")
