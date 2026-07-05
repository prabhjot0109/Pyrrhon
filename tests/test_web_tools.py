import httpx
import respx

from pyrrhon.core.tools.web import MAX_FETCH_CHARS, WebFetchTool, WebSearchTool


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
