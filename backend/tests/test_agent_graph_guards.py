from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage

from agent.runtime.agent_graph import ChatAgentRuntime
from agent.runtime.context_manager import ConversationContextManager
from config.settings import settings


def _tool_call(name: str, args: dict) -> dict:
    return {
        "name": name,
        "args": args,
        "id": f"call_{name}_{len(args)}",
        "type": "tool_call",
    }


def test_tool_round_count_ignores_previous_user_turns() -> None:
    messages = [
        HumanMessage(content="第一次搜索"),
        AIMessage(content="", tool_calls=[_tool_call("web_search", {"query": "A"})]),
        ToolMessage(content="A", tool_call_id="call_web_search_1"),
        AIMessage(content="答案 A"),
        HumanMessage(content="第二次搜索"),
        AIMessage(content="", tool_calls=[_tool_call("web_search", {"query": "B"})]),
    ]

    assert ChatAgentRuntime._tool_round_count(messages) == 1


def test_repeated_tool_guard_ignores_previous_user_turns() -> None:
    runtime = ChatAgentRuntime(settings)
    messages = [
        HumanMessage(content="第一次搜索"),
        AIMessage(content="", tool_calls=[_tool_call("search_docs", {"query": "MACS V6.5系统组成"})]),
        ToolMessage(content="old result", tool_call_id="call_search_docs_1"),
        AIMessage(content="旧答案"),
        HumanMessage(content="再查 MACS V6.5系统组成"),
        AIMessage(content="", tool_calls=[_tool_call("search_docs", {"query": "MACS V6.5系统组成"})]),
    ]

    assert runtime._has_repeated_pending_tool_call(messages) is False


def test_repeated_tool_guard_still_blocks_same_turn_repeat() -> None:
    runtime = ChatAgentRuntime(settings)
    messages = [
        HumanMessage(content="查 MACS V6.5系统组成"),
        AIMessage(content="", tool_calls=[_tool_call("search_docs", {"query": "MACS V6.5系统组成"})]),
        ToolMessage(content="result", tool_call_id="call_search_docs_1"),
        AIMessage(content="", tool_calls=[_tool_call("search_docs", {"query": "MACS V6.5系统组成"})]),
    ]

    assert runtime._has_repeated_pending_tool_call(messages) is True


def test_checkpoint_answer_sync_uses_latest_committed_ai_message() -> None:
    runtime = ChatAgentRuntime(settings)
    runtime._memory.load_messages = _async_return(  # type: ignore[method-assign]
        [
            HumanMessage(content="first"),
            AIMessage(content="old answer"),
            HumanMessage(content="current"),
            AIMessage(content="", tool_calls=[_tool_call("search_docs", {"query": "manual"})]),
            ToolMessage(content="tool result", tool_call_id="call_search_docs_1"),
            AIMessage(content="committed final answer"),
        ]
    )

    assert asyncio.run(runtime._latest_assistant_answer({})) == "committed final answer"


def test_checkpoint_answer_sync_does_not_reuse_previous_turn_after_empty_answer() -> None:
    runtime = ChatAgentRuntime(settings)
    runtime._memory.load_messages = _async_return(  # type: ignore[method-assign]
        [
            HumanMessage(content="first"),
            AIMessage(content="old answer"),
            HumanMessage(content="current"),
            AIMessage(content=""),
        ]
    )

    assert asyncio.run(runtime._latest_assistant_answer({})) == ""


def test_final_answer_empty_response_falls_back_to_current_turn_agent_text() -> None:
    runtime = ChatAgentRuntime(settings)
    runtime._agent = _FakeFinalAnswerAgent(AIMessage(content=""))  # type: ignore[assignment]

    result = asyncio.run(
        runtime._call_final_answer(
            {
                "messages": [
                    HumanMessage(content="current"),
                    AIMessage(content="agent direct answer", id="draft-answer"),
                ]
            },
            {},
        )
    )

    assert isinstance(result["messages"][0], RemoveMessage)
    assert result["messages"][0].id == "draft-answer"
    assert isinstance(result["messages"][1], AIMessage)
    assert result["messages"][1].content == "agent direct answer"


def test_final_answer_messages_only_include_latest_turn_and_summary() -> None:
    runtime = ChatAgentRuntime(settings)
    messages = [
        SystemMessage(content="[对话摘要]\n标题: old\n摘要:\n用户之前问过工具栏。"),
        HumanMessage(content="上一问"),
        AIMessage(content="", tool_calls=[_tool_call("search_docs", {"query": "上一问"})]),
        ToolMessage(content="上一问工具结果", tool_call_id="call_search_docs_1", name="search_docs"),
        AIMessage(content="上一问答案"),
        HumanMessage(content="当前问"),
        AIMessage(content="", tool_calls=[_tool_call("search_docs", {"query": "当前问"})]),
        ToolMessage(content="当前问工具结果", tool_call_id="call_search_docs_1", name="search_docs"),
    ]

    focused = runtime._final_answer_messages(messages)
    text = "\n".join(message.content for message in focused if isinstance(message.content, str))

    assert "用户之前问过工具栏" in text
    assert "当前问" in text
    assert "当前问工具结果" in text
    assert "上一问工具结果" not in text
    assert "上一问答案" not in text


