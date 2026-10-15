"""Market-implied probability: a quoted price from the market's own source
venue, attributed, and None whenever it cannot be matched to exactly one
question. Event fields below are as Polymarket's Gamma API returned them on
2026-10-14 (trimmed to the fields read)."""
from resolution_finder.market_odds import OddsSource, market_odds

BANKRUPTCY_EVENT = {
    "title": "Which companies announce bankruptcy before 2027?",
    "markets": [
        {"groupItemTitle": "MicroStrategy", "question": "Will MicroStrategy announce bankruptcy before 2027?",
         "outcomes": "[\"Yes\", \"No\"]", "outcomePrices": "[\"0.03\", \"0.97\"]",
         "updatedAt": "2026-10-14T08:34:44.062032Z"},
        {"groupItemTitle": "Beyond Meat", "question": "Will Beyond Meat announce bankruptcy before 2027?",
         "outcomes": "[\"Yes\", \"No\"]", "outcomePrices": "[\"0.21\", \"0.79\"]",
         "updatedAt": "2026-10-14T08:30:00Z"},
    ],
}
BLOCKADE_EVENT = {
    "markets": [
        {"groupItemTitle": "October 31", "question": "US announces end of Iranian blockade by October 31, 2026?",
         "outcomes": "[\"Yes\", \"No\"]", "outcomePrices": "[\"0.125\", \"0.875\"]", "updatedAt": "2026-10-14T08:32:00Z"},
        {"groupItemTitle": "December 31", "question": "US announces end of Iranian blockade by December 31, 2026?",
         "outcomes": "[\"Yes\", \"No\"]", "outcomePrices": "[\"0.404\", \"0.596\"]", "updatedAt": "2026-10-14T08:32:00Z"},
    ],
}
MSTR_GIST = ("Source URL: https://polymarket.com/event/which-companies-announce-bankruptcy-before-2027\n"
             "Source venue: polymarket\nCategory: Finance/Economics")


def test_the_one_venue_question_named_in_our_title_is_quoted_with_its_source():
    odds = market_odds(MSTR_GIST, "Will MicroStrategy declare bankruptcy by the end of 2026?",
                       fetch=lambda slug: BANKRUPTCY_EVENT)
    assert odds.value == 0.03
    assert odds.question == "Will MicroStrategy announce bankruptcy before 2027?"
    assert "Polymarket" in odds.describe() and "2026-10-14 08:34" in odds.describe()


def test_a_sub_pool_is_matched_to_the_venue_question_with_its_own_label():
    gist = "Source URL: https://polymarket.com/event/us-announces-end-of-iranian-blockade-by"
    source = OddsSource(gist, fetch=lambda slug: BLOCKADE_EVENT)
    assert source.odds("Will the US end the Iran naval blockade by October 31?", "October 31").value == 0.125
    assert source.odds("Will the US end the Iran naval blockade by December 31?", "December 31").value == 0.404
    assert source.odds("anything", "September 30") is None


def test_no_price_when_the_question_is_ambiguous_or_the_venue_is_not_read():
    # Title names neither company: which price would it be? None.
    assert market_odds(MSTR_GIST, "Will a company declare bankruptcy?", fetch=lambda slug: BANKRUPTCY_EVENT) is None
    kalshi = OddsSource("Source URL: https://kalshi.com/markets/kxoaianth/open-ai-vs-anthropic/kxoaianth-40",
                        fetch=lambda slug: BANKRUPTCY_EVENT)
    assert kalshi.odds("Will OpenAI or Anthropic IPO first?") is None
    assert "not Polymarket" in kalshi.unavailable
    assert "names no source" in OddsSource("Category: Crypto").unavailable
    assert "could not be read" in OddsSource(MSTR_GIST, fetch=lambda slug: None).unavailable
