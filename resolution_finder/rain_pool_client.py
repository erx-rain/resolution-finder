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

Hard rule enforced here, not just documented: plan_scan() returns no
targets -- "do not run the finder" -- whenever Rain can't be reached or
has no usable description. Nothing here ever falls back to the gist's
own placeholder text; a caller that gets a skip reason must skip the
scan, not substitute anything.
"""
import logging
import re
from dataclasses import dataclass, replace
from datetime import date
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
    into "skip this scan" by plan_scan -- never into a guessed
    description."""


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


@dataclass(frozen=True)
class SubPool:
    """One Rain sub-pool, as Rain itself reports it. Each is its own
    Yes/No question on rain.trade."""

    name: str                    # sub-pool `question`, verbatim ("December 31", "Anthropic")
    question: str                # `mergedQuestion`, verbatim ("Will ... by December 31?"), or ""
    end_date: Optional[date]     # the sub-pool's own `endDate`
    opened: Optional[date]       # the day Rain created it (`createdAt`)
    resolved: bool               # Rain has already settled it -- see _is_resolved


def _is_resolved(sub_pool: dict) -> bool:
    """Rain has settled this sub-pool: it is finalized, or a winning side
    has been recorded. Verified live 2026-10-13 on the U.S.-Iran "by
    <date>" market: its three elapsed sub-pools carry poolFinalized=true,
    a `winnerOption` (decidedBy "olympus-ai") and status "Closed"; the one
    still running has none of the three and status "Live".

    `status` alone is deliberately NOT the test: a sub-pool whose trading
    has closed but which nobody has settled yet is exactly the one a
    reviewer needs an answer for.

    The POOL's own `isFinalized` is not the test either. Real bug, shipped
    and found the same day (2026-10-13): plan_scan skipped any pool with
    isFinalized=true as "already resolved", and "Will Apple launch a
    foldable iPhone by the end of 2026?" -- Live, its one sub-pool
    unsettled, closing Jan 2027 -- came back with no finding at all. Of
    the 57 gist markets, 4 have isFinalized=true and 3 of those are Live
    with nothing settled; whatever the flag records, it is not "this
    market has been resolved". Only the sub-pools say that."""
    return bool(sub_pool.get("poolFinalized")) or bool(sub_pool.get("winnerOption"))


def _parse_end_date(raw) -> Optional[date]:
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _sub_pools(data: dict) -> list[SubPool]:
    """Every sub-pool Rain lists, in Rain's own order. A malformed list
    (an entry with no question of its own) is not trusted as a real split
    -- real markets never have this -- and yields []."""
    raw = data.get("subPools")
    if not isinstance(raw, list):
        return []
    sub_pools: list[SubPool] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return []
        name = entry.get("question")
        if not isinstance(name, str) or not name.strip():
            return []
        merged = entry.get("mergedQuestion")
        sub_pools.append(SubPool(
            name=name,
            question=merged if isinstance(merged, str) else "",
            end_date=_parse_end_date(entry.get("endDate")),
            opened=_parse_end_date(entry.get("createdAt")),
            resolved=_is_resolved(entry),
        ))
    return sub_pools


# A sub-pool named by a calendar date -- "August 18", "Sept 30", "December
# 31, 2026". The whole name must be a date: "August Wilson" is a person.
_DATE_NAME_RE = re.compile(
    r"^(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+\d{1,2}(?:st|nd|rd|th)?"
    r"(?:,?\s+\d{4})?$",
    re.IGNORECASE,
)


def _is_date_name(name: str) -> bool:
    return bool(_DATE_NAME_RE.match(name.strip()))


# ── Status of every market at once, for the market list ──────────────
#
# GET /markets must not show a market Rain has already resolved (owner
# decision 2026-10-13), and must say which sub-pools of the rest are
# settled. Reading each market's own pool record for that is too slow:
# measured 2026-10-13 on the 57 gist markets, 42 s one at a time and
# 16-18 s with 8-32 threads (Rain serialises a client's requests; one
# request then takes up to 15 s) -- past rain-admin's 20 s proxy timeout.
# Rain's public listing carries every pool with its sub-pools' own
# poolFinalized / winnerOption in ONE call: 400 pools, 1.4 MB, 0.4 s, all
# 57 present. It does NOT carry poolDescription, mergedQuestion or the
# sub-pools' dates, so a scan still reads the market's own record
# (plan_scan).
LISTING_PAGE_SIZE = 1000
LISTING_TIMEOUT_SECONDS = 30

ListingFetcher = Callable[[], list[dict]]


