# tests/test_peer_market.py
from unittest.mock import patch, MagicMock
from datetime import date
from resolution_finder.models import Market
from resolution_finder.peer_market import (
    find_polymarket_match,
    _has_conflicting_bill_number,
    _has_conflicting_entity,
    _has_conflicting_resolution_window,
    _has_conflicting_threshold,
    _extract_numeric_thresholds,
    _our_identifying_terms,
    _peer_end_date,
    _search_polymarket_events,
    _resolved_outcome,
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
                "question": "Will the Digital Asset Market Clarity Act of 2025 (H.R.3633) be signed into law by December 31 2026?",
                "description": LIVE_POLYMARKET_BOILERPLATE,
                "closed": True,
                "umaResolutionStatus": "resolved",
                "endDateIso": "2026-12-31",
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


BITCOIN_MARKET = Market(
    id="bitcoin-200k-2026",
    title="Will Bitcoin reach $200,000 in 2026?",
    description="Resolves Yes if Bitcoin trades at or above $200,000 at any point in 2026.",
    options=[],
    close_date=date(2026, 12, 31),
)


def test_peer_end_date_parses_both_live_field_formats():
    assert _peer_end_date({"endDateIso": "2025-12-31"}) == date(2025, 12, 31)
    assert _peer_end_date({"endDate": "2025-12-31T00:00:00Z"}) == date(2025, 12, 31)
    assert _peer_end_date({}) is None


def test_has_conflicting_resolution_window_rejects_earlier_deadline():
    # Real wrong-answer match found live: same bill (H.R.3633), deadline a
    # full year earlier, resolved NO for missing THAT deadline.
    assert _has_conflicting_resolution_window(date(2026, 12, 31), date(2025, 12, 31)) is True


def test_has_conflicting_resolution_window_rejects_later_deadline():
    # The other direction of the same real wrong-answer shape: our market
    # asks "...by Jan 31 2025", the peer asks "...by Dec 31 2025" and
    # resolved YES because the event happened in the intervening months. The
    # true answer for our (earlier-closing) market is NO, but the peer's YES
    # would have been proposed with high confidence.
    assert _has_conflicting_resolution_window(date(2025, 1, 31), date(2025, 12, 31)) is True


def test_has_conflicting_resolution_window_allows_small_date_differences():
    assert _has_conflicting_resolution_window(date(2026, 12, 31), date(2026, 12, 31)) is False
    assert _has_conflicting_resolution_window(date(2026, 12, 31), date(2026, 12, 29)) is False


def test_has_conflicting_resolution_window_ignores_missing_peer_date():
    assert _has_conflicting_resolution_window(date(2026, 12, 31), None) is False


def test_has_conflicting_threshold_detects_different_strike_price():
    # Real false positive found live at 0.65 similarity.
    assert _has_conflicting_threshold(
        "Will Bitcoin reach $200,000 in 2026?",
        "Will Bitcoin reach $80,000 by December 31, 2026?",
    ) is True


def test_has_conflicting_threshold_treats_equivalent_amounts_as_same():
    assert _has_conflicting_threshold(
        "Will Bitcoin reach $200,000 in 2026?", "Will Bitcoin hit $200K in 2026?"
    ) is False


def test_has_conflicting_threshold_false_when_no_numbers():
    assert _has_conflicting_threshold(
        "Will the CLARITY act be signed into law?", "Will the Clarity Act be signed into law?"
    ) is False


def test_extract_numeric_thresholds_handles_bn_and_mm_suffixes():
    # Previously the suffix alternation only recognized single-letter
    # ([KMB]) or full-word suffixes, so "$5bn" failed to match AT ALL (the
    # trailing \b anchor couldn't find a boundary between the digit and "b"),
    # making the threshold invisible to _has_conflicting_threshold.
    money, _ = _extract_numeric_thresholds("Will GDP exceed $5bn in 2026?")
    assert money == {5_000_000_000.0}

    money, _ = _extract_numeric_thresholds("Will GDP exceed $5 bn in 2026?")
    assert money == {5_000_000_000.0}

    money, _ = _extract_numeric_thresholds("Will revenue exceed $5mm in 2026?")
    assert money == {5_000_000.0}


def test_has_conflicting_threshold_detects_bn_suffix_mismatch():
    assert _has_conflicting_threshold(
        "Will GDP exceed $5bn in 2026?", "Will GDP exceed $2bn in 2026?"
    ) is True


def test_short_subject_term_does_not_substring_match_an_unrelated_bill():
    """A market naming no bill number gets no protection from the bill-number
    check, so the entity check has to carry it. Previously the short term
    "CLARITY" substring-matched "...Guidance Clarity Act..." and let the real
    false match through at 0.70 confidence.
    """
    weak_market = Market(
        id="clarity-act-2026",
        title="Will the CLARITY act be signed into law in 2026?",
        description="If these conditions are not met by the deadline, the market resolves to \"No\".",
        options=[],
        close_date=date(2026, 12, 31),
    )
    terms = _our_identifying_terms(weak_market)
    assert _has_conflicting_entity(
        terms, "Will the Guidance Clarity Act of 2025 (S.81) be signed into law by December 31 2025?"
    ) is True


@patch("resolution_finder.peer_market.util.cos_sim")
@patch("resolution_finder.peer_market._get_model")
@patch("resolution_finder.peer_market.requests.get")
def test_rejects_same_bill_resolving_before_our_deadline(mock_get, mock_get_model, mock_cos_sim):
    """The real wrong-answer false positive, end to end.

    Our clarity-act-2026 market asks whether H.R.3633 is signed into law by
    Dec 31 2026. Polymarket has the identical bill with a Dec 31 2025
    deadline, which resolved NO for missing that earlier deadline. Same
    subject, same bill number, high similarity -- and proposing NO for our
    still-open market is simply the wrong answer.
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
                "endDate": "2025-12-31T00:00:00Z",
                "endDateIso": "2025-12-31",
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
    mock_cos_sim.return_value = [[0.83]]  # the real observed score

    assert find_polymarket_match(CLARITY_MARKET) is None


@patch("resolution_finder.peer_market.util.cos_sim")
@patch("resolution_finder.peer_market._get_model")
@patch("resolution_finder.peer_market.requests.get")
def test_rejects_same_bill_resolving_after_our_deadline(mock_get, mock_get_model, mock_cos_sim):
    """The other direction of the same real wrong-answer shape, end to end.

    Live-verified against real Polymarket data: the GENIUS Act was actually
    signed into law in July 2025. A market asking whether it was signed into
    law by June 30, 2025 should resolve "No" -- but the real, resolved
    Polymarket event "GENIUS Act signed into law in 2025?" (deadline Dec 31
    2025) resolved "Yes", because the bill was signed in the intervening
    months. Matching our earlier-closing market to that later-closing peer
    would have proposed a confidently wrong YES.
    """
    our_market = Market(
        id="genius-act-june-2025",
        title="Will the GENIUS Act be signed into law by June 30, 2025?",
        description=(
            "This market resolves Yes if the GENIUS Act is signed into law "
            "by June 30, 2025."
        ),
        options=[],
        close_date=date(2025, 6, 30),
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "events": [{
            "slug": "genius-act-signed-into-law-in-2025",
            "markets": [{
                "question": "GENIUS Act signed into law in 2025?",
                "description": "",
                "closed": True,
                "umaResolutionStatus": "resolved",
                "endDateIso": "2025-12-31",
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
    mock_cos_sim.return_value = [[0.828]]  # the real observed score

    assert find_polymarket_match(our_market) is None


@patch("resolution_finder.peer_market.util.cos_sim")
@patch("resolution_finder.peer_market._get_model")
@patch("resolution_finder.peer_market.requests.get")
def test_rejects_same_asset_with_different_strike_price(mock_get, mock_get_model, mock_cos_sim):
    """Real false positive: same asset, same year, 2.5x different price."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "events": [{
            "slug": "bitcoin-80k-2026",
            "markets": [{
                "question": "Will Bitcoin reach $80,000 by December 31, 2026?",
                "description": "Resolves Yes if Bitcoin trades at or above $80,000.",
                "closed": True,
                "umaResolutionStatus": "resolved",
                "endDateIso": "2026-12-31",
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
    mock_cos_sim.return_value = [[0.65]]  # the real observed score

    assert find_polymarket_match(BITCOIN_MARKET) is None


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


# --- Malformed-response robustness (a 200 response is not a guarantee of a
# well-formed body; none of this should ever raise, only degrade to no
# candidates found / None) ----------------------------------------------------

@patch("resolution_finder.peer_market.requests.get")
def test_search_polymarket_events_handles_non_json_body(mock_get):
    """A 200 response with a non-JSON body raises ValueError from
    response.json(). Previously that call sat outside the try/except, so it
    propagated uncaught through find_polymarket_match -> _scan_market ->
    run_pipeline, causing the whole market to be skipped for the run."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
    mock_get.return_value = mock_response

    assert _search_polymarket_events("query") == []


@patch("resolution_finder.peer_market.requests.get")
def test_search_polymarket_events_handles_top_level_list_payload(mock_get):
    """A JSON top-level list (instead of the expected dict) raises
    AttributeError on `.get("events")` if unguarded."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = [{"events": []}]
    mock_get.return_value = mock_response

    assert _search_polymarket_events("query") == []


@patch("resolution_finder.peer_market.requests.get")
def test_find_polymarket_match_handles_non_json_body_gracefully_end_to_end(mock_get):
    """End-to-end: a malformed Polymarket response must not raise out of
    find_polymarket_match, since an uncaught exception here makes
    run_pipeline skip the whole market (see pipeline.py's per-market
    exception handling)."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.side_effect = ValueError("not json")
    mock_get.return_value = mock_response

    assert find_polymarket_match(CLARITY_MARKET) is None


def test_resolved_outcome_handles_malformed_outcome_prices():
    """A bad outcomePrices string (e.g. blank entries) must not raise out of
    float() -- previously the float() calls sat outside the try/except that
    only wrapped the json.loads calls."""
    peer_market = {"outcomes": '["Yes", "No"]', "outcomePrices": '["", ""]'}
    assert _resolved_outcome(peer_market) is None


def test_resolved_outcome_handles_non_numeric_outcome_prices():
    peer_market = {"outcomes": '["Yes", "No"]', "outcomePrices": '["abc", "def"]'}
    assert _resolved_outcome(peer_market) is None


def test_resolved_outcome_still_works_for_well_formed_data():
    peer_market = {"outcomes": '["Yes", "No"]', "outcomePrices": '["1", "0"]'}
    assert _resolved_outcome(peer_market) == "Yes"


# --- PEER_MARKET_SIMILARITY_THRESHOLD -----------------------------------------
# The peer-market similarity gate previously reused the news-relevance
# SIMILARITY_THRESHOLD (0.35), which does almost no safety work here: a real
# wrong-bill false match (Guidance Clarity Act S.81 vs. our real H.R.3633
# market) scored 0.71 cosine similarity, and a real bill-confirmed genuine
# match was separately observed as low as 0.7116 -- both far above 0.35, and
# close enough to each other that similarity alone cannot discriminate them
# in that range. Genuine matches that survive the 4 hard-reject conflict
# checks were observed at 0.83+. peer_market.py now uses a dedicated,
# stricter PEER_MARKET_SIMILARITY_THRESHOLD (0.75) instead.

def _genuine_candidate_response():
    return {
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


@patch("resolution_finder.peer_market.util.cos_sim")
@patch("resolution_finder.peer_market._get_model")
@patch("resolution_finder.peer_market.requests.get")
def test_peer_market_threshold_rejects_score_in_observed_false_match_range(
    mock_get, mock_get_model, mock_cos_sim
):
    """A conflict-free (would-otherwise-pass) candidate scoring 0.72 -- inside
    the observed false-match cluster (0.65-0.7116) -- must be rejected by the
    stricter peer-market threshold, even though it clears the old shared
    SIMILARITY_THRESHOLD (0.35) by a wide margin.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = _genuine_candidate_response()
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.return_value = [[0.72]]

    assert find_polymarket_match(CLARITY_MARKET) is None


@patch("resolution_finder.peer_market.util.cos_sim")
@patch("resolution_finder.peer_market._get_model")
@patch("resolution_finder.peer_market.requests.get")
def test_peer_market_threshold_still_accepts_genuine_high_similarity_match(
    mock_get, mock_get_model, mock_cos_sim
):
    """A genuine match at the observed genuine-match confidence level (0.83+)
    must still be accepted by the stricter threshold."""
    mock_response = MagicMock()
    mock_response.json.return_value = _genuine_candidate_response()
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.return_value = [[0.85]]

    result = find_polymarket_match(CLARITY_MARKET)
    assert result is not None
    assert result.outcome == "YES"


@patch("resolution_finder.peer_market.util.cos_sim")
@patch("resolution_finder.peer_market._get_model")
@patch("resolution_finder.peer_market.requests.get")
def test_peer_market_threshold_boundary(mock_get, mock_get_model, mock_cos_sim):
    """Exactly at PEER_MARKET_SIMILARITY_THRESHOLD (0.75) passes; just below
    it does not."""
    mock_response = MagicMock()
    mock_response.json.return_value = _genuine_candidate_response()
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model

    mock_cos_sim.return_value = [[0.75]]
    assert find_polymarket_match(CLARITY_MARKET) is not None

    mock_cos_sim.return_value = [[0.749]]
    assert find_polymarket_match(CLARITY_MARKET) is None
