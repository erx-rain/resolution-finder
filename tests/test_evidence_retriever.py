# tests/test_evidence_retriever.py
from unittest.mock import patch, MagicMock
from datetime import date
from resolution_finder.models import Market
from resolution_finder.evidence_retriever import search_google_news_rss, retrieve_evidence

CLARITY_MARKET = Market(
    id="clarity-act-2026",
    title="Will the CLARITY act be signed into law in 2026?",
    description="Primary resolution source: Congress.gov legislation tracker.",
    options=[],
    close_date=date(2026, 12, 31),
)


def make_fake_feed(entries):
    feed = MagicMock()
    feed.entries = entries
    return feed


def make_entry(link, title):
    entry = MagicMock()
    entry.link = link
    entry.title = title
    return entry


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_google_news_rss_tags_whitelisted_source(mock_parse):
    mock_parse.return_value = make_fake_feed([
        make_entry("https://www.reuters.com/article/x", "Reuters headline"),
        make_entry("https://randomblog.com/article/y", "Random headline"),
    ])
    results = search_google_news_rss("CLARITY act")
    assert results[0].source_type == "credible_backup"
    assert results[1].source_type == "general"


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_uses_tier1_domain_scoped_search(mock_parse, mock_sleep):
    mock_parse.return_value = make_fake_feed([
        make_entry("https://www.congress.gov/bill/3633", "Bill status"),
    ])
    evidence = retrieve_evidence(CLARITY_MARKET, ["CLARITY act"])
    assert any(e.source_type == "primary" for e in evidence)


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_deduplicates_urls(mock_parse, mock_sleep):
    mock_parse.return_value = make_fake_feed([
        make_entry("https://www.reuters.com/article/x", "Reuters headline"),
    ])
    evidence = retrieve_evidence(CLARITY_MARKET, ["CLARITY act", "CLARITY act status"])
    urls = [e.url for e in evidence]
    assert len(urls) == len(set(urls))


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_includes_social_search_for_known_organization(mock_parse, mock_sleep):
    nobel_market = Market(
        id="nobel-peace-2026",
        title="Who will win the 2026 Nobel Peace Prize?",
        description="Officially announced by the Norwegian Nobel Committee.",
        options=["Pope Leo XIV"],
        close_date=date(2027, 3, 31),
    )
    mock_parse.side_effect = [
        make_fake_feed([]),  # Tier 1 domain-scoped search (nobelprize.org)
        make_fake_feed([make_entry(
            "https://x.com/NobelPrize/status/123",
            "NobelPrize: The 2026 laureate is...",
        )]),  # X social search
        make_fake_feed([]),  # Instagram social search
        make_fake_feed([]),  # Tier 2 general search
    ]

    evidence = retrieve_evidence(nobel_market, ["Nobel Peace Prize winner"])

    social_hits = [e for e in evidence if e.source_type == "official_social"]
    assert len(social_hits) == 1
    assert social_hits[0].url == "https://x.com/NobelPrize/status/123"
    assert social_hits[0].summary == "NobelPrize: The 2026 laureate is..."


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_includes_instagram_search_for_known_organization(mock_parse, mock_sleep):
    nobel_market = Market(
        id="nobel-peace-2026",
        title="Who will win the 2026 Nobel Peace Prize?",
        description="Officially announced by the Norwegian Nobel Committee.",
        options=["Pope Leo XIV"],
        close_date=date(2027, 3, 31),
    )
    mock_parse.side_effect = [
        make_fake_feed([]),  # Tier 1 domain-scoped search (nobelprize.org)
        make_fake_feed([]),  # X social search
        make_fake_feed([make_entry(
            "https://instagram.com/p/abc123",
            "nobelprize_org: The 2026 laureate is...",
        )]),  # Instagram social search
        make_fake_feed([]),  # Tier 2 general search
    ]

    evidence = retrieve_evidence(nobel_market, ["Nobel Peace Prize winner"])

    social_hits = [e for e in evidence if e.source_type == "official_social"]
    assert len(social_hits) == 1
    assert social_hits[0].url == "https://instagram.com/p/abc123"


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_handles_empty_queries_for_known_organization(mock_parse, mock_sleep):
    nobel_market = Market(
        id="nobel-peace-2026",
        title="Who will win the 2026 Nobel Peace Prize?",
        description="Officially announced by the Norwegian Nobel Committee.",
        options=["Pope Leo XIV"],
        close_date=date(2027, 3, 31),
    )
    mock_parse.return_value = make_fake_feed([])

    evidence = retrieve_evidence(nobel_market, [])

    assert evidence == []
    social_hits = [e for e in evidence if e.source_type == "official_social"]
    assert len(social_hits) == 0
