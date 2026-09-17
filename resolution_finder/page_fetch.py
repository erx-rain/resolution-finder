# resolution_finder/page_fetch.py
"""Plain page fetching for structured resolvers (the Fed pages today).

Deliberately no caching: production always reads the live page. Caching
is allowed only inside run_eval.py's harness (standing project rule) --
see eval_cache.CachedPageFetcher.
"""
from typing import Callable

import requests

from resolution_finder.evidence_retriever import FEED_USER_AGENT

FETCH_TIMEOUT_SECONDS = 20

Fetcher = Callable[[str], str]


class FetchError(Exception):
    """A page could not be fetched. Resolvers turn this into UNCLEAR."""


def http_fetch(url: str) -> str:
    try:
        response = requests.get(
            url, timeout=FETCH_TIMEOUT_SECONDS, headers={"User-Agent": FEED_USER_AGENT}
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"could not fetch {url}: {exc}") from exc
    # federalreserve.gov serves "Content-Type: text/html" with no charset
    # (checked live 2026-09-17). requests then falls back to ISO-8859-1,
    # which mangles the U+2011 non-breaking hyphens real FOMC statements
    # use inside rates like "3-3/4" -- and the decision regex stops matching.
    if "charset" not in response.headers.get("Content-Type", "").lower():
        response.encoding = "utf-8"
    return response.text
