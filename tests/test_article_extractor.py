from unittest.mock import patch, MagicMock
import pytest
import curl_cffi.requests as curl_requests
from resolution_finder import article_extractor
from resolution_finder.article_extractor import (
    extract_article_text,
    is_blocked_url,
    is_known_unresolvable_url,
    is_allowed_by_robots_txt,
    IMPERSONATE,
)


@pytest.fixture(autouse=True)
def _permissive_robots_cache():
    """Every existing test's mocked `curl_requests.get` stands in for the
    *content* fetch. Without this, extract_article_text's own robots.txt
    check would consume that same mock first (see is_allowed_by_robots_txt),
    silently breaking every call-count/side_effect assertion in this file.
    Pre-seeding the cache as permissive for the hosts these tests actually
    use keeps the robots.txt code path exercised (cache hit -> allowed)
    without it touching the content-fetch mock. The dedicated robots.txt
    tests below use their own, unseeded host so they can mock the real
    fetch-and-parse behavior in isolation."""
    article_extractor._robots_cache.clear()
    for host in ("example.com", "redirector.example.com"):
        article_extractor._robots_cache[host] = (None, True)
    yield
    article_extractor._robots_cache.clear()


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


# --- robots.txt: a site's own stated automated-access policy ----------------

def make_robots_response(body, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.text = body
    return response


@patch("resolution_finder.article_extractor.curl_requests.get")
def test_is_allowed_by_robots_txt_respects_a_disallow_rule(mock_get):
    mock_get.return_value = make_robots_response("User-agent: *\nDisallow: /private/\n")
    assert is_allowed_by_robots_txt("https://robots-test-1.example/private/page") is False
    assert is_allowed_by_robots_txt("https://robots-test-1.example/public/page") is True


@patch("resolution_finder.article_extractor.curl_requests.get")
def test_is_allowed_by_robots_txt_permits_a_wide_open_site(mock_get):
    """Verified live against a real site (INEC Nigeria) before it was added
    as a named source: Disallow only covering /wp-admin/ (standard WordPress
    boilerplate) must not block real content pages."""
    mock_get.return_value = make_robots_response(
        "User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\n"
    )
    assert is_allowed_by_robots_txt("https://robots-test-2.example/election-results") is True


@patch("resolution_finder.article_extractor.curl_requests.get")
def test_is_allowed_by_robots_txt_defaults_to_allowed_when_genuinely_missing(mock_get):
    """A real 404 is the standard convention for 'no stated restriction' --
    must fail open. An actual block is still caught later by the real
    fetch's own error handling."""
    mock_get.return_value = make_robots_response("Not Found", status_code=404)
    assert is_allowed_by_robots_txt("https://robots-test-3.example/page") is True


@patch("resolution_finder.article_extractor.curl_requests.get")
def test_is_allowed_by_robots_txt_fails_closed_on_network_error(mock_get):
    """A network/SSL error is NOT the same as 'no robots.txt exists' -- we
    don't know the site's actual policy, so we must not guess permissively.
    Verified live: inecnigeria.org's robots.txt fetch fails with a real SSL
    certificate error (their cert chain is broken, not a client quirk), and
    treating that as 'allowed' would have been a real bug."""
    mock_get.side_effect = curl_requests.exceptions.ConnectionError("failed")
    assert is_allowed_by_robots_txt("https://robots-test-4.example/page") is False


@patch("resolution_finder.article_extractor.curl_requests.get")
def test_is_allowed_by_robots_txt_fails_closed_on_server_error(mock_get):
    """A 5xx (or any non-200/404 status) means we couldn't actually read the
    policy -- fail closed, same reasoning as a network error."""
    mock_get.return_value = make_robots_response("Internal Server Error", status_code=500)
    assert is_allowed_by_robots_txt("https://robots-test-4b.example/page") is False


@patch("resolution_finder.article_extractor.curl_requests.get")
def test_is_allowed_by_robots_txt_caches_per_host(mock_get):
    mock_get.return_value = make_robots_response("User-agent: *\nDisallow:\n")
    is_allowed_by_robots_txt("https://robots-test-5.example/a")
    is_allowed_by_robots_txt("https://robots-test-5.example/b")
    is_allowed_by_robots_txt("https://robots-test-5.example/c")
    assert mock_get.call_count == 1


@patch("resolution_finder.article_extractor.is_allowed_by_robots_txt")
@patch("resolution_finder.article_extractor.curl_requests.get")
def test_extract_article_text_refuses_when_robots_txt_disallows(mock_get, mock_robots):
    mock_robots.return_value = False
    result = extract_article_text("https://robots-test-6.example/blocked-page")
    assert result is None
    mock_get.assert_not_called()
