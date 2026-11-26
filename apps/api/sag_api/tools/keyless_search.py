"""Keyless search adapters based on ChemClaw's web-search.js public-page fallback.

These pages are best-effort, not a search API with an availability guarantee.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
from html import unescape
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx

from sag_api.core.logging import get_logger

log = get_logger("tools.keyless_search")
_MAX_BYTES = 2 * 1024 * 1024
_REDIRECTS = {301, 302, 303, 307, 308}
_ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)


def _attrs(fragment: str) -> dict[str, str]:
    return {m[1].lower(): unescape(m[2] if m[2] is not None else m[3]) for m in _ATTR.finditer(fragment)}


def _text(fragment: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", fragment)).split())


def _url(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(char.isspace() for char in value)
    ):
        return None
    return value


def _own_host(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def decode_bing_url(href: str) -> str | None:
    url = _url(href)
    if not url:
        return None
    parsed = urlsplit(url)
    if not _own_host(parsed.hostname or "", "bing.com"):
        return url
    encoded = parse_qs(parsed.query).get("u", [""])[0]
    if parsed.path != "/ck/a" or not encoded.startswith("a1"):
        return None
    raw = encoded[2:]
    try:
        return _url(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
    except (ValueError, UnicodeError, binascii.Error):
        return None


def _decode_ddg_url(href: str) -> str | None:
    try:
        parsed = urlsplit(urljoin("https://duckduckgo.com", href))
    except ValueError:
        return None
    if _own_host(parsed.hostname or "", "duckduckgo.com"):
        return _url(parse_qs(parsed.query).get("uddg", [""])[0]) if parsed.path == "/l/" else None
    return _url(parsed.geturl())


def _add(results: list[dict[str, str]], url: str | None, title: str, snippet: str) -> None:
    if url and title and not any(item["url"] == url for item in results):
        results.append({"url": url, "title": title, "content": snippet})


def parse_bing(html: str) -> list[dict[str, str]]:
    starts = [
        m.start()
        for m in re.finditer(r"<li\b([^>]*)>", html, re.I)
        if "b_algo" in _attrs(m[1]).get("class", "").split()
    ]
    results: list[dict[str, str]] = []
    for i, start in enumerate(starts):
        block = html[start : starts[i + 1] if i + 1 < len(starts) else start + 12000]
        heading = re.search(r"<h2\b[^>]*>\s*<a\b([^>]*)>(.*?)</a>", block, re.I | re.S)
        if not heading:
            continue
        paragraph = re.search(r"<p\b[^>]*>(.*?)</p>", block, re.I | re.S)
        _add(
            results,
            decode_bing_url(_attrs(heading[1]).get("href", "")),
            _text(heading[2]),
            _text(paragraph[1]) if paragraph else "",
        )
    return results


def parse_duckduckgo(html: str) -> list[dict[str, str]]:
    if re.search(r"anomaly-modal|bots use DuckDuckGo too|challenge-form", html, re.I):
        return []
    links = [
        m for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html, re.I | re.S)
        if "result__a" in _attrs(m[1]).get("class", "").split()
    ]
    results: list[dict[str, str]] = []
    for i, link in enumerate(links):
        rest = html[link.end() : links[i + 1].start() if i + 1 < len(links) else link.end() + 6000]
        snippet = next(
            (
                m for m in re.finditer(r"<(?:a|div|td)\b([^>]*)>(.*?)</(?:a|div|td)>", rest, re.I | re.S)
                if "result__snippet" in _attrs(m[1]).get("class", "").split()
            ),
            None,
        )
        _add(
            results,
            _decode_ddg_url(_attrs(link[1]).get("href", "")),
            _text(link[2]),
            _text(snippet[2]) if snippet else "",
        )
    return results


async def _fetch_html(client: httpx.AsyncClient, url: str, domain: str) -> str:
    # Match ChemClaw's one-hop regional redirect policy; never follow arbitrary origins.
    for _ in range(2):
        async with client.stream("GET", url) as response:
            if response.status_code in _REDIRECTS:
                target = urlsplit(urljoin(url, response.headers.get("location", "")))
                if (
                    target.scheme != "https"
                    or not _own_host(target.hostname or "", domain)
                    or target.username is not None
                    or target.password is not None
                    or target.port not in {None, 443}
                ):
                    raise ValueError("unexpected search redirect")
                url = target.geturl()
                continue
            if response.status_code != 200:
                raise ValueError(f"search HTTP {response.status_code}")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                if size + len(chunk) > _MAX_BYTES:
                    raise ValueError("search page too large")
                chunks.append(chunk)
                size += len(chunk)
            return b"".join(chunks).decode(response.charset_encoding or "utf-8", errors="replace")
    raise ValueError("too many search redirects")


async def search_keyless(query: str, limit: int) -> tuple[str, list[dict[str, str]]]:
    engines = (
        ("Bing", "https://www.bing.com/search", "bing.com", parse_bing),
        ("DuckDuckGo", "https://html.duckduckgo.com/html/", "duckduckgo.com", parse_duckduckgo),
    )
    async with httpx.AsyncClient(
        timeout=7.5,
        follow_redirects=False,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ChemClaw-KG/1.0)"},
    ) as client:
        for name, endpoint, domain, parse in engines:
            try:
                async with asyncio.timeout(7.5):
                    html = await _fetch_html(client, endpoint + "?" + urlencode({"q": query}), domain)
                    results = parse(html)
            except (httpx.HTTPError, ValueError, TimeoutError) as error:
                log.warning("%s free search unavailable: %s", name, type(error).__name__)
                continue
            if results:
                return name, results[:limit]
            log.warning("%s free search returned no usable results", name)
    return "", []
