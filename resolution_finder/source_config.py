from typing import Optional
from resolution_finder.query_builder import extract_urls

TIER1_DOMAINS = {
    "congress.gov": "congress.gov",
    "u.s. congress": "congress.gov",
    "house of representatives": "congress.gov",
    "norwegian nobel committee": "nobelprize.org",
    "nobel committee": "nobelprize.org",
    "sec.gov": "sec.gov",
    "securities and exchange commission": "sec.gov",
}

TIER2_OUTLETS = [
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "afp.com",
    "npr.org",
    "theguardian.com",
    "aljazeera.com",
    "cnn.com",
    "nytimes.com",
    "washingtonpost.com",
    "politico.com",
]

# Broader coverage, looser editorial standards than TIER2_OUTLETS. Included
# because this tool never auto-settles anything — a human always makes the
# final call in the dashboard — so more evidence (clearly labeled) is better
# than none. Forbes runs an open contributor platform where article quality
# varies by author, not just by outlet; MSN and Yahoo are aggregators that
# republish other outlets' wire content rather than doing original
# reporting, so their own editorial accountability is looser even when the
# underlying story is sound; Goal.com is a real, reputable outlet but only
# within its football/soccer niche, not a general-purpose news wire.
TIER2_SECONDARY_OUTLETS = [
    "forbes.com",
    "goal.com",
    "msn.com",
    "yahoo.com",
]

# Official X/Twitter handles for named organizations. Used only to build a
# site-scoped search query (see evidence_retriever.py) — the actual x.com page
# is NEVER fetched. This is a best-effort, zero-cost lookup: it relies on
# Google News RSS occasionally indexing a post, which is not guaranteed since
# News RSS is scoped to news publishers, not general web/social content. Any
# hit is surfaced to the reviewer as a link to check by hand, never treated as
# confirmed evidence on its own.
TIER1_SOCIAL_ACCOUNTS = {
    "congress.gov": "@HouseFloor",
    "norwegian nobel committee": "@NobelPrize",
    "sec.gov": "@SECGov",
}


# Same rationale as TIER1_SOCIAL_ACCOUNTS above, for Instagram instead of X.
# Instagram content is indexed by Google even less than X/news, so this will
# succeed less often — kept anyway since it's zero-cost and any hit still
# goes through the same official_social manual-verification flag.
TIER1_INSTAGRAM_ACCOUNTS = {
    "congress.gov": "@housefloor",
    "norwegian nobel committee": "@nobelprize_org",
    "sec.gov": "@secgov",
}


def resolve_instagram_handle(description: str) -> Optional[str]:
    lowered = description.lower()
    for phrase, handle in TIER1_INSTAGRAM_ACCOUNTS.items():
        if phrase in lowered:
            return handle
    return None


def resolve_named_source(description: str) -> Optional[str]:
    urls = extract_urls(description)
    if urls:
        return urls[0]
    lowered = description.lower()
    for phrase, domain in TIER1_DOMAINS.items():
        if phrase in lowered:
            return domain
    return None


def resolve_social_handle(description: str) -> Optional[str]:
    lowered = description.lower()
    for phrase, handle in TIER1_SOCIAL_ACCOUNTS.items():
        if phrase in lowered:
            return handle
    return None
