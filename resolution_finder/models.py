from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class Market:
    id: str
    title: str
    description: str
    options: list[str]
    # Optional: a real market can genuinely have no close_date in its
    # source data (confirmed live, 2026-09-07 -- some pulled March
    # Madness markets). Every real usage already guards this
    # (`if market.close_date and ...`) -- this annotation was just stale.
    close_date: Optional[date]


@dataclass
class ArticleRef:
    url: str
    title: str
    source_type: str  # "primary", "credible_backup", "credible_backup_secondary", "official_social", "peer_market", or "general"
    published_date: Optional[date] = None
    summary: Optional[str] = None  # search-result snippet text; used in place of a
                                    # fetched page for "official_social" refs, since
                                    # those URLs are never fetched directly
    source_domain: Optional[str] = None  # real publisher host (e.g. "www.reuters.com").
                                          # `url` is often a news.google.com redirect
                                          # wrapper, so it can NOT be used to identify
                                          # the publisher — see evidence_retriever.py.


@dataclass
class RankedArticle:
    article: ArticleRef
    text: str
    similarity: float


@dataclass
class Verdict:
    outcome: str  # "YES", "NO", "UNCLEAR", or "NO_EVIDENCE"
    confidence: float
    evidence_snippet: Optional[str]
    source_url: Optional[str]
    source_type: Optional[str]
    option: Optional[str] = None  # for a multi-outcome market's per-option verdict,
                                   # the option this verdict is about (e.g. "Aurora
                                   # Gaming"); None for a binary market's verdict, or
                                   # a multi-outcome market's whole-market UNCLEAR/
                                   # NO_EVIDENCE status when no option is determined.
