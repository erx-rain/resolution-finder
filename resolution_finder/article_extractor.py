import logging
from typing import Optional
from urllib.parse import urlparse
import requests
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

# A realistic browser header set, not just a User-Agent. Several real outlets
# (news18.com, azcentral.com, wionews.com, samaa.tv -- all verified live)
# return HTTP 403/405/406 specifically because a bare User-Agent-only request
# fingerprints as a script, not a browser. This does NOT help the two
# UNRESOLVABLE_HOSTS cases above -- those return 200 with no content to
# unlock, headers or not.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


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

    try:
        response = requests.get(url, timeout=timeout, headers=BROWSER_HEADERS)
        response.raise_for_status()
    except requests.RequestException as exc:
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
