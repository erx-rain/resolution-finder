# tests/test_pipeline.py
import logging
import os
import tempfile
from unittest.mock import patch
from datetime import date, timedelta
from resolution_finder.models import Market, ArticleRef, Verdict, RankedArticle
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


def make_market(market_id="clarity-act-2026", close_date=None):
    return Market(
        id=market_id,
        title="Will the CLARITY act be signed into law in 2026?",
        description="If these conditions are not met by the deadline, the market resolves to \"No\".",
        options=[],
        close_date=close_date if close_date is not None else date.today() + timedelta(days=365),
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
@patch("resolution_finder.pipeline.best_below_threshold")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_writes_a_finding_per_market(
    mock_retrieve, mock_extract, mock_rank, mock_best, mock_sleep
):
    mock_retrieve.return_value = [
        ArticleRef(url="https://congress.gov/bill/3633", title="t", source_type="primary")
    ]
    mock_extract.return_value = "The bill was signed into law today."
    mock_rank.return_value = []
    mock_best.return_value = None

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
@patch("resolution_finder.pipeline.best_below_threshold")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_never_fetches_official_social_urls(
    mock_retrieve, mock_extract, mock_rank, mock_best, mock_sleep
):
    mock_retrieve.return_value = [
        ArticleRef(
            url="https://x.com/NobelPrize/status/123",
            title="NobelPrize post",
            source_type="official_social",
            summary="NobelPrize: The 2026 laureate is Pope Leo XIV.",
        )
    ]
    mock_rank.return_value = []
    mock_best.return_value = None

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


def test_run_pipeline_defaults_peer_checker_to_the_flag_respecting_wrapper():
    """The default `peer_checker` must be `_default_peer_checker`, which
    itself respects PEER_MARKET_ENABLED -- see the two tests below. This is
    what makes PEER_MARKET_ENABLED an enforced property of run_pipeline
    itself rather than a convention only one caller happens to honour.
    """
    import resolution_finder.pipeline as pipeline_module
    assert pipeline_module.run_pipeline.__defaults__[1] is pipeline_module._default_peer_checker


@patch("resolution_finder.pipeline.PEER_MARKET_ENABLED", False)
@patch("resolution_finder.pipeline.find_polymarket_match")
def test_default_peer_checker_skips_polymarket_when_disabled(mock_find_match):
    from resolution_finder.pipeline import _default_peer_checker
    result = _default_peer_checker(make_market())
    assert result is None
    mock_find_match.assert_not_called()


@patch("resolution_finder.pipeline.PEER_MARKET_ENABLED", True)
@patch("resolution_finder.pipeline.find_polymarket_match")
def test_default_peer_checker_delegates_to_polymarket_when_enabled(mock_find_match):
    from resolution_finder.pipeline import _default_peer_checker
    mock_find_match.return_value = "sentinel-verdict"
    result = _default_peer_checker(make_market())
    assert result == "sentinel-verdict"
    mock_find_match.assert_called_once()


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_saves_every_verdict_when_engine_returns_a_list(
    mock_retrieve, mock_extract, mock_rank, mock_sleep
):
    """A multi-outcome market's verdict_engine can return one Verdict per
    option instead of a single Verdict -- the pipeline must persist all of
    them, not just the first."""
    mock_retrieve.return_value = []
    mock_rank.return_value = []

    def fake_engine(market, ranked):
        return [
            Verdict(outcome="YES", confidence=0.9, evidence_snippet="won",
                    source_url="https://example.com/a", source_type="primary", option="Team A"),
            Verdict(outcome="NO", confidence=0.9, evidence_snippet="won",
                    source_url="https://example.com/a", source_type="primary", option="Team B"),
        ]

    db_path = temp_db_path()
    run_pipeline(FakeMarketProvider(), db_path, verdict_engine=fake_engine, peer_checker=NO_PEER_MATCH)

    findings = get_latest_findings(db_path)
    assert len(findings) == 2
    assert {f["option"]: f["outcome"] for f in findings} == {"Team A": "YES", "Team B": "NO"}
    os.remove(db_path)


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.best_below_threshold")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_promotes_best_below_threshold_article_when_no_evidence_ranked(
    mock_retrieve, mock_extract, mock_rank, mock_best, mock_sleep
):
    """A real article was found and its text extracted, but it scored below
    SIMILARITY_THRESHOLD and rank_by_relevance dropped it -- decide() then
    gets an empty list and would normally produce NO_EVIDENCE. The pipeline
    must surface that closest article as UNCLEAR instead of losing it
    entirely (2026-08-18 finding: world-cup-highest-scoring-match had real,
    extracted evidence silently discarded this way)."""
    ref = ArticleRef(url="https://sports.example.com/x", title="t", source_type="credible_backup_secondary")
    mock_retrieve.return_value = [ref]
    mock_extract.return_value = "A relevant but below-threshold sentence."
    mock_rank.return_value = []
    mock_best.return_value = RankedArticle(
        article=ref, text="A relevant but below-threshold sentence.", similarity=0.22,
    )

    db_path = temp_db_path()
    run_pipeline(FakeMarketProvider(), db_path, peer_checker=NO_PEER_MATCH)

    findings = get_latest_findings(db_path)
    assert findings[0]["outcome"] == "UNCLEAR"
    assert findings[0]["evidence_snippet"] == "A relevant but below-threshold sentence."
    assert findings[0]["source_url"] == "https://sports.example.com/x"
    os.remove(db_path)


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_attaches_availability_to_every_finding(
    mock_retrieve, mock_extract, mock_rank, mock_sleep
):
    """check_availability is computed once per market and attached to
    every finding it produces -- not gated behind evidence being found,
    since a market with NO_EVIDENCE past its close_date is exactly the
    case a reviewer most needs the availability flag for."""
    mock_retrieve.return_value = []
    mock_rank.return_value = []

    # Past close_date -> resolution_available must be True.
    market = make_market(close_date=date.today() - timedelta(days=1))
    db_path = temp_db_path()
    run_pipeline(FakeMarketProvider([market]), db_path, peer_checker=NO_PEER_MATCH)

    findings = get_latest_findings(db_path)
    assert findings[0]["outcome"] == "NO_EVIDENCE"
    assert findings[0]["resolution_available"] is True
    assert findings[0]["availability_reason"] == "close_date_passed"
    os.remove(db_path)


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_availability_false_before_close_date(
    mock_retrieve, mock_extract, mock_rank, mock_sleep
):
    mock_retrieve.return_value = []
    mock_rank.return_value = []

    market = make_market(close_date=date.today() + timedelta(days=365))
    db_path = temp_db_path()
    run_pipeline(FakeMarketProvider([market]), db_path, peer_checker=NO_PEER_MATCH)

    findings = get_latest_findings(db_path)
    assert findings[0]["resolution_available"] is False
    os.remove(db_path)


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_still_retrieves_evidence_when_not_yet_available(
    mock_retrieve, mock_extract, mock_rank, mock_sleep
):
    """Availability must never gate retrieval -- an early triggering
    event (Path B) is exactly what retrieval exists to catch BEFORE the
    close date, so a not-yet-available market still gets scanned."""
    mock_retrieve.return_value = []
    mock_rank.return_value = []

    market = make_market(close_date=date.today() + timedelta(days=365))
    db_path = temp_db_path()
    run_pipeline(FakeMarketProvider([market]), db_path, peer_checker=NO_PEER_MATCH)

    mock_retrieve.assert_called_once()
    os.remove(db_path)


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_attaches_availability_to_a_peer_market_finding(
    mock_retrieve, mock_extract, mock_rank, mock_sleep
):
    """The peer-market fast path returns before the news pipeline runs
    at all -- availability must still be attached there too, not only on
    the news path."""
    def fake_peer_checker(market):
        return Verdict(
            outcome="YES", confidence=0.85, evidence_snippet="Resolved on a peer market",
            source_url="https://polymarket.com/event/x", source_type="peer_market",
        )

    market = make_market(close_date=date.today() - timedelta(days=1))
    db_path = temp_db_path()
    run_pipeline(FakeMarketProvider([market]), db_path, peer_checker=fake_peer_checker)

    findings = get_latest_findings(db_path)
    assert findings[0]["resolution_available"] is True
    mock_retrieve.assert_not_called()
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
        run_pipeline(provider, db_path, peer_checker=NO_PEER_MATCH)

    findings = get_latest_findings(db_path)
    assert [f["market_id"] for f in findings] == ["clarity-act-2026"]
    assert "boom" in caplog.text
    assert "upstream exploded" in caplog.text
    os.remove(db_path)


