from __future__ import annotations

from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from agent.runtime import message_utils
from agent.runtime.conversation_memory import ConversationMemory
from config.settings import Settings


class SummarizingAgent(Protocol):
    """Agent capability needed by the context manager."""

    async def summarize(self, messages: list[BaseMessage], config: RunnableConfig | None = None) -> BaseMessage:
        """Summarize a list of old messages."""


class ConversationContextManager:
    """Apply context-window trimming and persistent conversation summarization."""

    def __init__(self, app_settings: Settings, memory: ConversationMemory) -> None:
        self._settings = app_settings
        self._memory = memory

    def recent_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Keep recent messages without breaking AI tool-call and ToolMessage pairs."""
        max_messages = max(1, self._settings.llm_context_messages)
        summary_message = next(
            (
                message
                for message in messages
                if isinstance(message, SystemMessage) and message_utils.summary_title(message)
            ),
            None,
        )

        if summary_message is not None and len(messages) > max_messages:
            if max_messages == 1:
                return [summary_message]
            recent_without_summary = [message for message in messages if message is not summary_message]
            return [summary_message, *self._safe_recent_window(recent_without_summary, max_messages - 1)]

        return self._safe_recent_window(messages, max_messages)

    async def summarize_if_needed(
        self,
        config: RunnableConfig | dict[str, Any],
        messages: list[BaseMessage],
        agent: SummarizingAgent,
    ) -> list[BaseMessage]:
        """Summarize old messages once a thread grows beyond the configured limit."""
        trigger_messages = max(1, self._settings.summary_trigger_messages)
        keep_messages = max(1, self._settings.summary_keep_messages)
        if len(messages) < trigger_messages or len(messages) <= keep_messages:
            return messages

        title = message_utils.session_title(messages)
        old_messages = messages[:-keep_messages]
        recent_messages = messages[-keep_messages:]
        summary_response = await agent.summarize(old_messages, config=config)
        summary_message = SystemMessage(
            content=message_utils.summary_content(title, message_utils.chunk_text(summary_response))
        )
        compacted_messages = [summary_message, *self._safe_recent_window(messages, keep_messages)]
        await self._memory.replace_messages(config, compacted_messages)
        return compacted_messages

    def _safe_recent_window(self, messages: list[BaseMessage], max_messages: int) -> list[BaseMessage]:
        """Return a tail slice expanded left when needed to keep tool-call blocks valid."""
        if len(messages) <= max_messages:
            return list(messages)

        start = self._tool_safe_start(messages, len(messages) - max_messages)
        return messages[start:]

    def _tool_safe_start(self, messages: list[BaseMessage], start: int) -> int:
        """Move the slice start before the assistant message that created tool results."""
        while start > 0 and isinstance(messages[start], ToolMessage):
            start = self._tool_block_start(messages, start)
        return start

    @staticmethod
    def _tool_block_start(messages: list[BaseMessage], tool_index: int) -> int:
        """Find the AIMessage with tool_calls immediately preceding a ToolMessage block."""
        index = tool_index - 1
        while index >= 0 and isinstance(messages[index], ToolMessage):
            index -= 1

        if index >= 0 and isinstance(messages[index], AIMessage) and messages[index].tool_calls:
            return index
        return tool_index
