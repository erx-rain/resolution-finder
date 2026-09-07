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


def make_ranked(text, url="https://congress.gov/bill/3633", source_type="primary", similarity=0.8,
                 published_date=None):
    return RankedArticle(
        article=ArticleRef(url=url, title="t", source_type=source_type, published_date=published_date),
        text=text,
        similarity=similarity,
    )


def test_binary_market_resolves_yes_on_keyword_match():
    # Two independent sources -- see CORROBORATION_MIN_DOMAINS' own
    # comment: a single confirming source is no longer enough to commit
    # a verdict, only to reach a candidate.
    evidence = [
        make_ranked("The bill was signed into law by the President today."),
        make_ranked(
            "The Digital Asset Market CLARITY Act officially became law after the "
            "President's signature on Tuesday, capping months of negotiation "
            "between the House and Senate.",
            url="https://www.reuters.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"
    assert verdict.source_url == "https://congress.gov/bill/3633"


def test_binary_market_does_not_assert_stated_default_after_deadline():
    # Behaviour deliberately CHANGED 2026-08-26 (was: asserts the stated
    # default as a NO verdict). Emitting the market's stated fallback on
    # a close-date-passed market conflates "our retrieval found nothing"
    # with "the event did not happen". Measured on the real 30-market
    # eval, that path was a coin flip -- 2 correct (Powell, Canadian-NHL)
    # and 2 WRONG (Egypt and Man City, both truth=Yes, both asserted NO
    # only because the confirming article didn't surface on that run).
    # Under a minimize-wrong objective an unresolved answer is cheap and
    # a confident wrong one is not. The stated default is still surfaced
    # as reviewer context in the snippet, never as the outcome.
    past_deadline_market = Market(
        id="clarity-act-2026",
        title=CLARITY_MARKET.title,
        description=CLARITY_DESCRIPTION,
        options=[],
        close_date=date.today() - timedelta(days=1),
    )
    verdict = decide(past_deadline_market, [])
    assert verdict.outcome == "NO_EVIDENCE"
    assert "stated default is NO" in (verdict.evidence_snippet or "")


# Verbatim from data/markets.json
# (will-trump-try-to-fire-powell-as-fed-board-member-by-july-31-2026...).
# Real bug found live (2026-08-25): the default-outcome trigger list only
# recognized negation phrasing ("has not", "not met", ...) before
# "resolve(s) to X" -- this market instead uses "Otherwise, this market
# will resolve to 'No'.", which has no negation word at all. 9 of the 18
# real markets in the current dataset use exactly this "Otherwise..."
# phrasing, so this silently disabled the deadline-passed default for
# roughly half the dataset.
POWELL_DESCRIPTION = (
    "This market will resolve to \"Yes\" if Donald Trump publicly and "
    "unequivocally announces that he is removing Jerome Powell as a member "
    "of the Federal Reserve Board of Governors, or takes formal action "
    "toward doing so, such as issuing a directive or formal request, by "
    "the listed date, 11:59 PM ET. Otherwise, this market will resolve to "
    "“No”."
)

POWELL_MARKET = Market(
    id="will-trump-try-to-fire-powell-as-fed-board-member-by-july-31-2026",
    title="Will Trump try to fire Powell as Fed Board Member by July 31, 2026?",
    description=POWELL_DESCRIPTION,
    options=[],
    close_date=date.today() - timedelta(days=1),
)


def test_binary_market_parses_otherwise_phrased_default_but_does_not_assert_it():
    # The "Otherwise, this market will resolve to 'No'." parsing fix
    # (2026-08-25) is still exercised -- it must still be RECOGNISED, it
    # just isn't emitted as the verdict any more (see the test above).
    verdict = decide(POWELL_MARKET, [])
    assert verdict.outcome == "NO_EVIDENCE"
    assert "stated default is NO" in (verdict.evidence_snippet or "")


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
    evidence = [
        make_ranked("Congress enacted the measure this morning."),
        make_ranked(
            "Lawmakers enacted the Digital Asset Market Clarity Act earlier today.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
    ]
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
    evidence = [
        make_ranked("The CLARITY Act was signed into law by the President on Tuesday."),
        make_ranked(
            "The Digital Asset Market CLARITY Act became law this week, capping a "
            "bruising two-year fight in Congress over crypto regulation.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
    ]
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
    evidence = [
        make_ranked("The Senate passed the war powers resolution in a 51-47 vote on Thursday."),
        make_ranked(
            "In a narrow 51-47 vote Thursday evening, the Senate passed the Iran "
            "war powers resolution after hours of contentious floor debate.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(WAR_POWERS_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_binary_market_resolves_yes_on_house_passage_for_a_resolution_market():
    evidence = [
        make_ranked("The House passed the resolution by a vote of 221-206 on Wednesday."),
        make_ranked(
            "Wednesday's roll call ended 221-206 as the House passed the war "
            "powers resolution following a contentious floor fight over war authority.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(WAR_POWERS_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_binary_market_resolves_yes_on_passed_by_congress_phrasing():
    # Real bug found live (2026-09-02, 42-market eval): the real
    # congress-passes-bill-banning-tiktok market (truth: Yes) never
    # matched off "TikTok Ban Bill Passed by Congress. What Happens
    # Next. - Barron's" -- bicameral-bill headlines routinely say "passed
    # by Congress" as a whole, without naming the Senate/House
    # specifically.
    evidence = [
        make_ranked(
            "Iran War Powers Resolution Bill Passed by Congress. What Happens Next.",
            url="https://www.barrons.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "The Iran war powers resolution cleared both chambers of Congress on Wednesday.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(WAR_POWERS_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_binary_market_resolves_yes_on_both_chambers_passage_phrasing():
    evidence = [
        make_ranked("The measure cleared both chambers after months of negotiation."),
        make_ranked(
            "After months of behind-the-scenes negotiation between leadership, "
            "the war powers measure cleared both chambers of Congress this week.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(WAR_POWERS_MARKET, evidence)
    assert verdict.outcome == "YES"


# Real market pulled from Polymarket (fda-approves-viridian-therapeutics-
# veligrotug-06-30-2026) -- title verbatim.
VIRIDIAN_FDA_MARKET = Market(
    id="fda-approves-viridian-therapeutics-veligrotug-06-30-2026",
    title="FDA approves Viridian Therapeutics' Veligrotug?",
    description="Primary resolution source: official FDA announcement.",
    options=[],
    close_date=date.today() + timedelta(days=30),
)


def test_binary_market_resolves_yes_on_fda_approval_language():
    # Real gap found live (2026-08-26): BINARY_YES_KEYWORDS was entirely
    # legislative vocabulary ("signed into law", "passed the senate", ...)
    # -- structurally cannot confirm an FDA drug-approval market, which
    # never gets "signed into law" or "passed" at all.
    #
    # The real production evidence for this market ("First approved
    # treatment for thyroid eye disease... DRI Healthcare is entitled to
    # a tiered royalty...") scores 0.908 on the NLI check once given the
    # chance, confirming the keyword gap is real -- but that exact
    # sentence also names "DRI Healthcare" (a royalty partner) without
    # repeating "Viridian", which the wrong-subject-entity veto correctly
    # rejects on its own terms (an article-lead-sentence fallback for
    # that veto was tried and reverted 2026-08-26: it reopened the
    # original GENIUS/CLARITY false positive the veto exists to prevent
    # -- see _sentence_mentions_other_entity's docstring). That specific
    # production sentence remains a known, accepted gap; this test
    # verifies the keyword fix itself on a clean sentence that names the
    # subject directly, which is the common case this fix is really for.
    evidence = [
        make_ranked(
            "The FDA approved Viridian Therapeutics' Veligrotug on Tuesday "
            "for the treatment of thyroid eye disease, clearing the drug after "
            "a review process that ran several months longer than expected."
        ),
        make_ranked(
            "Viridian Therapeutics announced FDA approval of Veligrotug for "
            "thyroid eye disease, with shares of the biotech firm rising "
            "sharply on the news as analysts pointed to a strong addressable "
            "patient population and a favorable reimbursement outlook heading "
            "into next year's launch.",
            url="https://www.reuters.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(VIRIDIAN_FDA_MARKET, evidence)
    assert verdict.outcome == "YES"


# Real markets pulled from Polymarket (titles verbatim).
EGYPT_MARKET = Market(
    id="egypt-presidential-election-will-abdel-fattah-el-sisi-win",
    title="Egypt Presidential Election: Will Abdel Fattah el-Sisi win?",
    description="Primary resolution source: official Egyptian election results.",
    options=[],
    close_date=date(2023, 12, 12),
)

# Description verbatim from data/markets.json -- needed as-is (not
# simplified) because the wrong-subject-entity veto's escape hatch reads
# subject_terms from the FULL description; a simplified description
# doesn't reproduce the real bug below.
EPSTEIN_DISCLOSURE_MARKET = Market(
    id="congress-passes-epstein-disclosure-billresolution-in-2025",
    title="Congress passes Epstein disclosure bill/resolution in 2025?",
    description=(
        'This market will resolve to "Yes" if both the U.S. House of '
        "Representatives and the U.S. Senate pass the same bill, "
        "measure, or resolution that explicitly mandates, compels, or "
        "formally calls for the public release of documents related to "
        "Jeffrey Epstein by December 31, 2025, at 11:59 PM ET. "
        'Otherwise, this market will resolve to "No."\n\n'
        "A measure amended by either chamber will only qualify if the "
        "amended version is subsequently passed by both chambers in "
        "identical form.\n\n"
        "The resolution source will be official congressional voting "
        "records and a consensus of credible reporting."
    ),
    options=[],
    close_date=date(2025, 12, 31),
)


def test_binary_market_resolves_yes_on_sworn_in_language():
    # Real bug found live (2026-08-26): the real Egypt presidential
    # election market (real winner: Yes, el-Sisi won) wrongly resolved
    # NO. BINARY_YES_KEYWORDS has no election/inauguration vocabulary at
    # all. Doesn't trip the wrong-subject-entity veto or the hedge guard
    # -- purely a missing keyword, verified via a narrower, separately-
    # calibrated threshold (see CONSEQUENCE_VERIFICATION_THRESHOLD's own
    # comment for why the shared 0.85 threshold can't be reused here).
    evidence = [
        make_ranked(
            "Egyptian President Abdel Fattah al-Sisi was sworn in for his "
            "third term on Tuesday in the country's new capital, the largest "
            "of the mega-projects that have signaled his push toward "
            "development."
        ),
        make_ranked(
            "Egypt's electoral commission confirmed on Tuesday that Abdel Fattah "
            "el-Sisi was sworn in to begin a third term as president, following a "
            "ceremony attended by foreign dignitaries in the new capital.",
            url="https://www.reuters.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(EGYPT_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_binary_market_resolves_yes_on_forced_release_language():
    # Real bug found live (2026-08-26): the real Congress/Epstein
    # disclosure market (real answer: Yes) wrongly resolved NO, TWICE
    # over. First: "voted... to force the release of" files doesn't
    # match any BINARY_YES_KEYWORDS phrase (fixed by CONSEQUENCE_YES_
    # KEYWORDS). Second, deeper bug found re-testing against the REAL
    # full article text (not a simplified single-sentence version): the
    # real sentence also names "Justice Department" and "President
    # Donald Trump" -- unrelated entities -- and extract_entities'
    # pattern requires 2+ CONSECUTIVE capitalized words, so it can never
    # extract a single-word distinctive term like "Epstein" from this
    # market's own title, silently disabling the wrong-subject veto's
    # escape hatch for this market entirely (see
    # _distinctive_subject_terms' own comment).
    evidence = [
        make_ranked(
            "The Republican-controlled US Congress voted almost unanimously "
            "on Tuesday to force the release of Justice Department files on "
            "the late convicted sex offender Jeffrey Epstein, an outcome "
            "President Donald Trump had fought for months before ending his "
            "opposition."
        ),
        make_ranked(
            "According to congressional records, a bipartisan majority in "
            "Congress moved on Tuesday to force the release of the Epstein "
            "files after a lengthy procedural standoff over the discharge petition.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(EPSTEIN_DISCLOSURE_MARKET, evidence)
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
    evidence = [
        make_ranked("The bill was signed into law by the President today."),
        make_ranked(
            "The measure was enacted into law earlier today.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
    ]
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
    evidence = [
        make_ranked(
            "The Norwegian Nobel Committee announced that the prize is awarded to Pope Leo XIV.",
            url="https://nobelprize.org/announcement",
        ),
        make_ranked(
            "Pope Leo XIV has been named winner of this year's Nobel Peace Prize, the committee said Friday.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
    ]
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


def test_multi_outcome_market_does_not_assert_stated_no_default_after_deadline():
    # Behaviour deliberately CHANGED 2026-08-26, same reasoning as the
    # binary case: resolving every option NO purely because the close
    # date passed asserts an outcome from OUR failure to find evidence.
    past_deadline_market = Market(
        id="nobel-peace-2026", title=NOBEL_MARKET.title, description=NOBEL_DESCRIPTION,
        options=NOBEL_MARKET.options, close_date=date.today() - timedelta(days=1),
    )
    verdicts = decide(past_deadline_market, [])
    assert all(v.outcome == "NO_EVIDENCE" for v in verdicts)


def test_multi_outcome_market_matches_announcement_keyword_as_whole_word():
    evidence = [
        make_ranked(
            "Official: Arsenal wins the race for Vinicius Junior.",
            url="https://www.bbc.com/sport/1", source_type="credible_backup",
        ),
        make_ranked(
            "Vinicius Junior has completed his move to Arsenal, ending his "
            "spell at Real Madrid, the club confirmed Tuesday.",
            url="https://www.skysports.com/x", source_type="credible_backup",
        ),
    ]
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


def test_multi_outcome_market_does_not_crown_a_named_default_option_after_deadline():
    # Behaviour deliberately CHANGED 2026-08-26. This was the WORST case
    # of the stated-default rule: a named default ("...resolve to Real
    # Madrid") crowned a specific option YES on literally no evidence,
    # purely because the close date had passed.
    past_deadline_market = Market(
        id="vinicius-transfer-2026", title=VINICIUS_MARKET.title, description=VINICIUS_DESCRIPTION,
        options=VINICIUS_MARKET.options, close_date=date.today() - timedelta(days=1),
    )
    verdicts = decide(past_deadline_market, [])
    assert all(v.outcome != "YES" for v in verdicts)


def test_multi_outcome_market_resolves_yes_on_contract_renewal_language():
    # Real gap found live: NYTimes/The Athletic on Vinicius Junior described
    # him staying at Real Madrid as "reached an agreement to renew his
    # contract", not any of the old ANNOUNCEMENT_KEYWORDS.
    evidence = [
        make_ranked(
            "Real Madrid have confirmed Vinicius Junior signed a new contract, "
            "ending Arsenal's interest in the forward.",
            url="https://www.nytimes.com/athletic/1", source_type="credible_backup",
        ),
        make_ranked(
            "Real Madrid have fended off Arsenal's interest, confirming "
            "Wednesday that Vinicius Junior signed a new deal to stay.",
            url="https://www.marca.com/x", source_type="credible_backup",
        ),
    ]
    verdicts = decide(VINICIUS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Real Madrid": "YES", "Arsenal": "NO"}


def test_multi_outcome_market_resolves_yes_on_winner_of_phrase():
    # Real gap found live: BBC Pidgin on the Osun election used "declare ...
    # winner of ..." — "winner of" wasn't in ANNOUNCEMENT_KEYWORDS.
    evidence = [
        make_ranked(
            "INEC declare Ademola Adeleke winner of the Osun State election.",
            url="https://www.bbc.com/pidgin/1", source_type="credible_backup",
        ),
        make_ranked(
            "Ademola Adeleke has been declared the winner of the Osun governorship race by the electoral commission.",
            url="https://www.premiumtimesng.com/x", source_type="credible_backup",
        ),
    ]
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
    evidence = [
        make_ranked(
            "The Los Angeles Lakers' first-round playoff victory over the "
            "Houston Rockets may have accomplished more than advancing the "
            "franchise to the Western Conference semifinals.",
            url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "The Lakers eliminated the Rockets in the first round to reach "
            "the Western Conference semifinals.",
            url="https://www.espn.com/x", source_type="credible_backup",
        ),
    ]
    verdicts = decide(LAKERS_ROCKETS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Lakers": "YES", "Rockets": "NO"}


def test_multi_outcome_head_to_head_winner_is_order_independent():
    # The exact same evidence must still correctly crown Lakers even when
    # market.options lists Rockets first -- proves the fix reads direction
    # from the phrase itself ("winner_option ... verb ... loser_option"),
    # not from list position. This is the specific failure mode a naive
    # ANNOUNCEMENT_KEYWORDS addition would have had: both team names sit
    # within the same 40-char proximity window as "victory over".
    evidence = [
        make_ranked(
            "The Los Angeles Lakers' first-round playoff victory over the "
            "Houston Rockets may have accomplished more than advancing the "
            "franchise to the Western Conference semifinals.",
            url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "The Lakers eliminated the Rockets in the first round to reach "
            "the Western Conference semifinals.",
            url="https://www.espn.com/x", source_type="credible_backup",
        ),
    ]
    verdicts = decide(LAKERS_ROCKETS_MARKET_REVERSED_OPTIONS, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Lakers": "YES", "Rockets": "NO"}


def test_multi_outcome_market_resolves_yes_on_head_to_head_defeated_verb():
    evidence = [
        make_ranked(
            "The Lakers defeated the Rockets in six games to advance.",
            url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "The Lakers advanced to the next round after eliminating the Rockets in six games on Sunday night.",
            url="https://www.espn.com/x", source_type="credible_backup",
        ),
    ]
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


GERMANY_ELECTION_MARKET = Market(
    id="germany-parliamentary-election-test",
    title="Germany Parliamentary Election Winner",
    description="This market resolves to the party that wins the most seats.",
    options=["CDU/CSU", "AfD", "SPD"],
    close_date=date.today() + timedelta(days=30),
)


def test_multi_outcome_market_ignores_pre_election_polling_projection():
    # Real bug found live (2026-09-02, 42-market eval): the real Germany
    # Parliamentary Election market (truth: CDU/CSU) wrongly crowned AfD
    # off "AfD remain on course for record result in YouGov's second MRP
    # model of the 2025 German election" -- a PRE-ELECTION POLLING
    # PROJECTION, not the actual result. Corroboration alone didn't catch
    # this since multiple outlets ran similar pre-election polling
    # headlines -- the fix has to be a hedge on the polling/forecast
    # language itself, generalizable to any election market.
    evidence = [
        make_ranked(
            "AfD remain on course for record result in YouGov's second "
            "MRP model of the 2025 German election - YouGov",
            url="https://yougov.de/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "A new MRP poll shows AfD on course to win the most seats in the upcoming German election.",
            url="https://www.politico.eu/x", source_type="credible_backup",
        ),
    ]
    verdicts = decide(GERMANY_ELECTION_MARKET, evidence)
    assert not any(v.option == "AfD" and v.outcome == "YES" for v in verdicts)


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
    evidence = [
        make_ranked(
            "The Los Angeles Lakers' first-round playoff victory over the "
            "Houston Rockets may have accomplished more than advancing the "
            "franchise to the Western Conference semifinals.",
            url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "The Lakers eliminated the Rockets in the first round to reach "
            "the Western Conference semifinals.",
            url="https://www.espn.com/x", source_type="credible_backup",
        ),
    ]
    verdicts = decide(LAKERS_ROCKETS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Lakers": "YES", "Rockets": "NO"}


def test_multi_outcome_market_still_resolves_yes_when_evidence_states_matching_year():
    # The matching year (2026, same as the market's own) must not be
    # treated as a conflict just because the check fires.
    evidence = [
        make_ranked(
            "The Lakers defeated the Rockets in the 2026 Western Conference "
            "First Round series.",
            url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "The Lakers advanced to the next round after eliminating the Rockets in the 2026 first-round series on Sunday night.",
            url="https://www.espn.com/x", source_type="credible_backup",
        ),
    ]
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
    evidence = [
        make_ranked(
            "Official: Arsenal wins the race for Vinicius Junior.",
            url="https://www.bbc.com/sport/1", source_type="credible_backup",
        ),
        make_ranked(
            "Vinicius Junior has completed his move to Arsenal, ending his "
            "spell at Real Madrid, the club confirmed Tuesday.",
            url="https://www.skysports.com/x", source_type="credible_backup",
        ),
    ]
    verdicts = decide(VINICIUS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Arsenal": "YES", "Real Madrid": "NO"}


def test_multi_outcome_market_head_to_head_still_works_via_wins_over_phrase():
    # "wins over" between two LISTED options must still correctly
    # resolve directionally, via _match_head_to_head_winner -- only the
    # generic ANNOUNCEMENT_KEYWORDS proximity path is suppressed for this
    # phrase, not head-to-head confirmation entirely.
    evidence = [
        make_ranked(
            "The Lakers' string of wins over the Rockets this postseason "
            "sealed the series.",
            url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "The Lakers eliminated the Rockets in the first round to reach "
            "the Western Conference semifinals.",
            url="https://www.espn.com/x", source_type="credible_backup",
        ),
    ]
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
    evidence = [
        make_ranked(
            "The Lakers defeated the Rockets in the playoffs to advance to "
            "the Western Conference semifinals.",
            url="https://sports.yahoo.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "The Lakers advanced to the next round after eliminating the Rockets in the first-round playoff series on Sunday night.",
            url="https://www.espn.com/x", source_type="credible_backup",
        ),
    ]
    verdicts = decide(LAKERS_ROCKETS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Lakers": "YES", "Rockets": "NO"}


CANADIAN_CUP_MARKET = Market(
    id="will-a-canadian-team-win-nhl-stanley-cup-782",
    title="Will a Canadian team win NHL Stanley Cup?",
    description="This market will resolve to \"Yes\" if a Canadian team wins the NHL Stanley Cup.",
    options=[],
    close_date=date(2025, 6, 24),
)


def test_binary_market_does_not_treat_a_question_headline_as_a_result():
    # Real regression found live (2026-08-26) after date-scoped archive
    # retrieval started supplying HEADLINES as evidence: the real
    # "Will a Canadian team win NHL Stanley Cup?" market (truth: No)
    # flipped to YES off the headline "Canada's Stanley Cup drought is
    # decades long: Can Edmonton Oilers end it?" -- an interrogative,
    # i.e. speculation, not a report of a result. Headlines make this
    # failure mode far more common than full article prose did, and no
    # existing hedge phrase catches "Can ... ?" framing.
    evidence = [make_ranked(
        "Canada's Stanley Cup drought is decades long: Can Edmonton "
        "Oilers end it? - usatoday.com",
        url="https://news.google.com/rss/articles/xyz",
        source_type="credible_backup_secondary",
    )]
    verdict = decide(CANADIAN_CUP_MARKET, evidence)
    assert verdict.outcome != "YES"


def test_multi_outcome_market_does_not_treat_a_question_headline_as_a_winner():
    # Same failure mode on the multi-outcome path: "Who could replace Joe
    # Biden as the 2024 Democratic nominee?" names real options but
    # confirms nothing.
    evidence = [make_ranked(
        "Who could replace Joe Biden as the 2024 Democratic nominee? - CBS News",
        url="https://news.google.com/rss/articles/abc",
        source_type="credible_backup",
    )]
    verdicts = decide(NOBEL_MARKET, evidence)
    assert all(v.outcome != "YES" for v in verdicts)


def test_multi_outcome_market_does_not_treat_a_nomination_as_a_win():
    # Real bug found live (2026-08-25) against real production evidence
    # for the 2026 Nobel Peace Prize: "In addition to UNRWA, the ICJ was
    # also nominated for its seeming contributions to peace through
    # international law..." wrongly confirmed UNRWA as the WINNER
    # (0.557 similarity, above the winner-verification threshold). Being
    # a NOMINEE is not the same claim as having WON -- the sentence
    # never says UNRWA received the prize, only that it was nominated,
    # same as hundreds of other nominees every year.
    evidence = [make_ranked(
        "In addition to UNRWA, the ICJ was also nominated for its "
        "seeming contributions to peace through international law, "
        "including its rulings concerning Israel and Russia.",
        url="https://www.jpost.com/breaking-news/article-824048",
        source_type="credible_backup_secondary",
    )]
    verdicts = decide(NOBEL_MARKET, evidence)
    assert not any(v.option == "UNRWA" and v.outcome == "YES" for v in verdicts)


# Real market pulled from Polymarket (democratic-nominee-2024) --
# title/description verbatim.
DEMOCRATIC_NOMINEE_MARKET = Market(
    id="democratic-nominee-2024",
    title="Democratic Nominee 2024",
    description="This is a market on who will be the Democratic nominee for the 2024 presidential election.",
    options=["Joe Biden", "Gavin Newsom", "Robert F. Kennedy Jr.", "Kamala Harris", "Hillary Clinton",
             "Michelle Obama", "Elizabeth Warren", "Other (Incl. Whitmer)", "Dean Phillips"],
    close_date=date(2024, 8, 21),
)


def test_multi_outcome_market_resolves_yes_when_becoming_the_nominee_is_the_win_condition():
    # Real bug found live (2026-08-26), a direct regression from the fix
    # right above: bare "nominee" in NEGATION_HEDGE_WORDS also matches
    # "It's official: Kamala Harris becomes Democrats' 2024 presidential
    # nominee - NPR" -- which for THIS market (a nomination CONTEST, not
    # an award) is the actual win condition, not mere candidacy. The
    # sentence never reached NLI verification at all; direct calibration
    # confirmed it would have scored 0.997 if it had. The real market
    # wrongly crowned Michelle Obama instead, off a separate, lower-
    # similarity sentence merely describing her speaking at the
    # convention. Distinguishing "nominated FOR an award" (candidacy,
    # hedge) from "becomes THE nominee" (the win itself, don't hedge)
    # needs the qualifier -- see NEGATION_HEDGE_WORDS' own comment.
    evidence = [
        make_ranked(
            "It's official: Kamala Harris becomes Democrats' 2024 "
            "presidential nominee - NPR",
            url="https://news.google.com/rss/articles/npr-harris",
            source_type="credible_backup",
            similarity=0.581,
        ),
        make_ranked(
            "Kamala Harris has clinched the 2024 Democratic presidential nomination, delegates confirmed Monday.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
    ]
    verdicts = decide(DEMOCRATIC_NOMINEE_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts}["Kamala Harris"] == "YES"


def test_multi_outcome_market_abstains_when_two_options_both_independently_verify():
    # Real bug found live (2026-08-27): the winner loop used to stop
    # checking entirely once the FIRST option verified, so whichever
    # option's confirming sentence happened to be ranked/ordered first
    # WON -- even when a genuinely different option ALSO independently
    # verifies from other real evidence in the same batch. Confirmed
    # concretely for this exact real market: the true Kamala Harris
    # sentence above (0.997 NLI score) AND this real, separate Michelle
    # Obama sentence (a false positive -- it only describes her speaking
    # at the convention, not winning anything) BOTH independently verify
    # as winners. The market previously resolved "correctly" only
    # because Harris's article happened to rank higher and got checked
    # first -- a coin flip on retrieval order, not a real decision.
    # Under a minimize-wrong objective, genuine disagreement in the
    # evidence must abstain, not bet on whichever was found first.
    #
    # Updated 2026-09-02 (phase-5 conflict fix): "conflict" is now judged
    # on CORROBORATED options only (see the code's own comment for the
    # real world-series-winner/premier-league-winner cases this was
    # built to fix -- a single stray match could veto a well-supported
    # answer). Both Harris and Obama here are SINGLE-source claims,
    # neither reaches CORROBORATION_MIN_DOMAINS, so this no longer
    # reports as an explicit multi-option "Conflicting evidence" --
    # it falls through to the plain uncorroborated-note path instead.
    # The behavior this test actually guards (abstain, don't crown
    # whichever option was found first) still holds either way.
    evidence = [
        make_ranked(
            "It's official: Kamala Harris becomes Democrats' 2024 "
            "presidential nominee - NPR",
            url="https://news.google.com/rss/articles/npr-harris",
            source_type="credible_backup",
            similarity=0.581,
        ),
        make_ranked(
            "Ready to go: Barack and Michelle Obama electrify the 2024 "
            "Democratic National Convention with powerful calls to "
            "action - Northwest Progressive Institute",
            url="https://news.google.com/rss/articles/obama-dnc",
            source_type="credible_backup_secondary",
            similarity=0.476,
        ),
    ]
    verdicts = decide(DEMOCRATIC_NOMINEE_MARKET, evidence)
    assert len(verdicts) == 1
    assert verdicts[0].outcome == "UNCLEAR"
    assert verdicts[0].option is None
    assert "independent source" in (verdicts[0].evidence_snippet or "")


def test_multi_outcome_market_abstains_when_two_corroborated_options_both_verify():
    # Companion to the test above: with the phase-5 conflict fix,
    # "Conflicting evidence" now only fires when BOTH competing options
    # are themselves independently corroborated (2+ domains each) --
    # this is the case that must still abstain, not silently pick one.
    #
    # Deliberately built on two CLEAN, unambiguous winner claims (not the
    # Harris/Obama pair above) -- that Obama sentence is documented
    # elsewhere in this file as a genuine borderline NLI case (that's the
    # real bug it exists to guard against), so reusing it here to build a
    # SECOND corroborating source made this test flaky by construction,
    # independent of whether the conflict logic itself was correct.
    evidence = [
        make_ranked(
            "The Norwegian Nobel Committee announced that the prize is awarded to Pope Leo XIV.",
            url="https://nobelprize.org/announcement",
        ),
        make_ranked(
            "Pope Leo XIV has been named winner of this year's Nobel Peace Prize, the committee said Friday.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
        make_ranked(
            "The Norwegian Nobel Committee announced that the prize is awarded to Donald Trump.",
            url="https://reuters.com/announcement", source_type="credible_backup",
        ),
        make_ranked(
            "Donald Trump has been named winner of this year's Nobel Peace Prize, the committee said Friday.",
            url="https://bbc.com/x", source_type="credible_backup_secondary",
        ),
    ]
    verdicts = decide(NOBEL_MARKET, evidence)
    assert len(verdicts) == 1
    assert verdicts[0].outcome == "UNCLEAR"
    assert verdicts[0].option is None
    assert "Conflicting evidence" in (verdicts[0].evidence_snippet or "")


# Real market pulled from Polymarket (2026-nhl-stanley-cup-champion) --
# title/description verbatim, options trimmed to the two relevant below
# (real full list has 32).
STANLEY_CUP_MARKET = Market(
    id="2026-nhl-stanley-cup-champion",
    title="2026 NHL Stanley Cup Champion ",
    description="This market is to predict the winner of the 2025–26 NHL Stanley Cup championship.",
    options=["Pittsburgh Penguins", "Carolina Hurricanes"],
    close_date=date(2026, 6, 30),
)


def test_multi_outcome_market_does_not_treat_making_the_playoffs_as_winning_the_championship():
    # Real bug found live (2026-08-26): the real 2026-nhl-stanley-cup-
    # champion market (real winner Carolina Hurricanes) wrongly crowned
    # the Pittsburgh Penguins off "The Pittsburgh Penguins returned to
    # the Stanley Cup Playoffs in 2026." Reaching the playoffs is
    # eligibility to compete for the championship, not the outcome
    # itself -- same "describes standing, not a decided outcome" shape
    # as the nomination bug above, different domain.
    evidence = [make_ranked(
        "The Pittsburgh Penguins returned to the Stanley Cup Playoffs in 2026.",
        url="https://sports.yahoo.com/articles/penguins-glaring-roster-flaw-must-223156833.html",
        source_type="credible_backup_secondary",
    )]
    verdicts = decide(STANLEY_CUP_MARKET, evidence)
    assert not any(v.option == "Pittsburgh Penguins" and v.outcome == "YES" for v in verdicts)


def test_multi_outcome_market_does_not_crown_loser_when_sentence_names_the_real_winner():
    # Real bug found live (2026-08-25) against real production evidence
    # for The International 2026: a schedule-recap sentence reading
    # "TEAM VISION 2-3 Team Spirit ... Team Spirit are the champions of
    # TI 2026." wrongly confirmed TEAM VISION (the loser, scored 2-3) as
    # the winner. Root cause: _winner_hypotheses' negative label ("has
    # not won ..., or it has not been decided yet") is vague, the exact
    # same failure mode already documented and fixed for head-to-head
    # (see HEAD_TO_HEAD_VERIFICATION_THRESHOLD's comment) -- a vague
    # negative doesn't give the model a real contrastive alternative, so
    # it just detects topical relevance instead of judging direction.
    # This sentence explicitly names Team Spirit -- a DIFFERENT listed
    # option -- as the actual champion in the same breath.
    evidence = [make_ranked(
        "Grand Final\n- TEAM VISION 2-3 Team Spirit (7am CEST / 1pm UTC "
        "+8 / 1am ET) \n  - Team Spirit are the champions of TI 2026.",
        url="https://dotesports.com/dota-2/news/dota-2-ti-2026-schedule-results",
        source_type="credible_backup_secondary",
    )]
    verdicts = decide(INTERNATIONAL_MARKET_FULL, evidence)
    assert not any(v.option == "Team Vision" and v.outcome == "YES" for v in verdicts)


# Real market pulled from Polymarket (premier-league-winner-24-25) --
# title/description verbatim, options trimmed to the two relevant to the
# bug below (real full list has 20).
PREMIER_LEAGUE_24_25_MARKET = Market(
    id="premier-league-winner-24-25",
    title="Premier League Winner",
    description="This is a market on who will win the Premier League in the 2024-25 season.",
    options=["Arsenal", "Liverpool"],
    close_date=date(2025, 5, 25),
)


# Real market pulled from Polymarket (epl-team-to-qualify-for-uefa-
# champions-league) -- title/description verbatim, options trimmed to
# the two relevant to the bug below (real full list has 20).
CHAMPIONS_LEAGUE_QUALIFY_MARKET = Market(
    id="epl-team-to-qualify-for-uefa-champions-league",
    title="EPL: Team to qualify for UEFA Champions League",
    description=(
        "This market will resolve to \"Yes\" if the listed team clinches a "
        "league phase spot in the 2026-27 Champions League per UEFA rules. "
        "Otherwise, the associated market will resolve to \"No\"."
    ),
    options=["Crystal Palace", "Manchester United"],
    close_date=date(2026, 9, 1),
)


def test_multi_outcome_market_ignores_a_different_uefa_competitions_result():
    # Real bug found live (2026-08-26): the real epl-team-to-qualify-for-
    # uefa-champions-league market (real winner Manchester United) wrongly
    # crowned Crystal Palace. Root cause: the real evidence sentence is
    # about the CONFERENCE LEAGUE -- a different, lower-tier UEFA
    # competition Crystal Palace actually won and is playing in this
    # season -- not the Champions League the market asks about. Nothing
    # checked that the sentence names the SAME UEFA competition as the
    # market before letting a candidate through.
    evidence = [make_ranked(
        "Conference League champions Crystal Palace take on a long trip "
        "to Turkey to take on Besiktas, while they have also been drawn "
        "with Real Sociedad, Lyon and Sparta Prague.",
        url="https://sports.yahoo.com/articles/uefa-europa-league-draw-british-124500790.html",
        source_type="credible_backup_secondary",
    )]
    verdicts = decide(CHAMPIONS_LEAGUE_QUALIFY_MARKET, evidence)
    assert not any(v.option == "Crystal Palace" and v.outcome == "YES" for v in verdicts)


def test_multi_outcome_market_recognizes_a_common_club_nickname():
    # Real gap found live (2026-08-26): the real epl-team-to-qualify-for-
    # uefa-champions-league market's real confirming evidence --
    # "Arsenal, Man Utd, Liverpool, Man City and Aston Villa will all
    # take part in the 2026/27 Champions League" -- never even became a
    # CANDIDATE for "Manchester United", because British football
    # journalism commonly abbreviates ("Man Utd"), and the option-mention
    # gate only checks whether the full option string appears verbatim.
    # Market fell through to a safe UNCLEAR rather than a wrong answer,
    # but the real, findable confirmation was sitting right there.
    # Deliberately a small, unambiguous alias list (not "Forest"/
    # "Palace"/"Villa", which are ordinary English words with real
    # false-trigger risk) -- see TEAM_NICKNAME_ALIASES' own comment.
    #
    # Doesn't assert YES: this same real sentence exposed a SEPARATE,
    # unrelated bug -- _winner_hypotheses' generic "{option} has won
    # {market.title}" template reads badly for a QUALIFICATION-shaped
    # title ("EPL: Team to qualify for UEFA Champions League"), scoring
    # only 0.161. That's real, but fixing hypothesis wording broadly has
    # repeatedly backfired this session (4 rejected attempts elsewhere);
    # not bundling an unvalidated fix into this one. What's verified here
    # is the actual fix: the option is now correctly recognized as a
    # genuine, exclusively-mentioned candidate at all (it wasn't before),
    # and the wrong option (Crystal Palace) still isn't crowned either.
    evidence = [make_ranked(
        "Arsenal, Man Utd , Liverpool, Man City and Aston Villa will "
        "all take part in the 2026/27 Champions League; draw for the "
        "league phase will take place in Monaco from 5pm UK time on "
        "Thursday.",
        url="https://www.skysports.com/football/news/champions-league-qualified",
        source_type="credible_backup_secondary",
    )]
    verdicts = decide(CHAMPIONS_LEAGUE_QUALIFY_MARKET, evidence)
    assert not any(v.option == "Crystal Palace" and v.outcome == "YES" for v in verdicts)
    from resolution_finder.verdict_engine import _option_mentioned
    assert _option_mentioned(evidence[0].text.lower(), "Manchester United")


def test_multi_outcome_market_ignores_a_different_seasons_prediction_with_no_year_in_the_matching_sentence():
    # Real bug found live (2026-08-25): the real premier-league-winner-24-25
    # market (asking about the already-decided 2024-25 season, real winner
    # Liverpool) wrongly crowned Arsenal. Root cause: the real evidence
    # article's OPENING sentence establishes it's about the 2026-27 season
    # ("ahead of the 2026-27 campaign"), but the specific sentence that
    # actually matched "Arsenal" (a headline-style restatement further into
    # the article) states no year at all, so _sentence_mentions_conflicting_
    # year -- deliberately a per-sentence-only check, see its own docstring
    # -- has nothing to compare against on that sentence and lets it through.
    evidence = [make_ranked(
        "Sky Sports' supercomputer has attempted to predict who will win "
        "the Premier League title ahead of the 2026-27 campaign. Arsenal "
        "are aiming to retain their top-four status.\n\n"
        "Arsenal tipped to win Premier League title by supercomputer\n"
        "Sky Sports' supercomputer has made Arsenal the overwhelming "
        "favourites to win the Premier League title.",
        url="https://sports.yahoo.com/articles/premier-league-title-supercomputer-predicts-143000616.html",
        source_type="credible_backup_secondary",
    )]
    verdicts = decide(PREMIER_LEAGUE_24_25_MARKET, evidence)
    assert not any(v.option == "Arsenal" and v.outcome == "YES" for v in verdicts)


def test_multi_outcome_market_ignores_last_seasons_result_via_publish_date():
    # Real bug found live (2026-08-26), a second real article for the same
    # premier-league-winner-24-25 market that evaded the year-conflict fix
    # above: "Winning the Premier League last season finally answered..."
    # has no explicit year at all. Published August 2026, "last season"
    # means Arsenal's 2025-26 title, not the market's 2024-25 season.
    evidence = [make_ranked(
        "Winning the Premier League last season finally answered one of "
        "the biggest questions surrounding Mikel Arteta's Arsenal.",
        url="https://sports.yahoo.com/articles/opinion-winning-premier-league-just-213500084.html",
        source_type="credible_backup_secondary",
        published_date=date(2026, 8, 17),
    )]
    verdicts = decide(PREMIER_LEAGUE_24_25_MARKET, evidence)
    assert not any(v.option == "Arsenal" and v.outcome == "YES" for v in verdicts)


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


def test_multi_outcome_market_named_default_outside_option_list_does_not_resolve():
    # Real scenario: Osun's "resolve to 'Other'" default, which matches
    # none of the listed candidates. Behaviour deliberately CHANGED
    # 2026-08-26 (was: every listed option resolves No). Still exercises
    # the "not known" DEFAULT_OUTCOME_PATTERN trigger -- the default is
    # still parsed and surfaced, just not asserted.
    past_deadline_market = Market(
        id="osun-state-governor-2026", title=OSUN_MARKET.title, description=OSUN_DESCRIPTION,
        options=OSUN_MARKET.options, close_date=date.today() - timedelta(days=1),
    )
    verdicts = decide(past_deadline_market, [])
    assert all(v.outcome == "NO_EVIDENCE" for v in verdicts)
    assert "stated default is Other" in (verdicts[0].evidence_snippet or "")


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


def test_date_threshold_option_does_not_resolve_no_once_its_own_date_has_passed():
    # Behaviour deliberately CHANGED 2026-08-26 (was: resolves NO). An
    # elapsed date tells us the deadline is behind us, never that the
    # event failed to occur -- the same absence-as-proof fallacy removed
    # from the binary and multi-outcome stated-default paths. Unlike
    # those two, this specific path has no ground-truth data measuring
    # it (the one date-threshold market in the eval set is
    # NO_GROUND_TRUTH), so it's changed for consistency of reasoning
    # rather than on measured error.
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
    assert by_option["August 1, 2026"] == "UNCLEAR"


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
    evidence = [
        make_ranked(
            "The app has now surpassed 6,000,000 downloads worldwide, the "
            "developer announced.",
            url="https://example.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "Total downloads for the app have climbed past 6,050,000 since "
            "launch, according to figures shared by the studio on Thursday.",
            url="https://example.org/y", source_type="credible_backup",
        ),
    ]
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
    evidence = [
        make_ranked(
            "Just Fontaine's incredible record that still stands",
            url="https://www.beinsports.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "Just Fontaine's record from 1958 remains unbroken heading into the final.",
            url="https://www.espn.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(WORLD_CUP_REAL_MARKET, evidence)
    assert verdict.outcome == "NO"


def test_numeric_threshold_market_resolves_yes_when_evidence_confirms_above_threshold():
    evidence = [
        make_ranked(
            "Bitcoin surged to $67,200 on Monday amid renewed institutional buying.",
            url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "The price of bitcoin climbed as high as $67,150 during Monday's "
            "trading session, extending its recent rally.",
            url="https://www.reuters.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_numeric_threshold_market_resolves_no_when_evidence_contradicts_threshold():
    evidence = [
        make_ranked(
            "Bitcoin fell sharply to $58,400 on Monday as traders took profits.",
            url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "Bitcoin slid to roughly $58,600 during Monday's session as risk "
            "appetite waned across crypto markets.",
            url="https://www.reuters.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome == "NO"


def test_numeric_threshold_market_handles_below_direction():
    evidence = [
        make_ranked(
            "Ethereum dropped to $1,150 on Monday, extending its weekly decline.",
            url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "Ether slipped below the $1,160 mark on Monday, its lowest level in weeks.",
            url="https://www.reuters.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(ETHEREUM_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_numeric_threshold_market_handles_reach_at_least_phrasing():
    evidence = [
        make_ranked(
            "Gold prices hit $4,512 an ounce on Friday, a fresh all-time high.",
            url="https://www.cnbc.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "Spot gold touched $4,505 an ounce on Friday, setting a new record.",
            url="https://www.reuters.com/x", source_type="credible_backup",
        ),
    ]
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
    evidence = [
        make_ranked(
            "Just Fontaine's incredible record that still stands",
            url="https://www.beinsports.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "Just Fontaine's record from 1958 remains unbroken heading into the final.",
            url="https://www.espn.com/x", source_type="credible_backup",
        ),
    ]
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


def test_numeric_threshold_market_ignores_aggregate_field_wide_goal_count():
    # Real bug found live (2026-09-02, 42-market eval): real evidence for
    # this exact market, "FIFA's biggest global showpiece saw 1,039
    # players from 48 nations play across 16 venues and score 308 goals."
    # -- an AGGREGATE tournament-wide total (every player's combined
    # goals) wrongly extracted as 308 and compared against the 14-goal
    # INDIVIDUAL threshold, resolving YES. "N players/teams from M
    # nations" is boilerplate describing the field's scale, never a
    # single subject's own statistic -- generalizes beyond this one
    # tournament.
    evidence = [
        make_ranked(
            "FIFA's biggest global showpiece saw 1,039 players from 48 "
            "nations play across 16 venues and score 308 goals.",
            url="https://www.aljazeera.com/x", source_type="credible_backup",
        ),
        make_ranked(
            "A record 1,039 players from 48 competing nations combined "
            "for 308 goals across the tournament's 16 host venues.",
            url="https://www.bbc.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(WORLD_CUP_REAL_MARKET, evidence)
    assert verdict.outcome != "YES"


def test_numeric_threshold_market_ignores_one_shy_of_the_record_phrasing():
    # Real bug found live (2026-09-02, corroboration re-verification run):
    # real evidence for this exact market, "Watch Out, Messi: Mbappe
    # Scores 18th World Cup Goal, One Shy Of All-Time Record" -- wrongly
    # confirmed the record as broken. "One shy of" plainly states the
    # record was NOT met -- a near-miss reported as news precisely
    # because it didn't happen.
    evidence = [make_ranked(
        "Watch Out, Messi: Mbappe Scores 18th World Cup Goal, One Shy Of "
        "All-Time Record",
        url="https://foxsports.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(WORLD_CUP_REAL_MARKET, evidence)
    assert verdict.outcome != "YES"


# Real market pulled from Polymarket (will-spcx-reach-145-in-august-2026)
# -- title verbatim. Real description states this resolves on "any
# 1-minute candle" reaching the price "at any point during August 2026",
# with Pyth's own historical price data as the resolution source -- a
# real, structural gap ordinary news search cannot answer, deliberately
# deferred (see docs/superpowers/plans/2026-08-10-...: "Submarket
# price-history tracking for numeric-threshold markets").
SPCX_MARKET = Market(
    id="will-spcx-reach-145-in-august-2026",
    title="Will SpaceX (SPCX) hit (HIGH) $145 in August?",
    description=(
        "This market will resolve to \"Yes\" if, at any point during "
        "August 2026, any 1-minute candle for SpaceX (SPCX) has a final "
        "\"High\" price equal to or above the listed price. Otherwise, "
        "this market will resolve to \"No\"."
    ),
    options=[],
    close_date=date(2026, 9, 1),
)


def test_numeric_threshold_market_declines_to_answer_a_price_window_market_from_a_news_snapshot():
    # Real bug found live (2026-09-02): this real market (truth: Yes)
    # wrongly resolved NO off "SpaceX Stock Price Prediction: SPCX Sinks
    # 35%, Eyes August Earnings" -- a snapshot headline, not the market's
    # real "at any point during the window" condition. No amount of
    # incidental news-headline text can answer that; it needs the real
    # historical price-history data source, which is explicitly
    # deferred. The archive pass made this WORSE, not better: it now
    # finds a misleading snapshot where it previously found nothing,
    # converting a safe unresolved into a wrong answer.
    evidence = [make_ranked(
        "SpaceX Stock Price Prediction: SPCX Sinks 35%, Eyes August "
        "Earnings - Coin Gabbar",
        url="https://news.google.com/rss/articles/spcx-sinks",
        source_type="general",
    )]
    verdict = decide(SPCX_MARKET, evidence)
    assert verdict.outcome != "NO"
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
    evidence = [
        make_ranked(
            "Bitcoin climbed from $61,000 to $64,500 over the trading session.",
            url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "Bitcoin rose steadily throughout the session, ending the day near $64,450.",
            url="https://www.reuters.com/x", source_type="credible_backup",
        ),
    ]
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
    evidence = [
        make_ranked(
            "Bitcoin trading volume dipped slightly, with the price at $50 "
            "before the announcement.",
            url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "Bitcoin was changing hands at just $50 ahead of Monday's Fed announcement.",
            url="https://www.reuters.com/x", source_type="credible_backup",
        ),
    ]
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
    evidence = [
        make_ranked(
            "The game sold 1,200,000 copies in its first week, publishers said.",
            url="https://www.gamesindustry.biz/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "First-week sales for the title reached roughly 1.19 million units, "
            "according to figures reported by the publisher.",
            url="https://www.ign.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(SALES_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_numeric_threshold_prefers_real_count_over_a_trailing_year():
    # Both a real count and a year can appear in the same sentence -- the
    # year must be skipped even when it comes LAST (the position
    # _extract_latest_number normally trusts most), falling back to the
    # genuine count earlier in the sentence instead.
    evidence = [
        make_ranked(
            "The game sold 1,200,000 copies since its 2023 launch.",
            url="https://www.gamesindustry.biz/x", source_type="credible_backup_secondary",
        ),
        make_ranked(
            "Total unit sales for the game have reached 1,190,000 since it "
            "launched, the publisher reported this week.",
            url="https://www.ign.com/x", source_type="credible_backup",
        ),
    ]
    verdict = decide(SALES_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_non_numeric_binary_market_unaffected():
    # Regression guard: CLARITY Act (legislative binary, no $ threshold in
    # its own text) must be completely unaffected -- routed to the
    # existing _decide_binary path, not misdetected as threshold-shaped.
    evidence = [
        make_ranked("The bill was signed into law by the President today."),
        make_ranked(
            "The measure was enacted into law earlier today.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
    ]
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
    # Two sources, each needing its own semantic positive/negative check
    # (calls 1-2, 3-4), then one more call for the corroboration wire-
    # duplicate domain-merge check (call 5) -- see CORROBORATION_MIN_DOMAINS.
    mock_cos_sim.side_effect = [[[0.75]], [[0.30]], [[0.80]], [[0.20]], [[0.50]]]

    evidence = [
        make_ranked(
            "Regulators granted approval for the subcutaneous formulation this week.",
            url="https://example.com/x", source_type="credible_backup",
        ),
        make_ranked(
            "The subcutaneous formulation cleared regulatory review this week.",
            url="https://example.org/y", source_type="credible_backup",
        ),
    ]
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
    evidence = [
        make_ranked("The bill was signed into law by the President today."),
        make_ranked(
            "The measure was enacted into law earlier today.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
    ]
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
    evidence = [
        make_ranked(
            "Sanofi's subcutaneous Sarclisa Escena approved in the US as first "
            "anticancer treatment administered via on-body injector",
            url="https://uk.finance.yahoo.com/news/press-release-sanofi-subcutaneous-sarclisa-123500426.html",
            source_type="credible_backup_secondary",
        ),
        # Second, independently-worded source -- corroboration (see
        # CORROBORATION_MIN_DOMAINS) needs a distinct domain agreeing.
        make_ranked(
            "Sanofi said Wednesday that the FDA approved the subcutaneous "
            "formulation of Sarclisa, giving patients a faster at-home "
            "injection option instead of the original intravenous infusion.",
            url="https://apnews.com/x", source_type="credible_backup",
        ),
    ]
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


def test_numeric_threshold_ignores_a_bare_may_hedge_and_unrelated_number():
    # Real bug found live (2026-09-02), surfaced by the date-scoped
    # archive pass (headline-only evidence made this failure mode more
    # reachable than full article prose did): the real world-cup-most-
    # goals-record-broken market (truth: No -- Just Fontaine's 13-goal
    # SINGLE-TOURNAMENT record, real record still stands) wrongly
    # resolved YES off a vague, multi-sport summary headline: "World Cup
    # goals, 800m landmarks and Tour de France wins: Famous records that
    # may soon be broken - The Athletic - The New York Times".
    # _extract_latest_number grabbed "800" from the unrelated "800m
    # landmarks" (an athletics reference, not World Cup goals) and
    # compared it against the 14-goal threshold. "may" -- despite
    # "might be"/"could be"/"would be" already being covered -- was
    # missing from NEGATION_HEDGE_WORDS entirely, so this sentence never
    # got hedge-blocked at all before reaching number extraction.
    evidence = [make_ranked(
        "World Cup goals, 800m landmarks and Tour de France wins: "
        "Famous records that may soon be broken - The Athletic - The "
        "New York Times",
        url="https://news.google.com/rss/articles/records-may-be-broken",
        source_type="credible_backup",
    )]
    verdict = decide(WORLD_CUP_REAL_MARKET, evidence)
    assert verdict.outcome != "YES"
