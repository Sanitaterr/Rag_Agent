from __future__ import annotations

import json
import asyncio
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from agent.tools import telemetry


def test_latest_telemetry_uses_latest_point_snapshot(monkeypatch) -> None:
    session = _FakeSession(
        [
            {
                "id": 9,
                "device_id": "dev-1",
                "point_code": "TEMP",
                "point_value": Decimal("32.5"),
                "unit": "℃",
                "quality": "GOOD",
                "source_protocol": "MQTT",
                "sampled_at": datetime(2026, 5, 28, 10, 0, 0),
                "collected_at": datetime(2026, 5, 28, 10, 0, 1),
                "source_topic": "factory/source/dev-1/telemetry",
                "target_topic": "factory/agent/dev-1/telemetry",
            }
        ]
    )
    monkeypatch.setattr(telemetry, "_async_session_local", lambda: lambda: session)
    monkeypatch.setattr(telemetry, "_access_point_services", lambda: (_fake_get_device, None))

    text = asyncio.run(telemetry.get_latest_telemetry.coroutine(device_id="dev-1", limit=5))
    payload = json.loads(text)

    assert "ROW_NUMBER() OVER" in session.executed_sql
    assert "PARTITION BY device_id, point_code" in session.executed_sql
    assert payload["mode"] == "latest_per_device_point"
    assert payload["records"][0]["point_code"] == "TEMP"
    assert payload["records"][0]["point_value"] == 32.5
    assert payload["device"]["status"] == "online"


def test_list_telemetry_devices_returns_status_and_latest_points(monkeypatch) -> None:
    monkeypatch.setattr(telemetry, "_access_point_services", lambda: (None, _fake_list_devices))

    text = asyncio.run(telemetry.list_telemetry_devices.coroutine(limit=10))
    payload = json.loads(text)

    assert payload["mode"] == "latest_device_status_with_points"
    assert payload["stats"]["devices"] == 1
    assert payload["devices"][0]["device_id"] == "dev-1"
    assert payload["devices"][0]["status"] == "online"
    assert payload["devices"][0]["latest_points"][0]["point_code"] == "TEMP"


async def _fake_get_device(device_id: str):
    return SimpleNamespace(
        device=_FakeModel(
            {
                "device_id": device_id,
                "status": "online",
                "point_count": 1,
                "latest_points": [{"point_code": "TEMP", "point_value": 32.5}],
            }
        ),
        online_threshold_seconds=300,
    )


async def _fake_list_devices():
    return SimpleNamespace(
        generated_at=datetime(2026, 5, 28, 10, 1, 0),
        online_threshold_seconds=300,
        stats=_FakeModel({"devices": 1, "online": 1, "warning": 0, "offline": 0, "points": 1, "warning_points": 0}),
        devices=[
            _FakeModel(
                {
                    "device_id": "dev-1",
                    "status": "online",
                    "point_count": 1,
                    "last_seen_at": "2026-05-28T10:00:01",
                    "latest_points": [{"point_code": "TEMP", "point_value": 32.5, "quality": "GOOD"}],
                }
            )
        ],
    )


class _FakeModel:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.executed_sql = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def execute(self, query, params):
        self.executed_sql = str(query)
        return _FakeResult(self._rows)


class _FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def all(self) -> list[dict]:
        return self._rows
