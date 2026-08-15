# tests/test_pipeline.py
import logging
import os
import tempfile
from unittest.mock import patch
from datetime import date, timedelta
from resolution_finder.models import Market, ArticleRef, Verdict
from resolution_finder.pipeline import run_pipeline
from resolution_finder.storage import get_latest_findings


def make_market(market_id="clarity-act-2026"):
    return Market(
        id=market_id,
        title="Will the CLARITY act be signed into law in 2026?",
        description="If these conditions are not met by the deadline, the market resolves to \"No\".",
        options=[],
        close_date=date.today() + timedelta(days=365),
    )


class FakeMarketProvider:
    def __init__(self, markets=None):
        self._markets = markets if markets is not None else [make_market()]

    def get_unresolved_markets(self):
        return self._markets


def temp_db_path():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    return db_path


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_writes_a_finding_per_market(mock_retrieve, mock_extract, mock_rank, mock_sleep):
    mock_retrieve.return_value = [
        ArticleRef(url="https://congress.gov/bill/3633", title="t", source_type="primary")
    ]
    mock_extract.return_value = "The bill was signed into law today."
    mock_rank.return_value = []

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)

    run_pipeline(FakeMarketProvider(), db_path)

    findings = get_latest_findings(db_path)
    assert len(findings) == 1
    assert findings[0]["market_id"] == "clarity-act-2026"
    os.remove(db_path)


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_never_fetches_official_social_urls(mock_retrieve, mock_extract, mock_rank, mock_sleep):
    mock_retrieve.return_value = [
        ArticleRef(
            url="https://x.com/NobelPrize/status/123",
            title="NobelPrize post",
            source_type="official_social",
            summary="NobelPrize: The 2026 laureate is Pope Leo XIV.",
        )
    ]
    mock_rank.return_value = []

    db_path = temp_db_path()

    run_pipeline(FakeMarketProvider(), db_path)

    mock_extract.assert_not_called()
    articles_passed = mock_rank.call_args[0][1]
    assert articles_passed[0][1] == "NobelPrize: The 2026 laureate is Pope Leo XIV."
    os.remove(db_path)


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_uses_injected_verdict_engine(mock_retrieve, mock_extract, mock_rank, mock_sleep):
    """The Verdict Engine must be swappable (e.g. for an AI-based one) without
    editing the pipeline, the same way the Market Provider already is."""
    mock_retrieve.return_value = []
    mock_rank.return_value = []

    calls = []

    def fake_engine(market, ranked):
        calls.append((market.id, ranked))
        return Verdict(
            outcome="YES",
            confidence=0.99,
            evidence_snippet="from the injected engine",
            source_url="https://example.com/x",
            source_type="primary",
        )

    db_path = temp_db_path()
    run_pipeline(FakeMarketProvider(), db_path, verdict_engine=fake_engine)

    assert [market_id for market_id, _ in calls] == ["clarity-act-2026"]
    findings = get_latest_findings(db_path)
    assert findings[0]["outcome"] == "YES"
    assert findings[0]["evidence_snippet"] == "from the injected engine"
    os.remove(db_path)


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_defaults_to_the_rule_based_engine(mock_retrieve, mock_extract, mock_rank, mock_sleep):
    mock_retrieve.return_value = []
    mock_rank.return_value = []

    db_path = temp_db_path()
    run_pipeline(FakeMarketProvider(), db_path)

    findings = get_latest_findings(db_path)
    assert findings[0]["outcome"] == "NO_EVIDENCE"
    os.remove(db_path)


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_logs_and_skips_a_failing_market(mock_retrieve, mock_extract, mock_rank, mock_sleep, caplog):
    """One market blowing up must not abort the whole run."""
    def retrieve(market, queries):
        if market.id == "boom":
            raise RuntimeError("upstream exploded")
        return []

    mock_retrieve.side_effect = retrieve
    mock_rank.return_value = []

    provider = FakeMarketProvider([
        make_market("boom"),
        make_market("clarity-act-2026"),
    ])

    db_path = temp_db_path()
    with caplog.at_level(logging.ERROR, logger="resolution_finder.pipeline"):
        run_pipeline(provider, db_path)

    findings = get_latest_findings(db_path)
    assert [f["market_id"] for f in findings] == ["clarity-act-2026"]
    assert "boom" in caplog.text
    assert "upstream exploded" in caplog.text
    os.remove(db_path)
