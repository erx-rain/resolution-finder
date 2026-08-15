# tests/test_verdict_engine.py
from datetime import date, timedelta
from resolution_finder.models import Market, ArticleRef, RankedArticle
from resolution_finder.verdict_engine import decide

CLARITY_DESCRIPTION = (
    "This market resolves to \"Yes\" if the Digital Asset Market Clarity Act "
    "of 2025 (H.R. 3633) is approved by both the U.S. House of Representatives "
    "and the U.S. Senate, and is signed into law no later than December 31, 2026, "
    "at 11:59 PM ET. If these conditions are not met by the deadline, the market "
    "resolves to \"No\"."
)

CLARITY_MARKET = Market(
    id="clarity-act-2026",
    title="Will the CLARITY act be signed into law in 2026?",
    description=CLARITY_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=365),
)

NOBEL_DESCRIPTION = (
    "This market will be settled based on the recipient of the 2026 Nobel "
    "Peace Prize officially announced by the Norwegian Nobel Committee. "
    "The listed person or entity that receives the 2026 Nobel Peace Prize "
    "will resolve to \"Yes\", and all other listed outcomes will resolve to "
    "\"No\". If the 2026 Nobel Peace Prize has not been officially announced "
    "by March 31, 2027, 11:59 PM ET, all listed outcomes will resolve to \"No\"."
)

NOBEL_MARKET = Market(
    id="nobel-peace-2026",
    title="Who will win the 2026 Nobel Peace Prize?",
    description=NOBEL_DESCRIPTION,
    options=["Yulia Navalnaya", "Volodymyr Zelenskyy", "UNRWA", "Pope Leo XIV", "Donald Trump"],
    close_date=date.today() + timedelta(days=365),
)

VINICIUS_DESCRIPTION = (
    "This market will settle based on the next team Vinicius Junior "
    "officially joins by September 1, 2026, at 11:59 PM ET. If he has not "
    "officially joined a new team by that deadline, the market will resolve "
    "to \"Real Madrid\". If he joins a team that is not included among the "
    "listed options, all listed teams on this market will resolve to \"No\"."
)

VINICIUS_MARKET = Market(
    id="vinicius-transfer-2026",
    title="Which team will Vinicius Junior join next?",
    description=VINICIUS_DESCRIPTION,
    options=["Real Madrid", "Arsenal"],
    close_date=date.today() + timedelta(days=365),
)


def make_ranked(text, url="https://congress.gov/bill/3633", source_type="primary", similarity=0.8):
    return RankedArticle(
        article=ArticleRef(url=url, title="t", source_type=source_type),
        text=text,
        similarity=similarity,
    )


def test_binary_market_resolves_yes_on_keyword_match():
    evidence = [make_ranked("The bill was signed into law by the President today.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"
    assert verdict.source_url == "https://congress.gov/bill/3633"


def test_binary_market_applies_stated_default_after_deadline():
    past_deadline_market = Market(
        id="clarity-act-2026",
        title=CLARITY_MARKET.title,
        description=CLARITY_DESCRIPTION,
        options=[],
        close_date=date.today() - timedelta(days=1),
    )
    verdict = decide(past_deadline_market, [])
    assert verdict.outcome == "NO"


def test_binary_market_unclear_when_evidence_inconclusive():
    evidence = [make_ranked("The committee discussed the bill's implications for markets.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_binary_market_no_evidence():
    verdict = decide(CLARITY_MARKET, [])
    assert verdict.outcome == "NO_EVIDENCE"


def test_multi_outcome_market_picks_matching_option():
    evidence = [make_ranked(
        "The Norwegian Nobel Committee announced that the prize is awarded to Pope Leo XIV.",
        url="https://nobelprize.org/announcement",
    )]
    verdict = decide(NOBEL_MARKET, evidence)
    assert verdict.outcome == "Pope Leo XIV"


def test_multi_outcome_market_unclear_when_no_option_matches():
    evidence = [make_ranked("The Nobel Committee will announce the winner next week.")]
    verdict = decide(NOBEL_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_multi_outcome_market_applies_stated_no_default_after_deadline():
    past_deadline_market = Market(
        id="nobel-peace-2026",
        title=NOBEL_MARKET.title,
        description=NOBEL_DESCRIPTION,
        options=NOBEL_MARKET.options,
        close_date=date.today() - timedelta(days=1),
    )
    verdict = decide(past_deadline_market, [])
    assert verdict.outcome == "NO"


def test_multi_outcome_market_matches_announcement_keyword_as_whole_word():
    evidence = [make_ranked(
        "Official: Arsenal wins the race for Vinicius Junior.",
        url="https://www.bbc.com/sport/1",
        source_type="credible_backup",
    )]
    verdict = decide(VINICIUS_MARKET, evidence)
    assert verdict.outcome == "Arsenal"


def test_multi_outcome_market_ignores_keyword_inside_a_longer_word():
    """"wins" must not fire on "Winston" / "winsome" — a real risk now that
    options include short common words like "Arsenal"."""
    evidence = [make_ranked(
        "Arsenal supporter Winston Reid offered a winsome take on the transfer.",
        url="https://www.bbc.com/sport/2",
        source_type="credible_backup",
    )]
    verdict = decide(VINICIUS_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_multi_outcome_market_ignores_option_inside_a_longer_word():
    evidence = [make_ranked(
        "Arsenalization of the transfer market wins few fans.",
        url="https://www.bbc.com/sport/3",
        source_type="credible_backup",
    )]
    verdict = decide(VINICIUS_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_binary_market_ignores_yes_keyword_inside_a_longer_word():
    evidence = [make_ranked("The bill was re-enactedly mischaracterised by pundits.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_binary_market_matches_yes_keyword_as_whole_word():
    evidence = [make_ranked("Congress enacted the measure this morning.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_multi_outcome_market_applies_stated_option_default_after_deadline():
    past_deadline_market = Market(
        id="vinicius-transfer-2026",
        title=VINICIUS_MARKET.title,
        description=VINICIUS_DESCRIPTION,
        options=VINICIUS_MARKET.options,
        close_date=date.today() - timedelta(days=1),
    )
    verdict = decide(past_deadline_market, [])
    assert verdict.outcome == "Real Madrid"
