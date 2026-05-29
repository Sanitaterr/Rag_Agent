from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from sqlalchemy import text

from agent.runtime import message_utils
from db.database import AsyncSessionLocal


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

    async def delete_session(self, thread_id: str) -> dict[str, int]:
        """Delete all LangGraph checkpoint rows that belong to one conversation thread."""
        if not thread_id:
            return {"checkpoint_writes": 0, "checkpoints": 0, "checkpoint_blobs": 0}

        async with AsyncSessionLocal() as session:
            async with session.begin():
                # LangGraph MySQL persistence stores one logical conversation across
                # checkpoints, intermediate writes, and channel blobs. The sidebar
                # session ID is the checkpointer thread_id, so it is the deletion key.
                write_result = await session.execute(
                    text("DELETE FROM checkpoint_writes WHERE thread_id = :thread_id"),
                    {"thread_id": thread_id},
                )
                checkpoint_result = await session.execute(
                    text("DELETE FROM checkpoints WHERE thread_id = :thread_id"),
                    {"thread_id": thread_id},
                )
                blob_result = await session.execute(
                    text("DELETE FROM checkpoint_blobs WHERE thread_id = :thread_id"),
                    {"thread_id": thread_id},
                )

        return {
            "checkpoint_writes": write_result.rowcount or 0,
            "checkpoints": checkpoint_result.rowcount or 0,
            "checkpoint_blobs": blob_result.rowcount or 0,
        }
