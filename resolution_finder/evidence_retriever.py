# resolution_finder/evidence_retriever.py
import logging
import time
from typing import Optional
from urllib.parse import quote_plus, urlparse, parse_qs
import feedparser
from resolution_finder.models import Market, ArticleRef
from resolution_finder.source_config import (
    resolve_named_source,
    resolve_social_handle,
    resolve_instagram_handle,
    TIER2_OUTLETS,
    TIER2_SECONDARY_OUTLETS,
)
from resolution_finder.config import REQUEST_DELAY_SECONDS

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

# Hosts that count as "the X/Twitter platform" and "the Instagram platform"
# when validating a site-scoped social search result.
SOCIAL_PLATFORM_HOSTS = {
    "x.com": ("x.com", "twitter.com"),
    "instagram.com": ("instagram.com",),
}

CREDIBLE_TIERS = ("credible_backup", "credible_backup_secondary")


def _source_field(source, name: str):
    """Read a field from a feedparser `entry.source`.

    feedparser returns a FeedParserDict, which supports both attribute and
    key access; be tolerant of either shape.
    """
    if source is None:
        return None
    value = getattr(source, name, None)
    if value is None and isinstance(source, dict):
        value = source.get(name)
    return value if isinstance(value, str) else None


def _host_of(netloc_source: str) -> Optional[str]:
    """Lowercase host from a URL or bare `host[:port]` string, no scheme required."""
    netloc = urlparse(netloc_source if "//" in netloc_source else "//" + netloc_source).netloc
    host = netloc.split("@")[-1].split(":")[0].strip().lower()
    return host or None


def entry_source_domain(entry) -> Optional[str]:
    """The REAL publisher host for a Google News RSS entry.

    `entry.link` is a `https://news.google.com/rss/articles/CBMi...` redirect
    wrapper and never contains the publisher's domain, so it must not be used
    to identify the source. Google News populates the RSS `<source url="...">`
    tag with the publisher origin (e.g. `https://www.reuters.com`), which
    feedparser exposes as `entry.source.href`, with `entry.source.title` as a
    human-readable name that is *sometimes* itself a bare domain
    ("thebanker.com", "NobelPrize.org") and sometimes not ("Reuters").
    """
    source = getattr(entry, "source", None)

    href = _source_field(source, "href")
    if href:
        host = _host_of(href)
        if host:
            return host

    title = _source_field(source, "title")
    if title:
        candidate = title.strip().lower()
        # Only usable as a domain when it actually looks like one.
        if candidate and " " not in candidate and "." in candidate:
            return candidate

    return None


def _domain_matches(host: Optional[str], domain: str) -> bool:
    """True if `host` is `domain` or a subdomain of it.

    Suffix-aware rather than a substring test, so "www.reuters.com" and
    "feeds.reuters.com" match "reuters.com" while "notreuters.com" and
    "reuters.com.example.net" do not.
    """
    if not host or not domain:
        return False
    host = host.strip().lower().strip(".")
    domain = domain.strip().lower().strip(".")
    if not host or not domain:
        return False
    return host == domain or host.endswith("." + domain)


def _outlet_tier(host: Optional[str]) -> Optional[str]:
    """Which credibility tier `host` belongs to, or None if neither."""
    if any(_domain_matches(host, outlet) for outlet in TIER2_OUTLETS):
        return "credible_backup"
    if any(_domain_matches(host, outlet) for outlet in TIER2_SECONDARY_OUTLETS):
        return "credible_backup_secondary"
    return None


BING_NEWS_RSS = "https://www.bing.com/news/search?q={query}&format=RSS"


def _bing_target_url(wrapper_url: str) -> Optional[str]:
    """The real article URL from a Bing News RSS wrapper link.

    Unlike Google News RSS's opaque redirect, Bing's wrapper
    (`bing.com/news/apiclick.aspx?...`) carries the real destination as a
    plain `url` query parameter — verified against live Bing output. No
    decoding or JavaScript execution needed, just URL parsing.
    """
    values = parse_qs(urlparse(wrapper_url).query).get("url")
    return values[0] if values else None


def search_bing_news_rss(query: str) -> list[ArticleRef]:
    """General (unscoped) credible-outlet search, replacing Google News RSS
    for Tier 2. Bing's RSS endpoint doesn't honor `site:` scoping (verified
    empirically — see plan Task 14), so this is only used for the unscoped
    Tier 2 fallback. Bing wrapper links resolve to real, directly fetchable
    URLs, unlike Google's JS-redirect wrapper, which is why Tier 2 moved
    here instead of trying to unwrap Google's link.
    """
    url = BING_NEWS_RSS.format(query=quote_plus(query))
    feed = feedparser.parse(url)
    results = []
    for entry in feed.entries:
        real_url = _bing_target_url(entry.link)
        if not real_url:
            logger.warning(
                "Bing News RSS entry has no resolvable url= param: %r",
                getattr(entry, "title", "?"),
            )
            continue
        domain = _host_of(real_url)
        source_type = _outlet_tier(domain) or "general"
        results.append(ArticleRef(
            url=real_url,
            title=entry.title,
            source_type=source_type,
            source_domain=domain,
        ))
    return results


