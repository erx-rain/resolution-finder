from datetime import date
from resolution_finder.models import Market, ArticleRef, RankedArticle, Verdict


def test_market_creation():
    market = Market(
        id="clarity-act-2026",
        title="Will the CLARITY act be signed into law in 2026?",
        description="This market resolves to \"Yes\" if...",
        options=[],
        close_date=date(2026, 12, 31),
    )
    assert market.id == "clarity-act-2026"
    assert market.options == []


def test_article_ref_defaults():
    ref = ArticleRef(url="https://example.com/a", title="A", source_type="primary")
    assert ref.published_date is None
    assert ref.summary is None


def test_verdict_creation():
    v = Verdict(outcome="YES", confidence=0.9, evidence_snippet="signed into law",
                source_url="https://congress.gov/x", source_type="primary")
    assert v.outcome == "YES"
