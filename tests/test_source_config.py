# tests/test_source_config.py
from resolution_finder.source_config import resolve_named_source, resolve_social_handle, resolve_instagram_handle


def test_resolves_literal_url_first():
    description = "See https://www.congress.gov/bill/119th-congress/house-bill/3633 for status."
    assert resolve_named_source(description) == "https://www.congress.gov/bill/119th-congress/house-bill/3633"


def test_resolves_named_organization_to_domain():
    description = (
        "This market will be settled based on the recipient of the 2026 Nobel "
        "Peace Prize officially announced by the Norwegian Nobel Committee."
    )
    assert resolve_named_source(description) == "nobelprize.org"


def test_returns_none_when_no_named_source():
    description = "This market resolves based on consensus of credible reporting."
    assert resolve_named_source(description) is None


def test_resolves_social_handle_for_known_organization():
    description = "Officially announced by the Norwegian Nobel Committee."
    assert resolve_social_handle(description) == "@NobelPrize"


def test_resolves_social_handle_returns_none_when_unknown():
    description = "This market resolves based on consensus of credible reporting."
    assert resolve_social_handle(description) is None


def test_resolves_instagram_handle_for_known_organization():
    description = "Officially announced by the Norwegian Nobel Committee."
    assert resolve_instagram_handle(description) == "@nobelprize_org"


def test_resolves_instagram_handle_returns_none_when_unknown():
    description = "This market resolves based on consensus of credible reporting."
    assert resolve_instagram_handle(description) is None
