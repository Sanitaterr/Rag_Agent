# type: ignore

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from agent.chat_agent import ChatAgent
from agent.llm import create_deepseek_llm, create_deepseek_tool_llm
from agent.runtime import message_utils
from agent.runtime.checkpointer import MySQLCheckpointerManager
from agent.runtime.context_manager import ConversationContextManager
from agent.runtime.conversation_memory import ConversationMemory
from agent.tools.registry import tools
from config.settings import Settings, settings


class AgentState(MessagesState):
    """Studio-visible graph state for the standard tool-calling loop."""


class ChatAgentRuntime:
    """LangGraph runtime that coordinates routing, tools, memory, and context policy."""

    def __init__(self, app_settings: Settings) -> None:
        self._settings = app_settings
        self._agent: ChatAgent | None = None
        self._checkpointer = MySQLCheckpointerManager(app_settings)
        # Import-time fallback keeps tests and LangGraph Studio imports lightweight.
        self._graph = self._build_state_graph().compile(checkpointer=MemorySaver())
        self._memory = ConversationMemory(self._graph, self._checkpointer)
        self._context = ConversationContextManager(app_settings, self._memory)

    async def setup(self) -> None:
        """Initialize MySQL-backed conversation persistence for FastAPI runtime."""
        checkpointer = await self._checkpointer.setup()
        self._graph = self._build_state_graph().compile(checkpointer=checkpointer)
        self._memory.bind_graph(self._graph)

    async def close(self) -> None:
        """Release the MySQL checkpointer connection on application shutdown."""
        await self._checkpointer.close()

    async def stream_chat(self, message: str, session_id: str | None = None) -> AsyncIterator[tuple[str, str]]:
        """Run one streaming turn; emit session, token, and done events."""
        thread_id = session_id or uuid4().hex
        config = self._thread_config(thread_id)
        yield "session", thread_id

        user_message = HumanMessage(content=message)
        history = await self._memory.load_messages(config)
        await self._context.summarize_if_needed(config, history, self._get_agent())

        async for event_name, payload in self._stream_graph_events(user_message, config):
            yield event_name, payload

        yield "done", thread_id

    async def list_sessions(self, limit: int = 50) -> list[dict[str, str]]:
        """Return the latest persisted conversation threads for the sidebar."""
        return await self._memory.list_sessions(limit)

    async def load_session_messages(self, session_id: str) -> list[dict[str, str]]:
        """Load one persisted conversation as frontend-friendly chat messages."""
        messages = await self._memory.load_messages(self._thread_config(session_id))
        return [message_utils.message_payload(message) for message in messages if message_utils.message_role(message)]

    def _build_state_graph(self) -> StateGraph:
        """Build the graph structure without choosing a checkpoint strategy."""
        workflow = StateGraph(AgentState)
        workflow.add_node("agent", self._call_agent)
        workflow.add_node("tools", ToolNode(tools))
        workflow.add_node("force_answer", self._force_answer)
        workflow.add_node("final_answer", self._call_final_answer)

        workflow.add_edge(START, "agent")
        workflow.add_conditional_edges(
            "agent",
            self._route_after_agent,
            {"tools": "tools", "force_answer": "force_answer", "final_answer": "final_answer"},
        )
        workflow.add_edge("tools", "agent")
        workflow.add_edge("force_answer", "agent")
        workflow.add_edge("final_answer", END)

        return workflow

    async def _call_agent(self, state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
        """Let the model decide whether to call tools or produce the final answer."""
        messages = self._context.recent_messages(state["messages"])
        response = await self._get_agent().ainvoke_tools_agent(messages, config=config)
        return {"messages": [response]}

    async def _call_final_answer(self, state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
        """Generate the only user-streamed assistant response."""
        messages = self._context.recent_messages(state["messages"])
        response = await self._get_agent().ainvoke_final_answer(messages, config=config)

        last_message = state["messages"][-1] if state["messages"] else None
        if isinstance(last_message, AIMessage) and not last_message.tool_calls and last_message.id:
            return {"messages": [RemoveMessage(id=last_message.id), response]}
        return {"messages": [response]}

    async def _force_answer(self, state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
        """Replace runaway repeated tool calls with tool messages that force a final answer."""
        last_message = state["messages"][-1]
        if not isinstance(last_message, AIMessage):
            return {"messages": []}

        tool_messages = [
            ToolMessage(
                content="Tool loop stopped by runtime guard. Use the available prior tool results to answer the user now. Do not call another tool.",
                name=tool_call["name"],
                tool_call_id=tool_call["id"],
                status="error",
            )
            for tool_call in last_message.tool_calls
        ]
        return {"messages": tool_messages}

    def _route_after_agent(self, state: AgentState) -> str:
        """Use LangGraph's tool condition plus local guardrails against runaway loops."""
        if tools_condition(state) == "__end__":
            return "final_answer"
        if self._tool_round_count(state["messages"]) >= self._settings.agent_max_tool_rounds:
            return "force_answer"
        if self._has_repeated_pending_tool_call(state["messages"]):
            return "force_answer"
        return "tools"

    async def _stream_graph_events(
        self,
        user_message: HumanMessage,
        config: RunnableConfig | dict[str, Any],
    ) -> AsyncIterator[tuple[str, str]]:
        """Stream sanitized graph progress plus final-answer tokens."""
        yielded_agent_start = False
        async for event in self._graph.astream_events(
            {"messages": [user_message]},
            config=config,
            version="v2",
        ):
            event_type = event.get("event")
            metadata = event.get("metadata") or {}
            node = metadata.get("langgraph_node")

            if event_type == "on_chain_start" and node == "agent" and not yielded_agent_start:
                yielded_agent_start = True
                yield "thought", "正在分析问题并判断是否需要工具"
                continue

            if event_type == "on_chain_start" and node == "force_answer":
                yield "thought", "工具调用达到保护条件，正在基于已有结果生成回答"
                continue

            if event_type == "on_chain_start" and node == "final_answer":
                yield "thought", "正在整理最终回答"
                continue

            if event_type == "on_tool_start":
                tool_name = event.get("name") or "tool"
                yield "tool", f"正在调用 {self._display_tool_name(tool_name)}"
                continue

            if event_type == "on_tool_end":
                tool_name = event.get("name") or "tool"
                yield "tool", f"{self._display_tool_name(tool_name)} 调用完成"
                continue

            if event_type != "on_chat_model_stream" or node != "final_answer":
                continue

            chunk = (event.get("data") or {}).get("chunk")
            if getattr(chunk, "tool_call_chunks", None):
                continue

            token = message_utils.chunk_text(chunk)
            if token:
                yield "token", token

    def _get_agent(self) -> ChatAgent:
        """Create the agent lazily so FastAPI can boot before local secrets are set."""
        if self._agent is None:
            llm = create_deepseek_llm(self._settings)
            tool_llm = create_deepseek_tool_llm(self._settings)
            self._agent = ChatAgent(llm, tool_llm)
        return self._agent

    def _thread_config(self, thread_id: str) -> dict[str, Any]:
        """Build the LangGraph config used to address one persisted thread."""
        return {
            "recursion_limit": self._settings.agent_recursion_limit,
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "recursion_limit": self._settings.agent_recursion_limit,
            },
        }

    @staticmethod
    def _tool_round_count(messages: list[BaseMessage]) -> int:
        """Count assistant turns that requested tools in the current graph state."""
        return sum(1 for message in messages if isinstance(message, AIMessage) and message.tool_calls)

    def _has_repeated_pending_tool_call(self, messages: list[BaseMessage]) -> bool:
        """Detect when the model is trying to call an already-seen tool with same args."""
        last_message = messages[-1] if messages else None
        if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
            return False

        previous_signatures = {
            self._tool_call_signature(tool_call)
            for message in messages[:-1]
            if isinstance(message, AIMessage)
            for tool_call in message.tool_calls
        }
        return any(self._tool_call_signature(tool_call) in previous_signatures for tool_call in last_message.tool_calls)

    @staticmethod
    def _tool_call_signature(tool_call: dict[str, Any]) -> str:
        """Build a stable signature for duplicate tool-call detection."""
        return json.dumps(
            {
                "name": tool_call.get("name"),
                "args": tool_call.get("args", {}),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @staticmethod
    def _display_tool_name(tool_name: str) -> str:
        """Convert internal tool names to concise user-facing labels."""
        return {
            "web_search": "联网搜索",
            "get_weather": "天气查询",
            "get_current_time": "时间查询",
            "calculate": "计算器",
            "get_latest_telemetry": "最新设备数据查询",
            "summarize_telemetry": "设备数据统计",
            "list_telemetry_devices": "设备列表查询",
        }.get(tool_name, tool_name)


chat_agent = ChatAgentRuntime(settings)

# LangGraph Studio imports this graph from langgraph.json. It is intentionally
# compiled without a custom checkpointer because Studio manages its own state.
graph = chat_agent._build_state_graph().compile()
