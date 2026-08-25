# pull_test_batch.py
"""Pull real, resolved markets from Polymarket's public API into
data/markets.json, verbatim -- no field is authored, paraphrased, merged,
or invented here. See docs/superpowers/plans -- test/market data purity is
a hard project rule (every field must trace back to the source API's own
response).

Binary markets: one event, one market, options=[] (existing convention).
Multi-outcome markets: one event grouping several per-option sub-markets
(e.g. "Nobel Peace Prize" -> Navalnaya / Zelenskyy / ... each its own
Yes/No sub-market) -- the EVENT's own title/description is used as-is,
never a synthesized merge of the sub-markets' own descriptions (that
merge was a real, previously-flagged mistake). Each option string is the
sub-market's own `groupItemTitle`, verbatim, in the source's own order.

resolved_to is not a field the API hands back directly -- it's derived
by picking whichever outcome's own outcomePrices the source itself
settled near 1.0 (>=0.9, same convention already used in
resolution_finder/peer_market.py's _resolved_outcome), then taking that
outcome's own string. This is a numeric selection over source-provided
data, not synthesized text.
"""
import json
import sys
from urllib.parse import quote_plus

import requests

POLYMARKET_SEARCH = "https://gamma-api.polymarket.com/public-search?q={query}&limit_per_type=20"
MARKETS_PATH = "data/markets.json"
RESOLVED_PRICE_THRESHOLD = 0.9


def search_events(query: str) -> list[dict]:
    url = POLYMARKET_SEARCH.format(query=quote_plus(query))
    response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return []
    return payload.get("events", [])


def resolved_outcome_for_market(market: dict) -> "str | None":
    try:
        outcomes = json.loads(market.get("outcomes", "[]"))
        raw_prices = json.loads(market.get("outcomePrices", "[]"))
    except (ValueError, TypeError):
        return None
    if not outcomes or len(outcomes) != len(raw_prices):
        return None
    try:
        prices = [float(p) for p in raw_prices]
    except (ValueError, TypeError):
        return None
    best_idx = max(range(len(prices)), key=lambda i: prices[i])
    if prices[best_idx] < RESOLVED_PRICE_THRESHOLD:
        return None
    return outcomes[best_idx]


def market_is_resolved(market: dict) -> bool:
    return bool(market.get("closed")) and market.get("umaResolutionStatus") == "resolved"


def _single_market_as_binary(m: dict) -> "dict | None":
    """A single Polymarket "market" object isn't always a Yes/No question --
    an outright-winner bet (e.g. "World Series Winner") is also encoded as
    ONE market object, just with its own outcomes list holding the real
    named contenders (e.g. ["Blue Jays", "Dodgers"]) instead of ["Yes",
    "No"]. Verified live: 'world-series-winner' has outcomes=["Blue Jays",
    "Dodgers"], not Yes/No. Treating that as a binary market (options=[])
    would leave resolved_to holding a team name our schema can't use --
    so route it through the multi-outcome shape instead, using the
    source's own outcomes list verbatim as options."""
    try:
        outcomes = json.loads(m.get("outcomes", "[]"))
    except (ValueError, TypeError):
        outcomes = []
    is_yes_no = {o.strip().lower() for o in outcomes} == {"yes", "no"}

    resolved = resolved_outcome_for_market(m)
    if resolved is None:
        return None

    if is_yes_no:
        return {
            "id": m.get("slug"),
            "title": m.get("question"),
            "description": m.get("description", ""),
            "options": [],
            "close_date": m.get("endDateIso"),
            "resolved_to": resolved,
        }
    return {
        "id": m.get("slug"),
        "title": m.get("question"),
        "description": m.get("description", ""),
        "options": outcomes,
        "close_date": m.get("endDateIso"),
        "resolved_to": resolved,
    }


def _is_bundled_binary_group(markets: list[dict]) -> bool:
    """True if this event is a grab-bag of independently-resolvable Yes/No
    questions grouped under one event for browsing (e.g. a matchday's
    worth of separate games), not a true multi-outcome market. Verified
    live: real distinguishing signal is groupItemTitle == the sub-market's
    OWN full question ("Will Burnley beat Aston Villa?") -- a genuine
    multi-outcome market's groupItemTitle is instead a short, distinct
    entity name ("Arsenal") separate from its own longer question
    ("Will Arsenal qualify for...?")."""
    return any(m.get("groupItemTitle") == m.get("question") for m in markets)


def convert_event(event: dict) -> list[dict]:
    """Returns a list of Market-schema dicts (0, 1, or many) -- never
    guesses: any sub-market not closed+resolved, or with no outcome
    settled >=0.9, is left out rather than force-included."""
    markets = event.get("markets", [])
    if not markets or not all(market_is_resolved(m) for m in markets):
        return []

    if len(markets) == 1:
        converted = _single_market_as_binary(markets[0])
        return [converted] if converted else []

    if _is_bundled_binary_group(markets):
        # Not a real multi-outcome market -- each sub-market is its own
        # independent binary question, just grouped for browsing.
        results = []
        for m in markets:
            converted = _single_market_as_binary(m)
            if converted:
                results.append(converted)
        return results

    # Genuine multi-outcome: each sub-market is one named option's own
    # independent Yes/No pick on whether THAT option is the answer.
    options = []
    resolved_option = None
    for m in markets:
        option_name = m.get("groupItemTitle")
        if not option_name:
            return []
        options.append(option_name)
        if resolved_outcome_for_market(m) == "Yes":
            resolved_option = option_name
    if resolved_option is None:
        return []
    return [{
        "id": event.get("slug"),
        "title": event.get("title"),
        "description": event.get("description", ""),
        "options": options,
        "close_date": event.get("endDateIso") or (event.get("endDate", "")[:10] or None),
        "resolved_to": resolved_option,
    }]


def main():
    queries = sys.argv[1:]
    if not queries:
        print("Usage: python pull_test_batch.py <query1> <query2> ...")
        sys.exit(1)

    with open(MARKETS_PATH, encoding="utf-8") as f:
        existing = json.load(f)
    existing_ids = {m["id"] for m in existing}

    new_markets = []
    seen_ids = set()
    for query in queries:
        print(f"searching: {query!r}")
        events = search_events(query)
        for event in events:
            for converted in convert_event(event):
                if converted["id"] in existing_ids or converted["id"] in seen_ids:
                    continue
                seen_ids.add(converted["id"])
                new_markets.append(converted)
                print(f"  + {converted['id']}  (resolved_to={converted['resolved_to']!r}, options={len(converted['options'])})")

    print(f"\n{len(new_markets)} new resolved market(s) found across {len(queries)} quer{'y' if len(queries)==1 else 'ies'}.")
    out_path = "scratchpad/pulled_batch_preview.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(new_markets, f, indent=2, ensure_ascii=False)
    print(f"Written to {out_path} for review -- not merged into {MARKETS_PATH} automatically.")


if __name__ == "__main__":
    main()
