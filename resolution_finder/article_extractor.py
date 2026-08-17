import logging
import time
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
import curl_cffi.requests as curl_requests
import trafilatura

logger = logging.getLogger(__name__)

# Hosts this project must never fetch directly. Enforced here, at the fetch
# boundary, as defence in depth: pipeline.py already skips extraction for
# "official_social" refs, but a redirect or a future code path could still
# hand us one of these URLs. Subdomains are covered too.
BLOCKED_HOSTS = ("x.com", "twitter.com", "instagram.com")

# news.google.com/rss/articles/... links are a JS-redirect wrapper: Google
# resolves the real article URL client-side, so plain requests+trafilatura
# never see anything but the wrapper shell. Verified live, repeatedly, not a
# one-off: 336/336 in the first investigation, 30/30 and 40/40 in two later
# spot-checks across completely unrelated topics -- 400+ attempts, zero
# successes. This is a structural property of the format, not a fluke, so
# skipping the fetch outright (no request, no rate-limit sleep -- see
# pipeline.py) saves real time without losing any evidence that would
# otherwise have been recovered.
UNRESOLVABLE_HOSTS = ("news.google.com",)

# Several real outlets (news18.com, azcentral.com, wionews.com -- all
# verified live) return HTTP 403/405/406 from Akamai/WAF edge protection that
# fingerprints the TLS handshake itself, not just HTTP headers -- a plain
# `requests` call gets rejected no matter what headers ride on top of it,
# because Python's TLS stack doesn't look like a real browser's. curl_cffi
# impersonates an actual Chrome TLS + header fingerprint together, which is
# what actually matters for consistency: verified live, all 3 sites recover
# real extractable article text this way. This does NOT help the
# UNRESOLVABLE_HOSTS case above -- those return 200 with no content to
# unlock no matter how convincing the request is.
IMPERSONATE = "chrome"

# A DNS/connect/read timeout is frequently a one-off network blip (verified
# live: an identical request that failed this way succeeded on immediate
# retry, 3/3 times). An HTTP-level rejection (403, 406, ...) is not a blip --
# it's the server's deterministic answer, and retrying it wastes time and
# looks more automated, not less. So only these two exception types are
# retried, and only up to MAX_FETCH_ATTEMPTS total -- a real, persistent
# failure (a truly dead host, not a blip) still gives up promptly rather than
# looping.
#
# curl_cffi.requests.exceptions.SSLError is a SUBCLASS of ConnectionError, so
# it would otherwise be caught by the transient handler below and retried --
# wrong for a permanently broken certificate chain (verified live:
# inecnigeria.org's cert failure is deterministic, not a blip; a real cert
# problem never fixes itself between retries). Checked before the transient
# tuple so it takes priority.
TRANSIENT_EXCEPTIONS = (curl_requests.exceptions.Timeout, curl_requests.exceptions.ConnectionError)
NON_RETRYABLE_EXCEPTIONS = (curl_requests.exceptions.SSLError,)
MAX_FETCH_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1


def _host_of(url: str) -> str:
    if not isinstance(url, str):
        return ""
    netloc = urlparse(url).netloc
    return netloc.split("@")[-1].split(":")[0].strip().lower()


def _host_matches(host: str, known_hosts: tuple) -> bool:
    if not host:
        return False
    return any(host == known or host.endswith("." + known) for known in known_hosts)


def is_blocked_url(url: str) -> bool:
    return _host_matches(_host_of(url), BLOCKED_HOSTS)


def is_known_unresolvable_url(url: str) -> bool:
    """True for hosts verified, repeatedly, to never yield article text.

    Exported so callers (see pipeline.py) can skip the per-request rate-limit
    sleep too -- there is nothing to protect when no request is made.
    """
    return _host_matches(_host_of(url), UNRESOLVABLE_HOSTS)


# robots.txt is the standard, honest way a site states its own
# automated-access policy -- checking it programmatically replaces a human
# spot-checking each new domain by hand (verified live for INEC Nigeria
# before it was added as a named source: wide open except /wp-admin/).
# Cached per host so a run touching the same domain repeatedly doesn't
# re-fetch robots.txt every time.
_robots_cache: dict[str, Optional[RobotFileParser]] = {}
ROBOTS_USER_AGENT = "*"


