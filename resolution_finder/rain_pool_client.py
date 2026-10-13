"""Fetches a market's real resolution rules from Rain's own public pool
API -- the one place the service path gets REAL rules instead of the
team gist's thin placeholder description.

Owner decision (2026-10-13): the gist file this service scans
(RF_GIST_FILE, currently rtap_history_2026.json) carries no resolution
rules at all -- its "description" is source metadata GistMarketProvider
synthesizes from su/sv/g/n (source URL/venue/category/note), median 130
chars, vs. the calibrated 165-market eval set's median 576 chars of real
rules. Scanning on that metadata instead of real rules produced a wrong
verdict live (2026-10-12, the OpenAI/Anthropic IPO market: an unrelated
article was surfaced as "UNCLEAR" evidence because there was no real
Yes/No definition to check it against).

Rain's own pool record carries the real text in `poolDescription`,
served publicly with no token (verified live 2026-10-13 against
prod2-api.rain.one/pools/pool/<id> -- the exact endpoint rain-admin's
own fetchRainPoolDetail() calls, services/market-wizard/rain.ts).

Hard rule enforced here, not just documented: enrich_market_with_rain_
description() returns None -- "do not run the finder" -- whenever Rain
can't be reached or has no usable description. Nothing here ever falls
back to the gist's own placeholder text; a caller that gets None must
skip the scan, not substitute anything.
"""
import logging
from dataclasses import replace
from typing import Callable, Optional

import requests

from resolution_finder.models import Market

logger = logging.getLogger(__name__)

RAIN_API_BASE = "https://prod2-api.rain.one"
RAIN_USER_AGENT = "resolution-finder-service (rain pool client)"
FETCH_TIMEOUT_SECONDS = 15

# A fetcher takes a Rain market/pool id and returns the pool's own "data"
# object (already unwrapped from the {"data": {...}} envelope) -- same
# fetch/parse split as gist_market_provider.py, so tests cover the
# parsing logic below with zero network.
PoolFetcher = Callable[[str], dict]


class RainPoolError(Exception):
    """The pool record could not be fetched or decoded. Always turned
    into "skip this scan" by enrich_market_with_rain_description --
    never into a guessed description."""


def _default_fetch(market_id: str) -> dict:
    url = f"{RAIN_API_BASE}/pools/pool/{market_id}"
    try:
        response = requests.get(
            url, timeout=FETCH_TIMEOUT_SECONDS,
            headers={"User-Agent": RAIN_USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RainPoolError(f"could not fetch Rain pool {market_id!r}: {exc}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise RainPoolError(f"Rain pool {market_id!r} response was not JSON: {exc}") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise RainPoolError(f"Rain pool {market_id!r} response had no 'data' object")
    return data


def _options_from_subpools(data: dict) -> list[str]:
    """Verified live 2026-10-13 on two real pools:
    - 2+ subPools = a real multi-outcome market, one sub-pool per named
      outcome (e.g. "Anthropic" / "OpenAI" on the OpenAI-vs-Anthropic IPO
      market -- each sub-pool its own internal Yes/No bet on THAT outcome
      happening first). Matches pull_test_batch.py's own groupItemTitle
      convention: the sub-pool's own question, verbatim, in source order.
    - Exactly 1 subPool = Rain's binary-market convention (the
      MicroStrategy-bankruptcy market has one sub-pool whose own
      question is the placeholder "Yes", not a real outcome name).
      Returning [] here is what routes it to verdict_engine's
      _decide_binary instead of wrongly treating "Yes" as a 1-item
      outcome list for _decide_multi_outcome.
    A malformed/partial list (some sub-pool missing its own question) is
    not trusted as a real split either -- real markets never have this,
    so treat it the same as "no split" rather than guess."""
    sub_pools = data.get("subPools")
    if not isinstance(sub_pools, list) or len(sub_pools) < 2:
        return []
    names = [
        sp.get("question") for sp in sub_pools
        if isinstance(sp, dict) and isinstance(sp.get("question"), str) and sp.get("question").strip()
    ]
    return names if len(names) == len(sub_pools) else []


def enrich_market_with_rain_description(
    market: Market, fetch: Optional[PoolFetcher] = None
) -> Optional[Market]:
    """The one place a service-path market's REAL resolution rules get
    attached before it reaches verdict_engine.decide(). `market` as
    passed in carries only what the gist supplies (id/title/close_date);
    this overlays Rain's own `poolDescription` as `description` and
    derives `options` from `subPools` -- both verbatim, never merged
    with the gist's own placeholder text.

    Returns None when Rain can't be reached or has no usable description
    -- the caller's contract (see service/app.py::_real_scan) is that
    None means "do not run the finder": no scan, no finding, no verdict,
    for this run."""
    fetch_fn = fetch or _default_fetch
    try:
        data = fetch_fn(market.id)
    except RainPoolError as exc:
        logger.warning("Rain pool fetch failed for market %s — skipping scan: %s", market.id, exc)
        return None
    description = data.get("poolDescription")
    if not isinstance(description, str) or not description.strip():
        logger.info("Rain has no resolution rules yet for market %s — skipping scan", market.id)
        return None
    return replace(market, description=description, options=_options_from_subpools(data))