def test_agent_decision_messages_drop_historical_tool_noise() -> None:
    runtime = ChatAgentRuntime(settings)
    messages = [
        HumanMessage(content="上一问"),
        AIMessage(content="", tool_calls=[_tool_call("search_docs", {"query": "上一问"})]),
        ToolMessage(content="很长的上一问 RAG 结果", tool_call_id="call_search_docs_1", name="search_docs"),
        AIMessage(content="上一问答案"),
        HumanMessage(content="当前问"),
    ]

    focused = runtime._agent_decision_messages(messages)
    text = "\n".join(message.content for message in focused if isinstance(message.content, str))

    assert "上一问答案" in text
    assert "当前问" in text
    assert "很长的上一问 RAG 结果" not in text
    assert all(not (isinstance(message, AIMessage) and message.tool_calls) for message in focused)


def test_realtime_telemetry_decision_drops_stale_assistant_answer() -> None:
    runtime = ChatAgentRuntime(settings)
    messages = [
        HumanMessage(content="现在设备情况"),
        AIMessage(content="", tool_calls=[_tool_call("get_latest_telemetry", {"limit": 10})]),
        ToolMessage(
            content='{"records":[{"device_id":"old","point_value":1}]}',
            tool_call_id="call_get_latest_telemetry_1",
            name="get_latest_telemetry",
        ),
        AIMessage(content="当前 old 设备在线，温度 1"),
        HumanMessage(content="现在设备情况"),
    ]

    focused = runtime._agent_decision_messages(messages)
    text = "\n".join(message.content for message in focused if isinstance(message.content, str))

    assert "旧遥测工具结果都已过期" in text
    assert "当前 old 设备在线" not in text
    assert '"device_id":"old"' not in text
    assert text.count("现在设备情况") == 1


def test_realtime_telemetry_final_answer_keeps_current_tool_result_only() -> None:
    runtime = ChatAgentRuntime(settings)
    messages = [
        SystemMessage(content="[对话摘要]\n标题: 设备\n摘要:\n旧设备状态：old 在线"),
        HumanMessage(content="现在设备情况"),
        AIMessage(content="", tool_calls=[_tool_call("get_latest_telemetry", {"limit": 10})]),
        ToolMessage(
            content='{"records":[{"device_id":"new","point_value":2}]}',
            tool_call_id="call_get_latest_telemetry_1",
            name="get_latest_telemetry",
        ),
    ]

    focused = runtime._final_answer_messages(messages)
    text = "\n".join(message.content for message in focused if isinstance(message.content, str))

    assert "旧设备状态" not in text
    assert '"device_id":"new"' in text


def test_rag_and_force_answer_edges_stop_at_final_answer() -> None:
    graph = ChatAgentRuntime(settings)._build_state_graph()

    assert graph.edges == {
        ("__start__", "agent"),
        ("rag_prepare_query", "rag_graph_recall"),
        ("rag_graph_recall", "rag_vector_recall"),
        ("rag_vector_recall", "rag_keyword_recall"),
        ("rag_keyword_recall", "rag_rerank"),
        ("rag_rerank", "rag_format_sources"),
        ("rag_format_sources", "final_answer"),
        ("tools", "agent"),
        ("force_answer", "final_answer"),
        ("final_answer", "__end__"),
    }


def test_recent_context_drops_blank_assistant_turns() -> None:
    manager = ConversationContextManager(settings, memory=None)  # type: ignore[arg-type]
    messages = [
        HumanMessage(content="first"),
        AIMessage(content=""),
        HumanMessage(content="current"),
        AIMessage(content="", tool_calls=[_tool_call("web_search", {"query": "capybara"})]),
        ToolMessage(content="tool result", tool_call_id="call_web_search_1"),
    ]

    recent = manager.recent_messages(messages)

    assert all(not (isinstance(message, AIMessage) and message.content == "" and not message.tool_calls) for message in recent)
    assert any(isinstance(message, AIMessage) and message.tool_calls for message in recent)


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


class _FakeFinalAnswerAgent:
    def __init__(self, response: AIMessage) -> None:
        self._response = response

    async def ainvoke_final_answer(self, messages, config=None):  # noqa: ANN001
        return self._response
