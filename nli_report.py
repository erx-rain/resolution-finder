# nli_report.py
"""Diagnostic report for the NLI zero-shot verification layer
(resolution_finder/verdict_engine.py's _verify_winner_candidate /
_verify_head_to_head_candidate / _verify_elimination_candidate), added
2026-08-23 to replace the old MiniLM cosine-similarity approach.

Calls the REAL production verify_* functions -- not a reimplementation --
against a curated set of real cases: sentences and markets already
established in tests/test_verdict_engine.py, each grounded in an actual
live-found bug or real evidence text (BBC Pidgin, Yahoo Sports, GosuGamers
articles), not invented for this script. Each case is labeled with the
expected TRUE/FALSE outcome the production code is supposed to reach.
Prints the raw entailment score(s) per case alongside pass/fail, so the
model's actual confidence and TRUE/FALSE separation gap are visible, not
just a final verdict -- this is what "does it work, and how well" looks
like for a model with no fixed accuracy metric of its own.

    python nli_report.py
"""
from datetime import date, timedelta

from resolution_finder.models import Market
from resolution_finder.verdict_engine import (
    NLI_VERIFICATION_THRESHOLD,
    HEAD_TO_HEAD_VERIFICATION_THRESHOLD,
    _classify_scores,
    _winner_hypotheses,
    _head_to_head_hypotheses,
    _elimination_hypotheses,
    _verify_winner_candidate,
    _verify_head_to_head_candidate,
    _verify_elimination_candidate,
)

# --- Real markets, copied from tests/test_verdict_engine.py -----------------

OSUN_MARKET = Market(
    id="osun-state-governor-2026",
    title="Osun State Gubernatorial Election Winner",
    description=(
        "This market will resolve according to the listed candidate who wins "
        "the 2026 Osun State gubernatorial elections. If the results are not "
        "known definitively by June 30, 2027, 11:59 PM ET, this market will "
        "resolve to \"Other\"."
    ),
    options=["Ademola Adeleke", "Taofeek Adeleke"],
    close_date=date.today() + timedelta(days=365),
)

INTERNATIONAL_DESCRIPTION = (
    "This market will resolve based on the team officially recognized as the "
    "champion of The International 2026. The winning team's market will "
    "resolve to \"Yes\". All other team markets will resolve to \"No\". If "
    "The International 2026 champion has not been officially determined by "
    "September 6, 2026, 11:59 PM ET, or if the champion is not one of the "
    "listed teams, all markets will resolve to \"No\"."
)

INTERNATIONAL_MARKET_FULL = Market(
    id="international-2026-champion",
    title="The International 2026 Champion",
    description=INTERNATIONAL_DESCRIPTION,
    options=[
        "Team Yandex", "Team Vision", "Team Falcons", "BoomBoys",
        "Team Spirit", "Team Liquid", "1w Team", "Aurora Gaming",
    ],
    close_date=date.today() + timedelta(days=365),
)

LAKERS_ROCKETS_MARKET = Market(
    id="nba-playoffs-who-will-win-series-lakers-vs-rockets",
    title="NBA Playoffs: Who Will Win Series? - Lakers vs. Rockets",
    description=(
        "This market will resolve to \"Lakers\" if the Los Angeles Lakers win "
        "the 2026 NBA Playoffs First Round series between the Los Angeles "
        "Lakers and Houston Rockets. This market will resolve to \"Rockets\" "
        "if the Houston Rockets win."
    ),
    options=["Lakers", "Rockets"],
    close_date=date.today() + timedelta(days=30),
)

LAKERS_VICTORY_SENTENCE = (
    "The Los Angeles Lakers' first-round playoff victory over the Houston "
    "Rockets may have accomplished more than advancing the franchise to the "
    "Western Conference semifinals."
)

# --- Cases: (label, kind, sentence, expected, extra) ------------------------
# Each sentence and its expected TRUE/FALSE outcome is copied from an
# existing, already-passing test in tests/test_verdict_engine.py -- most are
# real article text (BBC Pidgin, Yahoo Sports, GosuGamers), grounded in an
# actual live-found bug, not invented for this report.

WINNER_CASES = [
    (
        "Osun election, real BBC Pidgin phrasing ('winner of')",
        "INEC declare Ademola Adeleke winner of the Osun State election.",
        "Ademola Adeleke", OSUN_MARKET, True,
    ),
    (
        "BoomBoys losing 2-3 win-loss record (real bug: wrongly crowned champion off bare 'wins')",
        "BoomBoys posted a 2-3 win-loss record with wins over OG and Iron "
        "Wing, but suffered consecutive losses against TEAM VISION, Aurora "
        "Gaming, and Team Falcons.",
        "BoomBoys", INTERNATIONAL_MARKET_FULL, False,
    ),
]

