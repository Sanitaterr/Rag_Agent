from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool


_TAG_DELIMITER = r"(?:\|{2}|\uff5c{2})"
_TOOL_CALLS_RE = re.compile(
    rf"<{_TAG_DELIMITER}DSML{_TAG_DELIMITER}tool_calls[^>]*>\s*(?P<body>.*?)\s*</{_TAG_DELIMITER}DSML{_TAG_DELIMITER}tool_calls>",
    re.DOTALL,
)
_INVOKE_RE = re.compile(
    rf"<{_TAG_DELIMITER}DSML{_TAG_DELIMITER}invoke\s+name=\"(?P<name>[^\"]+)\"[^>]*>\s*(?P<body>.*?)\s*</{_TAG_DELIMITER}DSML{_TAG_DELIMITER}invoke>",
    re.DOTALL,
)
_PARAMETER_RE = re.compile(
    rf"<{_TAG_DELIMITER}DSML{_TAG_DELIMITER}parameter\s+name=\"(?P<name>[^\"]+)\"[^>]*>\s*(?P<value>.*?)\s*</{_TAG_DELIMITER}DSML{_TAG_DELIMITER}parameter>",
    re.DOTALL,
)

_TOOL_ALIASES = {
    "get_telemetry_data": "get_latest_telemetry",
}
_TOOL_CALL_OPEN_MARKERS = ("<||DSML||tool_calls", "<\uff5c\uff5cDSML\uff5c\uff5ctool_calls")
_TOOL_CALL_CLOSE_MARKERS = ("</||DSML||tool_calls>", "</\uff5c\uff5cDSML\uff5c\uff5ctool_calls>")


def normalize_text_tool_calls(message: BaseMessage, available_tools: list[BaseTool]) -> BaseMessage:
    """Convert DeepSeek DSML text calls into LangChain tool calls and hide DSML text."""
    if not isinstance(message, AIMessage):
        return message

    content = message.content
    if not _content_contains_dsml(content):
        return message

    text = _content_to_text(content)
    parsed_tool_calls = _parse_tool_calls(text, available_tools)
    tool_calls = list(message.tool_calls or []) + parsed_tool_calls
    return AIMessage(
        content=strip_dsml_tool_calls(text),
        additional_kwargs=dict(message.additional_kwargs),
        response_metadata=dict(message.response_metadata),
        id=message.id,
        tool_calls=tool_calls,
        usage_metadata=message.usage_metadata,
    )


def strip_dsml_tool_calls(content: str, *, trim: bool = True) -> str:
    """Remove complete DSML tool-call blocks from user-visible text."""
    if "DSML" not in content:
        return content
    cleaned = _TOOL_CALLS_RE.sub("", content)
    return cleaned.strip() if trim else cleaned


def strip_dsml_from_message(message: BaseMessage) -> BaseMessage:
    """Return a message copy with DSML tool-call markup removed from text content."""
    if not _content_contains_dsml(message.content):
        return message

    cleaned_content = _strip_dsml_from_content(message.content)
    return message.model_copy(update={"content": cleaned_content})


class DsmlStreamFilter:
    """Drop DSML tool-call blocks from token streams, including split markers."""

    def __init__(self) -> None:
        self._buffer = ""
        self._inside_dsml = False

    def feed(self, token: str) -> str:
        """Consume one streamed token and return only safe user-visible text."""
        if not token:
            return ""

        self._buffer += token
        return self._drain(final=False)

    def flush(self) -> str:
        """Flush any buffered non-DSML text at the end of the stream."""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> str:
        output: list[str] = []

        while self._buffer:
            if self._inside_dsml:
                close_index = _earliest_marker_index(self._buffer, _TOOL_CALL_CLOSE_MARKERS)
                if close_index < 0:
                    if final:
                        self._buffer = ""
                        self._inside_dsml = False
                    break

                close_marker = _matching_marker_at(self._buffer, close_index, _TOOL_CALL_CLOSE_MARKERS)
                self._buffer = self._buffer[close_index + len(close_marker) :]
                self._inside_dsml = False
                continue

            open_index = _earliest_marker_index(self._buffer, _TOOL_CALL_OPEN_MARKERS)
            if open_index >= 0:
                output.append(self._buffer[:open_index])
                self._buffer = self._buffer[open_index:]
                self._inside_dsml = True
                continue

            keep = 0 if final else _longest_marker_prefix_suffix(self._buffer, _TOOL_CALL_OPEN_MARKERS)
            emit_length = len(self._buffer) - keep
            if emit_length <= 0:
                break

            output.append(self._buffer[:emit_length])
            self._buffer = self._buffer[emit_length:]

        return strip_dsml_tool_calls("".join(output), trim=False)


