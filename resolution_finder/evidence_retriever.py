# resolution_finder/evidence_retriever.py
import logging
import time
from datetime import date, timedelta
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
from resolution_finder.config import REQUEST_DELAY_SECONDS, MAX_RESULTS_PER_QUERY

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


def _warn_if_feed_fetch_failed(feed, source: str, query: str) -> None:
    """feedparser never raises on HTTP errors or rate-limiting -- a blocked
    or rate-limited fetch looks identical to "found nothing" unless the
    caller checks `feed.bozo` (malformed/failed parse) and `feed.status`
    (HTTP status, when available) itself. This only logs; it does not change
    what gets returned, since a genuine zero-results feed is a valid outcome
    too and callers must keep treating it that way.
    """
    status = getattr(feed, "status", None)
    if feed.bozo or (status is not None and status >= 400):
        logger.warning(
            "%s RSS fetch may have failed for query %r (status=%s, bozo=%s)",
            source, query, status, feed.bozo,
        )


def _entry_published_date(entry) -> Optional[date]:
    """The RSS entry's own publish date, or None -- best-effort, same
    degrade-safely convention as the rest of this module. Real, verified
    against a live Bing News RSS fetch (2026-08-26): feedparser exposes
    the RSS <pubDate> as `entry.published_parsed`, a time.struct_time,
    whenever the feed provides one at all."""
    parsed = getattr(entry, "published_parsed", None)
    if not parsed:
        return None
    try:
        return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
    except (TypeError, ValueError):
        return None


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
    _warn_if_feed_fetch_failed(feed, "Bing News", query)
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
            published_date=_entry_published_date(entry),
        ))
        if len(results) >= MAX_RESULTS_PER_QUERY:
            break
    return results


def search_google_news_rss(query: str, site: Optional[str] = None) -> list[ArticleRef]:
    full_query = f"{query} site:{site}" if site else query
    url = GOOGLE_NEWS_RSS.format(query=quote_plus(full_query))
    feed = feedparser.parse(url)
    _warn_if_feed_fetch_failed(feed, "Google News", full_query)
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
            published_date=_entry_published_date(entry),
        ))
        if len(results) >= MAX_RESULTS_PER_QUERY:
            break
    return results


# News search is recency-biased, which is a structural problem for a
# RESOLVED market: the real premier-league-winner-24-25 market (about the
# 2024-25 season) retrieved ONLY 2026-27 season articles, which is why it
# kept crowning the wrong champion no matter how many verification guards
# were added downstream. The fix belongs at retrieval time.
#
# Verified live 2026-08-26 against the real endpoints:
#   * Google News RSS DOES honor after:/before: operators. Same query,
#     scoped to the market's own window, returned "The Premier League
#     champions in profile: Liverpool's class of 2024-25" -- i.e. exactly
#     the confirming evidence, where the unscoped query returned nothing
#     but 2026-27 previews.
#   * Bing News RSS does NOT: it returns 0 entries when the operators are
#     present (treats them as literal search terms), and its only date
#     control is a RELATIVE qft=interval filter that cannot reach a
#     historical window. So this pass has to go through Google even though
#     Tier 2 uses Bing.
#   * Adding season text to the query ("...2024-25 season champions")
#     does NOT work -- still returns current-season articles. The date
#     operators are the entire win.
#
# Google's entry.link is an unfetchable JS-redirect wrapper (see
# UNRESOLVABLE_HOSTS in article_extractor.py), so the headline is the only
# text we will ever have for these refs -- carried in `summary`, exactly
# as the existing official_social path already does. Verified end-to-end
# through the real decide(): headlines are dense and factual enough to
# resolve correctly ("2026 NHL Stanley Cup Final: Carolina Hurricanes win
# first title in 20 years"), turning two long-standing WRONG markets
# correct.
ARCHIVE_LEAD_DAYS = 30
ARCHIVE_LAG_DAYS = 21

# Rate-limit budget: this pass adds one Google request per query on top of
# the existing per-query Bing request, and a big multi-outcome market
# already builds 20-33 queries. Capped because the generic, non-option
# queries carry most of the signal anyway -- verified in the prototype,
# where the first few queries alone produced the correct champion for both
# markets tested.
ARCHIVE_MAX_QUERIES = 8


def _date_scoped_query(query: str, close_date: date) -> str:
    """`query` restricted to a window around the market's own close date --
    where a resolved market's confirming coverage actually lives. The lag
    matters as much as the lead: results//reaction pieces are published in
    the days AFTER the event, not only before the deadline."""
    start = close_date - timedelta(days=ARCHIVE_LEAD_DAYS)
    end = close_date + timedelta(days=ARCHIVE_LAG_DAYS)
    return f"{query} after:{start.isoformat()} before:{end.isoformat()}"


