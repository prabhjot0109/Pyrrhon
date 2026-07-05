"""Web tools: DuckDuckGo search (ddgs, keyless) and page fetch (httpx + html2text).

Real-time discipline: ddgs is a synchronous client, so the search call runs in
asyncio.to_thread(); html2text conversion (CPU-bound on large pages) is
offloaded the same way. The httpx GET is natively async.
"""

from __future__ import annotations

import asyncio

import html2text
import httpx
from ddgs import DDGS

from pyrrhon.core.tools.base import Tool

MAX_FETCH_CHARS = 8000
MAX_SEARCH_RESULTS = 10
FETCH_TIMEOUT_SECONDS = 15.0


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web (DuckDuckGo). Use for library docs, error messages, and "
        "facts that are not in the repo. Returns title, URL, and snippet per result."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {
                "type": "integer",
                "description": "How many results (default 5, max 10)",
            },
        },
        "required": ["query"],
    }

    def __init__(self, client=None):
        self._client = client  # tests inject a fake; None → real DDGS, created lazily

    async def run(self, query: str, max_results: int = 5) -> str:
        max_results = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
        return await asyncio.to_thread(self._search, query, max_results)

    def _search(self, query: str, max_results: int) -> str:
        client = self._client or DDGS()
        try:
            results = client.text(query, max_results=max_results)
        except Exception as exc:  # ddgs raises assorted backend errors
            return f"ERROR: web search failed: {exc}"
        if not results:
            return "No results."
        blocks = [
            f"{r['title']} — {r['href']}\n{r['body']}" for r in results
        ]
        return "\n\n".join(blocks)


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "Fetch a web page and return its readable text (HTML is stripped). "
        "Only http(s) URLs. Output is capped, so fetch specific pages."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full http(s) URL to fetch"},
        },
        "required": ["url"],
    }

    async def run(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            return f"ERROR: only http(s) URLs are supported, got '{url}'."
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=FETCH_TIMEOUT_SECONDS
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            return f"ERROR: fetch failed: {exc}"
        if response.status_code >= 400:
            return f"ERROR: HTTP {response.status_code} for {url}"
        text = response.text
        if "html" in response.headers.get("content-type", ""):
            text = await asyncio.to_thread(_strip_html, text)
        text = text.strip()
        if len(text) > MAX_FETCH_CHARS:
            text = text[:MAX_FETCH_CHARS] + "\n(truncated)"
        return text or "(empty page)"


def _strip_html(html: str) -> str:
    converter = html2text.HTML2Text()
    converter.ignore_links = True
    converter.ignore_images = True
    converter.body_width = 0  # no hard wrapping — speakable prose stays intact
    return converter.handle(html)
