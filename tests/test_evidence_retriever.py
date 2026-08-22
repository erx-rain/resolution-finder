# tests/test_evidence_retriever.py
import logging
from unittest.mock import patch, MagicMock
from datetime import date
from urllib.parse import quote
from resolution_finder.models import Market
from resolution_finder.evidence_retriever import (
    search_google_news_rss,
    search_bing_news_rss,
    retrieve_evidence,
    entry_source_domain,
)

CLARITY_MARKET = Market(
    id="clarity-act-2026",
    title="Will the CLARITY act be signed into law in 2026?",
    description="Primary resolution source: Congress.gov legislation tracker.",
    options=[],
    close_date=date(2026, 12, 31),
)

NOBEL_MARKET = Market(
    id="nobel-peace-2026",
    title="Who will win the 2026 Nobel Peace Prize?",
    description="Officially announced by the Norwegian Nobel Committee.",
    options=["Pope Leo XIV"],
    close_date=date(2027, 3, 31),
)

# A real Google News RSS `entry.link`, verified by live dry run: an opaque
# news.google.com redirect wrapper that contains NO publisher information.
WRAPPER_URL = (
    "https://news.google.com/rss/articles/"
    "CBMi0wFBVV95cUxPVzRseExFRXFiTWZFUVVETFVZeWFUWVFhYzIxM3pDbFBScjF4SUcyeVV3SEtH"
)


def make_fake_feed(entries, bozo=False, status=200):
    feed = MagicMock()
    feed.entries = entries
    feed.bozo = bozo
    feed.status = status
    return feed


def make_entry(title, source_href, source_title=None, link=None):
    """Build an entry shaped like real Google News RSS output.

    `link` is a news.google.com redirect wrapper (never the publisher URL) and
    the publisher is identified only by the RSS `<source url="...">` tag, which
    feedparser exposes as `entry.source.href` / `entry.source.title`.
    """
    entry = MagicMock()
    entry.link = link or (WRAPPER_URL + str(abs(hash(title)))[:12])
    entry.title = title
    source = MagicMock()
    source.href = source_href
    source.title = source_title or source_href
    entry.source = source
    return entry


def make_bing_entry(title, real_url):
    entry = MagicMock()
    entry.title = title
    entry.link = (
        "http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&tid=abc123"
        f"&url={quote(real_url, safe='')}&c=123&mkt=en-ww"
    )
    return entry


def make_entry_without_source(title, link=None):
    entry = MagicMock()
    entry.link = link or (WRAPPER_URL + "nosource")
    entry.title = title
    entry.source = None
    return entry


# --- entry_source_domain -----------------------------------------------------

def test_entry_source_domain_reads_publisher_from_source_href():
    entry = make_entry("Reuters headline", "https://www.reuters.com", "Reuters")
    assert entry_source_domain(entry) == "www.reuters.com"


def test_entry_source_domain_ignores_the_google_wrapper_link():
    entry = make_entry("Reuters headline", "https://www.reuters.com", "Reuters")
    assert "news.google.com" in entry.link
    assert "google" not in entry_source_domain(entry)


def test_entry_source_domain_falls_back_to_domain_shaped_source_title():
    entry = make_entry("Banker headline", "", "thebanker.com")
    assert entry_source_domain(entry) == "thebanker.com"


def test_entry_source_domain_returns_none_when_unidentifiable():
    assert entry_source_domain(make_entry_without_source("Mystery headline")) is None
    # A human-readable source title is not a domain and must not be treated as one.
    assert entry_source_domain(make_entry("h", "", "The New York Times")) is None


# --- search_google_news_rss --------------------------------------------------

@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_google_news_rss_tags_whitelisted_source(mock_parse):
    mock_parse.return_value = make_fake_feed([
        make_entry("Reuters headline", "https://www.reuters.com", "Reuters"),
        make_entry("Random headline", "https://randomblog.com", "Random Blog"),
    ])
    results = search_google_news_rss("CLARITY act")
    assert results[0].source_type == "credible_backup"
    assert results[0].source_domain == "www.reuters.com"
    assert results[1].source_type == "general"


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_google_news_rss_whitelists_by_publisher_not_by_wrapper_url(mock_parse):
    """Regression: the whitelist used to substring-test entry.link, which is a
    news.google.com wrapper, so Tier 2 evidence never got through at all."""
    mock_parse.return_value = make_fake_feed([
        make_entry("BBC headline", "https://www.bbc.com", "BBC"),
    ])
    results = search_google_news_rss("Vinicius Junior transfer")
    assert "reuters" not in results[0].url and "bbc" not in results[0].url
    assert results[0].source_type == "credible_backup"


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_google_news_rss_does_not_whitelist_lookalike_domains(mock_parse):
    mock_parse.return_value = make_fake_feed([
        make_entry("Fake 1", "https://notreuters.com", "notreuters.com"),
        make_entry("Fake 2", "https://reuters.com.example.net", "spoof"),
        make_entry("Real", "https://feeds.reuters.com", "Reuters"),
    ])
    results = search_google_news_rss("q")
    assert results[0].source_type == "general"
    assert results[1].source_type == "general"
    assert results[2].source_type == "credible_backup"  # subdomain is legitimate


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_google_news_rss_handles_entry_without_source(mock_parse):
    mock_parse.return_value = make_fake_feed([make_entry_without_source("Mystery")])
    results = search_google_news_rss("q")
    assert results[0].source_type == "general"
    assert results[0].source_domain is None


