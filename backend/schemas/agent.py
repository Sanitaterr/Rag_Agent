from pydantic import BaseModel, Field


class AgentChatRequest(BaseModel):
    """Streaming chat request; empty session_id starts a new LangGraph thread."""

    message: str = Field(min_length=1, description="User message")
    session_id: str | None = Field(default=None, description="LangGraph session ID")


class AgentSessionSummary(BaseModel):
    """Sidebar summary for a persisted LangGraph thread."""

    id: str = Field(description="LangGraph thread ID")
    title: str = Field(description="Temporary title from the first user message")
    updated_at: str = Field(description="Last checkpoint timestamp")


class AgentSessionMessage(BaseModel):
    """Message payload used by the Vue chat view."""

    id: str = Field(description="Message ID")
    role: str = Field(description="Message role: user or assistant")
    text: str = Field(description="Plain message content")
    status: str = Field(default="done", description="Message render status")
