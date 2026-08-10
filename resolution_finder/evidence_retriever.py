# resolution_finder/evidence_retriever.py
import time
from typing import Optional
from urllib.parse import quote_plus
import feedparser
from resolution_finder.models import Market, ArticleRef
from resolution_finder.source_config import (
    resolve_named_source,
    resolve_social_handle,
    resolve_instagram_handle,
    TIER2_OUTLETS,
)
from resolution_finder.config import REQUEST_DELAY_SECONDS

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def _is_whitelisted(url: str) -> bool:
    return any(outlet in url for outlet in TIER2_OUTLETS)


def search_google_news_rss(query: str, site: Optional[str] = None) -> list[ArticleRef]:
    full_query = f"{query} site:{site}" if site else query
    url = GOOGLE_NEWS_RSS.format(query=quote_plus(full_query))
    feed = feedparser.parse(url)
    results = []
    for entry in feed.entries:
        source_type = "credible_backup" if _is_whitelisted(entry.link) else "general"
        results.append(ArticleRef(url=entry.link, title=entry.title, source_type=source_type))
    return results


def retrieve_evidence(market: Market, queries: list[str]) -> list[ArticleRef]:
    evidence: list[ArticleRef] = []
    named_source = resolve_named_source(market.description)

    if named_source and named_source.startswith("http"):
        evidence.append(ArticleRef(url=named_source, title="Named resolution source", source_type="primary"))
    elif named_source:
        for query in queries:
            for ref in search_google_news_rss(query, site=named_source):
                evidence.append(ArticleRef(url=ref.url, title=ref.title, source_type="primary"))
            time.sleep(REQUEST_DELAY_SECONDS)

    # Best-effort only: this searches Google News RSS scoped to x.com for a
    # known official handle, but never fetches the actual X page. News RSS is
    # scoped to news publishers, so this frequently finds nothing — any hit
    # is still surfaced to the reviewer as a link to verify by hand, never
    # treated as confirmed evidence on its own (see source_config.py).
    social_handle = resolve_social_handle(market.description)
    if social_handle and queries:
        for ref in search_google_news_rss(f"{queries[0]} {social_handle}", site="x.com"):
            evidence.append(ArticleRef(
                url=ref.url,
                title=ref.title,
                source_type="official_social",
                summary=ref.title,
            ))
        time.sleep(REQUEST_DELAY_SECONDS)

    instagram_handle = resolve_instagram_handle(market.description)
    if instagram_handle and queries:
        for ref in search_google_news_rss(f"{queries[0]} {instagram_handle}", site="instagram.com"):
            evidence.append(ArticleRef(
                url=ref.url,
                title=ref.title,
                source_type="official_social",
                summary=ref.title,
            ))
        time.sleep(REQUEST_DELAY_SECONDS)

    for query in queries:
        for ref in search_google_news_rss(query):
            if ref.source_type == "credible_backup":
                evidence.append(ref)
        time.sleep(REQUEST_DELAY_SECONDS)

    seen_urls = set()
    deduped = []
    for ref in evidence:
        if ref.url not in seen_urls:
            seen_urls.add(ref.url)
            deduped.append(ref)
    return deduped