ELIMINATION_CASES = [
    (
        "Aurora Gaming eliminated in the lower bracket",
        "Aurora Gaming was eliminated from The International 2026 in the "
        "lower bracket.",
        "Aurora Gaming", True,
    ),
    (
        "BoomBoys lost Game 2 but lead the series 2-1 (real bug: single loss != eliminated)",
        "BoomBoys lost to Team Falcons in Game 2, but lead the series 2-1.",
        "BoomBoys", False,
    ),
]

HEAD_TO_HEAD_CASES = [
    (
        "Lakers beat Rockets, real Yahoo Sports phrasing ('victory over')",
        LAKERS_VICTORY_SENTENCE, "Lakers", "Rockets", True,
    ),
    (
        "Same real sentence, reversed direction asked (the exact ambiguity the old cosine-similarity approach couldn't resolve)",
        LAKERS_VICTORY_SENTENCE, "Rockets", "Lakers", False,
    ),
]


def _mark(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def run_report() -> None:
    total = 0
    failed = 0
    true_scores: list[float] = []
    false_scores: list[float] = []

    print("=" * 100)
    print("WINNER VERIFICATION  (_verify_winner_candidate)")
    print(f"  threshold: positive score >= {NLI_VERIFICATION_THRESHOLD}")
    for label, sentence, option, market, expected in WINNER_CASES:
        total += 1
        positive, negative = _winner_hypotheses(option, market)
        scores = _classify_scores(sentence, [positive, negative])
        actual = _verify_winner_candidate(sentence, option, market)
        (true_scores if expected else false_scores).append(scores[positive])
        failed += actual != expected
        print(f"\n  [{_mark(actual == expected)}] {label}")
        print(f"    sentence:  {sentence!r}")
        print(f"    expected:  {expected}   actual: {actual}")
        print(f"    positive score ({positive!r}): {scores[positive]:.3f}")
        print(f"    negative score ({negative!r}): {scores[negative]:.3f}")

    print("\n" + "=" * 100)
    print("ELIMINATION VERIFICATION  (_verify_elimination_candidate)")
    print(f"  threshold: positive score >= {NLI_VERIFICATION_THRESHOLD}")
    for label, sentence, option, expected in ELIMINATION_CASES:
        total += 1
        positive, negative = _elimination_hypotheses(option)
        scores = _classify_scores(sentence, [positive, negative])
        actual = _verify_elimination_candidate(sentence, option, INTERNATIONAL_MARKET_FULL)
        (true_scores if expected else false_scores).append(scores[positive])
        failed += actual != expected
        print(f"\n  [{_mark(actual == expected)}] {label}")
        print(f"    sentence:  {sentence!r}")
        print(f"    expected:  {expected}   actual: {actual}")
        print(f"    positive score ({positive!r}): {scores[positive]:.3f}")
        print(f"    negative score ({negative!r}): {scores[negative]:.3f}")

    print("\n" + "=" * 100)
    print("HEAD-TO-HEAD VERIFICATION  (_verify_head_to_head_candidate)")
    print(f"  threshold: positive score >= {HEAD_TO_HEAD_VERIFICATION_THRESHOLD} AND positive is the max of 3")
    for label, sentence, option, other_option, expected in HEAD_TO_HEAD_CASES:
        total += 1
        positive, opposite, unresolved = _head_to_head_hypotheses(option, other_option)
        scores = _classify_scores(sentence, [positive, opposite, unresolved])
        actual = _verify_head_to_head_candidate(sentence, option, other_option)
        (true_scores if expected else false_scores).append(scores[positive])
        failed += actual != expected
        print(f"\n  [{_mark(actual == expected)}] {label}")
        print(f"    sentence:   {sentence!r}")
        print(f"    expected:   {expected}   actual: {actual}")
        print(f"    positive  ({positive!r}): {scores[positive]:.3f}")
        print(f"    opposite  ({opposite!r}): {scores[opposite]:.3f}")
        print(f"    unresolved ({unresolved!r}): {scores[unresolved]:.3f}")

    print("\n" + "=" * 100)
    print(f"RESULT: {total - failed}/{total} cases matched their expected outcome")
    if true_scores:
        print(f"  TRUE-case positive scores:  min={min(true_scores):.3f} max={max(true_scores):.3f}")
    if false_scores:
        print(f"  FALSE-case positive scores: min={min(false_scores):.3f} max={max(false_scores):.3f}")
    if true_scores and false_scores:
        gap = min(true_scores) - max(false_scores)
        print(f"  separation gap (min TRUE - max FALSE): {gap:+.3f}"
              f"  ({'clean separation' if gap > 0 else 'OVERLAP -- a real miscalibration risk'})")
    if failed:
        print(f"\n{failed} case(s) did NOT match their expected outcome -- see [FAIL] lines above.")
    else:
        print("\nAll cases matched their expected outcome.")


if __name__ == "__main__":
    run_report()
