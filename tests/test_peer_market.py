# tests/test_peer_market.py
from unittest.mock import patch, MagicMock
from datetime import date
from resolution_finder.models import Market
from resolution_finder.peer_market import (
    find_polymarket_match,
    _has_conflicting_bill_number,
    _has_conflicting_entity,
    _our_identifying_terms,
)

CLARITY_MARKET = Market(
    id="clarity-act-2026",
    title="Will the CLARITY act be signed into law in 2026?",
    description=(
        "This market resolves to \"Yes\" if the Digital Asset Market Clarity Act "
        "of 2025 (H.R. 3633) is approved by both the U.S. House of Representatives "
        "and the U.S. Senate, and is signed into law."
    ),
    options=[],
    close_date=date(2026, 12, 31),
)

VINICIUS_MARKET = Market(
    id="vinicius-transfer-2026",
    title="Which team will Vinicius Junior join next?",
    description="This market will settle based on the next team Vinicius Junior officially joins.",
    options=["Real Madrid", "Arsenal"],
    close_date=date(2026, 9, 1),
)


def test_has_conflicting_bill_number_detects_different_bill():
    # Real false match found via live Polymarket search during planning.
    assert _has_conflicting_bill_number(
        CLARITY_MARKET.description,
        "Will the Guidance Clarity Act of 2025 (S.81) be signed into law?",
    ) is True


def test_has_conflicting_bill_number_false_when_no_bill_number_in_either():
    assert _has_conflicting_bill_number(
        "no bill number here", "also no bill number here",
    ) is False


def test_has_conflicting_entity_detects_different_person():
    # Real false match found via live Polymarket search during planning.
    terms = _our_identifying_terms(VINICIUS_MARKET)
    assert _has_conflicting_entity(terms, "Will Steve Kerr join the Atlanta Hawks in 2026?") is True


def test_has_conflicting_entity_false_when_same_subject():
    terms = _our_identifying_terms(CLARITY_MARKET)
    assert _has_conflicting_entity(
        terms, "Will the Digital Asset Market Clarity Act be signed into law?"
    ) is False


def test_multi_outcome_market_never_checked():
    result = find_polymarket_match(VINICIUS_MARKET)
    assert result is None


@patch("resolution_finder.peer_market.requests.get")
def test_find_polymarket_match_rejects_conflicting_bill_number(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "events": [{
            "slug": "guidance-clarity-act",
            "markets": [{
                "question": "Will the Guidance Clarity Act of 2025 (S.81) be signed into law?",
                "description": "",
                "closed": True,
                "umaResolutionStatus": "resolved",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]',
            }],
        }],
    }
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    result = find_polymarket_match(CLARITY_MARKET)
    assert result is None


@patch("resolution_finder.peer_market.util.cos_sim")
@patch("resolution_finder.peer_market._get_model")
@patch("resolution_finder.peer_market.requests.get")
def test_find_polymarket_match_returns_verdict_for_genuine_match(mock_get, mock_get_model, mock_cos_sim):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "events": [{
            "slug": "clarity-act-hr-3633",
            "markets": [{
                "question": "Will the Digital Asset Market Clarity Act (H.R. 3633) be signed into law in 2026?",
                "description": "Resolves Yes if H.R. 3633 is signed into law.",
                "closed": True,
                "umaResolutionStatus": "resolved",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]',
            }],
        }],
    }
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.return_value = [[0.85]]

    result = find_polymarket_match(CLARITY_MARKET)
    assert result is not None
    assert result.outcome == "YES"
    assert result.source_type == "peer_market"
    assert result.source_url == "https://polymarket.com/event/clarity-act-hr-3633"
    assert "Digital Asset Market Clarity Act" in result.evidence_snippet


# Verbatim resolution boilerplate from the live Polymarket event
# "what-bills-will-be-signed-into-law-by-december-31". Every market in that
# event carries this same text, so it is what real candidates look like.
LIVE_POLYMARKET_BOILERPLATE = (
    'This market will resolve "Yes" if the bill listed is signed into law by '
    "the President of the United States by December 31, 2025, 11:59 PM ET. "
    'Otherwise, this market will resolve to "No".'
)


@patch("resolution_finder.peer_market.util.cos_sim")
@patch("resolution_finder.peer_market._get_model")
@patch("resolution_finder.peer_market.requests.get")
def test_genuine_match_survives_real_polymarket_boilerplate(mock_get, mock_get_model, mock_cos_sim):
    """Regression: the heuristic entity matcher pulls junk non-subject terms
    ("United States", "PM ET. Otherwise") out of Polymarket's boilerplate
    descriptions. Running the entity conflict check over the description
    therefore rejected 100% of live candidates, genuine ones included. The
    check runs against the peer question only for exactly this reason.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "events": [{
            "slug": "what-bills-will-be-signed-into-law-by-december-31",
            "markets": [{
                "question": "Will the Digital Asset Market Clarity Act of 2025 (H.R.3633) be signed into law by December 31 2025?",
                "description": LIVE_POLYMARKET_BOILERPLATE,
                "closed": True,
                "umaResolutionStatus": "resolved",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0", "1"]',
            }],
        }],
    }
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.return_value = [[0.83]]

    result = find_polymarket_match(CLARITY_MARKET)
    assert result is not None
    assert result.outcome == "NO"
    assert result.source_type == "peer_market"


@patch("resolution_finder.peer_market.util.cos_sim")
@patch("resolution_finder.peer_market._get_model")
@patch("resolution_finder.peer_market.requests.get")
def test_wrong_bill_still_rejected_despite_identical_boilerplate(mock_get, mock_get_model, mock_cos_sim):
    """Guards the above fix: narrowing the entity check to the question must
    NOT let the real "Guidance Clarity Act (S.81)" false match through. It is
    the bill-number check, run over the full peer text, that rejects it.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "events": [{
            "slug": "what-bills-will-be-signed-into-law-by-december-31",
            "markets": [{
                "question": "Will the Guidance Clarity Act of 2025 (S.81) be signed into law by December 31 2025?",
                "description": LIVE_POLYMARKET_BOILERPLATE,
                "closed": True,
                "umaResolutionStatus": "resolved",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0", "1"]',
            }],
        }],
    }
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    # Deliberately well above threshold: similarity must not be able to save
    # a candidate the conflict checks reject (0.71 was the real observed score).
    mock_cos_sim.return_value = [[0.95]]

    assert find_polymarket_match(CLARITY_MARKET) is None


@patch("resolution_finder.peer_market.requests.get")
def test_find_polymarket_match_ignores_unresolved_markets(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "events": [{
            "slug": "clarity-act-hr-3633",
            "markets": [{
                "question": "Will the Digital Asset Market Clarity Act (H.R. 3633) be signed into law in 2026?",
                "description": "",
                "closed": False,
                "umaResolutionStatus": "",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.5", "0.5"]',
            }],
        }],
    }
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    result = find_polymarket_match(CLARITY_MARKET)
    assert result is None


@patch("resolution_finder.peer_market.requests.get")
def test_find_polymarket_match_handles_search_failure_gracefully(mock_get):
    import requests
    mock_get.side_effect = requests.ConnectionError("failed")
    result = find_polymarket_match(CLARITY_MARKET)
    assert result is None
