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


# Closed-class English function words that legitimately start a sentence or
# title ("Will the CLARITY act...", "If The International 2026 champion has
# not been determined...") but are never themselves part of a proper noun.
# Deliberately does NOT include ENTITY_PATTERN's own connector words
# (of/the/for/and) -- those genuinely can start a real entity's own name
# ("The International", "The Beatles"), so stripping them unconditionally
# would wrongly gut those down to a single leftover word and drop them.
# This is a finite, stable grammatical closed class, not the open-ended
# "keyword signals a verdict" pattern this project moved away from
# elsewhere -- it can't grow unboundedly the way a real-world-phrasing
# keyword list would.
_LEADING_STOPWORDS = {
    "if", "this", "that", "these", "those", "when", "while", "since",
    "because", "although", "however", "otherwise", "please", "note",
    "only", "but", "so", "yet", "nor", "then", "thus", "now", "here",
    "there", "both", "each", "any", "no", "not", "where", "what",
    "which", "who", "whose", "why", "how", "will", "does", "did", "do",
    "is", "are", "was", "were",
}

# Trailing position adds ENTITY_PATTERN's own connector words (of/the/for/
# and) on top of _LEADING_STOPWORDS. Those are safe to keep at the START
# of a match (a real entity can legitimately begin "The International"),
# but a match can never legitimately END on a bare connector -- by
# construction every raw match ends on a capitalized word, so a trailing
# "The"/"Of"/etc. only ever appears when the chain accidentally continued
# past a sentence boundary into unrelated glue text.
_TRAILING_STOPWORDS = _LEADING_STOPWORDS | {"of", "the", "for", "and"}


def extract_entities(text: str) -> list[str]:
    """Heuristic proper-noun extraction, used only to enrich search queries
    (and, via verdict_engine._subject_terms, to build the wrong-subject
    veto's term list) -- not to make resolution decisions.

    Real garbled-query bugs found live (2026-08-23): a sentence/title-
    initial function word chaining onto a real entity right after it
    ("If The International 2026 Champion" -> raw match "If The
    International"; "Will the CLARITY act..." -> raw match "Will the
    CLARITY"); the same thing in reverse, a match running ACROSS a
    sentence boundary because the character class treats a period inside
    an abbreviation as just another word character, so the next
    sentence's leading function word gets glued onto the tail ("...at
    11:59 PM ET. Otherwise, this market..." -> raw match "PM ET.
    Otherwise"); and two adjacent short capitalized abbreviations with no
    real entity value ("11:59 PM ET" -> raw match "PM ET"). Fixed by
    trimming leading AND trailing closed-class stopwords off each raw
    match, then dropping any match where every remaining word is <=2
    characters (abbreviation-only, not a real multi-word name).
    """
    seen = []
    for match in ENTITY_PATTERN.finditer(text):
        words = match.group().strip().split()
        while words and words[0].lower() in _LEADING_STOPWORDS:
            words.pop(0)
        while words and words[-1].rstrip(".").lower() in _TRAILING_STOPWORDS:
            words.pop()
        if len(words) < 2:
            continue
        if all(len(w.rstrip(".")) <= 2 for w in words):
            continue
        candidate = " ".join(words)
        if candidate not in seen:
            seen.append(candidate)
    return seen


def build_queries(market: Market) -> list[str]:
    queries = [market.title]
    # Real gap found live (2026-08-26): a market's description can have
    # NO 2+-word capitalized phrase at all (e.g. "Primary resolution
    # source: official Egyptian election results.") while its own TITLE
    # clearly names real entities ("Egypt Presidential Election", "Abdel
    # Fattah") -- description-only extraction silently produced just ONE
    # query total for that market, with no redundancy against ordinary
    # search-result non-determinism. Measured live: re-running that exact
    # market 3 times against the real search endpoint found its
    # confirming evidence only 1 of 3 times. Title-derived entities are
    # a fallback, not additive, to avoid duplicating the bare title
    # query when the description already supplies real entities.
    entities = extract_entities(market.description) or extract_entities(market.title)
    if entities:
        queries.append(" ".join(entities[:3]) + " " + market.title.split("?")[0])
    for option in market.options:
        queries.append(f"{option} {market.title}")
    return queries