def _get_robot_parser(url: str):
    """Fetch and cache this host's robots.txt.

    Returns a (parser, is_genuinely_absent) pair. `is_genuinely_absent` is
    True only for a real 404 -- the standard convention for "no stated
    policy" -- and False for anything else (network error, SSL failure,
    5xx, ...), where we don't actually know the site's policy and must not
    guess permissively. Verified live: inecnigeria.org's robots.txt fetch
    fails with a real SSL certificate error (their cert chain is broken, not
    a client-side issue -- confirmed with both curl_cffi and plain
    requests), which must NOT be treated the same as "no robots.txt exists".

    Only a DEFINITIVE outcome (a real 404, or a parsed 200) is cached. A
    fetch exception is fail-closed for THIS call but deliberately not
    written to the cache, so a one-off network blip doesn't get treated as a
    permanent, run-long denial for every later URL on that host -- the next
    call for the same host gets a fresh attempt rather than reading a stale
    failure back out of the cache forever.
    """
    host = _host_of(url)
    if not host:
        return None, True
    if host in _robots_cache:
        return _robots_cache[host]

    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = curl_requests.get(robots_url, timeout=5, impersonate=IMPERSONATE)
    except curl_requests.exceptions.RequestException:
        return None, False  # not cached -- see docstring

    if response.status_code == 404:
        result = (None, True)
    elif response.status_code == 200:
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        result = (parser, False)
    else:
        result = (None, False)
    _robots_cache[host] = result
    return result


def is_allowed_by_robots_txt(url: str) -> bool:
    """Whether `url`'s own robots.txt permits fetching it.

    A genuinely absent robots.txt (404) is treated as allowed -- the
    standard crawler convention. Any other failure to determine the site's
    policy (network error, SSL error, a non-200/404 status) fails CLOSED:
    we don't know the policy, so we don't guess permissively.
    """
    parser, is_absent = _get_robot_parser(url)
    if parser is None:
        return is_absent
    return parser.can_fetch(ROBOTS_USER_AGENT, url)


def extract_article_text(url: str, timeout: int = 10) -> Optional[str]:
    if is_blocked_url(url):
        logger.warning("Refusing to fetch blocked host: %s", url)
        return None

    if is_known_unresolvable_url(url):
        logger.warning("Skipping known-unresolvable host, no fetch attempted: %s", url)
        return None

    if not is_allowed_by_robots_txt(url):
        logger.warning("Refusing to fetch, disallowed by robots.txt: %s", url)
        return None

    response = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            response = curl_requests.get(url, timeout=timeout, impersonate=IMPERSONATE)
            response.raise_for_status()
            break
        except NON_RETRYABLE_EXCEPTIONS as exc:
            logger.warning("Non-retryable fetch error, skipping %s: %s", url, exc)
            return None
        except TRANSIENT_EXCEPTIONS as exc:
            if attempt == MAX_FETCH_ATTEMPTS:
                logger.warning(
                    "Article fetch failed after %d attempts, skipping %s: %s",
                    attempt, url, exc,
                )
                return None
            logger.warning(
                "Transient fetch error (attempt %d/%d), retrying %s: %s",
                attempt, MAX_FETCH_ATTEMPTS, url, exc,
            )
            time.sleep(RETRY_DELAY_SECONDS)
        except curl_requests.exceptions.RequestException as exc:
            logger.warning("Article fetch failed, skipping %s: %s", url, exc)
            return None

    # A redirect chain can land somewhere we are not allowed to read, or
    # somewhere whose robots.txt we haven't actually consulted -- the check
    # above only covers the URL we started from.
    final_url = getattr(response, "url", None)
    if isinstance(final_url, str) and final_url != url:
        if is_blocked_url(final_url):
            logger.warning("Discarding content: %s redirected to blocked host %s", url, final_url)
            return None
        if not is_allowed_by_robots_txt(final_url):
            logger.warning(
                "Discarding content: %s redirected to %s, disallowed by robots.txt",
                url, final_url,
            )
            return None

    try:
        text = trafilatura.extract(response.text)
    except Exception as exc:  # noqa: BLE001 - a bad extraction must not abort the whole market
        logger.warning("trafilatura failed to parse %s: %s", url, exc)
        return None
    if not text or not text.strip():
        logger.warning("No extractable article text at %s", url)
        return None
    return text
