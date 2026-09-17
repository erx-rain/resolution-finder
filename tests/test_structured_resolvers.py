from datetime import date

from resolution_finder import structured_resolvers
from resolution_finder.models import Market, Verdict
from resolution_finder.structured_resolvers import resolve_structured

CLARITY = Market(
    id="clarity-act-2026",
    title="Will the CLARITY act be signed into law in 2026?",
    description="If these conditions are not met by the deadline, the market resolves to \"No\".",
    options=[],
    close_date=date(2026, 12, 31),
)


def test_non_structured_market_returns_none_without_fetching():
    fetched = []
    assert resolve_structured(CLARITY, fetch=fetched.append) is None
    assert fetched == []


def test_first_resolver_that_claims_the_market_wins(monkeypatch):
    claimed = [Verdict(outcome="UNCLEAR", confidence=0.0, evidence_snippet="x",
                       source_url=None, source_type="primary")]
    calls = []
    monkeypatch.setattr(structured_resolvers, "STRUCTURED_RESOLVERS", [
        lambda market, fetch: calls.append("first") or None,
        lambda market, fetch: calls.append("second") or claimed,
        lambda market, fetch: calls.append("third") or None,
    ])
    assert resolve_structured(CLARITY, fetch=lambda url: "") is claimed
    assert calls == ["first", "second"]
