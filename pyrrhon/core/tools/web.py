"""Web tools: DuckDuckGo search (ddgs, keyless) and page fetch (httpx + html2text).

Real-time discipline: ddgs is a synchronous client, so the search call runs in
asyncio.to_thread(); html2text conversion (CPU-bound on large pages) is
offloaded the same way. The httpx GET is natively async.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import html2text
import httpx
from ddgs import DDGS

from pyrrhon.core.tools.base import Tool

MAX_FETCH_CHARS = 8000
MAX_SEARCH_RESULTS = 10
FETCH_TIMEOUT_SECONDS = 15.0
MAX_FETCH_BYTES = 2_000_000  # hard stop before decoding — an OOM guard
MAX_REDIRECTS = 3
INTERNAL_REFUSAL = (
    "ERROR: refusing to fetch an internal address. web_fetch reaches the public "
    "web only — loopback, private, link-local and reserved ranges are blocked."
)


def is_public_host(host: str) -> bool:
    """True only if every address `host` resolves to is publicly routable.

    Every address, not the first: a hostname an attacker controls can resolve
    to one public and one internal address, and picking either at connect time
    would be a coin flip. DNS is a blocking call, so callers run this in a
    worker thread.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    for *_unused, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return bool(infos)


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
        # The URL is chosen by a model that reads repo content, and a cloned
        # repo is untrusted input (M11) — so this tool must not be a way to
        # reach the cloud metadata endpoint or anything else behind the
        # network boundary.
        #
        # Redirects are followed BY HAND: httpx's follow_redirects would chase
        # a 302 into the internal network after the first host passed the check.
        for _hop in range(MAX_REDIRECTS + 1):
            if not url.startswith(("http://", "https://")):
                return f"ERROR: only http(s) URLs are supported, got '{url}'."
            host = urlparse(url).hostname or ""
            if not await asyncio.to_thread(is_public_host, host):
                return INTERNAL_REFUSAL
            try:
                async with httpx.AsyncClient(
                    follow_redirects=False, timeout=FETCH_TIMEOUT_SECONDS
                ) as client:
                    async with client.stream("GET", url) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                return f"ERROR: HTTP {response.status_code} for {url}"
                            url = urljoin(url, location)
                            continue
                        if response.status_code >= 400:
                            return f"ERROR: HTTP {response.status_code} for {url}"
                        # Streamed and capped: response.text materialised the
                        # whole body before MAX_FETCH_CHARS trimmed it, so a
                        # large response was an OOM rather than a truncation.
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) >= MAX_FETCH_BYTES:
                                break
                        content_type = response.headers.get("content-type", "")
            except httpx.HTTPError as exc:
                return f"ERROR: fetch failed: {exc}"
            text = body.decode("utf-8", errors="replace")
            if "html" in content_type:
                text = await asyncio.to_thread(_strip_html, text)
            text = text.strip()
            if len(text) > MAX_FETCH_CHARS:
                text = text[:MAX_FETCH_CHARS] + "\n(truncated)"
            return text or "(empty page)"
        return f"ERROR: too many redirects (>{MAX_REDIRECTS}) for {url}"


def _strip_html(html: str) -> str:
    converter = html2text.HTML2Text()
    converter.ignore_links = True
    converter.ignore_images = True
    converter.body_width = 0  # no hard wrapping — speakable prose stays intact
    return converter.handle(html)
