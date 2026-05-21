from __future__ import annotations

import httpx
from langchain_core.tools import tool

from agent.tools.schemas import WebSearchInput
from config.settings import settings


@tool(args_schema=WebSearchInput)
def web_search(query: str) -> str:
    """
    Search the web for a given query.

    Returns a compact text summary with source URLs when Tavily is configured.
    """
    if not settings.tavily_api_key:
        return "Web search is unavailable because TAVILY_API_KEY is not configured."

    payload = {
        "api_key": settings.tavily_api_key,
        "query": query,
        "search_depth": settings.tavily_search_depth,
        "max_results": settings.tavily_max_results,
        "include_answer": True,
    }

    try:
        response = httpx.post(settings.tavily_search_url, json=payload, timeout=settings.tool_timeout_seconds)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return f"Web search failed: {exc}"

    data = response.json()
    answer = data.get("answer") or ""
    results = data.get("results") or []
    lines = [answer.strip()] if answer else []

    for index, item in enumerate(results, start=1):
        title = item.get("title") or "Untitled"
        url = item.get("url") or ""
        content = (item.get("content") or "").strip()
        lines.append(f"{index}. {title}\nURL: {url}\n摘要: {content}")

    return "\n\n".join(line for line in lines if line).strip() or "No web search results found."
