from unittest.mock import patch

import pytest
import requests

from resolution_finder.page_fetch import FetchError, http_fetch


def _response(body: bytes, content_type: str, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body
    response.headers["Content-Type"] = content_type
    # What requests' HTTP adapter does for a real response.
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    return response


def test_http_fetch_decodes_utf8_when_the_server_names_no_charset():
    # federalreserve.gov answers "Content-Type: text/html" with no charset
    # (checked live 2026-09-17). requests would fall back to ISO-8859-1 and
    # mangle the U+2011 hyphen real FOMC statements use in "3-3/4".
    body = "at 3-1/2 to 3‑3/4 percent".encode("utf-8")
    with patch("resolution_finder.page_fetch.requests.get", return_value=_response(body, "text/html")):
        assert http_fetch("https://www.federalreserve.gov/x.htm") == "at 3-1/2 to 3‑3/4 percent"


def test_http_fetch_respects_a_declared_charset():
    body = "café".encode("latin-1")
    with patch("resolution_finder.page_fetch.requests.get",
               return_value=_response(body, "text/html; charset=ISO-8859-1")):
        assert http_fetch("https://example.org/") == "café"


def test_http_fetch_raises_fetch_error_on_http_error_status():
    with patch("resolution_finder.page_fetch.requests.get", return_value=_response(b"", "text/html", 503)):
        with pytest.raises(FetchError):
            http_fetch("https://www.federalreserve.gov/x.htm")


def test_http_fetch_raises_fetch_error_on_network_failure():
    with patch("resolution_finder.page_fetch.requests.get", side_effect=requests.ConnectionError("down")):
        with pytest.raises(FetchError):
            http_fetch("https://www.federalreserve.gov/x.htm")
