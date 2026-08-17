# tests/test_run_scan.py
from unittest.mock import patch
import run_scan


@patch("run_scan.PEER_MARKET_ENABLED", False)
@patch("run_scan.run_pipeline")
@patch("run_scan.JsonFileMarketProvider")
def test_main_disables_peer_checker_when_peer_market_disabled(mock_provider, mock_run_pipeline):
    run_scan.main()

    _, kwargs = mock_run_pipeline.call_args
    assert kwargs["peer_checker"] is run_scan._no_peer_check
    assert run_scan._no_peer_check(None) is None


@patch("run_scan.PEER_MARKET_ENABLED", True)
@patch("run_scan.run_pipeline")
@patch("run_scan.JsonFileMarketProvider")
def test_main_leaves_peer_checker_default_when_peer_market_enabled(mock_provider, mock_run_pipeline):
    run_scan.main()

    _, kwargs = mock_run_pipeline.call_args
    assert "peer_checker" not in kwargs
