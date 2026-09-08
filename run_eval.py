# run_eval.py
"""Accuracy eval against real markets in data/markets.json.

Reruns the pipeline stages (no DB writes), prints a per-market trace so an
UNCLEAR/NO_EVIDENCE/WRONG result can be traced to its cause (no candidates
retrieved, candidates retrieved but no article text extracted, text
extracted but ranked below threshold, or ranked but no verdict-engine
match), and appends one JSON record per run to data/eval_history.jsonl --
timestamp, git commit, and a per-market {ground_truth, verdict,
correct/wrong/unresolved} breakdown.

Every run gets appended, whatever its size -- the point is to build up a
real, versioned history of "did this code change actually help", not just
compare full-suite runs. Pass one or more market IDs to run a small, fast
batch (seconds, not minutes) instead of the full set:

    python run_eval.py bitcoin-above-64k-on-august-17-2026 clarity-act-2026

With no arguments, runs every market in data/markets.json.

CACHING (see resolution_finder/eval_cache.py's own docstring for the full
reasoning): retrieval is live and non-deterministic, so by default this
script is now cache-first -- a market with an existing snapshot in
data/eval_cache.json replays against that FIXED evidence (real ranking,
real decide() still run -- only the retrieval inputs are cached, not the
verdict). This removes the network/rate-limit time entirely -- the
dominant cost on a replayed run is the one-time embedding/NLI model load
(measured: ~60-70s per process invocation, same whether 1 market or 97),
not per-market work, so replaying a big batch is dramatically faster
(minutes, not the ~90 for a full live run) while a tiny 1-2 market
replay mostly just pays that fixed model-load cost. Critically, this lets
two runs of different CODE be compared without retrieval noise
confounding the comparison -- verified live: a market re-run from the
same cached snapshot twice produced byte-identical VERDICT/evidence/
source output both times. A market with no cached snapshot yet is
fetched live and the result is cached automatically for next time. Pass
--live to force a fresh live fetch (and overwrite any existing snapshot)
instead:

    python run_eval.py --live                     # refresh everything
    python run_eval.py --live clarity-act-2026     # refresh just this one
"""
import json
import logging
import subprocess
import sys
from datetime import date, datetime, timezone

from resolution_finder.models import Market
from resolution_finder.query_builder import build_queries
from resolution_finder.evidence_retriever import retrieve_evidence
from resolution_finder.article_extractor import extract_article_text, is_known_unresolvable_url
from resolution_finder.relevance_ranker import rank_by_relevance
from resolution_finder.verdict_engine import decide
from resolution_finder.config import SIMILARITY_THRESHOLD, MARKETS_JSON_PATH
from resolution_finder.eval_cache import load_cache, save_cache, load_market_snapshot, store_market_snapshot

logging.basicConfig(level=logging.WARNING)  # quiet; this prints its own trace

EVAL_HISTORY_PATH = "data/eval_history.jsonl"


def _git_state() -> tuple[str, bool]:
    """(short commit sha, is_dirty) -- best-effort. Falls back to
    ("unknown", False) if git itself is unavailable, so a missing git
    binary never crashes the eval run.

    Tries "git" first, then "git.exe" -- this project's Python always runs
    inside WSL, but this worktree's `.git` pointer file was written by
    Windows git in Windows path format; WSL's own native git binary can't
    resolve that pointer for a worktree ("fatal: not a git repository"),
    while Windows git (reachable from WSL as git.exe) resolves it fine
    since it wrote that path format in the first place.
    """
    for binary in ("git", "git.exe"):
        try:
            sha = subprocess.check_output(
                [binary, "rev-parse", "--short", "HEAD"], text=True
            ).strip()
            dirty = bool(subprocess.check_output(
                [binary, "status", "--porcelain"], text=True
            ).strip())
            return sha, dirty
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            continue
    return "unknown", False


def _load_markets(market_ids: set[str]) -> tuple[list[Market], dict[str, str]]:
    with open(MARKETS_JSON_PATH, encoding="utf-8") as f:
        raw = json.load(f)

    if market_ids:
        missing = market_ids - {m["id"] for m in raw}
        if missing:
            raise SystemExit(f"Unknown market id(s), not in {MARKETS_JSON_PATH}: {sorted(missing)}")
        raw = [m for m in raw if m["id"] in market_ids]

    ground_truth = {m["id"]: m.get("resolved_to") for m in raw}
    markets = [
        Market(
            id=m["id"], title=m["title"], description=m["description"],
            options=m.get("options", []),
            # A real market can genuinely have no close_date in the source
            # API response (e.g. some pulled March Madness markets) --
            # Market.close_date is Optional and verdict_engine already
            # guards every use of it (`if market.close_date and ...`), so
            # this is a real, valid state to load, not bad data to reject.
            close_date=date.fromisoformat(m["close_date"]) if m.get("close_date") else None,
        )
        for m in raw
    ]
    return markets, ground_truth


