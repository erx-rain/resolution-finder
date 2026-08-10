# resolution_finder/query_builder.py
import re
from resolution_finder.models import Market

URL_PATTERN = re.compile(r"https?://[^\s)]+")

# Heuristic proper-noun matcher: a capitalized word, followed by one or more
# more capitalized words optionally joined by a short lowercase connector
# ("of", "the", "for", "and") — e.g. matches "U.S. House of Representatives"
# and "Digital Asset Market Clarity Act" as single entities. This replaces
# spaCy (see note above this task) with a dependency-free approximation; it
# is intentionally simple and only used to enrich search queries, not to
# make resolution decisions.
ENTITY_PATTERN = re.compile(
    r"\b[A-Z][a-zA-Z0-9.]*(?:\s+(?:of|the|for|and)?\s*[A-Z][a-zA-Z0-9.]*)+\b"
)


def extract_urls(text: str) -> list[str]:
    raw = URL_PATTERN.findall(text)
    return [u.rstrip(".,;:)") for u in raw]


def extract_entities(text: str) -> list[str]:
    seen = []
    for match in ENTITY_PATTERN.finditer(text):
        candidate = match.group().strip()
        if candidate not in seen:
            seen.append(candidate)
    return seen


def build_queries(market: Market) -> list[str]:
    queries = [market.title]
    entities = extract_entities(market.description)
    if entities:
        queries.append(" ".join(entities[:3]) + " " + market.title.split("?")[0])
    for option in market.options:
        queries.append(f"{option} {market.title}")
    return queries