def search_google_news_rss(query: str, site: Optional[str] = None) -> list[ArticleRef]:
    full_query = f"{query} site:{site}" if site else query
    url = GOOGLE_NEWS_RSS.format(query=quote_plus(full_query))
    feed = feedparser.parse(url)
    results = []
    for entry in feed.entries:
        domain = entry_source_domain(entry)
        if domain is None:
            logger.warning(
                "Google News RSS entry has no usable <source> domain; "
                "cannot verify publisher for %r", getattr(entry, "title", "?")
            )
        source_type = _outlet_tier(domain) or "general"
        results.append(ArticleRef(
            url=entry.link,
            title=entry.title,
            source_type=source_type,
            source_domain=domain,
        ))
    return results


def _social_refs(query: str, site: str) -> list[ArticleRef]:
    """Site-scoped social search, validated to actually be from that platform.

    Google News RSS does not strictly honour the `site:` operator, so results
    are re-checked against their real publisher domain. Anything that isn't
    genuinely from the platform is discarded rather than mislabelled — the
    dashboard asks a human to "verify this is the real official account",
    which is actively misleading when the item is ordinary news coverage.
    """
    allowed = SOCIAL_PLATFORM_HOSTS.get(site, (site,))
    refs = []
    for ref in search_google_news_rss(query, site=site):
        if any(_domain_matches(ref.source_domain, host) for host in allowed):
            refs.append(ArticleRef(
                url=ref.url,
                title=ref.title,
                source_type="official_social",
                summary=ref.title,
                source_domain=ref.source_domain,
            ))
        else:
            logger.warning(
                "Discarding site:%s result not actually from that platform "
                "(source domain %r): %r", site, ref.source_domain, ref.title
            )
    return refs


def retrieve_evidence(market: Market, queries: list[str]) -> list[ArticleRef]:
    evidence: list[ArticleRef] = []
    named_source = resolve_named_source(market.description)

    if named_source and named_source.startswith("http"):
        evidence.append(ArticleRef(url=named_source, title="Named resolution source", source_type="primary"))
    elif named_source:
        for query in queries:
            for ref in search_google_news_rss(query, site=named_source):
                # Only tag "primary" when the result really came from the named
                # source's domain. A domain-scoped search is a request, not a
                # guarantee, and "primary" is the highest-confidence tier — a
                # mislabel here is worse than dropping the result.
                if _domain_matches(ref.source_domain, named_source):
                    evidence.append(ArticleRef(
                        url=ref.url,
                        title=ref.title,
                        source_type="primary",
                        source_domain=ref.source_domain,
                    ))
                elif ref.source_type in CREDIBLE_TIERS:
                    evidence.append(ref)
                else:
                    logger.warning(
                        "Dropping site:%s result from unverified domain %r: %r",
                        named_source, ref.source_domain, ref.title
                    )
            time.sleep(REQUEST_DELAY_SECONDS)

    # Best-effort only: this searches Google News RSS scoped to x.com for a
    # known official handle, but never fetches the actual X page. News RSS is
    # scoped to news publishers, so this frequently finds nothing — any hit
    # is still surfaced to the reviewer as a link to verify by hand, never
    # treated as confirmed evidence on its own (see source_config.py).
    social_handle = resolve_social_handle(market.description)
    if social_handle and queries:
        evidence.extend(_social_refs(f"{queries[0]} {social_handle}", site="x.com"))
        time.sleep(REQUEST_DELAY_SECONDS)

    instagram_handle = resolve_instagram_handle(market.description)
    if instagram_handle and queries:
        evidence.extend(_social_refs(f"{queries[0]} {instagram_handle}", site="instagram.com"))
        time.sleep(REQUEST_DELAY_SECONDS)

    for query in queries:
        for ref in search_bing_news_rss(query):
            if ref.source_type in CREDIBLE_TIERS:
                evidence.append(ref)
        time.sleep(REQUEST_DELAY_SECONDS)

    seen_urls = set()
    deduped = []
    for ref in evidence:
        if ref.url not in seen_urls:
            seen_urls.add(ref.url)
            deduped.append(ref)
    return deduped