def search_google_news_archive(query: str, close_date: date) -> list[ArticleRef]:
    """Date-scoped headline search for an already-closed market. Best-effort
    and purely additive -- callers keep every candidate the existing
    unscoped passes already found."""
    scoped = _date_scoped_query(query, close_date)
    url = GOOGLE_NEWS_RSS.format(query=quote_plus(scoped))
    feed = feedparser.parse(url)
    _warn_if_feed_fetch_failed(feed, "Google News archive", scoped)
    results = []
    for entry in feed.entries:
        domain = entry_source_domain(entry)
        results.append(ArticleRef(
            url=entry.link,
            title=entry.title,
            source_type=_outlet_tier(domain) or "general",
            source_domain=domain,
            published_date=_entry_published_date(entry),
            # The headline IS the evidence text for these -- the URL can
            # never be fetched. See this block's comment above.
            summary=entry.title,
        ))
        if len(results) >= MAX_RESULTS_PER_QUERY:
            break
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
    # A non-URL named_source (e.g. "congress.gov") used to get its own
    # site:-scoped Google News RSS search here. That was structurally dead
    # on arrival: EVERY Google News RSS entry.link is an opaque JS-redirect
    # wrapper (see UNRESOLVABLE_HOSTS in article_extractor.py), so every
    # "primary"-tagged ref this produced could never be fetched -- confirmed
    # live (Task 14 report: 158/158 and 153/153 extraction failures on
    # exactly this path; reconfirmed 2026-08-18 on
    # congress-passes-iran-war-powers-resolution: 7/7 failures, all
    # news.google.com wrapper links). Task 14 already established the fix
    # pattern for this exact failure mode for Tier 2 (Bing's wrapper links
    # resolve to real URLs, Google's don't) but explicitly deferred applying
    # it to Tier 1 as "worth its own task". Bing's endpoint doesn't honor a
    # site: operator in the query text (verified empirically, see
    # search_bing_news_rss's docstring), so there is no scoped-query
    # equivalent to request -- instead, the domain-match promotion below
    # reuses the SAME unscoped Bing results the Tier 2 loop already fetches
    # per query, promoting a named_source match to "primary" with its real,
    # fetchable URL, rather than issuing a second, separately-wasted Bing
    # call per query for a scoping operator Bing ignores anyway.

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
            # Only tag "primary" when the result really came from the named
            # source's domain -- a mislabel here is worse than dropping the
            # result, since "primary" is the highest-confidence tier. See
            # the comment above the removed site:-scoped Google block for
            # why this promotion moved here instead.
            if named_source and _domain_matches(ref.source_domain, named_source):
                evidence.append(ArticleRef(
                    url=ref.url,
                    title=ref.title,
                    source_type="primary",
                    source_domain=ref.source_domain,
                ))
            elif ref.source_type in CREDIBLE_TIERS:
                evidence.append(ref)
        time.sleep(REQUEST_DELAY_SECONDS)

    # Archive pass -- only for a market whose close date has already passed.
    # For a still-open market today's news IS the correct window, and
    # scoping to a past one would only hide current evidence. See the
    # comment above search_google_news_archive for why this goes through
    # Google rather than the Bing endpoint Tier 2 uses.
    #
    # Deliberately NOT filtered to CREDIBLE_TIERS, unlike the Bing pass
    # above: in the real prototype the decisive headlines came from
    # official competition sites (nhl.com, premierleague.com,
    # liverpoolfc.com, olympics.com) that aren't on the Tier 2 whitelist
    # but are about as authoritative as it gets for their own
    # competition's result. Filtering them out loses the correct answer
    # outright for premier-league-winner-24-25. Every ref still keeps its
    # real tier for ranking and reviewer display, and still has to clear
    # the full verification layer -- this widens what we can SEE, it does
    # not lower the bar for what we will ASSERT.
    if market.close_date and date.today() > market.close_date:
        for query in queries[:ARCHIVE_MAX_QUERIES]:
            evidence.extend(search_google_news_archive(query, market.close_date))
            time.sleep(REQUEST_DELAY_SECONDS)

    seen_urls = set()
    deduped = []
    for ref in evidence:
        if ref.url not in seen_urls:
            seen_urls.add(ref.url)
            deduped.append(ref)
    return deduped
