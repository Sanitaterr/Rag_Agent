from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from db.database import AsyncSessionLocal
from schemas.access_points import (
    AccessPointDevice,
    AccessPointDeviceDetail,
    AccessPointDeviceList,
    AccessPointStats,
    AccessPointStatus,
    SensorPoint,
)


ONLINE_THRESHOLD = timedelta(minutes=5)


LATEST_POINTS_SQL = text(
    """
    WITH ranked_points AS (
        SELECT
            id,
            device_id,
            point_code,
            point_value,
            unit,
            quality,
            source_protocol,
            sampled_at,
            collected_at,
            ROW_NUMBER() OVER (
                PARTITION BY device_id, point_code
                ORDER BY collected_at DESC, id DESC
            ) AS rn
        FROM gateway_telemetry_record
    )
    SELECT
        id,
        device_id,
        point_code,
        point_value,
        unit,
        quality,
        source_protocol,
        sampled_at,
        collected_at
    FROM ranked_points
    WHERE rn = 1
    ORDER BY device_id, point_code
    """
)


LATEST_DEVICE_POINTS_SQL = text(
    """
    WITH ranked_points AS (
        SELECT
            id,
            device_id,
            point_code,
            point_value,
            unit,
            quality,
            source_protocol,
            sampled_at,
            collected_at,
            ROW_NUMBER() OVER (
                PARTITION BY device_id, point_code
                ORDER BY collected_at DESC, id DESC
            ) AS rn
        FROM gateway_telemetry_record
        WHERE device_id = :device_id
    )
    SELECT
        id,
        device_id,
        point_code,
        point_value,
        unit,
        quality,
        source_protocol,
        sampled_at,
        collected_at
    FROM ranked_points
    WHERE rn = 1
    ORDER BY point_code
    """
)


async def list_devices(status: AccessPointStatus | None = None, keyword: str | None = None) -> AccessPointDeviceList:
    """Return read-only devices and their latest point values from gateway telemetry."""
    now = datetime.now()
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(LATEST_POINTS_SQL)).mappings().all()

    points = [_point_from_row(row) for row in _dedupe_latest_points(rows)]
    devices = _build_devices(points, now)
    devices = _filter_devices(devices, status=status, keyword=keyword)
    return AccessPointDeviceList(
        devices=devices,
        stats=_build_stats(devices),
        generated_at=now,
        online_threshold_seconds=int(ONLINE_THRESHOLD.total_seconds()),
    )


async def get_device(device_id: str) -> AccessPointDeviceDetail | None:
    """Return one device and all latest points, or None when the device has no records."""
    now = datetime.now()
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(LATEST_DEVICE_POINTS_SQL, {"device_id": device_id})).mappings().all()

    points = [_point_from_row(row) for row in _dedupe_latest_points(rows)]
    if not points:
        return None

    device = _build_devices(points, now)[0]
    return AccessPointDeviceDetail(
        device=device,
        points=points,
        generated_at=now,
        online_threshold_seconds=int(ONLINE_THRESHOLD.total_seconds()),
    )


def determine_device_status(
    last_seen_at: datetime | None,
    has_warning_points: bool,
    now: datetime,
    threshold: timedelta = ONLINE_THRESHOLD,
) -> AccessPointStatus:
    """Classify device health from freshness first, then point quality."""
    if last_seen_at is None:
        return "offline"

    comparable_last_seen = _as_naive_datetime(last_seen_at)
    comparable_now = _as_naive_datetime(now)
    if comparable_now - comparable_last_seen > threshold:
        return "offline"
    if has_warning_points:
        return "warning"
    return "online"


def _dedupe_latest_points(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Keep the newest row for each device/point pair; mirrors the SQL ordering defensively."""
    latest: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (str(row["device_id"]), str(row["point_code"]))
        current = latest.get(key)
        if current is None or _is_newer_point(row, current):
            latest[key] = row
    return sorted(latest.values(), key=lambda item: (str(item["device_id"]), str(item["point_code"])))


def _is_newer_point(candidate: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    candidate_time = candidate.get("collected_at")
    current_time = current.get("collected_at")
    if candidate_time != current_time:
        if current_time is None:
            return True
        if candidate_time is None:
            return False
        return _as_naive_datetime(candidate_time) > _as_naive_datetime(current_time)
    return int(candidate.get("id") or 0) > int(current.get("id") or 0)


def _build_devices(points: list[SensorPoint], now: datetime) -> list[AccessPointDevice]:
    devices: list[AccessPointDevice] = []
    by_device: dict[str, list[SensorPoint]] = {}
    for point in points:
        by_device.setdefault(point.device_id, []).append(point)

    for device_id, device_points in by_device.items():
        sorted_points = sorted(device_points, key=lambda item: item.point_code)
        last_seen_at = max((point.collected_at for point in sorted_points if point.collected_at), default=None)
        warning_point_count = sum(1 for point in sorted_points if point.quality.upper() != "GOOD")
        status = determine_device_status(last_seen_at, warning_point_count > 0, now)
        latest_point = max(
            sorted_points,
            key=lambda item: _as_naive_datetime(item.collected_at) if item.collected_at else datetime.min,
        )
        devices.append(
            AccessPointDevice(
                device_id=device_id,
                name=f"设备 {device_id}",
                status=status,
                source_protocol=latest_point.source_protocol,
                point_count=len(sorted_points),
                warning_point_count=warning_point_count,
                last_seen_at=last_seen_at,
                latest_points=sorted_points[:6],
            )
        )

    return sorted(devices, key=lambda item: item.device_id)


def _build_stats(devices: list[AccessPointDevice]) -> AccessPointStats:
    return AccessPointStats(
        devices=len(devices),
        online=sum(1 for device in devices if device.status == "online"),
        warning=sum(1 for device in devices if device.status == "warning"),
        offline=sum(1 for device in devices if device.status == "offline"),
        points=sum(device.point_count for device in devices),
        warning_points=sum(device.warning_point_count for device in devices),
    )


def _filter_devices(
    devices: list[AccessPointDevice],
    status: AccessPointStatus | None,
    keyword: str | None,
) -> list[AccessPointDevice]:
    filtered = [device for device in devices if status is None or device.status == status]
    normalized_keyword = (keyword or "").strip().lower()
    if not normalized_keyword:
        return filtered

    return [
        device
        for device in filtered
        if normalized_keyword in device.device_id.lower()
        or normalized_keyword in device.name.lower()
        or normalized_keyword in device.source_protocol.lower()
        or any(normalized_keyword in point.point_code.lower() for point in device.latest_points)
    ]


def _point_from_row(row: Mapping[str, Any]) -> SensorPoint:
    return SensorPoint(
        device_id=str(row["device_id"]),
        point_code=str(row["point_code"]),
        point_value=_to_float(row.get("point_value")),
        unit=row.get("unit") or "",
        quality=str(row.get("quality") or "UNKNOWN"),
        source_protocol=str(row.get("source_protocol") or "UNKNOWN"),
        sampled_at=row.get("sampled_at"),
        collected_at=row.get("collected_at"),
    )


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _as_naive_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)
