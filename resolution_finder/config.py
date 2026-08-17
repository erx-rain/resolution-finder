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
REQUEST_DELAY_SECONDS = 1
