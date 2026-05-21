from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from agent.runtime import message_utils


class ConversationMemory:
    """Read and mutate persisted LangGraph conversation state."""

    def __init__(self, graph: Any, checkpointer_manager: Any) -> None:
        self._graph = graph
        self._checkpointer_manager = checkpointer_manager

    def bind_graph(self, graph: Any) -> None:
        """Update the compiled graph used for state reads and writes."""
        self._graph = graph

    async def load_messages(self, config: RunnableConfig | dict[str, Any]) -> list[BaseMessage]:
        """Load conversation memory from the runtime LangGraph checkpointer."""
        state = await self._graph.aget_state(config)
        values = state.values or {}
        return list(values.get("messages", []))

    async def save_turn(
        self,
        config: RunnableConfig | dict[str, Any],
        user_message: HumanMessage,
        ai_message: AIMessage,
    ) -> None:
        """Append the completed turn to LangGraph memory after direct token streaming."""
        await self._graph.aupdate_state(
            config,
            {"messages": [user_message, ai_message]},
            as_node="agent",
        )

    async def replace_messages(
        self,
        config: RunnableConfig | dict[str, Any],
        messages: list[BaseMessage],
    ) -> None:
        """Replace a thread's message channel with compacted context."""
        await self._graph.aupdate_state(
            config,
            {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages]},
            as_node="agent",
        )

    async def list_sessions(self, limit: int = 50) -> list[dict[str, str]]:
        """Return the latest persisted conversation threads for the sidebar."""
        sessions: list[dict[str, str]] = []
        seen_thread_ids: set[str] = set()

        async for checkpoint in self._checkpointer_manager.checkpointer.alist(None, limit=limit * 4):
            thread_id = checkpoint.config["configurable"]["thread_id"]
            if thread_id in seen_thread_ids:
                continue

            messages = message_utils.checkpoint_messages(checkpoint)
            sessions.append(
                {
                    "id": thread_id,
                    "title": message_utils.session_title(messages),
                    "updated_at": message_utils.checkpoint_updated_at(checkpoint),
                }
            )
            seen_thread_ids.add(thread_id)

            if len(sessions) >= limit:
                break

        return sessions
