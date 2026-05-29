from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from openai import APITimeoutError

from config.settings import Settings


class TimeoutFallbackModel:
    """Small wrapper that retries model calls on timeout with a fallback model."""

    def __init__(self, primary: Any, fallback: Any | None) -> None:
        self._primary = primary
        self._fallback = fallback
        self.model_name = getattr(primary, "model_name", "")

    async def ainvoke(self, input_: Any, config: Any = None, **kwargs: Any) -> Any:
        """Invoke the primary model, retrying once on APITimeoutError."""
        try:
            return await self._primary.ainvoke(input_, config=config, **kwargs)
        except APITimeoutError:
            if self._fallback is None:
                raise
            return await self._fallback.ainvoke(input_, config=config, **kwargs)

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> "TimeoutFallbackModel":
        """Bind the same tools to both primary and fallback models."""
        bound_primary = self._primary.bind_tools(tools, **kwargs)
        bound_fallback = self._fallback.bind_tools(tools, **kwargs) if self._fallback is not None else None
        return TimeoutFallbackModel(bound_primary, bound_fallback)


class DeepSeekChatOpenAI(ChatOpenAI):
    """ChatOpenAI adapter that preserves DeepSeek thinking-mode reasoning content."""

    def _create_chat_result(self, response: dict | Any, generation_info: dict | None = None) -> Any:
        """Copy DeepSeek `reasoning_content` from raw responses into AIMessage metadata."""
        result = super()._create_chat_result(response, generation_info)
        response_dict = response if isinstance(response, dict) else response.model_dump()

        for generation, choice in zip(result.generations, response_dict.get("choices", []), strict=False):
            message = generation.message
            raw_message = choice.get("message", {})
            reasoning_content = raw_message.get("reasoning_content")
            if isinstance(message, AIMessage) and reasoning_content:
                message.additional_kwargs["reasoning_content"] = reasoning_content

        return result

    def _get_request_payload(self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any) -> dict:
        """Pass persisted DeepSeek `reasoning_content` back on assistant messages."""
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        for source_message, payload_message in zip(messages, payload.get("messages", []), strict=False):
            if not isinstance(source_message, AIMessage):
                continue

            if self._thinking_disabled():
                payload_message.pop("reasoning_content", None)
                continue

            reasoning_content = source_message.additional_kwargs.get("reasoning_content")
            if reasoning_content and payload_message.get("role") == "assistant":
                payload_message["reasoning_content"] = reasoning_content

        return payload

    def _thinking_disabled(self) -> bool:
        """Return whether this model request explicitly disables DeepSeek thinking."""
        extra_body = getattr(self, "extra_body", None) or getattr(self, "model_kwargs", {}).get("extra_body") or {}
        thinking = extra_body.get("thinking") if isinstance(extra_body, dict) else None
        return isinstance(thinking, dict) and thinking.get("type") == "disabled"


def create_deepseek_llm(settings: Settings):
    """Create the final-answer model with token streaming enabled."""
    primary = _create_deepseek_model(settings, settings.deepseek_model, streaming=True, disable_thinking=True)
    fallback = _optional_fallback(settings, settings.deepseek_fallback_model, streaming=True, disable_thinking=True)
    return _with_timeout_fallback(primary, fallback)


def create_deepseek_tool_llm(settings: Settings, *, streaming: bool = False):
    """Create the tool-calling model with optional timeout fallback."""
    primary = _create_deepseek_model(settings, settings.deepseek_tool_model, streaming=streaming, disable_thinking=True)
    fallback_model = settings.deepseek_fallback_tool_model or settings.deepseek_fallback_model
    fallback = _optional_fallback(settings, fallback_model, streaming=streaming, disable_thinking=True)
    return _with_timeout_fallback(primary, fallback)


def _create_deepseek_model(
    settings: Settings,
    model: str,
    *,
    streaming: bool,
    disable_thinking: bool,
) -> ChatOpenAI:
    """Create one DeepSeek OpenAI-compatible model instance."""
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured.")
    if not model:
        raise RuntimeError("DeepSeek model is not configured.")

    kwargs: dict[str, Any] = {}
    if disable_thinking:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    return DeepSeekChatOpenAI(
        model=model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=settings.llm_temperature,
        streaming=streaming,
        timeout=settings.llm_timeout_seconds,
        **kwargs,
    )


def _optional_fallback(
    settings: Settings,
    model: str,
    *,
    streaming: bool,
    disable_thinking: bool,
) -> ChatOpenAI | None:
    """Create a fallback model only when a different fallback is configured."""
    if not model:
        return None
    return _create_deepseek_model(settings, model, streaming=streaming, disable_thinking=disable_thinking)


def _with_timeout_fallback(primary: ChatOpenAI, fallback: ChatOpenAI | None):
    """Retry once on APITimeoutError with the configured fallback model."""
    if fallback is None or fallback.model_name == primary.model_name:
        return primary
    return TimeoutFallbackModel(primary, fallback)
