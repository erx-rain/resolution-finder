from sentence_transformers import SentenceTransformer, util
from resolution_finder.models import Market, ArticleRef, RankedArticle
from resolution_finder.config import EMBEDDING_MODEL_NAME, SIMILARITY_THRESHOLD

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def rank_by_relevance(
    market: Market,
    articles: list[tuple[ArticleRef, str]],
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[RankedArticle]:
    if not articles:
        return []

    model = _get_model()
    query_text = f"{market.title} {market.description}"
    query_embedding = model.encode(query_text, convert_to_tensor=True)

    ranked = []
    for article_ref, text in articles:
        article_embedding = model.encode(text, convert_to_tensor=True)
        similarity = float(util.cos_sim(query_embedding, article_embedding)[0][0])
        if similarity >= threshold:
            ranked.append(RankedArticle(article=article_ref, text=text, similarity=similarity))

    ranked.sort(key=lambda r: r.similarity, reverse=True)
    return ranked
