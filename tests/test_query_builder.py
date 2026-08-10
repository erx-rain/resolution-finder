# tests/test_query_builder.py
from datetime import date
from resolution_finder.models import Market
from resolution_finder.query_builder import extract_urls, build_queries

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