def _score(verdicts: list, ground_truth: str | None, options: list[str]) -> str:
    """correct/wrong/unresolved/no_ground_truth for one market's verdicts.

    Real bug found live (2026-08-23): the original scoring only compared
    ground_truth against verdict.outcome ("YES"/"NO"), which is correct
    for binary markets but wrong for multi-outcome ones, where
    ground_truth is an OPTION NAME (e.g. "Lakers") -- a market that
    correctly resolved Lakers=YES/Rockets=NO got scored WRONG, because
    "LAKERS" was never in the outcome set {"YES", "NO"} to begin with.
    """
    if ground_truth is None:
        return "no_ground_truth"

    if options:
        matching = [v for v in verdicts if v.option is not None and v.option.lower() == ground_truth.lower()]
        if matching:
            return "correct" if any(v.outcome == "YES" for v in matching) else "wrong"
        # No verdict for the ground-truth option itself -- still wrong if
        # some OTHER option was wrongly declared the winner instead.
        return "wrong" if any(v.outcome == "YES" for v in verdicts) else "unresolved"

    outcomes = {v.outcome for v in verdicts}
    if outcomes & {"YES", "NO"}:
        got = [v.outcome for v in verdicts if v.outcome in ("YES", "NO")]
        return "correct" if ground_truth.upper() in [g.upper() for g in got] else "wrong"
    return "unresolved"


def _fetch_live(market: Market) -> tuple[list, dict, list[str]]:
    """Real retrieval + extraction, exactly as before caching existed --
    returns (candidates, article_text_by_url, queries). `article_text`
    maps every candidate's url to its extracted text, or None for a real
    fetch/extract failure (kept, not dropped, so a later cached replay
    reproduces the SAME failures rather than silently fewer candidates
    than this live run actually had)."""
    queries = build_queries(market)
    candidates = retrieve_evidence(market, queries)
    article_text: dict = {}
    for ref in candidates:
        # Mirrors pipeline.py: an unfetchable host (news.google.com
        # JS-redirect wrapper) contributes its search-result headline
        # instead. Covers official_social and the date-scoped archive pass.
        if ref.source_type == "official_social" or (ref.summary and is_known_unresolvable_url(ref.url)):
            article_text[ref.url] = ref.summary or ref.title
        else:
            article_text[ref.url] = extract_article_text(ref.url)
    return candidates, article_text, queries


