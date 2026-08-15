import socket

import httpx
import pytest
import respx

from pyrrhon.core.tools.web import (
    MAX_FETCH_CHARS,
    WebFetchTool,
    WebSearchTool,
    is_public_host,
)


class FakeDDGS:
    """ddgs.DDGS stand-in: same .text(query, max_results=...) shape."""

    def __init__(self, results=None, error=None):
        self._results = results or []
        self._error = error
        self.calls: list[tuple[str, int]] = []

    def text(self, query: str, max_results: int = 10):
        self.calls.append((query, max_results))
        if self._error is not None:
            raise self._error
        return self._results[:max_results]


RESULTS = [
    {
        "title": "asyncio — Asynchronous I/O",
        "href": "https://docs.python.org/3/library/asyncio.html",
        "body": "asyncio is a library to write concurrent code.",
    },
    {
        "title": "Real Python: Async IO",
        "href": "https://realpython.com/async-io-python/",
        "body": "A complete walkthrough of async IO in Python.",
    },
]


async def test_search_formats_title_url_snippet_blocks():
    tool = WebSearchTool(client=FakeDDGS(results=RESULTS))
    out = await tool.run(query="python asyncio")
    blocks = out.split("\n\n")
    assert len(blocks) == 2
    assert blocks[0] == (
        "asyncio — Asynchronous I/O — https://docs.python.org/3/library/asyncio.html\n"
        "asyncio is a library to write concurrent code."
    )


async def test_search_passes_clamped_max_results():
    fake = FakeDDGS(results=RESULTS)
    await WebSearchTool(client=fake).run(query="q", max_results=99)
    assert fake.calls == [("q", 10)]  # clamped to 10


async def test_search_empty_and_error_paths():
    assert await WebSearchTool(client=FakeDDGS()).run(query="q") == "No results."
    failing = FakeDDGS(error=RuntimeError("rate limited"))
    out = await WebSearchTool(client=failing).run(query="q")
    assert out.startswith("ERROR:")
    assert "rate limited" in out


@respx.mock
async def test_fetch_strips_html_to_text():
    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(
            200,
            text="<html><body><h1>Title</h1><p>Hello <b>world</b>.</p></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    out = await WebFetchTool().run(url="https://example.com/page")
    assert "Title" in out
    assert "Hello" in out and "world" in out
    assert "<h1>" not in out and "<b>" not in out


@respx.mock
async def test_fetch_caps_output_length():
    respx.get("https://example.com/big").mock(
        return_value=httpx.Response(
            200, text="x" * 20000, headers={"content-type": "text/plain"}
        )
    )
    out = await WebFetchTool().run(url="https://example.com/big")
    assert out.endswith("(truncated)")
    assert len(out) <= MAX_FETCH_CHARS + len("\n(truncated)")


@respx.mock
async def test_fetch_http_error_is_error_string():
    respx.get("https://example.com/missing").mock(return_value=httpx.Response(404))
    out = await WebFetchTool().run(url="https://example.com/missing")
    assert out.startswith("ERROR:")
    assert "404" in out


async def test_fetch_rejects_non_http_urls():
    tool = WebFetchTool()
    assert (await tool.run(url="file:///etc/passwd")).startswith("ERROR:")
    assert (await tool.run(url="ftp://example.com/x")).startswith("ERROR:")


# --- SSRF guard ------------------------------------------------------------
#
# is_public_host resolves for real, so the fetch tests below would otherwise
# depend on live DNS for example.com. Pin it to a public address instead: the
# guard's actual logic (classifying the resolved IP) still runs untouched, and
# literal-IP cases resolve offline anyway.
@pytest.fixture(autouse=True)
def _deterministic_dns(monkeypatch):
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "169.254.169.254", "10.0.0.1", "192.168.1.1", "::1"],
)
def test_internal_hosts_are_not_public(host):
    assert is_public_host(host) is False


async def test_fetching_the_cloud_metadata_endpoint_is_refused():
    result = await WebFetchTool().run(url="http://169.254.169.254/latest/meta-data/")
    assert "ERROR" in result
    assert "internal" in result.lower()


async def test_fetching_localhost_is_refused():
    result = await WebFetchTool().run(url="http://localhost:8080/admin")
    assert "ERROR" in result


@respx.mock
async def test_a_redirect_into_the_internal_network_is_refused():
    respx.get("https://example.com/start").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    )
    result = await WebFetchTool().run(url="https://example.com/start")
    assert "ERROR" in result


@respx.mock
async def test_an_oversized_body_is_truncated_not_loaded_whole():
    respx.get("https://example.com/big").mock(
        return_value=httpx.Response(
            200, text="x" * 5_000_000, headers={"content-type": "text/plain"}
        )
    )
    result = await WebFetchTool().run(url="https://example.com/big")
    assert result.endswith("(truncated)")
    assert len(result) < 20_000