def test_run_pipeline_saves_structured_verdicts_and_skips_peer_check_and_news():
    # run_pipeline logs and skips a market whose scan raises, so "must not
    # run" is checked by recording calls, not by raising inside the stubs.
    statement_url = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20251210a.htm"
    peer_calls, engine_calls = [], []

    def fake_structured_resolver(market):
        return [
            Verdict(outcome="YES", option="25 bps decrease", confidence=1.0,
                    evidence_snippet="the Committee decided to lower the target range",
                    source_url=statement_url, source_type="primary"),
            Verdict(outcome="NO", option="No change", confidence=1.0,
                    evidence_snippet="the Committee decided to lower the target range",
                    source_url=statement_url, source_type="primary"),
        ]

    db_path = temp_db_path()
    with patch("resolution_finder.pipeline.retrieve_evidence") as mock_retrieve:
        run_pipeline(
            FakeMarketProvider(), db_path,
            verdict_engine=lambda market, ranked: engine_calls.append(market.id),
            peer_checker=lambda market: peer_calls.append(market.id),
            structured_resolver=fake_structured_resolver,
        )

    findings = {f["option"]: f for f in get_latest_findings(db_path)}
    assert findings["25 bps decrease"]["outcome"] == "YES"
    assert findings["25 bps decrease"]["source_type"] == "primary"
    assert findings["No change"]["outcome"] == "NO"
    assert peer_calls == []
    assert engine_calls == []
    mock_retrieve.assert_not_called()
    os.remove(db_path)


def test_run_pipeline_defaults_structured_resolver_to_resolve_structured():
    import resolution_finder.pipeline as pipeline_module
    assert pipeline_module.run_pipeline.__defaults__[2] is pipeline_module.resolve_structured
