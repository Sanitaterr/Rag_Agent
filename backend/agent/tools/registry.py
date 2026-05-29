from __future__ import annotations

from agent.tools.knowledge import search_docs
from agent.tools.telemetry import get_latest_telemetry, list_telemetry_devices, summarize_telemetry
from agent.tools.utility import calculate, get_current_time
from agent.tools.weather import get_weather
from agent.tools.web_search import web_search


tools = [
    get_weather,
    web_search,
    search_docs,
    get_latest_telemetry,
    summarize_telemetry,
    list_telemetry_devices,
    get_current_time,
    calculate,
]


__all__ = ["tools"]