def _default_listing_fetch() -> list[dict]:
    """Every pool in Rain's public listing. `offset` is a PAGE number,
    1-based (verified live, and how rain-admin's own client calls it)."""
    pools: list[dict] = []
    page, pages = 1, 1
    while page <= pages:
        try:
            response = requests.get(
                f"{RAIN_API_BASE}/pools/public-pools",
                params={"limit": LISTING_PAGE_SIZE, "offset": page},
                timeout=LISTING_TIMEOUT_SECONDS,
                headers={"User-Agent": RAIN_USER_AGENT, "Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json().get("data")
        except (requests.RequestException, ValueError) as exc:
            raise RainPoolError(f"could not fetch Rain pool listing page {page}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("pools"), list):
            raise RainPoolError(f"Rain pool listing page {page} had no 'pools' list")
        pools.extend(p for p in data["pools"] if isinstance(p, dict))
        pages = data.get("pages") if isinstance(data.get("pages"), int) else 1
        page += 1
    return pools


@dataclass(frozen=True)
class SubPoolStatus:
    name: str          # sub-pool `question`, verbatim
    resolved: bool     # Rain has settled it -- _is_resolved


@dataclass(frozen=True)
class PoolStatus:
    """Where Rain says a market stands. `known` is False when the market
    was not in the listing (or the listing could not be read): nothing
    below is then known, and the market is NOT treated as resolved."""
    known: bool
    sub_pools: tuple[SubPoolStatus, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.known and bool(self.sub_pools) and all(sp.resolved for sp in self.sub_pools)


UNKNOWN_STATUS = PoolStatus(known=False)


def pool_statuses(market_ids: list[str], fetch: Optional[ListingFetcher] = None) -> dict[str, PoolStatus]:
    """One PoolStatus per id, from one read of Rain's listing. A read
    failure raises RainPoolError: the caller decides what to show, and
    must not show "resolved" for anything."""
    by_id = {p.get("_id"): p for p in (fetch or _default_listing_fetch)() if p.get("_id")}
    statuses: dict[str, PoolStatus] = {}
    for market_id in market_ids:
        pool = by_id.get(market_id)
        if pool is None:
            statuses[market_id] = UNKNOWN_STATUS
            continue
        raw = pool.get("subPools")
        sub_pools = tuple(
            SubPoolStatus(name=sp["question"], resolved=_is_resolved(sp))
            for sp in (raw if isinstance(raw, list) else [])
            if isinstance(sp, dict) and isinstance(sp.get("question"), str)
        )
        statuses[market_id] = PoolStatus(known=True, sub_pools=sub_pools)
    return statuses


SKIP_NO_RULES = "No finding: Rain has no resolution rules for this market yet, so it was not scanned."
SKIP_ALL_RESOLVED = "No finding: every sub-pool of this market is already resolved on Rain, so nothing was scanned."


@dataclass(frozen=True)
class ScanTarget:
    """One thing to hand to the pipeline. `option` is None when `market`
    is scanned as a whole (its own options, if any, are reported by the
    engine); it is a sub-pool's name when `market` is that ONE sub-pool
    scanned as its own Yes/No question, and every finding it produces is
    filed under that name."""

    market: Market
    option: Optional[str] = None
    opened: Optional[date] = None   # with `option`: the day Rain created that sub-pool


@dataclass(frozen=True)
class ScanPlan:
    targets: tuple[ScanTarget, ...] = ()
    skip_reason: Optional[str] = None


def plan_scan(market: Market, fetch: Optional[PoolFetcher] = None) -> ScanPlan:
    """What to scan for a service-path market, from Rain's own record.
    `market` as passed in carries only what the gist supplies
    (id/title/close_date); Rain's `poolDescription` becomes the
    description, verbatim, never merged with the gist's placeholder text.

    Nothing is scanned -- "do not run the finder" -- when Rain can't be
    reached or has no description, and when Rain has already resolved
    every sub-pool. The caller reports either as a FAILED run with the
    reason (owner decision 2026-10-13: a run that ends without a finding
    is an error to look at, never a quiet "nothing").

    Sub-pools Rain has already resolved are never scanned (owner decision
    2026-10-13): Rain's own settlement is the answer for those, and
    re-deriving it can only add noise.

    How the open sub-pools are scanned depends on what they are:
    - ONE sub-pool in total is Rain's binary-market shape (its own name
      is the placeholder "Yes", not an outcome): a plain Yes/No market.
    - Sub-pools named by DATES ("August 18", "December 31") are thresholds
      of one event, not rivals: each open one is scanned as its own Yes/No
      market -- Rain's own `mergedQuestion` as the title ("Will ... by
      December 31?") and the sub-pool's own end date as its deadline.
      Found live 2026-10-13: scanned together as a "who wins" market, all
      four U.S.-Iran sub-pools were reported "can't be determined until
      Jan 1, 2027" (the pool's close), including three whose own dates had
      passed, and the engine was asking whether "August 18 has won".
    - Anything else is a contest between named outcomes: one market whose
      options are the open sub-pools' names, in Rain's order."""
    fetch_fn = fetch or _default_fetch
    try:
        data = fetch_fn(market.id)
    except RainPoolError as exc:
        logger.warning("Rain pool fetch failed for market %s — skipping scan: %s", market.id, exc)
        return ScanPlan(skip_reason=SKIP_NO_RULES)
    description = data.get("poolDescription")
    if not isinstance(description, str) or not description.strip():
        logger.info("Rain has no resolution rules yet for market %s — skipping scan", market.id)
        return ScanPlan(skip_reason=SKIP_NO_RULES)

    sub_pools = _sub_pools(data)
    open_sub_pools = [sp for sp in sub_pools if not sp.resolved]
    if sub_pools and not open_sub_pools:
        return ScanPlan(skip_reason=SKIP_ALL_RESOLVED)

    whole = replace(market, description=description, options=[])
    if len(sub_pools) < 2:
        return ScanPlan(targets=(ScanTarget(whole),))

    if all(_is_date_name(sp.name) for sp in sub_pools) and all(sp.question.strip() for sp in open_sub_pools):
        return ScanPlan(targets=tuple(
            ScanTarget(
                replace(whole, title=sp.question, close_date=sp.end_date or market.close_date),
                option=sp.name, opened=sp.opened,
            )
            for sp in open_sub_pools
        ))

    return ScanPlan(targets=(ScanTarget(replace(whole, options=[sp.name for sp in open_sub_pools])),))
