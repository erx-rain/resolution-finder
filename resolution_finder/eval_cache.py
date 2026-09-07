# resolution_finder/eval_cache.py
"""Local, regenerable cache for run_eval.py's retrieval stage.

Retrieval is live and non-deterministic (Google/Bing News RSS return
different results run to run), which means every eval number produced
this project's whole test-find-fix-retest history is confounded by
which articles happened to surface that particular hour -- the real
Germany election market produced three DIFFERENT real bugs across three
separate runs this project's history (see
docs/superpowers/plans/2026-09-07-opus-plan.md, Priority 2). A 97-market
live run also costs ~90 minutes, which makes honest before/after
comparison expensive enough that it doesn't happen as often as it should.

This module caches one real, live snapshot of retrieval per market --
the candidate ArticleRefs retrieval returned and the article text
actually extracted for each -- so run_eval.py can replay against a FIXED
evidence set on demand. Ranking (rank_by_relevance) and the verdict
(decide()) still run for real on every replay, against the cached text --
nothing about the VERDICT itself is cached, only the retrieval inputs.
That's what makes replay a legitimate way to measure the effect of a code
change, not a memorized answer: change a keyword list or a corroboration
rule and the cached evidence will produce a different, real verdict.

Deliberately NOT committed to git (see .gitignore) -- a local performance/
reproducibility aid, not a versioned historical record the way
data/eval_history.jsonl is. Regenerate for specific markets with
`python run_eval.py --live <market_id ...>`, or for everything with
`python run_eval.py --live`.
"""
import json
from datetime import date
from typing import Optional

from resolution_finder.models import ArticleRef

CACHE_PATH = "data/eval_cache.json"


def load_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict) -> None:
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def _serialize_ref(ref: ArticleRef) -> dict:
    return {
        "url": ref.url,
        "title": ref.title,
        "source_type": ref.source_type,
        "published_date": ref.published_date.isoformat() if ref.published_date else None,
        "summary": ref.summary,
        "source_domain": ref.source_domain,
    }


def _deserialize_ref(d: dict) -> ArticleRef:
    return ArticleRef(
        url=d["url"],
        title=d["title"],
        source_type=d["source_type"],
        published_date=date.fromisoformat(d["published_date"]) if d.get("published_date") else None,
        summary=d.get("summary"),
        source_domain=d.get("source_domain"),
    )


def store_market_snapshot(
    cache: dict,
    market_id: str,
    queries: list[str],
    candidates: list[ArticleRef],
    article_text: dict[str, Optional[str]],
) -> None:
    """Records one market's real retrieval result into `cache` (in
    memory -- call save_cache separately to persist). `article_text` maps
    url -> extracted text, or None for a real fetch/extract failure (kept,
    not dropped, so a replay reproduces the SAME failures, not silently
    fewer candidates than the live run actually had)."""
    cache[market_id] = {
        "queries": queries,
        "candidates": [_serialize_ref(r) for r in candidates],
        "article_text": article_text,
    }


def load_market_snapshot(cache: dict, market_id: str) -> Optional[dict]:
    """Returns {"queries": [...], "candidates": [ArticleRef, ...],
    "article_text": {url: text_or_None}}, or None if this market has no
    cached snapshot yet."""
    entry = cache.get(market_id)
    if entry is None:
        return None
    return {
        "queries": entry["queries"],
        "candidates": [_deserialize_ref(d) for d in entry["candidates"]],
        "article_text": entry["article_text"],
    }
