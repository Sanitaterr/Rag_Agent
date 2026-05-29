from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

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


def text_only_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Return messages with provider-unsupported content blocks converted to text."""
    return [message.model_copy(update={"content": text_only_content(message)}) for message in messages]


def text_only_content(message: BaseMessage) -> str:
    """Convert multimodal LangChain content blocks to plain text for text-only models."""
    content = message.content
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            parts.append(str(item))
            continue

        item_type = item.get("type")
        if isinstance(item.get("text"), str):
            parts.append(item["text"])
        elif item_type in {"image_url", "image"}:
            parts.append("[用户上传了一张图片，当前文本模型无法直接读取图片内容]")
        else:
            parts.append(f"[已省略不支持的内容块: {item_type or 'unknown'}]")

    return "\n".join(part for part in parts if part).strip()


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


def conversation_payloads(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Convert persisted LangGraph messages while preserving per-turn trace events."""
    payloads: list[dict[str, Any]] = []
    pending_thoughts: list[str] = []

    for message in messages:
        if isinstance(message, HumanMessage):
            payloads.append(message_payload(message))
            pending_thoughts = []
            continue

        if isinstance(message, AIMessage) and message.tool_calls:
            for tool_call in message.tool_calls:
                pending_thoughts.extend(tool_trace_steps(str(tool_call.get("name", ""))))
            continue

        if isinstance(message, ToolMessage):
            tool_name = str(getattr(message, "name", "") or "")
            completion = tool_completion_trace(tool_name)
            if completion:
                pending_thoughts.append(completion)
            continue

        if isinstance(message, AIMessage):
            if message_role(message):
                payloads.append(message_payload(message, thoughts=_unique(pending_thoughts)))
            pending_thoughts = []

    return payloads


def message_payload(message: BaseMessage, *, thoughts: list[str] | None = None) -> dict[str, Any]:
    """Convert LangChain messages to the frontend message contract."""
    return {
        "id": getattr(message, "id", None) or uuid4().hex,
        "role": message_role(message),
        "text": chunk_text(message),
        "thoughts": thoughts or [],
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


def tool_trace_steps(tool_name: str) -> list[str]:
    """Rebuild visible trace steps from persisted tool calls."""
    if tool_name == "search_docs":
        return [
            "RAG：启动知识库检索流程",
            "RAG：问题预处理",
            "RAG：向量召回",
            "RAG：标题关键词召回",
            "RAG：候选片段重排",
        ]

    display_name = {
        "web_search": "联网搜索",
        "get_weather": "天气查询",
        "get_current_time": "时间查询",
        "calculate": "计算器",
        "get_latest_telemetry": "最新设备数据查询",
        "summarize_telemetry": "设备数据统计",
        "list_telemetry_devices": "设备列表查询",
    }.get(tool_name, tool_name or "工具")
    return [f"正在调用 {display_name}"]


def tool_completion_trace(tool_name: str) -> str:
    """Return the completion trace text for a persisted ToolMessage."""
    if tool_name == "search_docs":
        return "RAG：来源整理完成"
    if not tool_name:
        return ""

    display_name = {
        "web_search": "联网搜索",
        "get_weather": "天气查询",
        "get_current_time": "时间查询",
        "calculate": "计算器",
        "get_latest_telemetry": "最新设备数据查询",
        "summarize_telemetry": "设备数据统计",
        "list_telemetry_devices": "设备列表查询",
    }.get(tool_name, tool_name)
    return f"{display_name} 调用完成"


def _unique(items: list[str]) -> list[str]:
    """Keep trace events stable when checkpoints contain repeated tool messages."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


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
