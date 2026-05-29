# type: ignore

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
from typing import Any, NotRequired
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
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
from agent.runtime.dsml_tool_calls import DsmlStreamFilter, strip_dsml_from_message
from agent.tools.registry import tools
from config.settings import Settings, settings
from services.knowledge_base import knowledge_base


class AgentState(MessagesState):
    """Studio-visible graph state for the standard tool-calling loop."""

    rag_query: NotRequired[str]
    rag_top_k: NotRequired[int]
    rag_file_ids: NotRequired[list[str]]
    rag_processed_query: NotRequired[dict[str, Any]]
    rag_graph_candidates: NotRequired[list[dict[str, Any]]]
    rag_vector_candidates: NotRequired[list[dict[str, Any]]]
    rag_keyword_candidates: NotRequired[list[dict[str, Any]]]
    rag_results: NotRequired[list[dict[str, Any]]]


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

        final_answer = await self._latest_assistant_answer(config)
        if final_answer:
            yield "answer", final_answer

        yield "done", thread_id

    async def list_sessions(self, limit: int = 50) -> list[dict[str, str]]:
        """Return the latest persisted conversation threads for the sidebar."""
        return await self._memory.list_sessions(limit)

    async def load_session_messages(self, session_id: str) -> list[dict[str, str]]:
        """Load one persisted conversation as frontend-friendly chat messages."""
        messages = await self._memory.load_messages(self._thread_config(session_id))
        return [message_utils.message_payload(message) for message in messages if message_utils.message_role(message)]

    async def _latest_assistant_answer(self, config: RunnableConfig | dict[str, Any]) -> str:
        """Return the latest committed assistant answer from checkpoint state."""
        messages = await self._memory.load_messages(config)
        for message in reversed(messages):
            if isinstance(message, AIMessage) and not message.tool_calls:
                text = message_utils.chunk_text(message).strip()
                if text:
                    return text
            if isinstance(message, HumanMessage):
                break
        return ""

    async def delete_session(self, session_id: str) -> dict[str, int]:
        """Delete one persisted LangGraph conversation by thread ID."""
        return await self._memory.delete_session(session_id)

    def _build_state_graph(self) -> StateGraph:
        """Build the graph structure without choosing a checkpoint strategy."""
        workflow = StateGraph(AgentState)
        workflow.add_node("agent", self._call_agent)
        # RAG is expanded into first-class nodes so LangGraph Studio and
        # LangSmith show the actual retrieval flow instead of one tool call.
        workflow.add_node("rag_prepare_query", self._rag_prepare_query)
        workflow.add_node("rag_graph_recall", self._rag_graph_recall)
        workflow.add_node("rag_vector_recall", self._rag_vector_recall)
        workflow.add_node("rag_keyword_recall", self._rag_keyword_recall)
        workflow.add_node("rag_rerank", self._rag_rerank)
        workflow.add_node("rag_format_sources", self._rag_format_sources)
        workflow.add_node("tools", ToolNode(tools))
        workflow.add_node("force_answer", self._force_answer)
        workflow.add_node("final_answer", self._call_final_answer)

        workflow.add_edge(START, "agent")
        workflow.add_conditional_edges(
            "agent",
            self._route_after_agent,
            {
                "rag_prepare_query": "rag_prepare_query",
                "tools": "tools",
                "force_answer": "force_answer",
                "final_answer": "final_answer",
            },
        )
        workflow.add_edge("rag_prepare_query", "rag_graph_recall")
        workflow.add_edge("rag_graph_recall", "rag_vector_recall")
        workflow.add_edge("rag_vector_recall", "rag_keyword_recall")
        workflow.add_edge("rag_keyword_recall", "rag_rerank")
        workflow.add_edge("rag_rerank", "rag_format_sources")
        workflow.add_edge("rag_format_sources", "final_answer")
        workflow.add_edge("tools", "agent")
        workflow.add_edge("force_answer", "final_answer")
        workflow.add_edge("final_answer", END)

        return workflow

    async def _call_agent(self, state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
        """Let the model decide whether to call tools or produce the final answer."""
        messages = self._agent_decision_messages(state["messages"])
        response = await self._get_agent().ainvoke_tools_agent(messages, config=config)
        return {"messages": [response]}

    async def _call_final_answer(self, state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
        """Generate the only user-streamed assistant response."""
        messages = self._final_answer_messages(state["messages"])
        response = await self._get_agent().ainvoke_final_answer(messages, config=config)
        response = strip_dsml_from_message(response)
        if not message_utils.chunk_text(response).strip():
            fallback_answer = self._fallback_answer_from_current_turn(state["messages"])
            response = AIMessage(content=fallback_answer or "本轮没有生成可展示的回答。请换一种问法，或新建会话后重试。")

        answer_text = message_utils.chunk_text(response).strip()
        answer_with_rag_context = self._apply_rag_display_context(answer_text, state.get("rag_results", []))
        if answer_with_rag_context != answer_text:
            response = AIMessage(content=answer_with_rag_context)

        last_message = state["messages"][-1] if state["messages"] else None
        if isinstance(last_message, AIMessage) and not last_message.tool_calls and last_message.id:
            return {"messages": [RemoveMessage(id=last_message.id), response]}
        return {"messages": [response]}

    def _final_answer_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Keep final-answer generation focused on the latest user turn."""
        current_turn = self._current_turn_messages(messages)
        if self._current_turn_needs_fresh_telemetry(current_turn):
            return self._context.recent_messages(current_turn)
        summary_message = next(
            (
                message
                for message in messages
                if isinstance(message, SystemMessage) and message_utils.summary_title(message)
            ),
            None,
        )
        focused_messages = [summary_message, *current_turn] if summary_message is not None else current_turn
        return self._context.recent_messages(focused_messages)

    def _agent_decision_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Keep old raw tool output from steering later tool decisions."""
        current_turn = self._current_turn_messages(messages)
        if self._current_turn_needs_fresh_telemetry(current_turn):
            guard = SystemMessage(
                content=(
                    "当前用户请求涉及现在/当前/最新设备遥测状态。历史回答、摘要和旧遥测工具结果都已过期，"
                    "不能作为当前设备情况。若当前轮还没有 get_latest_telemetry 或 list_telemetry_devices "
                    "工具结果，必须重新调用遥测工具；有当前轮工具结果后才能回答。"
                )
            )
            return self._context.recent_messages([guard, *current_turn])
        previous_messages = messages[: max(len(messages) - len(current_turn), 0)]
        cleaned_previous = [
            message
            for message in previous_messages
            if not isinstance(message, ToolMessage)
            and not (isinstance(message, AIMessage) and message.tool_calls)
        ]
        return self._context.recent_messages([*cleaned_previous, *current_turn])

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

    async def _rag_prepare_query(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Extract search_docs arguments and normalize the query."""
        tool_call = self._single_pending_tool_call(state["messages"], "search_docs")
        args = tool_call.get("args") or {}
        query = str(args.get("query") or "")
        top_k = int(args.get("top_k") or 5)
        # RAG uses local files and Chroma's sync APIs; keep those blocking calls
        # off LangGraph dev's ASGI event loop.
        prepared = await asyncio.to_thread(knowledge_base.prepare_graph_query, query, top_k=top_k)
        return {
            "rag_query": prepared["query"],
            "rag_top_k": prepared["limit"],
            "rag_file_ids": prepared["file_ids"],
            "rag_processed_query": prepared["processed_query"],
            "rag_graph_candidates": [],
            "rag_vector_candidates": [],
            "rag_keyword_candidates": [],
            "rag_results": [],
        }

    async def _rag_graph_recall(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Run Neo4j GraphRAG recall against structured industrial entities."""
        return {
            "rag_graph_candidates": await asyncio.to_thread(
                knowledge_base.graph_recall_for_graph,
                state.get("rag_query", ""),
                int(state.get("rag_top_k", 5) or 5),
            )
        }

    async def _rag_vector_recall(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Run vector similarity recall against Chroma."""
        return {
            "rag_vector_candidates": await asyncio.to_thread(
                knowledge_base.vector_recall_for_graph,
                state.get("rag_processed_query", {}),
                state.get("rag_file_ids", []),
            )
        }

    async def _rag_keyword_recall(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Run deterministic heading/body keyword recall."""
        return {
            "rag_keyword_candidates": await asyncio.to_thread(
                knowledge_base.keyword_recall_for_graph,
                state.get("rag_processed_query", {}),
                state.get("rag_file_ids", []),
            )
        }

    async def _rag_rerank(self, state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Merge and rerank vector plus keyword candidates."""
        return {
            "rag_results": await asyncio.to_thread(
                knowledge_base.rerank_for_graph,
                state.get("rag_query", ""),
                int(state.get("rag_top_k", 5) or 5),
                state.get("rag_processed_query", {}),
                state.get("rag_vector_candidates", []),
                state.get("rag_keyword_candidates", []),
                state.get("rag_graph_candidates", []),
            )
        }

    async def _rag_format_sources(self, state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
        """Return RAG results as the ToolMessage expected by the agent loop."""
        tool_call = self._single_pending_tool_call(state["messages"], "search_docs")
        formatted_sources = await asyncio.to_thread(
            knowledge_base.format_graph_sources,
            state.get("rag_results", []),
        )
        return {
            "messages": [
                ToolMessage(
                    content=formatted_sources,
                    name="search_docs",
                    tool_call_id=tool_call["id"],
                )
            ]
        }

    def _route_after_agent(self, state: AgentState) -> str:
        """Use LangGraph's tool condition plus local guardrails against runaway loops."""
        if tools_condition(state) == "__end__":
            return "final_answer"
        if self._tool_round_count(state["messages"]) >= self._settings.agent_max_tool_rounds:
            return "force_answer"
        if self._has_repeated_pending_tool_call(state["messages"]):
            return "force_answer"
        if self._pending_tool_names(state["messages"]) == ["search_docs"]:
            return "rag_prepare_query"
        return "tools"

    async def _stream_graph_events(
        self,
        user_message: HumanMessage,
        config: RunnableConfig | dict[str, Any],
    ) -> AsyncIterator[tuple[str, str]]:
        """Stream sanitized graph progress plus final-answer tokens."""
        yielded_agent_start = False
        stream_filter = DsmlStreamFilter()
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

            if event_type == "on_chain_start" and node == "rag_prepare_query":
                yield "tool", "RAG：启动知识库检索流程"
                yield "tool", "RAG：问题预处理"
                continue

            if event_type == "on_chain_start" and node == "rag_graph_recall":
                yield "tool", "RAG：Neo4j 图谱召回"
                continue

            if event_type == "on_chain_start" and node == "rag_vector_recall":
                yield "tool", "RAG：向量召回"
                continue

            if event_type == "on_chain_start" and node == "rag_keyword_recall":
                yield "tool", "RAG：标题关键词召回"
                continue

            if event_type == "on_chain_start" and node == "rag_rerank":
                yield "tool", "RAG：候选片段重排"
                continue

            if event_type == "on_chain_start" and node == "rag_format_sources":
                yield "tool", "RAG：来源整理完成"
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
            safe_token = stream_filter.feed(token)
            if safe_token:
                yield "token", safe_token

        trailing_text = stream_filter.flush()
        if trailing_text:
            yield "token", trailing_text

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
        """Count assistant tool-call turns only within the current user turn."""
        return sum(
            1
            for message in ChatAgentRuntime._current_turn_messages(messages)
            if isinstance(message, AIMessage) and message.tool_calls
        )

    def _has_repeated_pending_tool_call(self, messages: list[BaseMessage]) -> bool:
        """Detect duplicate pending tool calls only within the current user turn."""
        last_message = messages[-1] if messages else None
        if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
            return False

        current_turn_messages = self._current_turn_messages(messages)
        previous_signatures = {
            self._tool_call_signature(tool_call)
            for message in current_turn_messages[:-1]
            if isinstance(message, AIMessage)
            for tool_call in message.tool_calls
        }
        return any(self._tool_call_signature(tool_call) in previous_signatures for tool_call in last_message.tool_calls)

    @staticmethod
    def _current_turn_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
        """Return messages after the latest user message, including that message."""
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], HumanMessage):
                return messages[index:]
        return messages

    @staticmethod
    def _current_turn_needs_fresh_telemetry(messages: list[BaseMessage]) -> bool:
        """Return whether the latest user turn asks for fresh device telemetry."""
        latest_user = next((message for message in messages if isinstance(message, HumanMessage)), None)
        if latest_user is None:
            return False
        text = message_utils.chunk_text(latest_user).lower()
        telemetry_terms = [
            "设备情况",
            "设备状态",
            "设备数据",
            "遥测",
            "采集数据",
            "点位",
            "point",
            "telemetry",
            "device status",
            "device data",
        ]
        freshness_terms = [
            "现在",
            "当前",
            "最新",
            "实时",
            "目前",
            "此刻",
            "now",
            "current",
            "latest",
            "realtime",
            "real-time",
        ]
        if any(term in text for term in telemetry_terms) and any(term in text for term in freshness_terms):
            return True
        return any(
            phrase in text
            for phrase in [
                "获取设备状态",
                "查询设备状态",
                "查看设备状态",
                "设备和对应的点位",
                "设备对应点位",
            ]
        )

    @staticmethod
    def _pending_tool_names(messages: list[BaseMessage]) -> list[str]:
        """Return tool names requested by the latest assistant message."""
        last_message = messages[-1] if messages else None
        if not isinstance(last_message, AIMessage):
            return []
        return [str(tool_call.get("name", "")) for tool_call in last_message.tool_calls]

    @staticmethod
    def _fallback_answer_from_current_turn(messages: list[BaseMessage]) -> str:
        """Use the current turn's direct agent answer if the final pass is empty."""
        for message in reversed(ChatAgentRuntime._current_turn_messages(messages)):
            if not isinstance(message, AIMessage) or message.tool_calls:
                continue

            cleaned_message = strip_dsml_from_message(message)
            text = message_utils.chunk_text(cleaned_message).strip()
            if text:
                return text

        return ""

    @staticmethod
    def _append_missing_rag_images(answer: str, rag_results: list[dict[str, Any]], limit: int = 8) -> str:
        """Append narrowly related non-table RAG images when the model omits them."""
        images = ChatAgentRuntime._rag_markdown_images(rag_results, limit=limit)
        missing = [image for image in images if image["url"] not in answer]
        if not missing:
            return answer

        lines = ["", "", "相关原图："]
        lines.extend(image["markdown"] for image in missing)
        return ChatAgentRuntime._insert_before_source_section(answer, chr(10).join(lines).strip())

    @staticmethod
    def _apply_rag_display_context(answer: str, rag_results: list[dict[str, Any]]) -> str:
        """Apply deterministic display artifacts from RAG without relying on model table copying."""
        answer_with_tables = ChatAgentRuntime._append_table_context_markdown(answer, rag_results)
        return ChatAgentRuntime._append_missing_rag_images(answer_with_tables, rag_results)

    @staticmethod
    def _append_table_context_markdown(answer: str, rag_results: list[dict[str, Any]]) -> str:
        """Append authoritative table markdown from complete table chunks."""
        tables = ChatAgentRuntime._canonical_rag_tables(rag_results)
        if not tables:
            return answer
        cleaned = ChatAgentRuntime._remove_auto_image_sections(answer)
        cleaned = ChatAgentRuntime._remove_markdown_tables(cleaned).strip()
        blocks = []
        for table in tables:
            title = table.get("title") or "表格"
            markdown = table.get("markdown") or ""
            if markdown and markdown not in cleaned:
                blocks.append(f"{title}：\n{markdown}")
        # Keep the citation as the only visible location marker; table titles
        # are DOCX heading paths and read like stray content in chat answers.
        blocks = [
            block[block.find("|") :] if not block.lstrip().startswith("|") and "|" in block else block
            for block in blocks
        ]
        if not blocks:
            return cleaned or answer
        if not cleaned:
            return "\n\n".join(blocks)
        return ChatAgentRuntime._insert_before_source_section(cleaned, "\n\n".join(blocks))

    @staticmethod
    def _insert_before_source_section(answer: str, block: str) -> str:
        """Insert deterministic RAG artifacts before source citations when present."""
        clean_block = block.strip()
        if not clean_block:
            return answer
        if not answer.strip():
            return clean_block

        lines = answer.rstrip().splitlines()
        source_index = next(
            (
                index
                for index, line in enumerate(lines)
                if ChatAgentRuntime._is_source_line(line)
            ),
            None,
        )
        if source_index is None:
            return f"{answer.rstrip()}\n\n{clean_block}"

        before = "\n".join(lines[:source_index]).rstrip()
        after = "\n".join(lines[source_index:]).lstrip()
        return "\n\n".join(part for part in [before, clean_block, after] if part)

    @staticmethod
    def _is_source_line(line: str) -> bool:
        """Return whether a line starts the final source/citation section."""
        stripped = line.strip().lstrip("#").strip().lstrip("-*").strip().strip("*_").strip()
        lowered = stripped.lower()
        return (
            stripped.startswith("\u6765\u6e90")
            or lowered.startswith("source:")
            or lowered.startswith("source\uff1a")
            or lowered.startswith("sources:")
            or lowered.startswith("sources\uff1a")
            or lowered.startswith("reference:")
            or lowered.startswith("reference\uff1a")
            or lowered.startswith("references:")
            or lowered.startswith("references\uff1a")
        )

    @staticmethod
    def _canonical_rag_tables(rag_results: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Return de-duplicated canonical tables from table_chunk RAG payloads."""
        tables: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in rag_results:
            table_context = item.get("table_context") or {}
            if not isinstance(table_context, dict):
                continue
            table_id = str(table_context.get("table_id") or item.get("parent_table_id") or item.get("chunk_id") or "")
            markdown = str(item.get("canonical_markdown") or table_context.get("canonical_markdown") or "")
            if not table_id or not markdown or table_id in seen:
                continue
            heading = " > ".join(str(part) for part in table_context.get("heading_path") or [] if str(part))
            tables.append({"id": table_id, "title": heading or "表格", "markdown": markdown})
            seen.add(table_id)
        return tables

    @staticmethod
    def _remove_auto_image_sections(answer: str) -> str:
        """Remove older image appendices before rebuilding the answer table."""
        markers = ["相关图片表：", "相关原图："]
        positions = [answer.find(marker) for marker in markers if answer.find(marker) >= 0]
        if not positions:
            return answer
        return answer[: min(positions)].rstrip()

    @staticmethod
    def _remove_markdown_tables(answer: str) -> str:
        """Drop model-generated markdown tables before appending canonical table chunks."""
        lines = answer.splitlines()
        result: list[str] = []
        index = 0
        while index < len(lines):
            if ChatAgentRuntime._is_markdown_table_line(lines[index]):
                while index < len(lines) and ChatAgentRuntime._is_markdown_table_line(lines[index]):
                    index += 1
                continue
            result.append(lines[index])
            index += 1
        return "\n".join(result)

    @staticmethod
    def _is_markdown_table_line(line: str) -> bool:
        """Return whether a line is part of a markdown pipe table."""
        stripped = line.strip()
        return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2

    @staticmethod
    def _rag_markdown_images(rag_results: list[dict[str, Any]], limit: int) -> list[dict[str, str]]:
        """Build de-duplicated markdown images from structured RAG payloads."""
        seen_urls: set[str] = set()
        images: list[dict[str, str]] = []
        for item in rag_results:
            if not ChatAgentRuntime._should_auto_append_rag_images(item):
                continue
            for image in item.get("related_images") or []:
                image_id = str(image.get("image_id") or "")
                url = str(image.get("url") or "")
                if not image_id or not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                alt = ChatAgentRuntime._markdown_alt_text(item, image_id)
                images.append({"url": url, "markdown": f"![{alt}]({url})"})
                if len(images) >= limit:
                    return images
        return images

    @staticmethod
    def _should_auto_append_rag_images(item: dict[str, Any]) -> bool:
        """Only append scoped non-table images; table display comes from table chunks."""
        chunk_type = str(item.get("chunk_type") or item.get("kind") or "")
        retrieval = str(item.get("retrieval") or "")
        if item.get("parent_table_id") or chunk_type in {"table_chunk", "table_row_chunk"}:
            return False
        if retrieval == "inline_image_expansion":
            return True
        if chunk_type == "image_chunk":
            return True
        if chunk_type == "inline_image_group_chunk":
            return bool(item.get("parent_inline_id"))
        return False

    @staticmethod
    def _markdown_alt_text(item: dict[str, Any], image_id: str) -> str:
        """Create a compact alt label safe for Markdown image syntax."""
        raw_alt = " ".join(
            part
            for part in [
                str(item.get("file_name") or "source image"),
                str(item.get("section_title") or ""),
                image_id,
            ]
            if part
        )
        return raw_alt.replace("[", "(").replace("]", ")")

    @staticmethod
    def _single_pending_tool_call(messages: list[BaseMessage], tool_name: str) -> dict[str, Any]:
        """Return the single pending tool call handled by a custom graph path."""
        last_message = messages[-1] if messages else None
        if not isinstance(last_message, AIMessage):
            raise RuntimeError(f"No pending {tool_name} call.")
        for tool_call in last_message.tool_calls:
            if tool_call.get("name") == tool_name:
                return tool_call
        raise RuntimeError(f"No pending {tool_name} call.")

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
            "search_docs": "知识库检索",
            "get_latest_telemetry": "最新设备数据查询",
            "summarize_telemetry": "设备数据统计",
            "list_telemetry_devices": "设备列表查询",
        }.get(tool_name, tool_name)

chat_agent = ChatAgentRuntime(settings)

# LangGraph Studio imports this graph from langgraph.json. It is intentionally
# compiled without a custom checkpointer because Studio manages its own state.
graph = chat_agent._build_state_graph().compile()
