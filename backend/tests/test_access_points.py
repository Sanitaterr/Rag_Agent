from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.access_points import _dedupe_latest_points, determine_device_status


def test_device_status_online_when_recent_and_good() -> None:
    now = datetime(2026, 5, 25, 10, 0, 0)

    status = determine_device_status(now - timedelta(minutes=2), has_warning_points=False, now=now)

    assert status == "online"


def test_device_status_warning_when_recent_but_quality_is_not_good() -> None:
    now = datetime(2026, 5, 25, 10, 0, 0)

    status = determine_device_status(now - timedelta(minutes=2), has_warning_points=True, now=now)

    assert status == "warning"


def test_device_status_offline_when_last_seen_exceeds_threshold() -> None:
    now = datetime(2026, 5, 25, 10, 0, 0)

    status = determine_device_status(now - timedelta(minutes=6), has_warning_points=False, now=now)

    assert status == "offline"


def test_dedupe_latest_points_keeps_newest_collected_at_then_id() -> None:
    older_time = datetime(2026, 5, 25, 9, 0, 0)
    newer_time = datetime(2026, 5, 25, 9, 5, 0)
    rows = [
        {
            "id": 1,
            "device_id": "1",
            "point_code": "temp",
            "point_value": Decimal("11.0"),
            "collected_at": older_time,
        },
        {
            "id": 2,
            "device_id": "1",
            "point_code": "temp",
            "point_value": Decimal("12.0"),
            "collected_at": newer_time,
        },
        {
            "id": 3,
            "device_id": "1",
            "point_code": "temp",
            "point_value": Decimal("13.0"),
            "collected_at": newer_time,
        },
        {
            "id": 4,
            "device_id": "1",
            "point_code": "pressure",
            "point_value": Decimal("1.0"),
            "collected_at": older_time,
        },
    ]

    latest = _dedupe_latest_points(rows)

    assert len(latest) == 2
    assert latest[0]["point_code"] == "pressure"
    assert latest[1]["point_code"] == "temp"
    assert latest[1]["id"] == 3
