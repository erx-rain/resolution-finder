from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class Market:
    id: str
    title: str
    description: str
    options: list[str]
    close_date: date


@dataclass
class ArticleRef:
    url: str
    title: str
    source_type: str  # "primary", "credible_backup", "official_social", or "general"
    published_date: Optional[date] = None
    summary: Optional[str] = None  # search-result snippet text; used in place of a
                                    # fetched page for "official_social" refs, since
                                    # those URLs are never fetched directly


@dataclass
class RankedArticle:
    article: ArticleRef
    text: str
    similarity: float


@dataclass
class Verdict:
    outcome: str  # "YES", "NO", an option name, "UNCLEAR", or "NO_EVIDENCE"
    confidence: float
    evidence_snippet: Optional[str]
    source_url: Optional[str]
    source_type: Optional[str]
