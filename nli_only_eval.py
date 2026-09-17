# nli_only_eval.py
"""Experimental eval: the full real market batch (data/markets.json), but
the verdict is decided by the NLI zero-shot model ALONE -- no
BINARY_YES_KEYWORDS, no hedge-word guard, no wrong-subject veto, no
option-mention gate. Every sentence in every ranked article is judged
directly by NLI against every relevant hypothesis; nothing is filtered out
before the model sees it.

Answers a direct question raised this session: how good is NLI judgment on
its own, with none of the keyword-based scaffolding still wrapped around
it in production? Today's production pipeline still uses keyword/entity
gates as a cheap pre-filter for _decide_multi_outcome/_decide_date_thresholds
(a keyword match is only a CANDIDATE, verified by NLI after), or as the
PRIMARY decision mechanism entirely for _decide_binary (BINARY_YES_KEYWORDS
decides first; a DIFFERENT, older cosine-similarity model -- not NLI --
only runs as a fallback when keywords find nothing). This script removes
all of that and asks: what does NLI alone find, real evidence, real
retrieval, real markets, no shortcuts?

Numeric-threshold markets ($ figures, vote counts, etc.) intentionally
still use the EXISTING extraction logic (_decide_numeric_threshold,
unchanged) -- that mechanism is regex number-extraction, not "a keyword
phrase signals the verdict," so it isn't what this experiment is about;
there's no meaningful way to ask an NLI model "is 7.82 the right number"
without extracting it first.

    python nli_only_eval.py [market_id ...]

With no arguments, runs every market in data/markets.json.
"""
import sys

from resolution_finder.models import Market, RankedArticle, Verdict
from resolution_finder.query_builder import build_queries
from resolution_finder.evidence_retriever import retrieve_evidence
from resolution_finder.article_extractor import extract_article_text
from resolution_finder.relevance_ranker import rank_by_relevance
from resolution_finder.verdict_engine import (
    NLI_VERIFICATION_THRESHOLD,
    HEAD_TO_HEAD_VERIFICATION_THRESHOLD,
    _classify_scores,
    _split_sentences,
    _winner_hypotheses,
    _head_to_head_hypotheses,
    _elimination_hypotheses,
    _extract_threshold_condition,
    _decide_numeric_threshold,
)
from run_eval import _load_markets, _score, _git_state  # reuse, don't duplicate

# Mirrors _semantic_yes_signal's own templates in verdict_engine.py (the
# OLD cosine-similarity fallback for binary markets) -- same hypothesis
# wording, scored through the NLI classifier instead, so this is a fair
# same-question comparison of the two mechanisms, not a different question.
_BINARY_POSITIVE = "{title} This has been confirmed and has already happened."
_BINARY_NEGATIVE = "{title} This has not happened yet and remains unconfirmed or uncertain."