def _run_one_market(market: Market, ground_truth: str | None, cache: dict, live: bool) -> dict:
    print("\n" + "=" * 100)
    print(f"MARKET: {market.id}")
    print(f"  title: {market.title}")
    print(f"  close_date: {market.close_date}")
    print(f"  ground_truth resolved_to: {ground_truth}")

    snapshot = None if live else load_market_snapshot(cache, market.id)
    if snapshot is not None:
        print("  [replaying from cached retrieval snapshot -- pass --live to refresh]")
        candidates, article_text, queries = snapshot["candidates"], snapshot["article_text"], snapshot["queries"]
    else:
        candidates, article_text, queries = _fetch_live(market)
        store_market_snapshot(cache, market.id, queries, candidates, article_text)
        # Persist after EVERY live market, not once at the end of the run.
        # Real fragility found live (2026-09-08): a cold-cache full-batch
        # run is ~90 minutes of real network fetching, and the single
        # end-of-run save meant an interruption at minute 85 -- a Ctrl+C,
        # a crash, a laptop sleeping -- discarded every snapshot it had
        # just spent that time fetching, with nothing to resume from. The
        # whole point of the cache is that retrieval is the expensive,
        # non-deterministic part; losing all of it to a late interrupt
        # defeats that. One JSON write per market is negligible next to
        # the network round-trips it protects.
        save_cache(cache)

    print(f"  queries built ({len(queries)}): {queries}")
    print(f"  candidate refs retrieved: {len(candidates)}")
    by_type: dict[str, list] = {}
    for c in candidates:
        by_type.setdefault(c.source_type, []).append(c)
    candidates_by_type = {}
    for stype, items in by_type.items():
        print(f"    {stype}: {len(items)}  e.g. {items[0].url}")
        # Persist every URL per type, not just the printed example -- this
        # is exactly the detail a later debugging session needs (which
        # candidates existed at all) without re-running retrieval.
        candidates_by_type[stype] = [c.url for c in items]

    articles_with_text = []
    fetch_failure_urls = []
    for ref in candidates:
        text = article_text.get(ref.url)
        if text:
            articles_with_text.append((ref, text))
        else:
            fetch_failure_urls.append(ref.url)
    fetch_failures = len(fetch_failure_urls)
    print(f"  articles with extracted text: {len(articles_with_text)} (fetch/extract failures: {fetch_failures})")

    ranked = rank_by_relevance(market, articles_with_text)
    print(f"  ranked candidates above SIMILARITY_THRESHOLD={SIMILARITY_THRESHOLD}: {len(ranked)}")
    ranked_detail = []
    for r in ranked[:5]:
        # Console print stays short for a human skimming the terminal;
        # the JSONL field below keeps much more (still capped, not the
        # full article) so a later session can see the REAL surrounding
        # context a verdict came from, not just a 160-char fragment --
        # real gap found live (2026-09-07): that fragment was too short
        # to tell a genuine confirmation from a truncated one, and the
        # verdict's own evidence/source were never persisted to the
        # JSONL at all (console-only), so the eval history couldn't
        # answer "why" on its own.
        console_snippet = r.text[:160].replace("\n", " ")
        json_snippet = r.text[:1000].replace("\n", " ")
        print(f"    sim={r.similarity:.3f} [{r.article.source_type}] {r.article.url}")
        print(f"      text: {console_snippet!r}")
        ranked_detail.append({
            "similarity": round(r.similarity, 3), "source_type": r.article.source_type,
            "url": r.article.url, "text_snippet": json_snippet,
        })

    verdicts = decide(market, ranked)
    if not isinstance(verdicts, list):
        verdicts = [verdicts]
    for v in verdicts:
        print(f"  VERDICT: outcome={v.outcome} option={v.option} confidence={v.confidence:.3f}")
        if v.evidence_snippet:
            print(f"    evidence: {v.evidence_snippet[:200]!r}")
        if v.source_url:
            print(f"    source: {v.source_url}")

    verdict_class = _score(verdicts, ground_truth, market.options)

    return {
        "market_id": market.id,
        "ground_truth": ground_truth,
        "from_cache": snapshot is not None,
        "verdicts": [
            {
                "outcome": v.outcome, "option": v.option, "confidence": round(v.confidence, 3),
                "evidence_snippet": v.evidence_snippet, "source_url": v.source_url,
                "source_type": v.source_type,
            }
            for v in verdicts
        ],
        "verdict_class": verdict_class,
        # Pipeline-stage detail, not just the final outcome -- so a later
        # debugging session can see WHERE a market got stuck (no
        # candidates? candidates but extraction failed? extracted but
        # ranked below threshold? ranked but no verdict-engine match?)
        # straight from this saved record, without re-running any
        # network calls.
        "trace": {
            "queries": queries,
            "candidates_by_type": candidates_by_type,
            "articles_with_text_count": len(articles_with_text),
            "fetch_failure_urls": fetch_failure_urls,
            "ranked_above_threshold": ranked_detail,
        },
    }


def run_eval(market_ids: set[str], live: bool = False) -> None:
    markets, ground_truth = _load_markets(market_ids)
    cache = load_cache()
    run_results = [_run_one_market(m, ground_truth.get(m.id), cache, live) for m in markets]
    save_cache(cache)

    print("\n" + "=" * 100)
    print("SCORECARD")
    counts = {"correct": 0, "wrong": 0, "unresolved": 0, "no_ground_truth": 0}
    replayed = sum(1 for r in run_results if r["from_cache"])
    for r in run_results:
        counts[r["verdict_class"]] += 1
        got = [v["outcome"] for v in r["verdicts"]]
        print(f"  {r['verdict_class'].upper():14s} {r['market_id']}  truth={r['ground_truth']} got={got}")

    print(f"\ncorrect={counts['correct']} wrong={counts['wrong']} "
          f"unresolved={counts['unresolved']} no_ground_truth={counts['no_ground_truth']}")
    print(f"({replayed}/{len(markets)} market(s) replayed from cached retrieval, "
          f"{len(markets) - replayed} fetched live)")

    git_commit, git_dirty = _git_state()
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "market_ids": sorted(m.id for m in markets),
        "summary": counts,
        "results": run_results,
    }
    with open(EVAL_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    print(f"\nAppended this run ({len(markets)} market(s)) to {EVAL_HISTORY_PATH} "
          f"(commit {git_commit}{'*' if git_dirty else ''})")


if __name__ == "__main__":
    args = sys.argv[1:]
    live_flag = "--live" in args
    market_id_args = {a for a in args if a != "--live"}
    run_eval(market_id_args, live=live_flag)
