# resolution_finder/structured_resolvers.py
"""Resolvers that answer a market straight from its own named, structured
resolution source (an official page or data feed) instead of news search.

Each resolver takes (market, fetch) and returns None for "not my market
type", or a list of verdicts -- possibly a single UNCLEAR/NO_EVIDENCE --
for "mine". A claimed market never falls through to peer-check or news:
for these market types the news path is the known source of wrong answers
(Fed markets scored 0/14 there; see unsupported-market-types.md item 7).

Fed-decision markets are the only entry today. The price-market handoff
(docs/superpowers/plans/2026-09-17-price-markets-handoff.md) plugs in here.
"""
from typing import Callable, Optional

from resolution_finder.fed_decision import resolve_fed_decision
from resolution_finder.models import Market, Verdict
from resolution_finder.page_fetch import Fetcher, http_fetch

STRUCTURED_RESOLVERS: list[Callable[[Market, Fetcher], Optional[list[Verdict]]]] = [
    resolve_fed_decision,
]


def resolve_structured(market: Market, fetch: Fetcher = http_fetch) -> Optional[list[Verdict]]:
    for resolver in STRUCTURED_RESOLVERS:
        verdicts = resolver(market, fetch)
        if verdicts is not None:
            return verdicts
    return None