@patch("resolution_finder.evidence_retriever.MAX_RESULTS_PER_QUERY", 3)
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_google_news_rss_caps_results_per_query(mock_parse):
    """A single query can return far more entries than are worth pursuing --
    every one of them costs a real extraction attempt later in the pipeline,
    and Tier 1 (Google) links are guaranteed to fail that extraction (verified
    live: 336/336). Capping here bounds that wasted work regardless of how
    many entries the feed itself returns."""
    mock_parse.return_value = make_fake_feed([
        make_entry(f"Headline {i}", "https://www.reuters.com", "Reuters") for i in range(10)
    ])
    results = search_google_news_rss("q")
    assert len(results) == 3


# --- search_bing_news_rss ----------------------------------------------------

@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_bing_news_rss_extracts_real_url_and_tags_whitelisted(mock_parse):
    mock_parse.return_value = make_fake_feed([
        make_bing_entry("Reuters headline", "https://www.reuters.com/article/x"),
        make_bing_entry("Random headline", "https://randomblog.com/article/y"),
    ])
    results = search_bing_news_rss("CLARITY act")
    assert results[0].url == "https://www.reuters.com/article/x"
    assert results[0].source_type == "credible_backup"
    assert results[1].url == "https://randomblog.com/article/y"
    assert results[1].source_type == "general"


@patch("resolution_finder.evidence_retriever.MAX_RESULTS_PER_QUERY", 3)
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_bing_news_rss_caps_results_per_query(mock_parse):
    mock_parse.return_value = make_fake_feed([
        make_bing_entry(f"Headline {i}", f"https://www.reuters.com/article/{i}") for i in range(10)
    ])
    results = search_bing_news_rss("q")
    assert len(results) == 3


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_bing_news_rss_skips_entries_without_resolvable_url(mock_parse):
    unresolvable = MagicMock()
    unresolvable.title = "No url param"
    unresolvable.link = "http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&c=1&mkt=en-ww"
    mock_parse.return_value = make_fake_feed([unresolvable])
    results = search_bing_news_rss("CLARITY act")
    assert results == []


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_bing_news_rss_warns_on_bozo_feed(mock_parse, caplog):
    """feedparser never raises on HTTP errors/rate-limiting, so a blocked or
    rate-limited fetch looks identical to "found nothing" unless bozo/status
    are checked. Verifies the warning fires; return value is still whatever
    entries were parsed (possibly none), never a raised exception.
    """
    mock_parse.return_value = make_fake_feed([], bozo=True, status=429)
    with caplog.at_level(logging.WARNING, logger="resolution_finder.evidence_retriever"):
        results = search_bing_news_rss("CLARITY act")
    assert results == []
    assert "Bing News" in caplog.text
    assert "CLARITY act" in caplog.text


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_bing_news_rss_does_not_warn_on_healthy_feed(mock_parse, caplog):
    mock_parse.return_value = make_fake_feed([
        make_bing_entry("Reuters headline", "https://www.reuters.com/article/x"),
    ])
    with caplog.at_level(logging.WARNING, logger="resolution_finder.evidence_retriever"):
        search_bing_news_rss("CLARITY act")
    assert "fetch may have failed" not in caplog.text


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_google_news_rss_warns_on_bozo_feed(mock_parse, caplog):
    mock_parse.return_value = make_fake_feed([], bozo=True, status=503)
    with caplog.at_level(logging.WARNING, logger="resolution_finder.evidence_retriever"):
        results = search_google_news_rss("CLARITY act")
    assert results == []
    assert "Google News" in caplog.text
    assert "CLARITY act" in caplog.text


