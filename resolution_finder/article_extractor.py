import logging
import time
from typing import Optional
from urllib.parse import urlparse
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
TRANSIENT_EXCEPTIONS = (curl_requests.exceptions.Timeout, curl_requests.exceptions.ConnectionError)
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


def extract_article_text(url: str, timeout: int = 10) -> Optional[str]:
    if is_blocked_url(url):
        logger.warning("Refusing to fetch blocked host: %s", url)
        return None

    if is_known_unresolvable_url(url):
        logger.warning("Skipping known-unresolvable host, no fetch attempted: %s", url)
        return None

    response = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            response = curl_requests.get(url, timeout=timeout, impersonate=IMPERSONATE)
            response.raise_for_status()
            break
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

    # A redirect chain can land somewhere we are not allowed to read.
    final_url = getattr(response, "url", None)
    if isinstance(final_url, str) and is_blocked_url(final_url):
        logger.warning("Discarding content: %s redirected to blocked host %s", url, final_url)
        return None

    text = trafilatura.extract(response.text)
    if not text or not text.strip():
        logger.warning("No extractable article text at %s", url)
        return None
    return text
