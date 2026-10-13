from datetime import date
from unittest.mock import patch

import pytest
import requests

from resolution_finder.models import Market
from resolution_finder.rain_pool_client import (
    SKIP_ALL_RESOLVED,
    SKIP_NO_RULES,
    RainPoolError,
    _default_fetch,
    plan_scan,
)


def make_market(market_id="m-1"):
    return Market(id=market_id, title="Will X happen?", description="", options=[], close_date=None)


def _response(body: bytes, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body
    return response


# Sub-pool records below are verbatim from Rain's own pool API (fields this
# module reads), fetched 2026-10-13: the U.S.-Iran "by <date>" market
# 6a661e50f8e64ced2cd8e486, three sub-pools settled "No" by Rain, one open.
IRAN_SUB_POOLS = [
    {"question": "December 31",
     "mergedQuestion": "Will the U.S. and Iran finalize a nuclear agreement by December 31?",
     "poolFinalized": False, "endDate": "2027-01-01T04:59:00.000Z", "status": "Live"},
    {"question": "September 30",
     "mergedQuestion": "Will the U.S. and Iran finalize a nuclear agreement by September 30?",
     "poolFinalized": True, "endDate": "2026-10-06T07:56:41.011Z", "status": "Closed",
     "winnerOption": {"choiceIndex": 2, "optionId": "6a661e50f8e64ced2cd8e48d",
                      "createdAt": "2026-10-06T08:08:26.793Z", "decidedBy": "olympus-ai"}},
    {"question": "August 31",
     "mergedQuestion": "Will the U.S. and Iran finalize a nuclear agreement by August 31?",
     "poolFinalized": True, "endDate": "2026-09-01T12:54:16.086Z", "status": "Closed",
     "winnerOption": {"choiceIndex": 2, "optionId": "6a661e50f8e64ced2cd8e490",
                      "createdAt": "2026-09-01T12:56:44.144Z", "decidedBy": "olympus-ai"}},
    {"question": "August 18",
     "mergedQuestion": "Will the U.S. and Iran finalize a nuclear agreement by August 18?",
     "poolFinalized": True, "endDate": "2026-08-20T09:11:06.031Z", "status": "Closed",
     "winnerOption": {"choiceIndex": 2, "optionId": "6a661e50f8e64ced2cd8e493",
                      "createdAt": "2026-08-20T09:24:46.412Z", "decidedBy": "olympus-ai"}},
]
RULES = "This market will resolve to \"Yes\" if, by the specified date at 11:59 PM ET, ..."


def _pool(sub_pools, description=RULES, **extra):
    return lambda market_id: {"poolDescription": description, "subPools": sub_pools, **extra}


def _open(name, merged=""):
    return {"question": name, "mergedQuestion": merged, "poolFinalized": False}


# ── plan_scan: the "do not run the finder" contract ──────────────────

def test_plan_skips_when_rain_has_no_description():
    for fetch in (_pool([], description=None), _pool([], description="   ")):
        plan = plan_scan(make_market(), fetch=fetch)
        assert plan.targets == ()
        assert plan.skip_reason == SKIP_NO_RULES


def test_plan_skips_when_the_fetch_fails():
    def fetch(market_id):
        raise RainPoolError("HTTP 404")
    plan = plan_scan(make_market(), fetch=fetch)
    assert plan.targets == () and plan.skip_reason == SKIP_NO_RULES


def test_plan_skips_a_market_rain_has_already_resolved():
    resolved = [sp for sp in IRAN_SUB_POOLS if sp["poolFinalized"]]
    assert plan_scan(make_market(), fetch=_pool(resolved)).skip_reason == SKIP_ALL_RESOLVED
    settled_binary = [{"question": "Yes", "poolFinalized": True, "status": "Closed"}]
    assert plan_scan(make_market(), fetch=_pool(settled_binary)).skip_reason == SKIP_ALL_RESOLVED


def test_plan_scans_a_live_market_whose_pool_flag_says_finalized():
    # Verbatim from Rain 2026-10-13, "Will Apple launch a foldable iPhone
    # by the end of 2026?" (6a60d883f8e64ced2c3fe90a): the POOL carries
    # isFinalized=true while it is Live and its one sub-pool is unsettled.
    # That flag was read as "already resolved" and the market was skipped.
    apple = [{"question": "Yes",
              "mergedQuestion": "Will Apple launch a foldable iPhone by the end of 2026?",
              "poolFinalized": False, "status": "Live", "endDate": "2027-01-01T04:59:00.000Z"}]
    plan = plan_scan(make_market(), fetch=_pool(apple, isFinalized=True, status="Live"))
    assert plan.skip_reason is None
    (target,) = plan.targets
    assert target.option is None and target.market.options == []


# ── plan_scan: what gets scanned ─────────────────────────────────────

def test_plan_overlays_the_real_description_and_keeps_id_title_close_date():
    market = Market(id="m-1", title="Will X happen?", description="Source URL: ...", options=[],
                    close_date=date(2026, 12, 31))
    plan = plan_scan(market, fetch=_pool([]))
    (target,) = plan.targets
    assert target.option is None
    assert target.market == Market(id="m-1", title="Will X happen?", description=RULES, options=[],
                                   close_date=date(2026, 12, 31))


def test_plan_single_sub_pool_is_binary_not_a_one_item_option_list():
    # Rain's binary-market shape: one sub-pool, named "Yes" -- a
    # placeholder, not an outcome. Must stay options=[] so decide() routes
    # to _decide_binary.
    (target,) = plan_scan(make_market(), fetch=_pool([_open("Yes")])).targets
    assert target.option is None and target.market.options == []


def test_plan_named_sub_pools_are_one_market_with_those_options():
    (target,) = plan_scan(make_market(), fetch=_pool([_open("Anthropic"), _open("OpenAI")])).targets
    assert target.option is None
    assert target.market.options == ["Anthropic", "OpenAI"]


def test_plan_leaves_out_named_sub_pools_rain_already_resolved():
    sub_pools = [_open("Anthropic"), {"question": "OpenAI", "poolFinalized": True}, _open("xAI")]
    (target,) = plan_scan(make_market(), fetch=_pool(sub_pools)).targets
    assert target.market.options == ["Anthropic", "xAI"]


def test_plan_malformed_sub_pool_list_is_not_trusted_as_a_split():
    (target,) = plan_scan(make_market(), fetch=_pool([_open("Team A"), {"not_question": "oops"}])).targets
    assert target.market.options == []


def test_plan_date_named_sub_pools_are_each_their_own_yes_no_market():
    market = Market(id="6a661e50f8e64ced2cd8e486",
                    title="Will the U.S. and Iran finalize a nuclear agreement by...?",
                    description="", options=[], close_date=date(2027, 1, 1))
    plan = plan_scan(market, fetch=_pool(IRAN_SUB_POOLS))
    # Only the sub-pool Rain has not resolved; its own question and deadline.
    (target,) = plan.targets
    assert target.option == "December 31"
    assert target.market.id == "6a661e50f8e64ced2cd8e486"
    assert target.market.title == "Will the U.S. and Iran finalize a nuclear agreement by December 31?"
    assert target.market.close_date == date(2027, 1, 1)
    assert target.market.options == []
    assert target.market.description == RULES


def test_plan_carries_the_day_rain_opened_a_date_sub_pool():
    sub_pools = [
        {**_open("November 30", "Will it happen by November 30?"), "createdAt": "2026-07-26T14:48:48.311Z"},
        _open("December 31", "Will it happen by December 31?"),
    ]
    targets = plan_scan(make_market(), fetch=_pool(sub_pools)).targets
    assert [t.opened for t in targets] == [date(2026, 7, 26), None]


def test_plan_each_open_date_sub_pool_carries_its_own_deadline():
    sub_pools = [
        {**_open("November 30", "Will it happen by November 30?"), "endDate": "2026-12-01T04:59:00.000Z"},
        {**_open("December 31", "Will it happen by December 31?"), "endDate": "2027-01-01T04:59:00.000Z"},
    ]
    targets = plan_scan(make_market(), fetch=_pool(sub_pools)).targets
    assert [(t.option, t.market.close_date) for t in targets] == [
        ("November 30", date(2026, 12, 1)), ("December 31", date(2027, 1, 1)),
    ]


def test_plan_a_name_that_only_starts_like_a_date_is_a_named_option():
    (target,) = plan_scan(make_market(), fetch=_pool([_open("August Wilson"), _open("May Sarton")])).targets
    assert target.option is None
    assert target.market.options == ["August Wilson", "May Sarton"]


def test_sub_pool_closed_for_trading_but_not_settled_is_still_scanned():
    # status "Closed" without poolFinalized / winnerOption: nobody has
    # settled it yet, which is exactly when an answer is needed.
    sub_pools = [
        {"question": "September 30", "mergedQuestion": "Will it happen by September 30?",
         "poolFinalized": False, "status": "Closed", "endDate": "2026-10-01T03:59:00.000Z"},
        {"question": "December 31", "mergedQuestion": "Will it happen by December 31?",
         "poolFinalized": False, "status": "Live", "endDate": "2027-01-01T04:59:00.000Z"},
    ]
    assert [t.option for t in plan_scan(make_market(), fetch=_pool(sub_pools)).targets] == [
        "September 30", "December 31",
    ]


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
