"""Web search providers — a keyless default + pluggable third-party services.

`duckduckgo` works with no API key (our "starting version of our own"). `tavily` and `brave`
give better results but need a key (configured via the SecretStore / env). `exa` and
`parallel` are the other first-class search choices. All providers return a uniform
`list[SearchResult]`; the heavy client libs are lazy-imported.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

_TIMEOUT = 20.0


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


class WebSearchProvider(ABC):
    name: str = "base"
    requires_key: bool = False

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]: ...


class DuckDuckGoProvider(WebSearchProvider):
    """Keyless default via the `ddgs` library."""

    name = "duckduckgo"
    requires_key = False

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        from ddgs import DDGS

        rows = DDGS().text(query, max_results=max_results) or []
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("href", "") or r.get("url", ""),
                snippet=r.get("body", "") or r.get("snippet", ""),
            )
            for r in rows
        ]


def _snippet(value: object) -> str:
    """Normalize providers that return one or more excerpt/highlight strings."""
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if item).strip()
    return str(value).strip() if value else ""


class ExaProvider(WebSearchProvider):
    """Exa Search API, using the same key as Exa's MCP server."""

    name = "exa"
    requires_key = True

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        resp = httpx.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
            json={
                "query": query,
                "type": "auto",
                "numResults": max_results,
                "contents": {"highlights": True},
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(
                title=str(row.get("title", "")),
                url=str(row.get("url", "")),
                snippet=_snippet(row.get("highlights") or row.get("text")),
            )
            for row in data.get("results", [])
            if isinstance(row, dict)
        ]


class ParallelProvider(WebSearchProvider):
    """Parallel Search API, with the objective represented by the user's query."""

    name = "parallel"
    requires_key = True

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        resp = httpx.post(
            "https://api.parallel.ai/v1/search",
            headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
            json={"objective": query, "search_queries": [query]},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(
                title=str(row.get("title", "")),
                url=str(row.get("url", "")),
                snippet=_snippet(row.get("excerpts") or row.get("snippet")),
            )
            for row in data.get("results", [])[:max_results]
            if isinstance(row, dict)
        ]


class TavilyProvider(WebSearchProvider):
    name = "tavily"
    requires_key = True

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        resp = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": self.api_key, "query": query, "max_results": max_results},
            timeout=_TIMEOUT,
        )
        data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
            )
            for r in data.get("results", [])
        ]


class BraveProvider(WebSearchProvider):
    name = "brave"
    requires_key = True

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "X-Subscription-Token": self.api_key,
                "Accept": "application/json",
            },
            params={"q": query, "count": max_results},
            timeout=_TIMEOUT,
        )
        data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
            )
            for r in (data.get("web", {}) or {}).get("results", [])
        ]


_PROVIDERS = {
    "duckduckgo": DuckDuckGoProvider,
    "exa": ExaProvider,
    "parallel": ParallelProvider,
    "tavily": TavilyProvider,
    "brave": BraveProvider,
}


def build_provider(name: str, api_key: Optional[str] = None) -> WebSearchProvider:
    cls = _PROVIDERS.get(name, DuckDuckGoProvider)
    if cls.requires_key:
        if not api_key:
            raise ValueError(f"web search provider '{name}' needs an API key")
        return cls(api_key)  # type: ignore[call-arg]
    return cls()  # type: ignore[call-arg]


def provider_names() -> list[str]:
    return list(_PROVIDERS)