# --- Tier 1 (domain-scoped) --------------------------------------------------
#
# Tier 1 used to run its own site:-scoped Google News RSS search, but every
# result from that was an unfetchable news.google.com wrapper link (Task 14
# report concern #3, confirmed live 2026-08-18 on a real market: 7/7
# extraction failures). Tier 1's domain-match promotion now reuses the same
# unscoped Bing results the Tier 2 loop already fetches per query (Bing
# doesn't honor a site: operator anyway), so these tests mock Bing-shaped
# feeds via make_bing_entry, same as the Tier 2 tests below.

@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_uses_tier1_domain_scoped_search(mock_parse, mock_sleep):
    mock_parse.return_value = make_fake_feed([
        make_bing_entry(
            "Actions - H.R.3633 - Digital Asset Market Clarity Act",
            "https://www.congress.gov/bill/3633/actions",
        ),
    ])
    evidence = retrieve_evidence(CLARITY_MARKET, ["CLARITY act"])

    primary = [e for e in evidence if e.source_type == "primary"]
    assert len(primary) == 1
    # Assert on the identity of the tagged item, not merely that *something*
    # was tagged primary — that weak assertion is what let the original bug ship.
    assert primary[0].source_domain == "www.congress.gov"
    assert primary[0].title == "Actions - H.R.3633 - Digital Asset Market Clarity Act"
    # Real, directly fetchable URL -- not a news.google.com wrapper -- is the
    # whole point of this fix.
    assert primary[0].url == "https://www.congress.gov/bill/3633/actions"


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_tier1_does_not_tag_offdomain_results_as_primary(mock_parse, mock_sleep):
    """A domain check is a request, not a guarantee — an unrelated publisher
    leaking into the results must never be labelled the highest-confidence tier."""
    mock_parse.return_value = make_fake_feed([
        make_bing_entry("Crypto blog take on the CLARITY act", "https://randomblog.com/x"),
    ])
    evidence = retrieve_evidence(CLARITY_MARKET, ["CLARITY act"])
    assert not any(e.source_type == "primary" for e in evidence)
    assert evidence == []


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_tier1_leak_from_credible_outlet_is_downgraded_not_dropped(mock_parse, mock_sleep):
    mock_parse.return_value = make_fake_feed([
        make_bing_entry("Reuters on the CLARITY act", "https://www.reuters.com/x"),
    ])
    evidence = retrieve_evidence(CLARITY_MARKET, ["CLARITY act"])
    assert [e.source_type for e in evidence] == ["credible_backup"]


# --- Tier 2 ------------------------------------------------------------------

@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_surfaces_tier2_evidence(mock_parse, mock_sleep):
    """Regression for the shipped bug: no Tier 2 evidence ever surfaced."""
    vinicius = Market(
        id="vinicius-transfer-2026",
        title="Which team will Vinicius Junior join next?",
        description="A consensus of credible media reporting may also be used.",
        options=["Real Madrid", "Arsenal"],
        close_date=date(2026, 9, 1),
    )
    # This market has no named source and no known social account, so the only
    # search `retrieve_evidence` runs is the Tier 2 fallback — which now goes
    # through Bing News RSS, so the mock feed must be Bing-shaped.
    mock_parse.return_value = make_fake_feed([
        make_bing_entry("Will Gunners sign Vinicius Jr? - BBC",
                        "https://www.bbc.com/sport/football/articles/abc123"),
        make_bing_entry("Transfer rumour roundup",
                        "https://www.football365.com/news/rumour-roundup"),
    ])
    evidence = retrieve_evidence(vinicius, ["Vinicius Junior transfer"])

    backups = [e for e in evidence if e.source_type == "credible_backup"]
    assert len(backups) == 1
    assert backups[0].source_domain == "www.bbc.com"
    assert backups[0].url == "https://www.bbc.com/sport/football/articles/abc123"


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_bing_news_rss_tags_secondary_tier_outlet(mock_parse):
    mock_parse.return_value = make_fake_feed([
        make_bing_entry("Forbes headline", "https://www.forbes.com/sites/x/article"),
    ])
    results = search_bing_news_rss("CLARITY act")
    assert results[0].source_type == "credible_backup_secondary"


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_bing_news_rss_still_tags_primary_tier_outlet(mock_parse):
    mock_parse.return_value = make_fake_feed([
        make_bing_entry("Reuters headline", "https://www.reuters.com/article/x"),
    ])
    results = search_bing_news_rss("CLARITY act")
    assert results[0].source_type == "credible_backup"


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_includes_secondary_tier_evidence(mock_parse, mock_sleep):
    vinicius_market = Market(
        id="vinicius-transfer-2026",
        title="Which team will Vinicius Junior join next?",
        description="No named source in this description.",
        options=["Real Madrid", "Arsenal"],
        close_date=date(2026, 9, 1),
    )
    mock_parse.return_value = make_fake_feed([
        make_bing_entry("Goal.com headline", "https://www.goal.com/en/news/x"),
    ])
    evidence = retrieve_evidence(vinicius_market, ["Vinicius Junior transfer"])
    secondary = [e for e in evidence if e.source_type == "credible_backup_secondary"]
    assert len(secondary) == 1
    assert secondary[0].url == "https://www.goal.com/en/news/x"


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_deduplicates_urls(mock_parse, mock_sleep):
    mock_parse.return_value = make_fake_feed([
        make_entry("Reuters headline", "https://www.reuters.com", "Reuters",
                   link=WRAPPER_URL + "dedupe"),
    ])
    evidence = retrieve_evidence(CLARITY_MARKET, ["CLARITY act", "CLARITY act status"])
    urls = [e.url for e in evidence]
    assert len(urls) == len(set(urls))


