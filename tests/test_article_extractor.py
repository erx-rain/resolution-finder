from unittest.mock import patch, MagicMock
import pytest
import requests
from resolution_finder.article_extractor import extract_article_text, is_blocked_url


def make_response(html="<html>...</html>", final_url="https://example.com/article"):
    response = MagicMock()
    response.text = html
    response.url = final_url
    response.raise_for_status = MagicMock()
    return response


@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.requests.get")
def test_extract_article_text_returns_clean_text(mock_get, mock_extract):
    mock_get.return_value = make_response()
    mock_extract.return_value = "The bill was signed into law today."

    result = extract_article_text("https://example.com/article")
    assert result == "The bill was signed into law today."


@patch("resolution_finder.article_extractor.requests.get")
def test_extract_article_text_returns_none_on_network_error(mock_get):
    mock_get.side_effect = requests.ConnectionError("failed")
    result = extract_article_text("https://example.com/article")
    assert result is None


@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.requests.get")
def test_extract_article_text_returns_none_when_no_content_extracted(mock_get, mock_extract):
    mock_get.return_value = make_response(html="<html></html>")
    mock_extract.return_value = None

    result = extract_article_text("https://example.com/empty")
    assert result is None


@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.requests.get")
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
@patch("resolution_finder.article_extractor.requests.get")
def test_extract_article_text_never_fetches_blocked_hosts(mock_get, url):
    assert extract_article_text(url) is None
    mock_get.assert_not_called()


@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.requests.get")
def test_extract_article_text_discards_content_redirected_to_blocked_host(mock_get, mock_extract):
    mock_get.return_value = make_response(final_url="https://x.com/NobelPrize/status/123")
    mock_extract.return_value = "Some text that must not be used."

    result = extract_article_text("https://redirector.example.com/go")
    assert result is None
