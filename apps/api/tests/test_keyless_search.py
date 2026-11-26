import base64

import httpx
import pytest

from sag_api.tools import keyless_search


def test_bing_decodes_redirect_and_extracts_results():
    target = "https://example.com/report"
    encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    html = f'<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=a1{encoded}">Report</a></h2>'
    html += '<div class="b_caption"><p>Evidence &amp; facts</p></div></li>'
    assert keyless_search.parse_bing(html) == [
        {"url": target, "title": "Report", "content": "Evidence & facts"}
    ]


@pytest.mark.asyncio
async def test_keyless_falls_back_to_duckduckgo_without_authorization(monkeypatch):
    calls = []
    original = httpx.AsyncClient

    def handler(request):
        calls.append(request.url.host)
        assert "authorization" not in request.headers
        if request.url.host == "www.bing.com":
            return httpx.Response(429)
        return httpx.Response(
            200,
            text='<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Freport">'
            'Report</a><a class="result__snippet">Evidence</a>',
        )

    monkeypatch.setattr(
        keyless_search.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
    )
    name, results = await keyless_search.search_keyless("SBS", 6)
    assert calls == ["www.bing.com", "html.duckduckgo.com"]
    assert name == "DuckDuckGo"
    assert results == [{"url": "https://example.com/report", "title": "Report", "content": "Evidence"}]
