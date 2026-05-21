import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from agent.runtime.agent_graph import chat_agent
from schemas.agent import AgentChatRequest, AgentSessionMessage, AgentSessionSummary


router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat/stream")
async def stream_chat(request: AgentChatRequest) -> StreamingResponse:
    """SSE streaming chat endpoint."""

    async def event_stream():
        try:
            async for event_name, payload in chat_agent.stream_chat(request.message, request.session_id):
                yield _sse_event(event_name, payload)
                # 让出事件循环，促使 Uvicorn 尽快把当前 SSE 帧刷到客户端。
                await asyncio.sleep(0)
        except RuntimeError as exc:
            yield _sse_event("error", str(exc))
        except Exception as exc:
            yield _sse_event("error", f"LLM stream failed: {exc}")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions", response_model=list[AgentSessionSummary])
async def list_sessions() -> list[AgentSessionSummary]:
    """List persisted LangGraph threads for the chat sidebar."""
    return [AgentSessionSummary(**session) for session in await chat_agent.list_sessions()]


@router.get("/sessions/{session_id}/messages", response_model=list[AgentSessionMessage])
async def get_session_messages(session_id: str) -> list[AgentSessionMessage]:
    """Load messages for one persisted LangGraph thread."""
    return [AgentSessionMessage(**message) for message in await chat_agent.load_session_messages(session_id)]


def _sse_event(event_name: str, payload: str) -> str:
    """Format a server-sent event frame."""
    return f"event: {event_name}\ndata: {json.dumps({'value': payload}, ensure_ascii=False)}\n\n"
