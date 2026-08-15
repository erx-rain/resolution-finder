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


def _host_of(url: str) -> str:
    if not isinstance(url, str):
        return ""
    netloc = urlparse(url).netloc
    return netloc.split("@")[-1].split(":")[0].strip().lower()


def is_blocked_url(url: str) -> bool:
    host = _host_of(url)
    if not host:
        return False
    return any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_HOSTS)


def extract_article_text(url: str, timeout: int = 10) -> Optional[str]:
    if is_blocked_url(url):
        logger.warning("Refusing to fetch blocked host: %s", url)
        return None

    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
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
