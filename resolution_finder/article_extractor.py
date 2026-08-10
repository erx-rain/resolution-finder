from typing import Optional
import requests
import trafilatura


def extract_article_text(url: str, timeout: int = 10) -> Optional[str]:
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except requests.RequestException:
        return None

    text = trafilatura.extract(response.text)
    if not text or not text.strip():
        return None
    return text
