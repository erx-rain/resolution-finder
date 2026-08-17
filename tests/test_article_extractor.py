from unittest.mock import patch, MagicMock
import pytest
import curl_cffi.requests as curl_requests
from resolution_finder.article_extractor import (
    extract_article_text,
    is_blocked_url,
    is_known_unresolvable_url,
    IMPERSONATE,
)


def make_response(html="<html>...</html>", final_url="https://example.com/article"):
    response = MagicMock()
    response.text = html
    response.url = final_url
    response.raise_for_status = MagicMock()
    return response


@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.curl_requests.get")
def test_extract_article_text_returns_clean_text(mock_get, mock_extract):
    mock_get.return_value = make_response()
    mock_extract.return_value = "The bill was signed into law today."

    result = extract_article_text("https://example.com/article")
    assert result == "The bill was signed into law today."


@patch("resolution_finder.article_extractor.time.sleep")
@patch("resolution_finder.article_extractor.curl_requests.get")
def test_extract_article_text_returns_none_on_network_error(mock_get, mock_sleep):
    mock_get.side_effect = curl_requests.exceptions.ConnectionError("failed")
    result = extract_article_text("https://example.com/article")
    assert result is None


# --- transient-error retry, bounded ------------------------------------------

@patch("resolution_finder.article_extractor.time.sleep")
@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.curl_requests.get")
def test_extract_article_text_retries_transient_timeout_and_succeeds(mock_get, mock_extract, mock_sleep):
    """A DNS/connect/read timeout is frequently a one-off blip (verified
    live: an identical request succeeded on immediate retry). The second
    attempt succeeding must be used."""
    mock_get.side_effect = [
        curl_requests.exceptions.Timeout("blip"),
        make_response(),
    ]
    mock_extract.return_value = "recovered text"

    result = extract_article_text("https://example.com/article")

    assert result == "recovered text"
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once()


@patch("resolution_finder.article_extractor.time.sleep")
@patch("resolution_finder.article_extractor.curl_requests.get")
def test_extract_article_text_gives_up_after_max_attempts_on_persistent_timeout(mock_get, mock_sleep):
    """A truly dead host (not a blip) must not retry forever -- give up
    after MAX_FETCH_ATTEMPTS and return None."""
    from resolution_finder.article_extractor import MAX_FETCH_ATTEMPTS
    mock_get.side_effect = curl_requests.exceptions.Timeout("still down")

    result = extract_article_text("https://example.com/article")

    assert result is None
    assert mock_get.call_count == MAX_FETCH_ATTEMPTS


@patch("resolution_finder.article_extractor.time.sleep")
@patch("resolution_finder.article_extractor.curl_requests.get")
def test_extract_article_text_does_not_retry_deterministic_http_errors(mock_get, mock_sleep):
    """A 403/406 is the server's deterministic answer, not a blip -- retrying
    it wastes time and looks more automated, not less."""
    response = MagicMock()
    response.raise_for_status.side_effect = curl_requests.exceptions.HTTPError("403 Forbidden")
    mock_get.return_value = response

    result = extract_article_text("https://example.com/blocked")

    assert result is None
    mock_get.assert_called_once()
    mock_sleep.assert_not_called()


@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.curl_requests.get")
def test_extract_article_text_returns_none_when_no_content_extracted(mock_get, mock_extract):
    mock_get.return_value = make_response(html="<html></html>")
    mock_extract.return_value = None

    result = extract_article_text("https://example.com/empty")
    assert result is None


@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.curl_requests.get")
def test_extract_article_text_fails_gracefully_on_google_news_wrapper(mock_get, mock_extract):
    """Real Google News RSS links resolve to a JS interstitial on
    news.google.com that trafilatura cannot extract. That must return None,
    not raise — verified against live output."""
    mock_get.return_value = make_response(
        html="<!doctype html><html><head><base href='https://news.google.com/'></head></html>",
        final_url="https://news.google.com/rss/articles/CBMi0wFBVV95cUxPVzRseEx",
    )
    mock_extract.return_value = None

    result = extract_article_text("https://news.google.com/rss/articles/CBMi0wFBVV95cUxPVzRseEx")
    assert result is None


