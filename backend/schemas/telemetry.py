from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TelemetryRecord(BaseModel):
    """One MQTT telemetry message kept in the backend snapshot."""

    topic: str
    payload: dict[str, Any] | str
    received_at: datetime


class TelemetrySnapshot(BaseModel):
    """Latest MQTT telemetry values grouped by topic."""

    subscribed_topic: str
    latest: list[TelemetryRecord] = Field(default_factory=list)