def _nli_only_binary(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict:
    positive = _BINARY_POSITIVE.format(title=market.title)
    negative = _BINARY_NEGATIVE.format(title=market.title)
    best: Verdict | None = None
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            scores = _classify_scores(sentence, [positive, negative])
            if scores[positive] < NLI_VERIFICATION_THRESHOLD:
                continue
            if best is None or scores[positive] > best.confidence:
                best = Verdict(
                    outcome="YES", confidence=scores[positive],
                    evidence_snippet=sentence.strip()[:280],
                    source_url=item.article.url, source_type=item.article.source_type,
                )
    if best is not None:
        return best
    if ranked_evidence:
        top = ranked_evidence[0]
        return Verdict(outcome="UNCLEAR", confidence=top.similarity,
                        evidence_snippet=top.text[:280], source_url=top.article.url,
                        source_type=top.article.source_type)
    return Verdict(outcome="NO_EVIDENCE", confidence=0.0, evidence_snippet=None,
                    source_url=None, source_type=None)


def _nli_only_options(market: Market, ranked_evidence: list[RankedArticle]) -> list[Verdict]:
    best: dict[str, Verdict] = {}  # option -> best-scoring verdict so far
    two_way = market.options if len(market.options) == 2 else None

    def _consider(option: str, outcome: str, score: float, sentence: str, item: RankedArticle) -> None:
        current = best.get(option)
        if current is not None and current.confidence >= score:
            return
        best[option] = Verdict(
            outcome=outcome, option=option, confidence=score,
            evidence_snippet=sentence.strip()[:280],
            source_url=item.article.url, source_type=item.article.source_type,
        )

    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            for option in market.options:
                w_pos, w_neg = _winner_hypotheses(option, market)
                w_scores = _classify_scores(sentence, [w_pos, w_neg])
                if w_scores[w_pos] >= NLI_VERIFICATION_THRESHOLD:
                    _consider(option, "YES", w_scores[w_pos], sentence, item)

                e_pos, e_neg = _elimination_hypotheses(option)
                e_scores = _classify_scores(sentence, [e_pos, e_neg])
                if e_scores[e_pos] >= NLI_VERIFICATION_THRESHOLD:
                    _consider(option, "NO", e_scores[e_pos], sentence, item)

            if two_way:
                # One call, not two: asking "did o1 beat o2" already scores
                # BOTH directions (pos=o1-beat-o2, opp=o2-beat-o1) in the
                # same classifier call, since the NLI pipeline scores each
                # label independently against the sentence and normalizes
                # across the given label set -- a second call with the
                # options swapped would score the identical 3 label
                # strings (just reordered) and return identical numbers.
                o1, o2 = two_way
                pos, opp, unresolved = _head_to_head_hypotheses(o1, o2)
                scores = _classify_scores(sentence, [pos, opp, unresolved])
                top = max(scores.values())
                if scores[pos] >= HEAD_TO_HEAD_VERIFICATION_THRESHOLD and scores[pos] == top:
                    _consider(o1, "YES", scores[pos], sentence, item)
                    _consider(o2, "NO", scores[pos], sentence, item)
                elif scores[opp] >= HEAD_TO_HEAD_VERIFICATION_THRESHOLD and scores[opp] == top:
                    _consider(o2, "YES", scores[opp], sentence, item)
                    _consider(o1, "NO", scores[opp], sentence, item)

    return [
        best.get(option, Verdict(outcome="UNCLEAR", option=option, confidence=0.0,
                                  evidence_snippet=None, source_url=None, source_type=None))
        for option in market.options
    ]


def _nli_only_decide(market: Market, ranked_evidence: list[RankedArticle]):
    if market.options:
        return _nli_only_options(market, ranked_evidence)
    threshold_condition = _extract_threshold_condition(market)
    if threshold_condition is not None:
        direction, value = threshold_condition
        return _decide_numeric_threshold(market, ranked_evidence, direction, value)  # unchanged, see module docstring
    return _nli_only_binary(market, ranked_evidence)


def run(market_ids: set[str]) -> None:
    markets, ground_truth = _load_markets(market_ids)
    results = []
    for market in markets:
        print("\n" + "=" * 100)
        print(f"MARKET: {market.id}  (options: {len(market.options)})")
        queries = build_queries(market)
        candidates = retrieve_evidence(market, queries)
        articles_with_text = []
        for ref in candidates:
            if ref.source_type == "official_social":
                text = ref.summary or ref.title
            else:
                text = extract_article_text(ref.url)
            if text:
                articles_with_text.append((ref, text))
        ranked = rank_by_relevance(market, articles_with_text)
        print(f"  ranked candidates: {len(ranked)}")

        verdicts = _nli_only_decide(market, ranked)
        if not isinstance(verdicts, list):
            verdicts = [verdicts]
        for v in verdicts:
            print(f"  VERDICT: outcome={v.outcome} option={v.option} confidence={v.confidence:.3f}")
            if v.evidence_snippet:
                print(f"    evidence: {v.evidence_snippet[:200]!r}")

        gt = ground_truth.get(market.id)
        verdict_class = _score(verdicts, gt, market.options)
        print(f"  -> {verdict_class.upper()} (truth={gt})")
        results.append((market.id, verdict_class, gt, [v.outcome for v in verdicts]))

    print("\n" + "=" * 100)
    print("SCORECARD (NLI-only, no keyword gates)")
    counts = {"correct": 0, "wrong": 0, "unresolved": 0, "no_ground_truth": 0}
    for market_id, verdict_class, gt, outcomes in results:
        counts[verdict_class] += 1
        print(f"  {verdict_class.upper():14s} {market_id}  truth={gt} got={outcomes}")
    print(f"\ncorrect={counts['correct']} wrong={counts['wrong']} "
          f"unresolved={counts['unresolved']} no_ground_truth={counts['no_ground_truth']}")

    git_commit, git_dirty = _git_state()
    print(f"(commit {git_commit}{'*' if git_dirty else ''}) -- NOT appended to data/eval_history.jsonl, "
          "this is a different decision mechanism than production, not a production regression check")


if __name__ == "__main__":
    run(set(sys.argv[1:]))
