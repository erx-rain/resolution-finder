from unittest.mock import patch, MagicMock
from datetime import date
from resolution_finder.models import Market, ArticleRef
from resolution_finder.relevance_ranker import rank_by_relevance, best_below_threshold

MARKET = Market(
    id="m1", title="Will X happen?", description="Resolves Yes if X.",
    options=[], close_date=date(2026, 12, 31),
)


@patch("resolution_finder.relevance_ranker.util.cos_sim")
@patch("resolution_finder.relevance_ranker._get_model")
def test_filters_below_threshold(mock_get_model, mock_cos_sim):
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.9]], [[0.1]]]

    articles = [
        (ArticleRef(url="https://a.com", title="A", source_type="primary"), "relevant text"),
        (ArticleRef(url="https://b.com", title="B", source_type="general"), "irrelevant text"),
    ]
    ranked = rank_by_relevance(MARKET, articles, threshold=0.35)
    assert len(ranked) == 1
    assert ranked[0].article.url == "https://a.com"


@patch("resolution_finder.relevance_ranker.util.cos_sim")
@patch("resolution_finder.relevance_ranker._get_model")
def test_sorts_by_similarity_descending(mock_get_model, mock_cos_sim):
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.5]], [[0.8]]]

    articles = [
        (ArticleRef(url="https://a.com", title="A", source_type="primary"), "text a"),
        (ArticleRef(url="https://b.com", title="B", source_type="general"), "text b"),
    ]
    ranked = rank_by_relevance(MARKET, articles, threshold=0.35)
    assert [r.article.url for r in ranked] == ["https://b.com", "https://a.com"]


def test_empty_articles_returns_empty_list():
    ranked = rank_by_relevance(MARKET, [])
    assert ranked == []


@patch("resolution_finder.relevance_ranker.util.cos_sim")
@patch("resolution_finder.relevance_ranker._get_model")
def test_best_below_threshold_returns_the_highest_scoring_candidate_regardless_of_threshold(
    mock_get_model, mock_cos_sim
):
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.10]], [[0.22]]]  # both well below SIMILARITY_THRESHOLD

    articles = [
        (ArticleRef(url="https://a.com", title="A", source_type="primary"), "text a"),
        (ArticleRef(url="https://b.com", title="B", source_type="general"), "text b"),
    ]
    best = best_below_threshold(MARKET, articles)
    assert best is not None
    assert best.article.url == "https://b.com"
    assert best.similarity == 0.22


def test_best_below_threshold_returns_none_for_no_articles():
    assert best_below_threshold(MARKET, []) is None
