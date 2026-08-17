# tests/test_pipeline.py
import logging
import os
import tempfile
from unittest.mock import patch
from datetime import date, timedelta
from resolution_finder.models import Market, ArticleRef, Verdict
from resolution_finder.pipeline import run_pipeline
from resolution_finder.storage import get_latest_findings

# The pipeline consults Polymarket (via the injected `peer_checker`) before
# the news pipeline. Without stubbing it out, every pipeline test would make
# a real network call to the live Polymarket API -- slow, flaky, and able to
# short-circuit the pipeline with a real match and break assertions about the
# news path. `peer_checker` is injected the same way `verdict_engine` is
# (see pipeline.py), so tests that don't care about peer matching just pass
# this stub explicitly; the one test that does care about peer matching
# passes its own peer_checker instead.
NO_PEER_MATCH = lambda market: None


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

    run_pipeline(FakeMarketProvider(), db_path, peer_checker=NO_PEER_MATCH)

    findings = get_latest_findings(db_path)
    assert len(findings) == 1
    assert findings[0]["market_id"] == "clarity-act-2026"
    os.remove(db_path)


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_skips_rate_limit_sleep_for_known_unresolvable_urls(
    mock_retrieve, mock_extract, mock_rank, mock_sleep
):
    """A news.google.com wrapper URL never gets a real request (see
    article_extractor.is_known_unresolvable_url), so there is nothing to
    rate-limit -- sleeping after it anyway would just waste real time for no
    protective benefit."""
    mock_retrieve.return_value = [
        ArticleRef(
            url="https://news.google.com/rss/articles/CBMi0wFBVV95cUxP",
            title="t", source_type="primary",
        )
    ]
    mock_extract.return_value = None
    mock_rank.return_value = []

    db_path = temp_db_path()
    run_pipeline(FakeMarketProvider(), db_path, peer_checker=NO_PEER_MATCH)

    mock_sleep.assert_not_called()
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

    run_pipeline(FakeMarketProvider(), db_path, peer_checker=NO_PEER_MATCH)

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
    run_pipeline(FakeMarketProvider(), db_path, verdict_engine=fake_engine, peer_checker=NO_PEER_MATCH)

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
    run_pipeline(FakeMarketProvider(), db_path, peer_checker=NO_PEER_MATCH)

    findings = get_latest_findings(db_path)
    assert findings[0]["outcome"] == "NO_EVIDENCE"
    os.remove(db_path)


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_uses_peer_market_match_and_skips_rest_of_pipeline(
    mock_retrieve, mock_extract, mock_rank, mock_sleep
):
    """peer_checker is injected the same way verdict_engine is, so this test
    passes its own stub rather than patching the module-level
    find_polymarket_match -- proving the injection point actually works, not
    just that the module default (find_polymarket_match) happens to still be
    wired up.
    """
    peer_match_calls = []

    def fake_peer_checker(market):
        peer_match_calls.append(market.id)
        return Verdict(
            outcome="YES", confidence=0.85,
            evidence_snippet="Resolved on a peer market",
            source_url="https://polymarket.com/event/x",
            source_type="peer_market",
        )

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)

    run_pipeline(FakeMarketProvider(), db_path, peer_checker=fake_peer_checker)

    findings = get_latest_findings(db_path)
    assert findings[0]["outcome"] == "YES"
    assert findings[0]["source_type"] == "peer_market"
    assert peer_match_calls == ["clarity-act-2026"]
    mock_retrieve.assert_not_called()
    os.remove(db_path)


def test_run_pipeline_defaults_peer_checker_to_find_polymarket_match():
    """The default `peer_checker` parameter must actually be the real
    `find_polymarket_match`, so behavior is unchanged for callers that don't
    inject one -- this is what makes the injection additive, not a silent
    behavior change.
    """
    import resolution_finder.pipeline as pipeline_module
    from resolution_finder.peer_market import find_polymarket_match
    assert pipeline_module.run_pipeline.__defaults__[1] is find_polymarket_match


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
        run_pipeline(provider, db_path, peer_checker=NO_PEER_MATCH)

    findings = get_latest_findings(db_path)
    assert [f["market_id"] for f in findings] == ["clarity-act-2026"]
    assert "boom" in caplog.text
    assert "upstream exploded" in caplog.text
    os.remove(db_path)
