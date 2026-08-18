# tests/test_verdict_engine.py
from datetime import date, timedelta
from unittest.mock import patch
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


DATE_THRESHOLD_DESCRIPTION = (
    "This market will resolve to \"Yes\" for the earliest listed date by "
    "which the event has occurred, and \"Yes\" for every later listed date "
    "as well. If the event has not occurred by a given date, that date's "
    "option resolves to \"No\" once that date has passed."
)

DATE_THRESHOLD_MARKET = Market(
    id="date-threshold-test",
    title="Will the event happen, by which date?",
    description=DATE_THRESHOLD_DESCRIPTION,
    options=["August 1, 2026", "September 1, 2026", "October 1, 2026"],
    close_date=date(2026, 10, 1),
)


def test_date_threshold_option_resolves_no_once_its_own_date_has_passed():
    # Freeze "today" at a point after the August option's date but before
    # the September/October ones, with no evidence found for any option.
    past_date_market = Market(
        id="date-threshold-test", title=DATE_THRESHOLD_MARKET.title,
        description=DATE_THRESHOLD_DESCRIPTION,
        options=["August 1, 2026", "September 1, 2026", "October 1, 2026"],
        close_date=date(2026, 10, 1),
    )
    with patch("resolution_finder.verdict_engine.date") as mock_date:
        mock_date.today.return_value = date(2026, 8, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        verdicts = decide(past_date_market, [])
    by_option = {v.option: v.outcome for v in verdicts}
    assert by_option["August 1, 2026"] == "NO"


def test_date_threshold_option_stays_unclear_before_its_own_date():
    with patch("resolution_finder.verdict_engine.date") as mock_date:
        mock_date.today.return_value = date(2026, 8, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        verdicts = decide(DATE_THRESHOLD_MARKET, [])
    by_option = {v.option: v.outcome for v in verdicts}
    assert by_option["September 1, 2026"] == "UNCLEAR"
    assert by_option["October 1, 2026"] == "UNCLEAR"


def test_date_threshold_option_stays_unclear_on_its_own_date_not_after():
    # Boundary check: the date's own day still counts as open.
    with patch("resolution_finder.verdict_engine.date") as mock_date:
        mock_date.today.return_value = date(2026, 8, 1)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        verdicts = decide(DATE_THRESHOLD_MARKET, [])
    by_option = {v.option: v.outcome for v in verdicts}
    assert by_option["August 1, 2026"] == "UNCLEAR"


def test_date_threshold_evidence_takes_priority_over_elapsed_default():
    evidence = [make_ranked(
        "The event was declared winner of the process on August 1, 2026.",
        url="https://www.bbc.com/x", source_type="credible_backup",
    )]
    # Reuse an ANNOUNCEMENT_KEYWORDS phrase ("winner of") near the option
    # text itself so the existing evidence-matching path (not the new
    # date-elapsed path) is what actually resolves this option.
    date_option_market = Market(
        id="date-threshold-test", title=DATE_THRESHOLD_MARKET.title,
        description=DATE_THRESHOLD_DESCRIPTION,
        options=["August 1, 2026"], close_date=date(2026, 10, 1),
    )
    with patch("resolution_finder.verdict_engine.date") as mock_date:
        mock_date.today.return_value = date(2026, 9, 1)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        verdicts = decide(date_option_market, evidence)
    assert verdicts[0].source_url == "https://www.bbc.com/x"


def test_named_entity_option_that_looks_like_a_date_word_is_not_misdetected():
    # A candidate literally named "August Wilson" must not be treated as a
    # date-threshold option just because it starts with a month name.
    named_market = Market(
        id="named-entity-test", title="Who will win the award?",
        description="This market resolves based on the award winner.",
        options=["August Wilson", "Toni Morrison"],
        close_date=date.today() + timedelta(days=365),
    )
    with patch("resolution_finder.verdict_engine.date") as mock_date:
        mock_date.today.return_value = date.today()
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        verdicts = decide(named_market, [])
    # Falls through to the existing named-entity path: no evidence, no
    # default -> single whole-market UNCLEAR/NO_EVIDENCE, NOT a per-option
    # NO from a misfired date parse.
    assert len(verdicts) == 1
    assert verdicts[0].option is None


def test_date_shaped_options_without_cumulative_language_are_not_cascaded():
    # Options are dates, but the description asks "on which date" (a single
    # exact-date question), not "by which date" (cumulative) -- must NOT
    # get the cascade treatment.
    exact_date_market = Market(
        id="exact-date-test", title="On which date will the event happen?",
        description="This market resolves to the single date on which the event occurs.",
        options=["August 1, 2026", "September 1, 2026"],
        close_date=date(2026, 10, 1),
    )
    with patch("resolution_finder.verdict_engine.date") as mock_date:
        mock_date.today.return_value = date(2026, 8, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        verdicts = decide(exact_date_market, [])
    # Falls through to the existing named-entity path (no "by"/"no later
    # than"/etc. language), so no per-option elapsed-NO fires here either.
    assert len(verdicts) == 1
    assert verdicts[0].option is None


BITCOIN_DESCRIPTION = (
    "This market will resolve to \"Yes\" if the price of Bitcoin (BTC) is "
    "above $64,000 on August 17, 2026, according to the Binance BTC/USDT "
    "reference price. Otherwise, this market will resolve to \"No\"."
)

BITCOIN_MARKET = Market(
    id="bitcoin-above-64k-on-august-17-2026",
    title="Will the price of Bitcoin be above $64,000 on August 17?",
    description=BITCOIN_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)

ETHEREUM_DESCRIPTION = (
    "This market will resolve to \"Yes\" if the price of Ethereum (ETH) is "
    "less than $1,400 on August 17, 2026. Otherwise, this market will "
    "resolve to \"No\"."
)

ETHEREUM_MARKET = Market(
    id="ethereum-below-1400-on-august-17-2026",
    title="Will the price of Ethereum be less than $1,400 on August 17?",
    description=ETHEREUM_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)

GOLD_DESCRIPTION = (
    "This market will resolve to \"Yes\" if Gold (XAUUSD) reaches a high of "
    "at least $4,400 in August 2026. Otherwise, this market will resolve to \"No\"."
)

GOLD_MARKET = Market(
    id="gold-reach-4400-in-august-2026",
    title="Will Gold (XAUUSD) hit $4,400 in August?",
    description=GOLD_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)


def test_numeric_threshold_market_resolves_yes_when_evidence_confirms_above_threshold():
    evidence = [make_ranked(
        "Bitcoin surged to $67,200 on Monday amid renewed institutional buying.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_numeric_threshold_market_resolves_no_when_evidence_contradicts_threshold():
    evidence = [make_ranked(
        "Bitcoin fell sharply to $58,400 on Monday as traders took profits.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome == "NO"


def test_numeric_threshold_market_handles_below_direction():
    evidence = [make_ranked(
        "Ethereum dropped to $1,150 on Monday, extending its weekly decline.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(ETHEREUM_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_numeric_threshold_market_handles_reach_at_least_phrasing():
    evidence = [make_ranked(
        "Gold prices hit $4,512 an ounce on Friday, a fresh all-time high.",
        url="https://www.cnbc.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(GOLD_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_numeric_threshold_market_stays_unclear_without_a_number_in_evidence():
    evidence = [make_ranked(
        "Bitcoin traders are watching the Fed decision closely this week.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_numeric_threshold_market_no_evidence_at_all():
    verdict = decide(BITCOIN_MARKET, [])
    assert verdict.outcome == "NO_EVIDENCE"


def test_numeric_threshold_market_ignores_number_about_a_different_asset():
    # Real risk this guards against: an article about Bitcoin also
    # mentions Ethereum's price -- must not be mistaken for Bitcoin's.
    evidence = [make_ranked(
        "While Bitcoin held steady, Ethereum climbed to $67,000 in a rare "
        "moment of ETH outperformance.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome != "YES"


def test_numeric_threshold_market_ignores_hedged_number():
    evidence = [make_ranked(
        "Analysts say Bitcoin could reach $70,000 if the rally continues.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome != "YES"


def test_numeric_threshold_extracts_the_most_recently_stated_number():
    # "up from X to Y" states the current value last -- must use $64,500,
    # not the earlier $61,000 mentioned in the same sentence.
    evidence = [make_ranked(
        "Bitcoin climbed from $61,000 to $64,500 over the trading session.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_non_numeric_binary_market_unaffected():
    # Regression guard: CLARITY Act (legislative binary, no $ threshold in
    # its own text) must be completely unaffected -- routed to the
    # existing _decide_binary path, not misdetected as threshold-shaped.
    evidence = [make_ranked("The bill was signed into law by the President today.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"
    assert "signed into law" in verdict.evidence_snippet
