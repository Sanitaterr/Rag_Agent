import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from agent.runtime.agent_graph import chat_agent
from schemas.agent import (
    AgentChatRequest,
    AgentSessionDeleteResult,
    AgentSessionMessage,
    AgentSessionSummary,
)


router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat/stream")
async def stream_chat(request: Request, payload: AgentChatRequest) -> StreamingResponse:
    """SSE streaming chat endpoint."""

    async def event_stream():
        try:
            async for event_name, event_payload in chat_agent.stream_chat(payload.message, payload.session_id):
                if await request.is_disconnected():
                    break
                yield _sse_event(event_name, event_payload)
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
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


@router.delete("/sessions/{session_id}", response_model=AgentSessionDeleteResult)
async def delete_session(session_id: str) -> AgentSessionDeleteResult:
    """Delete one persisted LangGraph thread from all checkpoint tables."""
    counts = await chat_agent.delete_session(session_id)
    deleted = any(count > 0 for count in counts.values())
    return AgentSessionDeleteResult(deleted=deleted, **counts)


def _sse_event(event_name: str, payload: str) -> str:
    """Format a server-sent event frame."""
    return f"event: {event_name}\ndata: {json.dumps({'value': payload}, ensure_ascii=False)}\n\n"
