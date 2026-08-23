# tests/test_query_builder.py
from datetime import date
from resolution_finder.models import Market
from resolution_finder.query_builder import extract_urls, extract_entities, build_queries

CLARITY_MARKET = Market(
    id="clarity-act-2026",
    title="Will the CLARITY act be signed into law in 2026?",
    description=(
        "This market resolves to \"Yes\" if the Digital Asset Market Clarity Act "
        "of 2025 (H.R. 3633) is approved by both the U.S. House of Representatives "
        "and the U.S. Senate. The primary resolution source will be the legislation "
        "tracker on https://www.congress.gov/bill/119th-congress/house-bill/3633."
    ),
    options=[],
    close_date=date(2026, 12, 31),
)

NOBEL_MARKET = Market(
    id="nobel-peace-2026",
    title="Who will win the 2026 Nobel Peace Prize?",
    description="This market will be settled by the Norwegian Nobel Committee.",
    options=["Yulia Navalnaya", "Volodymyr Zelenskyy", "UNRWA", "Pope Leo XIV", "Donald Trump"],
    close_date=date(2027, 3, 31),
)


def test_extract_urls_finds_congress_link_without_trailing_period():
    urls = extract_urls(CLARITY_MARKET.description)
    assert urls == ["https://www.congress.gov/bill/119th-congress/house-bill/3633"]


def test_build_queries_includes_title():
    queries = build_queries(CLARITY_MARKET)
    assert CLARITY_MARKET.title in queries


def test_build_queries_includes_each_option():
    queries = build_queries(NOBEL_MARKET)
    for option in NOBEL_MARKET.options:
        assert any(option in q for q in queries)


# Real description copied verbatim from data/markets.json
# (international-2026-champion) -- this is what actually produced the
# garbled live query, not a constructed example.
TI2026_DESCRIPTION = (
    "This market will resolve based on the team officially recognized as the champion of "
    "The International 2026. The winning team's market will resolve to \"Yes\". All other "
    "team markets will resolve to \"No\". If multiple teams are officially recognized as "
    "champions, the team whose listed name appears first alphabetically will have its "
    "market resolve to \"Yes\", and all remaining team markets will resolve to \"No\". If "
    "The International 2026 champion has not been officially determined by September 6, "
    "2026, 11:59 PM ET, or if the champion is not one of the listed teams, all markets "
    "will resolve to \"No\". The resolution source for this market will be official "
    "information from Dotabuff (https://www.dotabuff.com). However, a consensus of "
    "credible reporting may also be used."
)

TI2026_MARKET = Market(
    id="international-2026-champion",
    title="The International 2026 Champion",
    description=TI2026_DESCRIPTION,
    options=["Team Yandex", "Team Vision", "Team Falcons", "BoomBoys"],
    close_date=date(2026, 9, 6),
)


def test_extract_entities_trims_leading_sentence_initial_word():
    # Real bug found live (2026-08-23): the raw regex match "If The
    # International" (from "...resolve to \"No\". If The International
    # 2026 champion has not...") chained the sentence-initial "If" onto
    # the real entity right after it. The trimmed "If" must not survive,
    # but "The International" (the market's own real name) must.
    entities = extract_entities(TI2026_DESCRIPTION)
    assert "If The International" not in entities
    assert "The International" in entities


def test_extract_entities_drops_abbreviation_only_match():
    # Real bug found live: "11:59 PM ET" produced the raw match "PM ET" --
    # two adjacent short capitalized abbreviations with no real entity
    # value, not a genuine multi-word name.
    entities = extract_entities(TI2026_DESCRIPTION)
    assert "PM ET" not in entities


def test_build_queries_does_not_garble_on_ti2026_market():
    queries = build_queries(TI2026_MARKET)
    assert not any("If The International" in q for q in queries)
    assert not any("PM ET" in q for q in queries)


def test_extract_entities_trims_trailing_word_glued_across_sentence_boundary():
    # Real bug found live (2026-08-23, fda-approves-sanofi... market): the
    # raw match "PM ET. Otherwise" spans a sentence boundary, because the
    # period inside "ET." isn't treated as a boundary by the character
    # class -- "Otherwise" is the NEXT sentence's leading word, not part
    # of any entity. After trimming it, only "PM ET" (both <=2 chars)
    # remains, so the whole match must be dropped.
    text = (
        "...at 11:59 PM ET. Otherwise, this market will resolve to \"No.\""
    )
    entities = extract_entities(text)
    assert not any("Otherwise" in e for e in entities)
    assert "PM ET" not in entities


def test_extract_entities_trims_leading_question_word_from_title():
    # Same bug class, title side: "Will the CLARITY act..." raw-matched
    # "Will the CLARITY" -- "Will" is a question word, never part of a
    # real entity name.
    entities = extract_entities(CLARITY_MARKET.title)
    assert "Will the CLARITY" not in entities
