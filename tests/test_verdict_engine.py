# tests/test_verdict_engine.py
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
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

WAR_POWERS_DESCRIPTION = (
    "This market will resolve to \"Yes\" if both the U.S. House of "
    "Representatives and the U.S. Senate pass the same war powers "
    "resolution by June 30, 2026. Otherwise, this market will resolve "
    "to \"No\"."
)

WAR_POWERS_MARKET = Market(
    id="congress-passes-iran-war-powers-resolution-by-june-30",
    title="Congress passes Iran war powers resolution by June 30?",
    description=WAR_POWERS_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
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


def test_binary_market_ignores_hypothetical_keyword_match_that_evades_the_hedge_guard():
    # Real bug found live (2026-08-23): BINARY_YES_KEYWORDS deciding the
    # outcome directly, with no verification step, means ANY sentence
    # containing an exact keyword phrase resolves YES -- including a
    # purely explanatory/hypothetical sentence that the hedge guard
    # doesn't catch. "would need to be signed into law" contains the
    # literal phrase "signed into law", but NEGATION_HEDGE_WORDS only has
    # the exact phrase "would be" (not "would need to be" -- the "need
    # to" in between means the substring check misses it), so this
    # sailed straight through to a confident, wrong YES on real
    # production code (confidence 0.8) before this fix.
    evidence = [make_ranked(
        "The bill would need to be signed into law by the president to "
        "take effect, following approval by both chambers."
    )]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome != "YES"


def test_binary_market_resolves_yes_on_senate_passage_for_a_resolution_market():
    # Real gap found live 2026-08-18: BINARY_YES_KEYWORDS was entirely
    # bill-SIGNING vocabulary ("signed into law", "enacted"), which
    # structurally cannot confirm a market like this one, whose Yes
    # condition is vote PASSAGE by both chambers -- a war powers resolution
    # is never signed into law at all.
    evidence = [make_ranked("The Senate passed the war powers resolution in a 51-47 vote on Thursday.")]
    verdict = decide(WAR_POWERS_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_binary_market_resolves_yes_on_house_passage_for_a_resolution_market():
    evidence = [make_ranked("The House passed the resolution by a vote of 221-206 on Wednesday.")]
    verdict = decide(WAR_POWERS_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_binary_market_resolves_yes_on_both_chambers_passage_phrasing():
    evidence = [make_ranked("The measure cleared both chambers after months of negotiation.")]
    verdict = decide(WAR_POWERS_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_binary_market_does_not_treat_procedural_advancement_as_final_passage():
    # The real live evidence found for this exact market: a procedural vote
    # to ADVANCE a resolution is not the same as the resolution PASSING --
    # the new vocabulary must not be so broad that it blurs this distinction.
    evidence = [make_ranked(
        "The Senate advanced a war-powers resolution on Tuesday that would "
        "end the Iran war unless Trump obtains Congress' authorization, a "
        "rare rebuke 80 days after strikes began. The vote on a procedural "
        "measure passed 51-47."
    )]
    verdict = decide(WAR_POWERS_MARKET, evidence)
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

# Real 8-option shape (data/markets.json), used specifically for the
# "wins over" bare-keyword bug below -- "BoomBoys" and its real opponents
# ("OG", "Iron Wing") only make sense with the full real option list.
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

LAKERS_ROCKETS_DESCRIPTION = (
    "This market will resolve to \"Lakers\" if the Los Angeles Lakers win "
    "the 2026 NBA Playoffs First Round series between the Los Angeles "
    "Lakers and Houston Rockets. This market will resolve to \"Rockets\" "
    "if the Houston Rockets win."
)

LAKERS_ROCKETS_MARKET = Market(
    id="nba-playoffs-who-will-win-series-lakers-vs-rockets",
    title="NBA Playoffs: Who Will Win Series? - Lakers vs. Rockets",
    description=LAKERS_ROCKETS_DESCRIPTION,
    options=["Lakers", "Rockets"],
    close_date=date.today() + timedelta(days=30),
)

# Same market, options listed in the OPPOSITE order -- the head-to-head
# winner must come from the phrase's own direction, not list position.
LAKERS_ROCKETS_MARKET_REVERSED_OPTIONS = Market(
    id="nba-playoffs-who-will-win-series-lakers-vs-rockets-reversed",
    title=LAKERS_ROCKETS_MARKET.title,
    description=LAKERS_ROCKETS_DESCRIPTION,
    options=["Rockets", "Lakers"],
    close_date=date.today() + timedelta(days=30),
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


def test_multi_outcome_market_resolves_yes_on_head_to_head_victory_phrase():
    # Real gap found live (2026-08-23): a real NBA playoff market's top
    # evidence was "The Los Angeles Lakers' first-round playoff victory
    # over the Houston Rockets..." -- neither ANNOUNCEMENT_KEYWORDS
    # ("wins", "winner of", ...) nor ELIMINATION_KEYWORDS covers "victory
    # over" phrasing, so this landed on UNCLEAR despite clearly confirming
    # the winner.
    evidence = [make_ranked(
        "The Los Angeles Lakers' first-round playoff victory over the "
        "Houston Rockets may have accomplished more than advancing the "
        "franchise to the Western Conference semifinals.",
        url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
    )]
    verdicts = decide(LAKERS_ROCKETS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Lakers": "YES", "Rockets": "NO"}


def test_multi_outcome_head_to_head_winner_is_order_independent():
    # The exact same evidence must still correctly crown Lakers even when
    # market.options lists Rockets first -- proves the fix reads direction
    # from the phrase itself ("winner_option ... verb ... loser_option"),
    # not from list position. This is the specific failure mode a naive
    # ANNOUNCEMENT_KEYWORDS addition would have had: both team names sit
    # within the same 40-char proximity window as "victory over".
    evidence = [make_ranked(
        "The Los Angeles Lakers' first-round playoff victory over the "
        "Houston Rockets may have accomplished more than advancing the "
        "franchise to the Western Conference semifinals.",
        url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
    )]
    verdicts = decide(LAKERS_ROCKETS_MARKET_REVERSED_OPTIONS, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Lakers": "YES", "Rockets": "NO"}


def test_multi_outcome_market_resolves_yes_on_head_to_head_defeated_verb():
    evidence = [make_ranked(
        "The Lakers defeated the Rockets in six games to advance.",
        url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
    )]
    verdicts = decide(LAKERS_ROCKETS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Lakers": "YES", "Rockets": "NO"}


def test_multi_outcome_market_ignores_head_to_head_result_from_a_different_year():
    # Real bug found live (2026-08-23): a market about the 2026 Lakers-
    # Rockets series wrongly resolved YES on real historical evidence
    # about their 2009 series -- same team names (a recurring matchup),
    # completely different year, and the wrong-subject veto can't help
    # since the named entities are identical either way.
    evidence = [make_ranked(
        "The Lakers defeated the Rockets in seven games during the 2009 "
        "Western Conference Semifinals, advancing to face the Nuggets.",
        url="https://example.com/old-article", source_type="credible_backup",
    )]
    verdicts = decide(LAKERS_ROCKETS_MARKET, evidence)
    assert all(v.outcome != "YES" for v in verdicts)


def test_multi_outcome_market_ignores_elimination_phrased_as_a_relative_recurring_range():
    # Real bug found live (2026-08-23, discovered via a from-scratch NLI-
    # only experiment with no keyword gates at all -- confirmed to
    # reproduce against the real production decide() too, not just the
    # experiment): the sentence names no explicit year at all, so
    # _sentence_mentions_conflicting_year can't help, and NLI confidently
    # (0.998-0.999) read "knocked ... out of the playoffs" as a clean
    # elimination regardless of when -- wrongly eliminating BOTH options.
    # A real, unrelated player's free-agency history, not a report on the
    # market's own 2026 series.
    evidence = [make_ranked(
        "In doing so, he left the Lakers and bypassed an opportunity to "
        "join the Golden State Warriors, and those are the two West "
        "rivals that knocked the Rockets out of the playoffs the past "
        "two years.",
        url="https://example.com/free-agency", source_type="credible_backup_secondary",
    )]
    verdicts = decide(LAKERS_ROCKETS_MARKET, evidence)
    assert all(v.outcome != "NO" for v in verdicts)


def test_multi_outcome_market_ignores_pre_tournament_preview_as_a_winner_confirmation():
    # Real bug found live (2026-08-23): real evidence for the real
    # International 2026 market, "Defending champions Team Falcons are
    # raring to retain the Aegis at Dota 2 TI 2026, but they face a
    # stacked field that includes TEAM VISION..." -- a pre-tournament
    # PREVIEW, not a result -- scored 0.926 for "Team Falcons has won",
    # well above threshold, wrongly crowning them champion. Made worse
    # live: separate real evidence in the same batch ("Team Liquid 2-1
    # Team Falcons ... Team Falcons have been eliminated") directly
    # contradicts this. "Defending champions" (their PAST title) plus
    # "raring to retain" (future intent) reads as strong lexical
    # confirmation to the model despite describing an undecided outcome.
    evidence = [make_ranked(
        "Defending champions Team Falcons are raring to retain the Aegis "
        "at Dota 2 TI 2026, but they face a stacked field that includes "
        "TEAM VISION (aka PARIVISION), Team Liquid, Team Spirit, Iron Wing "
        "(aka 1w Team).",
        url="https://www.gosugamers.net/dota2/x", source_type="credible_backup_secondary",
    )]
    verdicts = decide(INTERNATIONAL_MARKET_FULL, evidence)
    assert not any(v.option == "Team Falcons" and v.outcome == "YES" for v in verdicts)


def test_multi_outcome_market_still_resolves_yes_when_evidence_has_no_year():
    # Safety check: the real, actual evidence that motivated the
    # head-to-head fix (no year mentioned at all) must still work --
    # the new year-conflict guard must not be a blanket "any year
    # anywhere" filter, only a check on sentences that already matched.
    evidence = [make_ranked(
        "The Los Angeles Lakers' first-round playoff victory over the "
        "Houston Rockets may have accomplished more than advancing the "
        "franchise to the Western Conference semifinals.",
        url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
    )]
    verdicts = decide(LAKERS_ROCKETS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Lakers": "YES", "Rockets": "NO"}


def test_multi_outcome_market_still_resolves_yes_when_evidence_states_matching_year():
    # The matching year (2026, same as the market's own) must not be
    # treated as a conflict just because the check fires.
    evidence = [make_ranked(
        "The Lakers defeated the Rockets in the 2026 Western Conference "
        "First Round series.",
        url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
    )]
    verdicts = decide(LAKERS_ROCKETS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Lakers": "YES", "Rockets": "NO"}


def test_multi_outcome_market_ignores_bare_wins_in_a_win_loss_record_summary():
    # Real bug found live (2026-08-23), asked for by name ("test the
    # international again"): "BoomBoys posted a 2-3 win-loss record with
    # wins over OG and Iron Wing, but suffered consecutive losses..."
    # wrongly crowned BoomBoys tournament CHAMPION off the bare "wins"
    # keyword -- a losing overall record (2-3), with "wins over X and Y"
    # describing individual match results within the tournament, not
    # overall victory. Neither OG nor Iron Wing is even a listed option
    # in this market.
    evidence = [make_ranked(
        "BoomBoys posted a 2-3 win-loss record with wins over OG and "
        "Iron Wing, but suffered consecutive losses against TEAM VISION, "
        "Aurora Gaming, and Team Falcons.",
        url="https://www.gosugamers.net/dota2/x", source_type="credible_backup_secondary",
    )]
    verdicts = decide(INTERNATIONAL_MARKET_FULL, evidence)
    assert all(v.outcome != "YES" for v in verdicts)


def test_multi_outcome_market_still_matches_bare_wins_for_genuine_victory():
    # Safety check: the existing, already-passing "Arsenal wins the race
    # for Vinicius Junior" case must still work -- the fix must not
    # blanket-suppress bare "wins", only the "wins over"/"win over"
    # head-to-head phrasing specifically.
    evidence = [make_ranked(
        "Official: Arsenal wins the race for Vinicius Junior.",
        url="https://www.bbc.com/sport/1", source_type="credible_backup",
    )]
    verdicts = decide(VINICIUS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Arsenal": "YES", "Real Madrid": "NO"}


def test_multi_outcome_market_head_to_head_still_works_via_wins_over_phrase():
    # "wins over" between two LISTED options must still correctly
    # resolve directionally, via _match_head_to_head_winner -- only the
    # generic ANNOUNCEMENT_KEYWORDS proximity path is suppressed for this
    # phrase, not head-to-head confirmation entirely.
    evidence = [make_ranked(
        "The Lakers' string of wins over the Rockets this postseason "
        "sealed the series.",
        url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
    )]
    verdicts = decide(LAKERS_ROCKETS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Lakers": "YES", "Rockets": "NO"}


def test_multi_outcome_market_ignores_head_to_head_result_from_a_different_phase():
    # Same year, but a DIFFERENT meeting within it: two teams can play
    # each other more than once in a season (a regular-season game and a
    # separate playoff series). The year-conflict guard alone can't catch
    # this since the year is identical -- confirmed by construction
    # (2026-08-23): a market specifically about the 2026 PLAYOFF series
    # wrongly resolved YES on a same-year REGULAR SEASON game.
    evidence = [make_ranked(
        "The Lakers defeated the Rockets 118-112 in a regular season "
        "game on Christmas Day, 2026.",
        url="https://example.com/regular-season-game", source_type="credible_backup",
    )]
    verdicts = decide(LAKERS_ROCKETS_MARKET, evidence)
    assert all(v.outcome != "YES" for v in verdicts)


def test_multi_outcome_market_still_resolves_yes_when_evidence_states_matching_phase():
    # The market's own phase (playoffs) stated explicitly in evidence
    # must not be treated as a conflict just because the check fires.
    evidence = [make_ranked(
        "The Lakers defeated the Rockets in the playoffs to advance to "
        "the Western Conference semifinals.",
        url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
    )]
    verdicts = decide(LAKERS_ROCKETS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Lakers": "YES", "Rockets": "NO"}


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


def test_multi_outcome_market_ignores_single_game_loss_as_elimination():
    # Mirror image of the "wins over" bug -- user explicitly asked for
    # this "vice versa" (2026-08-23): losing ONE game/match does not mean
    # eliminated from the tournament in most formats (a best-of-N series,
    # a group stage, a double-elimination bracket all let a team lose
    # individual games and still advance or ultimately win).
    evidence = [make_ranked(
        "BoomBoys lost to Team Falcons in Game 2, but lead the series 2-1.",
        url="https://www.dexerto.com/dota2/1", source_type="credible_backup",
    )]
    verdicts = decide(INTERNATIONAL_MARKET_FULL, evidence)
    assert not any(v.option == "BoomBoys" and v.outcome == "NO" for v in verdicts)


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

BOX_OFFICE_DESCRIPTION = (
    "This market will resolve to \"Yes\" if \"The Odyssey\" grosses at "
    "least $21,000,000 in its fifth weekend of release. Otherwise, this "
    "market will resolve to \"No\"."
)

BOX_OFFICE_MARKET = Market(
    id="the-odyssey-5th-weekend-box-office",
    title="Will \"The Odyssey\" 5th Weekend Box Office be at least $21,000,000?",
    description=BOX_OFFICE_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)

SALES_DESCRIPTION = (
    "This market will resolve to \"Yes\" if the game sells at least "
    "1,000,000 copies by year end. Otherwise, this market will resolve to \"No\"."
)

SALES_MARKET = Market(
    id="game-reach-1m-sales",
    title="Will the game reach 1,000,000 copies sold?",
    description=SALES_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)

# Real bug found live (2026-08-23): a genuine 5-category classification
# market (options=[], asking specifically about ONE category) with no
# threshold phrasing in its own title, but whose description -- explaining
# an unrelated exception branch -- happens to contain a "below N" phrase.
TYPHOON_DESCRIPTION = (
    "This market resolves to the intensity category corresponding to the "
    "maximum sustained wind speed shown in the applicable JMA advisory. "
    "The outcome categories are: \"Tropical Storm\" (34-47 kt), \"Typhoon\" "
    "(64-84 kt), \"Very Strong Typhoon\" (85-104 kt), and \"Violent "
    "Typhoon\" (105+ kt). If the applicable advisory classifies the "
    "system as a tropical depression (below 34 kt), the crossing does "
    "not count and the market resolves to \"No Qualifying Landfall\"."
)

TYPHOON_MARKET = Market(
    id="will-typhoon-dolphin-be-a-very-strong-typhoon-at-japan-landfall",
    title="Will Typhoon Dolphin be a \"Very Strong Typhoon\" at Japan landfall?",
    description=TYPHOON_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)

# A market whose title alone has no threshold phrasing (a vague human-
# readable question), but whose description's OPENING sentence states the
# real condition directly -- the case the title-only version of the fix
# would have missed.
DESC_ONLY_THRESHOLD_DESCRIPTION = (
    "This market will resolve to \"Yes\" if the app reaches at least "
    "5,000,000 downloads by the close date. Otherwise, this market will "
    "resolve to \"No\". Some unrelated later text mentions a completely "
    "different figure, like a $10 signup bonus offered below the 100th "
    "download milestone, purely for promotional context."
)

DESC_ONLY_THRESHOLD_MARKET = Market(
    id="will-the-app-hit-a-download-milestone",
    title="Will the app hit its download milestone?",
    description=DESC_ONLY_THRESHOLD_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)


def test_threshold_condition_falls_back_to_first_sentence_of_description():
    evidence = [make_ranked(
        "The app has now surpassed 6,000,000 downloads worldwide, the "
        "developer announced.",
        url="https://example.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(DESC_ONLY_THRESHOLD_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_threshold_condition_ignores_unrelated_number_in_description():
    # The bare "below 34 kt" in the description describes a DIFFERENT
    # outcome branch (an exception clause), not the market's own Yes/No
    # condition -- must not hijack the market into the numeric-threshold
    # path. Real evidence here (a building-damage count, unrelated to
    # wind speed) confirmed the live bug: comparing 14,000 against a
    # bogus threshold of 34 wrongly resolved NO with high confidence.
    evidence = [make_ranked(
        "Typhoon Dolphin has hit Japan's southern island of Okinawa, "
        "injuring five people and cutting power to 14,000 buildings.",
        url="https://www.aljazeera.com/x", source_type="credible_backup",
    )]
    verdict = decide(TYPHOON_MARKET, evidence)
    assert verdict.outcome != "NO"


def test_threshold_condition_recognizes_number_or_more_phrasing():
    # Real bug found live (2026-08-23): _THRESHOLD_CONDITION_PATTERNS only
    # recognized trigger-word-THEN-number phrasing ("at least X", "above
    # X"), not number-THEN-trigger-word phrasing ("14 or more goals") --
    # the real market's own real description. This market's real evidence
    # ("Just Fontaine's incredible record that still stands") never even
    # reached the numeric-threshold decide function at all before this
    # fix -- confirmed by checking _extract_threshold_condition directly.
    from resolution_finder.verdict_engine import _extract_threshold_condition
    assert _extract_threshold_condition(WORLD_CUP_REAL_MARKET) == ("up", 14.0)


def test_threshold_condition_reads_past_a_leading_note_sentence():
    # Real bug found live (2026-08-23): the real market's description
    # opens with a "Note: Current record 13 goals (...)." sentence before
    # the actual Yes/No condition ("...scores 14 or more goals...") --
    # _first_sentence only ever looks at the FIRST sentence, so the real
    # condition sentence (the second one) was invisible to threshold
    # detection entirely.
    evidence = [make_ranked(
        "Just Fontaine's incredible record that still stands",
        url="https://www.beinsports.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(WORLD_CUP_REAL_MARKET, evidence)
    assert verdict.outcome == "NO"


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


def test_numeric_threshold_market_confirms_no_from_plain_english_with_no_number():
    # Real gap found live (2026-08-23): _decide_numeric_threshold had NO
    # semantic verification at all, unlike every other decide function --
    # purely literal number-extraction. Real evidence for this exact
    # market, headlined "Just Fontaine's incredible record that still
    # stands", plainly confirms the record was NOT broken, but contains
    # no digit at all for the extraction regex to find, so it fell
    # through to UNCLEAR despite being an unambiguous NO in plain English.
    evidence = [make_ranked(
        "Just Fontaine's incredible record that still stands",
        url="https://www.beinsports.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(WORLD_CUP_REAL_MARKET, evidence)
    assert verdict.outcome == "NO"


def test_numeric_threshold_market_stays_unclear_on_ambiguous_prose_with_no_number():
    # Safety check: the new semantic-NO check must not fire on a sentence
    # that doesn't actually confirm anything either way -- this exact
    # sentence is an existing regression guard (test above) that must
    # keep passing.
    evidence = [make_ranked(
        "Bitcoin traders are watching the Fed decision closely this week.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_numeric_threshold_market_ignores_a_ratio_number_as_the_price():
    # Real bug found live (2026-08-23): real evidence for the real Bitcoin
    # $64,000 market, "Binance Bitcoin volume ratio hits record as futures
    # outweigh spot eight times over ... The ratio now stands at 7.82,
    # meaning that futures volume outweighs spot nearly eight times
    # over." -- _extract_latest_number grabbed the 7.82 (a futures-to-spot
    # VOLUME RATIO, a completely different metric) and compared it against
    # the real $64,000 PRICE threshold, wrongly resolving NO on a market
    # whose real ground truth is Yes.
    evidence = [make_ranked(
        "Binance Bitcoin volume ratio hits record as futures outweigh spot "
        "eight times over. The ratio now stands at 7.82, meaning that "
        "futures volume outweighs spot nearly eight times over.",
        url="https://cointelegraph.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome != "NO"


def test_numeric_threshold_market_ignores_unrelated_number_in_vague_records_reference():
    # Real regression found live (2026-08-23), same eval batch as the fix
    # above: real evidence for this exact real market, "NEED TO KNOW -
    # Several records were broken at the 2026 World Cup - ... The 2026
    # World Cup made history before the tournament was even underway when
    # a record 48 teams were invited to participate." -- the number-
    # extraction loop grabbed the unrelated "48" (team invite count, a
    # DIFFERENT record from the one this market asks about) and wrongly
    # compared it against the real 13-goal threshold, resolving YES.
    evidence = [make_ranked(
        "NEED TO KNOW\n- Several records were broken at the 2026 World Cup\n"
        "- Cape Verde is the smallest nation to ever make it to the knockout "
        "stage of the tournament\n- France's Kylian Mbappe became the "
        "all-time leading goal scorer in men's World Cup history\n"
        "The 2026 World Cup made history before the tournament was even "
        "underway when a record 48 teams were invited to participate.",
        url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(WORLD_CUP_REAL_MARKET, evidence)
    assert verdict.outcome != "YES"


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


def test_threshold_number_does_not_consume_next_word_as_magnitude_suffix():
    # Real bug found live (2026-08-22) while testing the bare-year fix
    # above: the single-letter "k"/"m"/"b" magnitude alternative has no
    # word-boundary guard, so a number followed by a space and an
    # unrelated word starting with one of those letters gets that word's
    # first letter spuriously read as a magnitude suffix. Without the
    # fix, "$50 before" misparses as $50 * 1e9 (the "b" from "before"),
    # wrongly clearing the $64,000 threshold; correctly read as plain 50,
    # it stays far below it.
    evidence = [make_ranked(
        "Bitcoin trading volume dipped slightly, with the price at $50 "
        "before the announcement.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome == "NO"


def test_numeric_threshold_ignores_bare_year_in_unrelated_sentence():
    # Real bug found live (2026-08-22): a box-office market's ranked
    # evidence was unrelated homepage boilerplate mentioning a bare year
    # ("...making his first return since 2023's Plane"), and the year 2023
    # was wrongly picked up as the dollar figure -- 2023 < $21,000,000, so
    # the market resolved NO despite there being no real box-office number
    # in the sentence at all. Must fall through to UNCLEAR instead of
    # guessing from an unrelated year.
    evidence = [make_ranked(
        "The movie originally premiered back in 2023 before expanding "
        "wide this year.",
        url="https://www.the-numbers.com/", source_type="primary",
    )]
    verdict = decide(BOX_OFFICE_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_numeric_threshold_still_extracts_bare_non_dollar_count():
    # Distinguishing a bare YEAR from a bare legitimate count matters: a
    # non-dollar numeric-threshold market (sales/vote-count/etc.) has a
    # real threshold figure with no "$" sign or magnitude suffix at all,
    # and must not be rejected just because it lacks one.
    evidence = [make_ranked(
        "The game sold 1,200,000 copies in its first week, publishers said.",
        url="https://www.gamesindustry.biz/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(SALES_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_numeric_threshold_prefers_real_count_over_a_trailing_year():
    # Both a real count and a year can appear in the same sentence -- the
    # year must be skipped even when it comes LAST (the position
    # _extract_latest_number normally trusts most), falling back to the
    # genuine count earlier in the sentence instead.
    evidence = [make_ranked(
        "The game sold 1,200,000 copies since its 2023 launch.",
        url="https://www.gamesindustry.biz/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(SALES_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_non_numeric_binary_market_unaffected():
    # Regression guard: CLARITY Act (legislative binary, no $ threshold in
    # its own text) must be completely unaffected -- routed to the
    # existing _decide_binary path, not misdetected as threshold-shaped.
    evidence = [make_ranked("The bill was signed into law by the President today.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"
    assert "signed into law" in verdict.evidence_snippet


SANOFI_DESCRIPTION = (
    "This market will resolve to \"Yes\" if the FDA approves Sanofi's "
    "Subcutaneous Sarclisa by the specified date. Otherwise, this market "
    "will resolve to \"No\"."
)

SANOFI_MARKET = Market(
    id="fda-approves-sanofi-subcutaneous-sarclisa",
    title="FDA approves Sanofi's Subcutaneous Sarclisa?",
    description=SANOFI_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)


@patch("resolution_finder.verdict_engine.util.cos_sim")
@patch("resolution_finder.verdict_engine._get_model")
def test_semantic_fallback_resolves_yes_above_threshold_and_margin(mock_get_model, mock_cos_sim):
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    # sentence-vs-positive-template, then sentence-vs-negative-template
    mock_cos_sim.side_effect = [[[0.75]], [[0.30]]]

    evidence = [make_ranked(
        "Regulators granted approval for the subcutaneous formulation this week.",
        url="https://example.com/x", source_type="credible_backup",
    )]
    verdict = decide(SANOFI_MARKET, evidence)
    assert verdict.outcome == "YES"


@patch("resolution_finder.verdict_engine.util.cos_sim")
@patch("resolution_finder.verdict_engine._get_model")
def test_semantic_fallback_stays_unclear_below_threshold(mock_get_model, mock_cos_sim):
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.40]], [[0.35]]]  # below whatever threshold gets calibrated

    evidence = [make_ranked(
        "The FDA is expected to review the application sometime next quarter.",
        url="https://example.com/x", source_type="credible_backup",
    )]
    verdict = decide(SANOFI_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


@patch("resolution_finder.verdict_engine.util.cos_sim")
@patch("resolution_finder.verdict_engine._get_model")
def test_semantic_fallback_stays_unclear_when_margin_too_thin(mock_get_model, mock_cos_sim):
    # High absolute similarity to BOTH templates (an ambiguous sentence)
    # must not resolve YES just because it clears the absolute bar --
    # the margin between positive and negative similarity matters too.
    # Values chosen relative to the real calibrated constants (Task 23
    # Step 3: SEMANTIC_CONFIRMATION_THRESHOLD=0.72, SEMANTIC_MARGIN=-0.03):
    # positive_sim (0.75) clears the threshold on its own, but the negative
    # template scores even higher (0.85), for a margin of -0.10, well
    # below -0.03 -- must reject on margin even though the absolute bar
    # for "positive" alone was cleared.
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.75]], [[0.85]]]

    evidence = [make_ranked(
        "The situation around the approval remains fluid.",
        url="https://example.com/x", source_type="credible_backup",
    )]
    verdict = decide(SANOFI_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_semantic_fallback_never_runs_when_keyword_match_already_found():
    # CLARITY Act already resolves via BINARY_YES_KEYWORDS -- the semantic
    # path must not even be reached (proven by not mocking the model at
    # all here; if the code tried to call the real model unexpectedly in
    # a test environment without the mock, this test's own setup gives no
    # semantic signal, so a false regression would surface as this test
    # timing out or erroring on a real model load, not silently passing).
    evidence = [make_ranked("The bill was signed into law by the President today.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"
    assert "signed into law" in verdict.evidence_snippet


@patch("resolution_finder.verdict_engine.util.cos_sim")
@patch("resolution_finder.verdict_engine._get_model")
def test_semantic_fallback_still_respects_hedge_guard(mock_get_model, mock_cos_sim):
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.95]], [[0.10]]]  # would clearly pass if reached

    evidence = [make_ranked(
        "The FDA could approve the drug if trial data holds up, analysts say.",
        url="https://example.com/x", source_type="credible_backup",
    )]
    verdict = decide(SANOFI_MARKET, evidence)
    assert verdict.outcome != "YES"  # hedge ("could") must reject before semantic scoring runs


@patch("resolution_finder.verdict_engine.util.cos_sim")
@patch("resolution_finder.verdict_engine._get_model")
def test_semantic_fallback_still_respects_wrong_subject_veto(mock_get_model, mock_cos_sim):
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.95]], [[0.10]]]  # would clearly pass if reached

    evidence = [make_ranked(
        "Regulators at the European Medicines Agency approved a different drug, Xarelto, this week.",
        url="https://example.com/x", source_type="credible_backup",
    )]
    verdict = decide(SANOFI_MARKET, evidence)
    assert verdict.outcome != "YES"  # wrong-subject veto must reject before semantic scoring runs


# Real full description copied verbatim from data/markets.json
# (fda-approves-sanofi-subcutaneous-sarclisa-07-23-2026) -- the longer FDA
# boilerplate is what actually produced the noisy subject-term list
# ("New Drug Application", "Biologics License Application", etc.) that
# exposed the bug below; the short SANOFI_DESCRIPTION above doesn't
# reproduce it.
SANOFI_REAL_DESCRIPTION = (
    "As of market creation, the FDA's expected decision date for the specified application is July 23, 2026.\n\n"
    "This market will resolve to \"Yes\" if the U.S. Food and Drug Administration (FDA) grants full or conditional "
    "approval for Sanofi's Subcutaneous Sarclisa in combination with approved standard-of-care regimens for the "
    "treatment of multiple myeloma across currently approved Sarclisa IV indications by August 6, 2026, 11:59 PM ET. "
    "Otherwise, this market will resolve to \"No.\"\n\n"
    "An approval is defined as:\n"
    "For new drugs: FDA issuance of an approval letter for a New Drug Application (NDA) or Biologics License Application (BLA)\n"
    "For already-marketed drugs seeking new indications: FDA approval of a supplemental NDA (sNDA) or supplemental BLA (sBLA) for the specific indication referenced\n"
    "For generic drugs: FDA approval of an Abbreviated New Drug Application (ANDA)\n"
    "For biosimilars: FDA approval of a 351(k) application\n\n"
    "The following constitute qualifying approvals:\n"
    "Standard approval (traditional approval based on clinical benefit), Accelerated approval (based on surrogate endpoints), "
    "Approval with Risk Evaluation and Mitigation Strategy (REMS), Approval with restricted distribution or indication "
    "limitations, except compassionate use/expanded access programs\n\n"
    "The following do not constitute qualifying approvals:\n"
    "Approvable letters that require additional actions before approval\n"
    "Tentative approvals pending patent or exclusivity expiration\n"
    "FDA requests for additional information or studies\n"
    "Extension of Prescription Drug User Fee Amendments dates\n"
    "Approval for compassionate use or expanded access programs only\n"
    "Approval only for export or for use outside the United States\n"
    "Emergency Use Authorization (EUA) without full approval\n"
    "Complete Response Letters (CRLs) indicating the application cannot be approved in its current form\n\n"
    "This market will immediately resolve to \"No\" if the FDA issues a Complete Response Letter (CRL) or explicitly "
    "declines to approve the application. If the drug sponsor withdraws the application before the end of the "
    "specified period, the market will resolve to \"No\" immediately.\n\n"
    "If the listed drug is approved before the end of the specified period, the market will resolve to \"Yes,\" "
    "regardless of potential Advisory Committee votes against approval or later withdrawal of approval.\n\n"
    "Conditional approvals may include post-marketing requirements or commitments and still qualify.\n\n"
    "The primary resolution source will be official information from the FDA; however, a consensus of credible "
    "reporting will also be used."
)

SANOFI_REAL_MARKET = Market(
    id="fda-approves-sanofi-subcutaneous-sarclisa-07-23-2026",
    title="FDA approves Sanofi's Subcutaneous Sarclisa?",
    description=SANOFI_REAL_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)


def test_wrong_subject_veto_does_not_reject_sentence_that_also_names_subject():
    # Real bug found live (2026-08-23, full-suite eval batch): this exact
    # sentence (real evidence, Yahoo Finance, uk.finance.yahoo.com/news/
    # press-release-sanofi-subcutaneous-sarclisa-123500426.html) is a clean
    # confirmation but was wrongly vetoed as "mentions another entity"
    # because "Sarclisa Escena" (a specific product-name variant named in
    # the article) doesn't substring-match the subject term "Subcutaneous
    # Sarclisa" -- even though the SAME sentence also plainly names the
    # subject ("subcutaneous Sarclisa"). The veto must not fire when the
    # subject itself is also present in the sentence.
    evidence = [make_ranked(
        "Sanofi's subcutaneous Sarclisa Escena approved in the US as first "
        "anticancer treatment administered via on-body injector",
        url="https://uk.finance.yahoo.com/news/press-release-sanofi-subcutaneous-sarclisa-123500426.html",
        source_type="credible_backup_secondary",
    )]
    verdict = decide(SANOFI_REAL_MARKET, evidence)
    assert verdict.outcome == "YES"  # matches this market's real ground truth


WORLD_CUP_DESCRIPTION = (
    "This market resolves \"Yes\" if the record for most goals scored by a "
    "single player at a single World Cup (currently 13 goals) is broken -- "
    "i.e. a player scores 14 or more goals at the 2026 World Cup. Otherwise, "
    "this market resolves \"No\"."
)

WORLD_CUP_MARKET = Market(
    id="world-cup-most-goals-record-broken-20260608192914170",
    title="World Cup: Most Player Goals Record Broken?",
    description=WORLD_CUP_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)

# Real full description copied verbatim from data/markets.json
# (world-cup-most-goals-record-broken-20260608192914170) -- unlike the
# simplified WORLD_CUP_DESCRIPTION above, this one actually names the
# real record holder ("Just Fontaine"), which real evidence sentences
# about the record naturally mention too. The simplified version doesn't
# name him, so a real sentence naming him gets (correctly) vetoed as an
# unrecognized entity -- this fixture is what real evidence needs to be
# tested against.
WORLD_CUP_REAL_DESCRIPTION = (
    "Note: Current record 13 goals (Just Fontaine, France, 1958).\n\n"
    "This market will resolve “Yes” if any player scores 14 or more goals "
    "across the entire 2026 FIFA World Cup, including extra time. Otherwise, "
    "this market will resolve to “No”.\n\n"
    "Penalty shootout goals do not count toward a player's total.\n\n"
    "If the 2026 FIFA World Cup is cancelled, postponed after August 2, 2026, "
    "11:59 PM ET, or it cannot be determined whether the record was broken "
    "within that timeframe, this market will resolve to “No”.\n\n"
    "The resolution source for this market will be official information from "
    "FIFA; however, a consensus of credible reporting may also be used."
)

WORLD_CUP_REAL_MARKET = Market(
    id="world-cup-most-goals-record-broken-20260608192914170",
    title="World Cup: Most Player Goals Record Broken?",
    description=WORLD_CUP_REAL_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)


@patch("resolution_finder.verdict_engine.util.cos_sim")
@patch("resolution_finder.verdict_engine._get_model")
def test_semantic_fallback_rejects_vague_other_records_reference(mock_get_model, mock_cos_sim):
    # Real live bug (2026-08-18 retest, see progress.md): the semantic
    # fallback resolved this market YES on this exact sentence, but ground
    # truth is NO -- the sentence never confirms the SPECIFIC record (most
    # goals by a single player) was broken, it just gestures vaguely at
    # "other records" in general. mock_cos_sim would clearly pass the
    # threshold/margin gate if the guard didn't reject the sentence first,
    # so the guard's rejection is what this test is actually proving.
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.95]], [[0.10]]]

    evidence = [make_ranked(
        "Since then, numerous other records have been broken by both "
        "individual players and national squads.",
        url="https://example.com/worldcup", source_type="credible_backup",
    )]
    verdict = decide(WORLD_CUP_MARKET, evidence)
    assert verdict.outcome != "YES"
    mock_get_model.assert_not_called()
