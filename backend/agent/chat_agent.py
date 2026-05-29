from __future__ import annotations

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from agent.prompts import CONVERSATION_SUMMARY_PROMPT, FINAL_ANSWER_SYSTEM_PROMPT, TOOL_AGENT_SYSTEM_PROMPT
from agent.runtime import message_utils
from agent.runtime.dsml_tool_calls import normalize_text_tool_calls
from agent.tools.registry import tools


class ChatAgent:
    """LLM adapter for the LangGraph tool loop and conversation summarization."""

    def __init__(self, llm, tool_llm) -> None:
        self._llm = llm
        self._tool_llm = tool_llm
        self._bound_llms: dict[str, object] = {}

    async def ainvoke_tools_agent(
        self,
        messages: list[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> BaseMessage:
        """Let the model either call tools or produce the final answer."""
        prompt_messages = [
            SystemMessage(content=TOOL_AGENT_SYSTEM_PROMPT.format(route="all")),
            *message_utils.text_only_messages(messages),
        ]
        response = await self._bound_llm("all", tools).ainvoke(prompt_messages, config=config, stream=False)
        return normalize_text_tool_calls(response, tools)

    async def summarize(self, messages: list[BaseMessage], config: RunnableConfig | None = None) -> BaseMessage:
        """Compress old conversation history into a durable summary."""
        return await self._llm.ainvoke(
            [SystemMessage(content=CONVERSATION_SUMMARY_PROMPT), *message_utils.text_only_messages(messages)],
            config=config,
        )

    async def ainvoke_final_answer(
        self,
        messages: list[BaseMessage],
        config: RunnableConfig | None = None,
    ) -> BaseMessage:
        """Streamable final answer pass without tools bound."""
        return await self._llm.ainvoke(
            [SystemMessage(content=FINAL_ANSWER_SYSTEM_PROMPT), *message_utils.text_only_messages(messages)],
            config=config,
        )

    def _bound_llm(self, cache_key: str, bound_tools: list[BaseTool]):
        """Cache the all-tools binding; LangChain owns tool schema formatting."""
        if cache_key not in self._bound_llms:
            self._bound_llms[cache_key] = self._tool_llm.bind_tools(bound_tools)
        return self._bound_llms[cache_key]
