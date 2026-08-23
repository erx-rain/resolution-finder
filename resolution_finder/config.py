DB_PATH = "data/resolution_finder.db"
MARKETS_JSON_PATH = "data/markets.json"
SIMILARITY_THRESHOLD = 0.35
# Peer-market matching (resolution_finder/peer_market.py) uses a separate,
# much stricter threshold than news-relevance ranking's SIMILARITY_THRESHOLD.
# Verified live during planning/Task 18: a wrong-bill false match ("Guidance
# Clarity Act S.81" vs. the real H.R.3633 market) scored 0.71 cosine
# similarity -- and a real, bill-confirmed genuine match was observed as low
# as 0.7116 -- while genuine matches that reach this final similarity gate
# (the 4 hard-reject conflict checks having already passed) were observed at
# 0.83+. Similarity does very little safety work here; the conflict checks
# carry it. 0.75 sits comfortably above the observed false-match cluster
# (0.65-0.7116) and below the observed genuine cluster (0.83+). Rejecting a
# genuine match at this stage is safe by design: the market simply falls
# through to the news pipeline instead of getting a wrong verdict.
PEER_MARKET_SIMILARITY_THRESHOLD = 0.75
# Off by default: the Polymarket cross-check (resolution_finder/peer_market.py)
# is a real, tested, reviewed feature, but the user chose to hold it back from
# live runs for now rather than remove it. Flip to True to re-enable; nothing
# else needs to change.
PEER_MARKET_ENABLED = False
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
# Used ONLY for multi-outcome candidate verification (verdict_engine.py),
# not for relevance_ranker.py's article-relevance filtering -- a different
# job needs a different model. Relevance filtering is a genuine similarity
# question ("is this article even about this market"); verdict verification
# is a classification question ("does this evidence confirm this specific
# outcome"), which cosine similarity answers poorly (real calibration
# 2026-08-23: a false "opinion" sentence scored a HIGHER raw similarity than
# a true confirmation). An NLI zero-shot entailment model answers the
# classification question directly instead of via a hand-tuned similarity
# margin -- real calibration on the same test sentences: TRUE cases scored
# 0.986-0.999 entailment probability, FALSE cases scored 0.226-0.751, a
# ~0.235 separation gap vs. the old approach's ~0.027 gap. xsmall variant
# chosen deliberately: smallest/fastest in the same free model family
# (142MB, 22M active params), purpose-built for zero-shot classification.
NLI_VERIFICATION_MODEL_NAME = "MoritzLaurer/deberta-v3-xsmall-zeroshot-v1.1-all-33"
REQUEST_DELAY_SECONDS = 1
# A live dry run against just 3 markets took ~65 minutes, dominated by
# hundreds of Tier 1 (Google News RSS) fetches that are guaranteed to fail
# extraction (verified: 336/336 live) -- feedparser returns every match for a
# query with no limit of its own. Capping results per query cuts that waste
# without changing which markets/queries are searched.
MAX_RESULTS_PER_QUERY = 5