# --- Social ------------------------------------------------------------------

@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_includes_social_search_for_known_organization(mock_parse, mock_sleep):
    # Tier 1's separate feedparser.parse call is gone (see the Tier 1 block
    # above) -- its domain-match promotion now reuses the Tier 2 Bing call
    # at the end of this list instead of issuing its own.
    mock_parse.side_effect = [
        make_fake_feed([make_entry(
            "The Nobel Prize (@NobelPrize) / Posts - x.com",
            "https://x.com", "x.com",
        )]),  # X social search
        make_fake_feed([]),  # Instagram social search
        make_fake_feed([]),  # Tier 2 general search
    ]

    evidence = retrieve_evidence(NOBEL_MARKET, ["Nobel Peace Prize winner"])

    social_hits = [e for e in evidence if e.source_type == "official_social"]
    assert len(social_hits) == 1
    assert social_hits[0].source_domain == "x.com"
    assert social_hits[0].summary == "The Nobel Prize (@NobelPrize) / Posts - x.com"


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_social_search_accepts_twitter_com_as_the_same_platform(mock_parse, mock_sleep):
    mock_parse.side_effect = [
        make_fake_feed([make_entry("@NobelPrize post", "https://twitter.com", "twitter.com")]),
        make_fake_feed([]),
        make_fake_feed([]),
    ]
    evidence = retrieve_evidence(NOBEL_MARKET, ["Nobel Peace Prize winner"])
    assert [e.source_type for e in evidence] == ["official_social"]


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_social_search_discards_ordinary_news_that_leaks_into_scoped_search(mock_parse, mock_sleep):
    """Regression: the live dry run tagged a plain Nobel news article as
    official_social, telling the reviewer to "verify the official account" for
    something that was never a social post."""
    mock_parse.side_effect = [
        make_fake_feed([make_entry(
            "Nobel Peace Prize 2026 speculation mounts",
            "https://www.newsweek.com", "Newsweek",
        )]),  # X search leaks ordinary news
        make_fake_feed([]),  # Instagram
        make_fake_feed([]),  # Tier 2
    ]

    evidence = retrieve_evidence(NOBEL_MARKET, ["Nobel Peace Prize winner"])
    assert not any(e.source_type == "official_social" for e in evidence)
    assert evidence == []


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_includes_instagram_search_for_known_organization(mock_parse, mock_sleep):
    mock_parse.side_effect = [
        make_fake_feed([]),  # X social search
        make_fake_feed([make_entry(
            "nobelprize_org: The 2026 laureate is...",
            "https://www.instagram.com", "Instagram",
        )]),  # Instagram social search
        make_fake_feed([]),  # Tier 2 general search
    ]

    evidence = retrieve_evidence(NOBEL_MARKET, ["Nobel Peace Prize winner"])

    social_hits = [e for e in evidence if e.source_type == "official_social"]
    assert len(social_hits) == 1
    assert social_hits[0].source_domain == "www.instagram.com"


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_instagram_search_discards_non_instagram_results(mock_parse, mock_sleep):
    mock_parse.side_effect = [
        make_fake_feed([]),
        make_fake_feed([make_entry("Nobel news", "https://www.cnn.com", "CNN")]),
        make_fake_feed([]),
    ]
    evidence = retrieve_evidence(NOBEL_MARKET, ["Nobel Peace Prize winner"])
    assert not any(e.source_type == "official_social" for e in evidence)


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_handles_empty_queries_for_known_organization(mock_parse, mock_sleep):
    mock_parse.return_value = make_fake_feed([])

    evidence = retrieve_evidence(NOBEL_MARKET, [])

    assert evidence == []
    social_hits = [e for e in evidence if e.source_type == "official_social"]
    assert len(social_hits) == 0
