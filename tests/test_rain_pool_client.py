from unittest.mock import patch

import pytest
import requests

from resolution_finder.models import Market
from resolution_finder.rain_pool_client import (
    RainPoolError,
    _default_fetch,
    enrich_market_with_rain_description,
)


def make_market(market_id="m-1"):
    return Market(id=market_id, title="Will X happen?", description="", options=[], close_date=None)


def _response(body: bytes, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body
    return response


# ── enrich_market_with_rain_description: the "do not run the finder"
#    contract and the real-rules overlay ───────────────────────────────

def test_enrich_overlays_real_description_and_preserves_id_title_close_date():
    market = Market(id="m-1", title="Will X happen?", description="Source URL: ...", options=[], close_date=None)
    fetch = lambda market_id: {"poolDescription": "Resolves YES if X happens by Dec 31.", "subPools": []}
    enriched = enrich_market_with_rain_description(market, fetch=fetch)
    assert enriched.id == "m-1"
    assert enriched.title == "Will X happen?"
    assert enriched.description == "Resolves YES if X happens by Dec 31."
    assert enriched.options == []


def test_enrich_derives_options_from_two_or_more_subpools():
    fetch = lambda market_id: {
        "poolDescription": "Resolves YES for whichever of Anthropic/OpenAI IPOs first.",
        "subPools": [{"question": "Anthropic"}, {"question": "OpenAI"}],
    }
    enriched = enrich_market_with_rain_description(make_market(), fetch=fetch)
    assert enriched.options == ["Anthropic", "OpenAI"]


def test_enrich_single_subpool_is_binary_not_a_one_item_option_list():
    # Rain's binary-market convention: one subPool, named "Yes" -- a
    # placeholder, not a real outcome. Must stay options=[] so decide()
    # routes to _decide_binary, not _decide_multi_outcome.
    fetch = lambda market_id: {
        "poolDescription": "Resolves YES if MicroStrategy declares bankruptcy.",
        "subPools": [{"question": "Yes"}],
    }
    enriched = enrich_market_with_rain_description(make_market(), fetch=fetch)
    assert enriched.options == []


def test_enrich_malformed_subpool_list_falls_back_to_no_split():
    fetch = lambda market_id: {
        "poolDescription": "Resolves YES for the winner.",
        "subPools": [{"question": "Team A"}, {"not_question": "oops"}],
    }
    enriched = enrich_market_with_rain_description(make_market(), fetch=fetch)
    assert enriched.options == []


def test_enrich_returns_none_when_description_missing():
    fetch = lambda market_id: {"subPools": []}
    assert enrich_market_with_rain_description(make_market(), fetch=fetch) is None


def test_enrich_returns_none_when_description_blank():
    fetch = lambda market_id: {"poolDescription": "   ", "subPools": []}
    assert enrich_market_with_rain_description(make_market(), fetch=fetch) is None


def test_enrich_returns_none_when_fetch_raises():
    def fetch(market_id):
        raise RainPoolError("HTTP 404")
    assert enrich_market_with_rain_description(make_market(), fetch=fetch) is None


# ── _default_fetch: the real HTTP call, mocked like page_fetch.py's tests ──

def test_default_fetch_returns_data_object_on_200():
    body = b'{"data": {"poolDescription": "Resolves YES.", "subPools": []}}'
    with patch("resolution_finder.rain_pool_client.requests.get", return_value=_response(body)):
        data = _default_fetch("m-1")
    assert data == {"poolDescription": "Resolves YES.", "subPools": []}


def test_default_fetch_raises_on_http_error_status():
    with patch("resolution_finder.rain_pool_client.requests.get", return_value=_response(b"{}", 404)):
        with pytest.raises(RainPoolError):
            _default_fetch("m-1")


def test_default_fetch_raises_on_network_failure():
    with patch("resolution_finder.rain_pool_client.requests.get", side_effect=requests.ConnectionError("down")):
        with pytest.raises(RainPoolError):
            _default_fetch("m-1")


def test_default_fetch_raises_on_non_json_body():
    with patch("resolution_finder.rain_pool_client.requests.get", return_value=_response(b"not json")):
        with pytest.raises(RainPoolError):
            _default_fetch("m-1")


def test_default_fetch_raises_when_no_data_object():
    with patch("resolution_finder.rain_pool_client.requests.get", return_value=_response(b'{"message": "no"}')):
        with pytest.raises(RainPoolError):
            _default_fetch("m-1")
