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


def test_binary_market_ignores_yes_keyword_inside_a_longer_word():
    evidence = [make_ranked("The bill was re-enactedly mischaracterised by pundits.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_binary_market_matches_yes_keyword_as_whole_word():
    evidence = [make_ranked("Congress enacted the measure this morning.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_binary_market_ignores_keyword_match_about_unrelated_entity():
    # Real false positive found via live data: an article about the CLARITY
    # Act cites the GENIUS Act (an unrelated law) as a comparison, and that
    # other law's "signed into law" sentence must not count as evidence for
    # the CLARITY Act.
    evidence = [make_ranked(
        "The CLARITY Act remains stalled in the Senate. Agency guidance is "
        "more easily reversed by the next administration than statute. The "
        "GENIUS Act's experience is instructive: signed into law in July "
        "2025, its agencies missed their one-year rulemaking deadline."
    )]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome != "YES"


def test_binary_market_ignores_hedged_keyword_match():
    evidence = [make_ranked(
        "The CLARITY Act, if enacted, would represent a significant shift "
        "in digital asset regulation."
    )]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome != "YES"


def test_binary_market_still_resolves_yes_on_genuine_match():
    evidence = [make_ranked(
        "The CLARITY Act was signed into law by the President on Tuesday."
    )]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"
    assert "CLARITY Act was signed into law" in verdict.evidence_snippet


def test_binary_market_ignores_unrelated_entity_across_an_abbreviation():
    # Regression guard for the sentence splitter: a naive split on "[.!?]\s+"
    # breaks this sentence at "U.S." and leaves "signed into law" in a fragment
    # with no "GENIUS" in it, so the wrong-subject veto never fires and the
    # original production false positive returns. This domain is US
    # legislation, so "U.S. Senate" / "H.R. ####" are everywhere.
    evidence = [make_ranked(
        "The GENIUS Act was passed by the U.S. Senate and signed into law in July 2025."
    )]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome != "YES"


def test_binary_market_resolves_yes_on_keyword_match_still_passes_without_subject_mention():
    # Existing test fixture (Task 9), re-asserted here: a sentence that names
    # no other entity should still match even without repeating the market's
    # own subject name — real articles use pronouns/short references after
    # establishing context once.
    evidence = [make_ranked("The bill was signed into law by the President today.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"


INTERNATIONAL_DESCRIPTION = (
    "This market will resolve based on the team officially recognized as the "
    "champion of The International 2026. The winning team's market will "
    "resolve to \"Yes\". All other team markets will resolve to \"No\". If "
    "The International 2026 champion has not been officially determined by "
    "September 6, 2026, 11:59 PM ET, or if the champion is not one of the "
    "listed teams, all markets will resolve to \"No\"."
)

INTERNATIONAL_MARKET = Market(
    id="international-2026-champion",
    title="The International 2026 Champion",
    description=INTERNATIONAL_DESCRIPTION,
    options=["Team Spirit", "Aurora Gaming"],
    close_date=date.today() + timedelta(days=365),
)

OSUN_DESCRIPTION = (
    "This market will resolve according to the listed candidate who wins "
    "the 2026 Osun State gubernatorial elections. If the results are not "
    "known definitively by June 30, 2027, 11:59 PM ET, this market will "
    "resolve to \"Other\"."
)

OSUN_MARKET = Market(
    id="osun-state-governor-2026",
    title="Osun State Gubernatorial Election Winner",
    description=OSUN_DESCRIPTION,
    options=["Ademola Adeleke", "Taofeek Adeleke"],
    close_date=date.today() + timedelta(days=365),
)


def test_multi_outcome_market_full_sweep_on_confirmed_winner():
    # Requirement: once an overall winner is confirmed, every option gets an
    # explicit verdict, not just the winner.
    evidence = [make_ranked(
        "The Norwegian Nobel Committee announced that the prize is awarded to Pope Leo XIV.",
        url="https://nobelprize.org/announcement",
    )]
    verdicts = decide(NOBEL_MARKET, evidence)
    assert isinstance(verdicts, list)
    by_option = {v.option: v.outcome for v in verdicts}
    assert by_option == {
        "Yulia Navalnaya": "NO", "Volodymyr Zelenskyy": "NO", "UNRWA": "NO",
        "Pope Leo XIV": "YES", "Donald Trump": "NO",
    }
    winner = next(v for v in verdicts if v.option == "Pope Leo XIV")
    assert winner.source_url == "https://nobelprize.org/announcement"


def test_multi_outcome_market_unclear_when_no_option_matches():
    evidence = [make_ranked("The Nobel Committee will announce the winner next week.")]
    verdicts = decide(NOBEL_MARKET, evidence)
    assert len(verdicts) == 1
    assert verdicts[0].outcome == "UNCLEAR"
    assert verdicts[0].option is None


def test_multi_outcome_market_applies_stated_no_default_after_deadline():
    past_deadline_market = Market(
        id="nobel-peace-2026", title=NOBEL_MARKET.title, description=NOBEL_DESCRIPTION,
        options=NOBEL_MARKET.options, close_date=date.today() - timedelta(days=1),
    )
    verdicts = decide(past_deadline_market, [])
    assert {v.option for v in verdicts} == set(NOBEL_MARKET.options)
    assert all(v.outcome == "NO" for v in verdicts)


def test_multi_outcome_market_matches_announcement_keyword_as_whole_word():
    evidence = [make_ranked(
        "Official: Arsenal wins the race for Vinicius Junior.",
        url="https://www.bbc.com/sport/1", source_type="credible_backup",
    )]
    verdicts = decide(VINICIUS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Arsenal": "YES", "Real Madrid": "NO"}


def test_multi_outcome_market_ignores_keyword_inside_a_longer_word():
    """"wins" must not fire on "Winston" / "winsome"."""
    evidence = [make_ranked(
        "Arsenal supporter Winston Reid offered a winsome take on the transfer.",
        url="https://www.bbc.com/sport/2", source_type="credible_backup",
    )]
    verdicts = decide(VINICIUS_MARKET, evidence)
    assert len(verdicts) == 1
    assert verdicts[0].outcome == "UNCLEAR"


def test_multi_outcome_market_ignores_option_inside_a_longer_word():
    evidence = [make_ranked(
        "Arsenalization of the transfer market wins few fans.",
        url="https://www.bbc.com/sport/3", source_type="credible_backup",
    )]
    verdicts = decide(VINICIUS_MARKET, evidence)
    assert len(verdicts) == 1
    assert verdicts[0].outcome == "UNCLEAR"


def test_multi_outcome_market_applies_stated_option_default_after_deadline():
    past_deadline_market = Market(
        id="vinicius-transfer-2026", title=VINICIUS_MARKET.title, description=VINICIUS_DESCRIPTION,
        options=VINICIUS_MARKET.options, close_date=date.today() - timedelta(days=1),
    )
    verdicts = decide(past_deadline_market, [])
    assert {v.option: v.outcome for v in verdicts} == {"Real Madrid": "YES", "Arsenal": "NO"}


def test_multi_outcome_market_resolves_yes_on_contract_renewal_language():
    # Real gap found live: NYTimes/The Athletic on Vinicius Junior described
    # him staying at Real Madrid as "reached an agreement to renew his
    # contract", not any of the old ANNOUNCEMENT_KEYWORDS.
    evidence = [make_ranked(
        "Real Madrid have confirmed Vinicius Junior signed a new contract, "
        "ending Arsenal's interest in the forward.",
        url="https://www.nytimes.com/athletic/1", source_type="credible_backup",
    )]
    verdicts = decide(VINICIUS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Real Madrid": "YES", "Arsenal": "NO"}


def test_multi_outcome_market_resolves_yes_on_winner_of_phrase():
    # Real gap found live: BBC Pidgin on the Osun election used "declare ...
    # winner of ..." — "winner of" wasn't in ANNOUNCEMENT_KEYWORDS.
    evidence = [make_ranked(
        "INEC declare Ademola Adeleke winner of the Osun State election.",
        url="https://www.bbc.com/pidgin/1", source_type="credible_backup",
    )]
    verdicts = decide(OSUN_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {
        "Ademola Adeleke": "YES", "Taofeek Adeleke": "NO",
    }


def test_multi_outcome_market_option_independently_resolves_no_on_elimination():
    # Real scenario: The International 2026 -- Aurora Gaming eliminated,
    # tournament champion still undecided. Must resolve just that option,
    # not force a whole-market guess.
    evidence = [make_ranked(
        "Aurora Gaming was eliminated from The International 2026 in the lower bracket.",
        url="https://www.dexerto.com/dota2/1", source_type="credible_backup",
    )]
    verdicts = decide(INTERNATIONAL_MARKET, evidence)
    assert len(verdicts) == 1
    assert verdicts[0].option == "Aurora Gaming"
    assert verdicts[0].outcome == "NO"


def test_multi_outcome_market_elimination_does_not_claim_the_other_option_won():
    # One option confirmed lost is not evidence the other one won.
    evidence = [make_ranked(
        "Aurora Gaming was eliminated from The International 2026 in the lower bracket.",
        url="https://www.dexerto.com/dota2/1", source_type="credible_backup",
    )]
    verdicts = decide(INTERNATIONAL_MARKET, evidence)
    assert "Team Spirit" not in {v.option for v in verdicts}


def test_multi_outcome_market_named_default_outside_option_list_resolves_all_no():
    # Real scenario: Osun's "resolve to 'Other'" default. "Other" matches
    # none of the listed candidates, so every listed option resolves No --
    # also exercises the new "not known" DEFAULT_OUTCOME_PATTERN trigger.
    past_deadline_market = Market(
        id="osun-state-governor-2026", title=OSUN_MARKET.title, description=OSUN_DESCRIPTION,
        options=OSUN_MARKET.options, close_date=date.today() - timedelta(days=1),
    )
    verdicts = decide(past_deadline_market, [])
    assert {v.option for v in verdicts} == set(OSUN_MARKET.options)
    assert all(v.outcome == "NO" for v in verdicts)
