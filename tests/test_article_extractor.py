from unittest.mock import patch, MagicMock
import requests
from resolution_finder.article_extractor import extract_article_text


@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.requests.get")
def test_extract_article_text_returns_clean_text(mock_get, mock_extract):
    mock_response = MagicMock()
    mock_response.text = "<html>...</html>"
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response
    mock_extract.return_value = "The bill was signed into law today."

    result = extract_article_text("https://example.com/article")
    assert result == "The bill was signed into law today."


@patch("resolution_finder.article_extractor.requests.get")
def test_extract_article_text_returns_none_on_network_error(mock_get):
    mock_get.side_effect = requests.ConnectionError("failed")
    result = extract_article_text("https://example.com/article")
    assert result is None


@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.requests.get")
def test_extract_article_text_returns_none_when_no_content_extracted(mock_get, mock_extract):
    mock_response = MagicMock()
    mock_response.text = "<html></html>"
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response
    mock_extract.return_value = None

    result = extract_article_text("https://example.com/empty")
    assert result is None
