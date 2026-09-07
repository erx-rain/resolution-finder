# tests/test_eval_cache.py
from datetime import date

from resolution_finder.models import ArticleRef
from resolution_finder import eval_cache


def test_load_cache_returns_empty_dict_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_cache, "CACHE_PATH", str(tmp_path / "does-not-exist.json"))
    assert eval_cache.load_cache() == {}


def test_load_cache_returns_empty_dict_on_corrupt_json(tmp_path, monkeypatch):
    # A cache file that's been half-written (e.g. an interrupted run) must
    # not crash every subsequent eval run -- fail open, not closed, since
    # this is a regenerable local performance aid, not authoritative data.
    path = tmp_path / "cache.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(eval_cache, "CACHE_PATH", str(path))
    assert eval_cache.load_cache() == {}


def test_save_and_load_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_cache, "CACHE_PATH", str(tmp_path / "cache.json"))
    eval_cache.save_cache({"some-market": {"queries": ["q1"], "candidates": [], "article_text": {}}})
    assert eval_cache.load_cache() == {"some-market": {"queries": ["q1"], "candidates": [], "article_text": {}}}


def test_load_market_snapshot_returns_none_for_unknown_market():
    assert eval_cache.load_market_snapshot({}, "never-cached-market") is None


def test_store_and_load_market_snapshot_preserves_article_ref_fields():
    # Real bug class this guards against: a naive round-trip through JSON
    # could silently drop or mangle Optional fields (published_date,
    # summary, source_domain) -- a replayed run must see the EXACT same
    # ArticleRef the live run did, since verdict_engine reads several of
    # these fields (e.g. published_date for season-conflict detection).
    ref_full = ArticleRef(
        url="https://apnews.com/x",
        title="AP headline",
        source_type="credible_backup",
        published_date=date(2026, 4, 2),
        summary=None,
        source_domain="apnews.com",
    )
    ref_minimal = ArticleRef(
        url="https://news.google.com/rss/y",
        title="Google News headline",
        source_type="general",
    )
    article_text = {ref_full.url: "Full article body here.", ref_minimal.url: None}

    cache: dict = {}
    eval_cache.store_market_snapshot(cache, "test-market", ["query one"], [ref_full, ref_minimal], article_text)
    snapshot = eval_cache.load_market_snapshot(cache, "test-market")

    assert snapshot["queries"] == ["query one"]
    assert snapshot["article_text"] == article_text
    got_full, got_minimal = snapshot["candidates"]
    assert got_full == ref_full
    assert got_minimal == ref_minimal


def test_store_and_load_market_snapshot_survives_a_real_json_round_trip(tmp_path, monkeypatch):
    # The dict-in-memory round trip above proves the (de)serializers are
    # correct; this proves the actual file I/O path (json.dump/json.load)
    # doesn't lose anything either -- e.g. date objects have no native
    # JSON representation, so this is the real test that the isoformat
    # string conversion is wired all the way through.
    monkeypatch.setattr(eval_cache, "CACHE_PATH", str(tmp_path / "cache.json"))
    ref = ArticleRef(
        url="https://reuters.com/x", title="t", source_type="credible_backup",
        published_date=date(2026, 1, 15), summary="a snippet", source_domain="reuters.com",
    )
    cache: dict = {}
    eval_cache.store_market_snapshot(cache, "m1", ["q"], [ref], {ref.url: "text"})
    eval_cache.save_cache(cache)

    reloaded = eval_cache.load_cache()
    snapshot = eval_cache.load_market_snapshot(reloaded, "m1")
    assert snapshot["candidates"] == [ref]
    assert snapshot["article_text"] == {ref.url: "text"}
