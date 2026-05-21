from __future__ import annotations

import re
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool


_TAG_DELIMITER = r"(?:\|{2}|\uff5c{2})"
_TOOL_CALLS_RE = re.compile(
    rf"<{_TAG_DELIMITER}DSML{_TAG_DELIMITER}tool_calls>\s*(?P<body>.*?)\s*</{_TAG_DELIMITER}DSML{_TAG_DELIMITER}tool_calls>",
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


def normalize_text_tool_calls(message: BaseMessage, available_tools: list[BaseTool]) -> BaseMessage:
    """Convert DeepSeek DSML text calls into LangChain tool calls, or strip them."""
    if not isinstance(message, AIMessage) or message.tool_calls:
        return message

    content = message.content
    if not isinstance(content, str) or "DSML" not in content:
        return message

    tool_calls = _parse_tool_calls(content, available_tools)
    return AIMessage(
        content=_strip_dsml(content),
        additional_kwargs=dict(message.additional_kwargs),
        response_metadata=dict(message.response_metadata),
        id=message.id,
        tool_calls=tool_calls,
        usage_metadata=message.usage_metadata,
    )


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


def _strip_dsml(content: str) -> str:
    """Remove DSML blocks so internal call syntax is never shown to users."""
    return _TOOL_CALLS_RE.sub("", content).strip()
