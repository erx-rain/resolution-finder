# tests/test_run_scan.py
from unittest.mock import patch
import run_scan


@patch("run_scan.run_pipeline")
@patch("run_scan.JsonFileMarketProvider")
def test_main_calls_run_pipeline_with_no_peer_checker_override(mock_provider, mock_run_pipeline):
    """run_scan.py no longer decides whether Polymarket is enabled itself --
    that's enforced inside run_pipeline's own default `peer_checker` (see
    pipeline._default_peer_checker, which reads PEER_MARKET_ENABLED). This
    just confirms run_scan.py doesn't pass any peer_checker override, so the
    pipeline's own default (and therefore the config flag) is what governs.
    """
    run_scan.main()

    _, kwargs = mock_run_pipeline.call_args
    assert "peer_checker" not in kwargs
