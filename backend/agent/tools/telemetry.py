from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from langchain_core.tools import tool
from sqlalchemy import text

from agent.tools.schemas import TelemetryDevicesInput, TelemetryLatestInput, TelemetrySummaryInput


@tool(args_schema=TelemetryLatestInput)
async def get_latest_telemetry(
    device_id: str | None = None,
    point_code: str | None = None,
    limit: int = 10,
) -> str:
    """Query latest telemetry records from gateway_telemetry_record."""
    safe_limit = max(1, min(limit, 50))
    filters = []
    params: dict[str, Any] = {"limit": safe_limit}
    if device_id:
        filters.append("device_id = :device_id")
        params["device_id"] = device_id
    if point_code:
        filters.append("point_code = :point_code")
        params["point_code"] = point_code

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    query = text(
        f"""
        SELECT device_id, point_code, point_value, unit, quality, sampled_at, collected_at, source_topic, target_topic
        FROM gateway_telemetry_record
        {where_clause}
        ORDER BY collected_at DESC
        LIMIT :limit
        """
    )

    try:
        async with _async_session_local()() as session:
            rows = (await session.execute(query, params)).mappings().all()
    except Exception as exc:
        return f"Telemetry query unavailable: {exc}"

    return _json_dumps({"records": [_mapping_to_dict(row) for row in rows]})


@tool(args_schema=TelemetrySummaryInput)
async def summarize_telemetry(
    device_id: str,
    point_code: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> str:
    """Analyze telemetry count, min, max, avg, and standard deviation for a point."""
    filters = ["device_id = :device_id", "point_code = :point_code"]
    params: dict[str, Any] = {"device_id": device_id, "point_code": point_code}
    if start_time:
        filters.append("sampled_at >= :start_time")
        params["start_time"] = _parse_datetime(start_time)
    if end_time:
        filters.append("sampled_at <= :end_time")
        params["end_time"] = _parse_datetime(end_time)

    query = text(
        f"""
        SELECT
            COUNT(*) AS sample_count,
            MIN(point_value) AS min_value,
            MAX(point_value) AS max_value,
            AVG(point_value) AS avg_value,
            STDDEV_SAMP(point_value) AS stddev_value,
            MIN(sampled_at) AS first_sampled_at,
            MAX(sampled_at) AS last_sampled_at
        FROM gateway_telemetry_record
        WHERE {' AND '.join(filters)}
        """
    )

    try:
        async with _async_session_local()() as session:
            row = (await session.execute(query, params)).mappings().one()
    except Exception as exc:
        return f"Telemetry summary unavailable: {exc}"

    return _json_dumps(
        {
            "device_id": device_id,
            "point_code": point_code,
            "summary": _mapping_to_dict(row),
        }
    )


@tool(args_schema=TelemetryDevicesInput)
async def list_telemetry_devices(limit: int = 20) -> str:
    """List devices and their latest telemetry collection time."""
    safe_limit = max(1, min(limit, 100))
    query = text(
        """
        SELECT device_id, COUNT(*) AS record_count, MAX(collected_at) AS latest_collected_at
        FROM gateway_telemetry_record
        GROUP BY device_id
        ORDER BY latest_collected_at DESC
        LIMIT :limit
        """
    )

    try:
        async with _async_session_local()() as session:
            rows = (await session.execute(query, {"limit": safe_limit})).mappings().all()
    except Exception as exc:
        return f"Telemetry device list unavailable: {exc}"

    return _json_dumps({"devices": [_mapping_to_dict(row) for row in rows]})


def _async_session_local():
    """Lazy import DB engine so tool registration still works in Studio without DB drivers."""
    try:
        from db.database import AsyncSessionLocal
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"missing database dependency: {exc.name}") from exc
    return AsyncSessionLocal


def _parse_datetime(value: str) -> datetime:
    """Parse ISO datetime strings accepted by the agent tools."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _mapping_to_dict(row: Any) -> dict[str, Any]:
    return {key: _json_safe(value) for key, value in dict(row).items()}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
