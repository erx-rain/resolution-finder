"""GistMarketProvider — reads rain-native markets from the team GitHub gist.

Owner decision (2026-10-06): the authoritative FULL DESCRIPTION for
rain-native markets lives in the team gist (the same store rain-admin's
team-state uses), and the set of markets this service scans is the
team-maintained list in that gist. This provider is the rain.trade-side
implementation of the MarketProvider seam market_provider.py has always
kept open.

Hard rules respected here:
- Market text is passed VERBATIM. No rewriting, summarizing or merging.
- Nothing is invented: a record with no close date gets close_date=None
  (models.py already allows it); a record with no description gets ""
  and is still listed — eligibility can still fire on close_date, and a
  reviewer should see the market rather than have it silently vanish.

Accepted gist file shapes (all three are real shapes in the team gist,
verified live 2026-10-06):
1. A JSON LIST of market objects — the same shape as data/markets.json:
   {id, title, description, options, close_date}
2. A JSON OBJECT keyed by market id, each value a record carrying at
   least a title/question and description.
3. Either of the above wrapped in an envelope under a "markets" key —
   possible-markets.json is {version, markets: [...], meta} and
   rtap_team_registry.json is {schemaVersion, markets: {id: record}, ...}.
Recognized field aliases: title|question, close_date|closeDate|endDate,
options|outcomes (list of strings, or list of {optionName|question}
objects).

No caching: every get_unresolved_markets() call re-fetches the gist
(the no-production-caching rule; a gist read is cheap).
"""
import json
import os
from datetime import date
from typing import Callable, Optional

import requests

from resolution_finder.models import Market

GIST_API_BASE = "https://api.github.com/gists"
USER_AGENT = "resolution-finder-service (market provider)"


class GistProviderError(Exception):
    pass


def _parse_close_date(raw) -> Optional[date]:
    """Accept 'YYYY-MM-DD' or a full ISO timestamp ('YYYY-MM-DDTHH:MM:SSZ').
    Returns None for anything absent or unparseable — never guesses."""
    if not raw or not isinstance(raw, str):
        return None
    candidate = raw[:10]
    try:
        return date.fromisoformat(candidate)
    except ValueError:
        return None


def _parse_options(record: dict) -> list[str]:
    # "options" is data/markets.json's name; "outcomes" is the name the
    # team's possible-markets.json actually uses (verified live).
    raw = record.get("options")
    if not isinstance(raw, list):
        raw = record.get("outcomes")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item)
        elif isinstance(item, dict):
            name = item.get("optionName") or item.get("question") or item.get("name")
            if isinstance(name, str) and name.strip():
                out.append(name)
    return out


def _record_to_market(market_id: str, record: dict) -> Optional[Market]:
    title = record.get("title") or record.get("question") or ""
    if not isinstance(title, str) or not title.strip():
        # A market with no title at all can't be searched or reviewed
        # meaningfully; skip rather than fabricate one.
        return None
    description = record.get("description")
    if not isinstance(description, str):
        description = ""
    close_raw = (
        record.get("close_date")
        or record.get("closeDate")
        or record.get("endDate")
        or record.get("end_date")
    )
    return Market(
        id=str(record.get("id") or record.get("_id") or market_id),
        title=title,
        description=description,
        options=_parse_options(record),
        close_date=_parse_close_date(close_raw),
    )


def parse_gist_markets(payload) -> list[Market]:
    """Pure mapping from a decoded gist-file JSON payload to Markets.
    Separated from fetching so tests exercise it with no network."""
    # Envelope unwrap: both real team gist files nest the records under a
    # "markets" key ({version|schemaVersion, markets, meta, ...}). Only
    # unwrap when the value is itself a list/dict of records — a top-level
    # record that happens to HAVE a "markets" field would not match this.
    if (
        isinstance(payload, dict)
        and isinstance(payload.get("markets"), (list, dict))
    ):
        payload = payload["markets"]

    markets: list[Market] = []
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            market = _record_to_market(str(item.get("id", "")), item)
            if market:
                markets.append(market)
    elif isinstance(payload, dict):
        for key, record in payload.items():
            if not isinstance(record, dict):
                continue
            market = _record_to_market(str(key), record)
            if market:
                markets.append(market)
    else:
        raise GistProviderError(
            "Gist markets file must be a JSON list or an object keyed by market id"
        )
    return markets


def _default_fetch(gist_id: str, file_name: str, token: Optional[str]) -> str:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(f"{GIST_API_BASE}/{gist_id}", headers=headers, timeout=30)
    if resp.status_code != 200:
        raise GistProviderError(f"Gist fetch failed: HTTP {resp.status_code}")
    files = resp.json().get("files", {})
    entry = files.get(file_name)
    if entry is None:
        raise GistProviderError(
            f"File {file_name!r} not found in gist {gist_id} "
            f"(has: {sorted(files)})"
        )
    if entry.get("truncated"):
        raw_url = entry.get("raw_url")
        raw_headers = {"User-Agent": USER_AGENT}
        if token:
            # A secret gist's raw_url needs the same auth as the API call.
            raw_headers["Authorization"] = f"Bearer {token}"
        raw_resp = requests.get(raw_url, headers=raw_headers, timeout=30)
        if raw_resp.status_code != 200:
            raise GistProviderError(f"Gist raw fetch failed: HTTP {raw_resp.status_code}")
        return raw_resp.text
    return entry.get("content", "")


class GistMarketProvider:
    """MarketProvider over a GitHub gist file. Config via constructor or env:
    RF_GIST_ID, RF_GIST_FILE, RF_GITHUB_TOKEN (token optional for a public
    gist, required for a secret one)."""

    def __init__(
        self,
        gist_id: Optional[str] = None,
        file_name: Optional[str] = None,
        token: Optional[str] = None,
        fetch: Optional[Callable[[str, str, Optional[str]], str]] = None,
    ):
        self.gist_id = gist_id or os.environ.get("RF_GIST_ID", "")
        self.file_name = file_name or os.environ.get("RF_GIST_FILE", "rf_markets.json")
        self.token = token if token is not None else os.environ.get("RF_GITHUB_TOKEN")
        self._fetch = fetch or _default_fetch
        if not self.gist_id:
            raise GistProviderError("RF_GIST_ID is not configured")

    def get_unresolved_markets(self) -> list[Market]:
        raw = self._fetch(self.gist_id, self.file_name, self.token)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GistProviderError(f"Gist file is not valid JSON: {exc}") from exc
        return parse_gist_markets(payload)

    def get_market(self, market_id: str) -> Optional[Market]:
        for market in self.get_unresolved_markets():
            if market.id == market_id:
                return market
        return None
