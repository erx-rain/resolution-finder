# tests/test_pipeline.py
import os
import tempfile
from unittest.mock import patch
from datetime import date, timedelta
from resolution_finder.models import Market, ArticleRef
from resolution_finder.pipeline import run_pipeline
from resolution_finder.storage import get_latest_findings


class FakeMarketProvider:
    def get_unresolved_markets(self):
        return [
            Market(
                id="clarity-act-2026",
                title="Will the CLARITY act be signed into law in 2026?",
                description="If these conditions are not met by the deadline, the market resolves to \"No\".",
                options=[],
                close_date=date.today() + timedelta(days=365),
            )
        ]


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

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)

    run_pipeline(FakeMarketProvider(), db_path)

    mock_extract.assert_not_called()
    articles_passed = mock_rank.call_args[0][1]
    assert articles_passed[0][1] == "NobelPrize: The 2026 laureate is Pope Leo XIV."
    os.remove(db_path)