# --- known-unresolvable hosts: skip the fetch entirely -----------------------

@pytest.mark.parametrize("url", [
    "https://news.google.com/rss/articles/CBMi0wFBVV95cUxP",
    "https://news.google.com/rss/search?q=x",
])
def test_is_known_unresolvable_url_covers_google_news(url):
    assert is_known_unresolvable_url(url) is True


@pytest.mark.parametrize("url", [
    "https://www.reuters.com/article/x",
    "https://notnews.google.com/page",
    "https://news.google.com.evil.net/page",
    "https://www.msn.com/en-us/news/article",
])
def test_is_known_unresolvable_url_allows_everything_else(url):
    assert is_known_unresolvable_url(url) is False


@patch("resolution_finder.article_extractor.curl_requests.get")
def test_extract_article_text_never_fetches_known_unresolvable_hosts(mock_get):
    """400+ live attempts, zero successes -- skip the request outright rather
    than pay for a fetch guaranteed to fail (see UNRESOLVABLE_HOSTS)."""
    result = extract_article_text("https://news.google.com/rss/articles/CBMi0wFBVV95cUxP")
    assert result is None
    mock_get.assert_not_called()


# --- TLS-fingerprint impersonation --------------------------------------------

@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.curl_requests.get")
def test_extract_article_text_impersonates_a_real_browser(mock_get, mock_extract):
    """Some real outlets (news18.com, azcentral.com, wionews.com -- all
    verified live) return HTTP 403/405/406 from Akamai/WAF protection that
    fingerprints the TLS handshake, not just HTTP headers -- a bare
    User-Agent header cannot fix that. curl_cffi's `impersonate` must
    actually be passed through, or the TLS fingerprint reverts to Python's
    default and these sites block us again."""
    mock_get.return_value = make_response()
    mock_extract.return_value = "text"

    extract_article_text("https://example.com/article")

    assert mock_get.call_args.kwargs["impersonate"] == IMPERSONATE == "chrome"


# --- blocked-host defence in depth -------------------------------------------

@pytest.mark.parametrize("url", [
    "https://x.com/NobelPrize/status/123",
    "https://www.x.com/NobelPrize",
    "https://twitter.com/SECGov/status/1",
    "https://mobile.twitter.com/SECGov",
    "https://instagram.com/p/abc123",
    "https://www.instagram.com/nobelprize_org/",
    "https://X.com/NobelPrize",
])
def test_is_blocked_url_covers_social_hosts_and_subdomains(url):
    assert is_blocked_url(url) is True


@pytest.mark.parametrize("url", [
    "https://www.reuters.com/article/x",
    "https://notx.com/page",
    "https://x.com.evil.net/page",
    "https://news.google.com/rss/articles/CBMi",
])
def test_is_blocked_url_allows_everything_else(url):
    assert is_blocked_url(url) is False


@pytest.mark.parametrize("url", [
    "https://x.com/NobelPrize/status/123",
    "https://twitter.com/SECGov/status/1",
    "https://www.instagram.com/p/abc123",
])
@patch("resolution_finder.article_extractor.curl_requests.get")
def test_extract_article_text_never_fetches_blocked_hosts(mock_get, url):
    assert extract_article_text(url) is None
    mock_get.assert_not_called()


@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.curl_requests.get")
def test_extract_article_text_discards_content_redirected_to_blocked_host(mock_get, mock_extract):
    mock_get.return_value = make_response(final_url="https://x.com/NobelPrize/status/123")
    mock_extract.return_value = "Some text that must not be used."

    result = extract_article_text("https://redirector.example.com/go")
    assert result is None
