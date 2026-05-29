from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


AccessPointStatus = Literal["online", "warning", "offline"]


class SensorPoint(BaseModel):
    """Latest value for one sensor point under a device."""

    device_id: str
    point_code: str
    point_value: float | None = None
    unit: str | None = None
    quality: str
    source_protocol: str
    sampled_at: datetime | None = None
    collected_at: datetime | None = None


class AccessPointDevice(BaseModel):
    """Read-only device summary derived from telemetry records."""

    device_id: str
    name: str
    status: AccessPointStatus
    source_protocol: str
    point_count: int
    warning_point_count: int
    last_seen_at: datetime | None = None
    latest_points: list[SensorPoint] = Field(default_factory=list)


class AccessPointStats(BaseModel):
    """Aggregate counters for the access point dashboard."""

    devices: int = 0
    online: int = 0
    warning: int = 0
    offline: int = 0
    points: int = 0
    warning_points: int = 0


class AccessPointDeviceList(BaseModel):
    """Device list response for the access point dashboard."""

    devices: list[AccessPointDevice] = Field(default_factory=list)
    stats: AccessPointStats = Field(default_factory=AccessPointStats)
    generated_at: datetime
    online_threshold_seconds: int


class AccessPointDeviceDetail(BaseModel):
    """Single-device detail response with all latest sensor points."""

    device: AccessPointDevice
    points: list[SensorPoint] = Field(default_factory=list)
    generated_at: datetime
    online_threshold_seconds: int
