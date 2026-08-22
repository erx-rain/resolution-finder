from typing import Optional
from resolution_finder.query_builder import extract_urls

# Before adding a new entry here, verify LIVE (not just from the market's
# description text) that the domain is actually reachable: check robots.txt
# (resolution_finder.article_extractor.is_allowed_by_robots_txt) AND do a
# real fetch. A robots.txt-permissive site can still be practically
# unreachable -- e.g. inecnigeria.org (Nigeria's INEC, cited as the
# resolution source for Osun State-style election markets) has a broken SSL
# certificate as of 2026-08-17, confirmed with both curl_cffi and plain
# requests (SSLCertVerificationError: unable to get local issuer
# certificate) -- so it was deliberately NOT added here even though its
# robots.txt is wide open. Do not work around a cert failure by disabling
# verification; that's a real security anti-pattern, especially for a
# government source where trust in the data is the entire point. Re-check
# live before adding it if this comes up again.
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
# varies by author, not just by outlet; Yahoo is an aggregator that
# republishes other outlets' wire content rather than doing original
# reporting, so its own editorial accountability is looser even when the
# underlying story is sound; Goal.com is a real, reputable outlet but only
# within its football/soccer niche, not a general-purpose news wire.
#
# msn.com was deliberately removed (not just never added): verified live
# that its pages are a client-rendered app shell with no article text, no
# JSON-LD, and no meta description in the static HTML at all -- 15/15 real
# fetches failed extraction across two unrelated topics (Vinicius Jr.
# transfer, Fed/SpaceX/NBA/OpenAI news). Since Bing surfaces MSN heavily,
# keeping it here was actively crowding out other outlets under the
# MAX_RESULTS_PER_QUERY cap for zero yield.
#
# Task 20 (2026-08-18): broadened this tier to more topic verticals --
# data/markets.json now spans crypto, sports/esports, science, finance,
# entertainment, and tech markets well beyond the original wire-service-news
# scope this tier was tuned for. Every domain below was verified live
# (robots.txt + a real article fetch through extract_article_text returning
# real, non-empty prose) immediately before being added -- see
# docs/superpowers/plans/2026-08-10-resolution-finder-scanner.md Task 20 for
# the full per-domain verification record. Two candidates from that same
# pass were deliberately NOT added:
#   - marketwatch.com: robots.txt has a blanket "User-agent: *\nDisallow: /"
#     -- only specifically named crawlers (googlebot, bingbot, ...) are
#     permitted, and this project's generic fetcher is not one of them, so
#     it fails the "not disallowed for generic bots" bar outright.
#   - nature.com: passes robots.txt, but its actual news/journalism articles
#     (the nature.com/articles/d41586-... URLs, which is what evidence
#     searches for a science-market resolution would realistically surface)
#     are paywalled -- extract_article_text returns only a short "Access
#     options / Subscribe to this journal" boilerplate block, not real
#     article text, on 2/2 different d41586 articles tried. (A minority of
#     nature.com URLs -- open-access primary-research papers in Nature
#     Portfolio journals like Scientific Reports/Nature Communications,
#     e.g. /articles/s41598-... -- DO extract real full text; but that is
#     not the representative article type this tool would encounter when
#     looking for science-news coverage, so the domain was left out rather
#     than added on the strength of an unrepresentative pass.)
# Weather has no clear additional candidate: general wire services
# (Reuters/AP/BBC, already in TIER2_OUTLETS) already cover major weather
# events adequately, and dedicated weather sites are forecast tools, not
# reporting outlets, so none was added for that vertical.
#
# 2026-08-18 (politics vertical, following the Iran war powers resolution
# extraction-failure investigation): after fixing Tier 1's Google-wrapper
# bug, retrieve_evidence's real Bing results for that market were still all
# being dropped as "general" -- none of jpost.com/timesofisrael.com/
# msn.com/columbian.com/i24news.tv were whitelisted. Verified live
# (robots.txt + a real extract_article_text fetch) before adding:
#   - jpost.com: real article, 3407 chars extracted -- added.
#   - timesofisrael.com: real article, 3900 chars extracted -- added.
#   - ktar.com (Phoenix AP-affiliate wire coverage, also surfaced by this
#     same live query): real article, 6285 chars extracted -- added.
#   - columbian.com: robots.txt allows it, but the real fetch returned
#     HTTP 403 (bot-blocked) -- not added.
#   - i24news.tv: robots.txt allows it, but the real fetch extracted only
#     46 chars (effectively empty) -- not added.
#   - msn.com: already excluded above (Task 20) for the same reason found
#     again live here -- not re-added.
#
# 2026-08-22 (sports/politics vertical, following the zero-retrieval
# investigation for two politics markets): live Bing results across 7
# real sports/politics markets in the test set surfaced several real,
# recurring outlets never whitelisted. Verified live (robots.txt + a real
# extract_article_text fetch) before adding, same as above -- all 7
# passed cleanly:
#   - gosugamers.net, win.gg (esports/Dota2 news, same class as the
#     existing dotesports.com): 10787 / 9920 chars extracted.
#   - shacknews.com (gaming/esports news): 2416 chars extracted.
#   - beinsports.com (major international sports broadcaster, soccer/
#     World Cup coverage -- same niche-but-reputable tier as goal.com):
#     2717 chars extracted.
#   - nypost.com (same editorial tier as forbes.com/yahoo.com already
#     here): 5409 chars extracted.
#   - foxbusiness.com (same tier as cnbc.com already here): 4518 chars
#     extracted.
#   - punchng.com (established Nigerian daily, complements existing
#     Osun-election-era African political coverage): 4203 chars
#     extracted.
TIER2_SECONDARY_OUTLETS = [
    "forbes.com",
    "goal.com",
    "yahoo.com",
    "espn.com",
    "skysports.com",
    "dexerto.com",
    "dotesports.com",
    "coindesk.com",
    "cointelegraph.com",
    "scientificamerican.com",
    "cnbc.com",
    "variety.com",
    "hollywoodreporter.com",
    "techcrunch.com",
    "theverge.com",
    "arstechnica.com",
    "axios.com",
    "jpost.com",
    "timesofisrael.com",
    "ktar.com",
    "gosugamers.net",
    "win.gg",
    "shacknews.com",
    "beinsports.com",
    "nypost.com",
    "foxbusiness.com",
    "punchng.com",
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
