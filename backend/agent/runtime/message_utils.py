from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

SUMMARY_PREFIX = "[对话摘要]"
SUMMARY_TITLE_PREFIX = "标题:"


def chunk_text(chunk: BaseMessage | None) -> str:
    """Normalize LangChain message chunks into plain text."""
    if chunk is None:
        return ""

    content = chunk.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return str(content)


def checkpoint_messages(checkpoint: Any) -> list[BaseMessage]:
    """Read messages from a checkpoint tuple without leaking checkpointer internals."""
    channel_values = checkpoint.checkpoint.get("channel_values", {})
    messages = channel_values.get("messages", [])
    return list(messages or [])


def session_title(messages: list[BaseMessage]) -> str:
    """Use the first user message, or persisted summary title, as the sidebar title."""
    for message in messages:
        if isinstance(message, SystemMessage):
            title = summary_title(message)
            if title:
                return title

    for message in messages:
        if isinstance(message, HumanMessage):
            text = chunk_text(message).strip()
            if text:
                return text[:18]

    return "新对话"


def checkpoint_updated_at(checkpoint: Any) -> str:
    """Expose checkpoint time as an ISO string for deterministic sidebar ordering."""
    timestamp = checkpoint.checkpoint.get("ts")
    if isinstance(timestamp, str):
        return timestamp
    return datetime.now().isoformat()


def message_payload(message: BaseMessage) -> dict[str, str]:
    """Convert LangChain messages to the frontend message contract."""
    return {
        "id": getattr(message, "id", None) or uuid4().hex,
        "role": message_role(message),
        "text": chunk_text(message),
        "status": "done",
    }


def message_role(message: BaseMessage) -> str:
    """Map persisted LangChain message classes to UI roles."""
    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, AIMessage):
        if message.tool_calls:
            return ""
        return "assistant"
    return ""


def summary_content(title: str, summary: str) -> str:
    """Format the persisted summary as hidden system context."""
    return f"{SUMMARY_PREFIX}\n{SUMMARY_TITLE_PREFIX} {title}\n摘要:\n{summary.strip()}"


def summary_title(message: SystemMessage) -> str:
    """Extract the original first-message title from a persisted summary."""
    content = chunk_text(message)
    if not content.startswith(SUMMARY_PREFIX):
        return ""

    for line in content.splitlines():
        if line.startswith(SUMMARY_TITLE_PREFIX):
            return line.removeprefix(SUMMARY_TITLE_PREFIX).strip()
    return ""