def _parse_tool_calls(content: str, available_tools: list[BaseTool]) -> list[dict]:
    """Parse only registered tools and schema-supported arguments."""
    tools_by_name = {tool.name: tool for tool in available_tools}
    calls: list[dict] = []

    for block_match in _TOOL_CALLS_RE.finditer(content):
        for invoke_match in _INVOKE_RE.finditer(block_match.group("body")):
            raw_tool_name = invoke_match.group("name").strip()
            tool_name = _TOOL_ALIASES.get(raw_tool_name, raw_tool_name)
            tool = tools_by_name.get(tool_name)
            if tool is None:
                continue

            calls.append(
                {
                    "name": tool_name,
                    "args": _parse_args(invoke_match.group("body"), tool),
                    "id": f"dsml_{uuid4().hex}",
                    "type": "tool_call",
                }
            )

    return calls


def _parse_args(invoke_body: str, tool: BaseTool) -> dict[str, str]:
    """Keep parsed arguments compatible with the tool's Pydantic schema."""
    schema = tool.args_schema
    allowed_args = set(getattr(schema, "model_fields", {}) or {})
    args: dict[str, str] = {}

    for parameter_match in _PARAMETER_RE.finditer(invoke_body):
        name = parameter_match.group("name").strip()
        if allowed_args and name not in allowed_args:
            continue
        args[name] = parameter_match.group("value").strip()

    return args


def _content_contains_dsml(content: Any) -> bool:
    """Return whether LangChain message content contains DSML text."""
    if isinstance(content, str):
        return "DSML" in content
    if isinstance(content, list):
        return any(_content_contains_dsml(item) for item in content)
    if isinstance(content, dict):
        return any(_content_contains_dsml(value) for value in content.values())
    return False


def _content_to_text(content: Any) -> str:
    """Flatten message content enough to parse provider-emitted DSML blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content)


def _strip_dsml_from_content(content: Any) -> Any:
    """Remove DSML from supported LangChain content shapes."""
    if isinstance(content, str):
        return strip_dsml_tool_calls(content)
    if isinstance(content, list):
        cleaned_items: list[Any] = []
        for item in content:
            if isinstance(item, str):
                cleaned_items.append(strip_dsml_tool_calls(item))
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                cleaned_items.append({**item, "text": strip_dsml_tool_calls(item["text"])})
            else:
                cleaned_items.append(item)
        return cleaned_items
    return content


def _earliest_marker_index(text: str, markers: tuple[str, ...]) -> int:
    """Find the earliest marker occurrence in text."""
    indexes = [index for marker in markers if (index := text.find(marker)) >= 0]
    return min(indexes) if indexes else -1


def _matching_marker_at(text: str, index: int, markers: tuple[str, ...]) -> str:
    """Return the marker that starts at the given index."""
    for marker in markers:
        if text.startswith(marker, index):
            return marker
    return ""


def _longest_marker_prefix_suffix(text: str, markers: tuple[str, ...]) -> int:
    """Keep the suffix that may become a DSML opening marker in the next token."""
    max_length = 0
    for marker in markers:
        limit = min(len(text), len(marker) - 1)
        for length in range(1, limit + 1):
            if text.endswith(marker[:length]):
                max_length = max(max_length, length)
    return max_length
