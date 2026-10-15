"""Market-implied probability for a market, from the venue the market itself
names as its source (the gist's "Source URL"), never computed here.

Owner request (2026-10-14): a finding should carry a probability, because
news ABOUT an event is not the event -- "Peter Schiff warns Strategy is
going bankrupt" says nothing about whether Strategy will announce
bankruptcy. This codebase cannot produce a calibrated forecast from news
text with its local models (see storage.FINDING_SCORE_VERSION: the
verifier scored false statements 0.98). A traded market can: its price is
the probability other people are paying for. So the probability here is a
quote, attributed -- which venue, which question, when -- and None
whenever it cannot be read or matched to exactly one question. It is not
the finder's own estimate and is never presented as one.

Only Polymarket so far: 50 of the 57 gist markets name a polymarket.com
event as their source (2026-10-14), read through the public Gamma API
already listed in docs/HANDOFF.md section 10. Kalshi (4 markets) is not
read; those get None.
"""
import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
FETCH_TIMEOUT_SECONDS = 15
USER_AGENT = "resolution-finder-service (market odds)"

_SOURCE_URL_RE = re.compile(r"^Source URL:\s*(\S+)", re.MULTILINE)
_POLYMARKET_EVENT_RE = re.compile(r"^https?://(?:www\.)?polymarket\.com/event/([^/?#]+)")

EventFetcher = Callable[[str], Optional[dict]]


@dataclass(frozen=True)
class Odds:
    value: float          # price of "Yes", 0..1
    source: str           # "Polymarket"
    question: str         # the venue's own question, verbatim
    url: str              # the venue page
    as_of: Optional[str]  # the venue's own last-update time for that market

    def describe(self) -> str:
        when = f", updated {self.as_of[:16].replace('T', ' ')} UTC" if self.as_of else ""
        return f"{self.source} price for \"{self.question}\"{when}"


def source_url(gist_description: str) -> Optional[str]:
    """The "Source URL: ..." line GistMarketProvider writes from the gist."""
    match = _SOURCE_URL_RE.search(gist_description or "")
    return match.group(1) if match else None


def _default_fetch(slug: str) -> Optional[dict]:
    try:
        response = requests.get(GAMMA_EVENTS_URL, params={"slug": slug}, timeout=FETCH_TIMEOUT_SECONDS,
                                headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        response.raise_for_status()
        events = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Polymarket event %r could not be read: %s", slug, exc)
        return None
    return events[0] if isinstance(events, list) and events and isinstance(events[0], dict) else None


def _yes_price(market: dict) -> Optional[float]:
    try:
        outcomes = json.loads(market.get("outcomes") or "[]")
        prices = [float(p) for p in json.loads(market.get("outcomePrices") or "[]")]
    except (ValueError, TypeError):
        return None
    if len(outcomes) != len(prices) or "Yes" not in outcomes:
        return None
    return prices[outcomes.index("Yes")]


def _mentions(text: str, name: str) -> bool:
    return bool(name) and re.search(rf"(?<!\w){re.escape(name.lower())}(?!\w)", text.lower()) is not None


def _pick_market(event: dict, title: str, option: Optional[str]) -> Optional[dict]:
    """Exactly one venue question for this rain.trade question, or None.
    - A sub-pool: the venue market whose own label IS the sub-pool's name
      ("October 31" <-> groupItemTitle "October 31").
    - A whole market: the event's only market; or, in a multi-market event
      ("Which companies announce bankruptcy before 2027?"), the one market
      whose label is named in our title ("MicroStrategy").
    Anything ambiguous is None -- a wrong market's price is worse than none."""
    markets = [m for m in event.get("markets") or [] if isinstance(m, dict)]
    if option is not None:
        found = [m for m in markets if (m.get("groupItemTitle") or "").strip().lower() == option.strip().lower()]
        return found[0] if len(found) == 1 else None
    if len(markets) == 1:
        return markets[0]
    found = [m for m in markets if _mentions(title, (m.get("groupItemTitle") or "").strip())]
    return found[0] if len(found) == 1 else None


class OddsSource:
    """The venue a market names as its source, read once, asked per
    question. `unavailable` says why there is no price at all, in words a
    reviewer can act on; per-question misses return None."""

    def __init__(self, gist_description: str, fetch: Optional[EventFetcher] = None):
        self.url = source_url(gist_description)
        self.event: Optional[dict] = None
        self.unavailable: Optional[str] = None
        match = _POLYMARKET_EVENT_RE.match(self.url or "")
        if not self.url:
            self.unavailable = "the market names no source venue"
        elif not match:
            self.unavailable = "its source venue is not Polymarket, the only venue read so far"
        else:
            self.event = (fetch or _default_fetch)(match.group(1))
            if not self.event:
                self.unavailable = "the Polymarket event could not be read"

    def odds(self, title: str, option: Optional[str] = None) -> Optional[Odds]:
        if not self.event:
            return None
        market = _pick_market(self.event, title, option)
        price = _yes_price(market) if market else None
        if price is None:
            return None
        return Odds(value=price, source="Polymarket", question=market.get("question") or "",
                    url=self.url, as_of=market.get("updatedAt"))


def market_odds(gist_description: str, title: str, option: Optional[str] = None,
                fetch: Optional[EventFetcher] = None) -> Optional[Odds]:
    return OddsSource(gist_description, fetch).odds(title, option)
