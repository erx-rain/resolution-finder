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

With no arguments, runs every market in data/markets.json (slow -- real
network fetches with rate-limit delays across the whole set).
"""
import json
import logging
import subprocess
import sys
from datetime import date, datetime, timezone

from resolution_finder.models import Market
from resolution_finder.query_builder import build_queries
from resolution_finder.evidence_retriever import retrieve_evidence
from resolution_finder.article_extractor import extract_article_text
from resolution_finder.relevance_ranker import rank_by_relevance
from resolution_finder.verdict_engine import decide
from resolution_finder.config import SIMILARITY_THRESHOLD, MARKETS_JSON_PATH

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
            close_date=date.fromisoformat(m["close_date"]),
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


def _run_one_market(market: Market, ground_truth: str | None) -> dict:
    print("\n" + "=" * 100)
    print(f"MARKET: {market.id}")
    print(f"  title: {market.title}")
    print(f"  close_date: {market.close_date}")
    print(f"  ground_truth resolved_to: {ground_truth}")

    queries = build_queries(market)
    print(f"  queries built ({len(queries)}): {queries}")

    candidates = retrieve_evidence(market, queries)
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
    fetch_failures = 0
    fetch_failure_urls = []
    for ref in candidates:
        if ref.source_type == "official_social":
            text = ref.summary or ref.title
            if text:
                articles_with_text.append((ref, text))
            continue
        text = extract_article_text(ref.url)
        if text:
            articles_with_text.append((ref, text))
        else:
            fetch_failures += 1
            fetch_failure_urls.append(ref.url)
    print(f"  articles with extracted text: {len(articles_with_text)} (fetch/extract failures: {fetch_failures})")

    ranked = rank_by_relevance(market, articles_with_text)
    print(f"  ranked candidates above SIMILARITY_THRESHOLD={SIMILARITY_THRESHOLD}: {len(ranked)}")
    ranked_detail = []
    for r in ranked[:5]:
        snippet = r.text[:160].replace("\n", " ")
        print(f"    sim={r.similarity:.3f} [{r.article.source_type}] {r.article.url}")
        print(f"      text: {snippet!r}")
        ranked_detail.append({
            "similarity": round(r.similarity, 3), "source_type": r.article.source_type,
            "url": r.article.url, "text_snippet": snippet,
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
        "verdicts": [
            {"outcome": v.outcome, "option": v.option, "confidence": round(v.confidence, 3)}
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


def run_eval(market_ids: set[str]) -> None:
    markets, ground_truth = _load_markets(market_ids)
    run_results = [_run_one_market(m, ground_truth.get(m.id)) for m in markets]

    print("\n" + "=" * 100)
    print("SCORECARD")
    counts = {"correct": 0, "wrong": 0, "unresolved": 0, "no_ground_truth": 0}
    for r in run_results:
        counts[r["verdict_class"]] += 1
        got = [v["outcome"] for v in r["verdicts"]]
        print(f"  {r['verdict_class'].upper():14s} {r['market_id']}  truth={r['ground_truth']} got={got}")

    print(f"\ncorrect={counts['correct']} wrong={counts['wrong']} "
          f"unresolved={counts['unresolved']} no_ground_truth={counts['no_ground_truth']}")

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
    run_eval(set(sys.argv[1:]))
