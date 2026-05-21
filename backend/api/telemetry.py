from fastapi import APIRouter

from schemas.telemetry import TelemetrySnapshot
from services.mqtt_subscriber import telemetry_subscriber


router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("/latest", response_model=TelemetrySnapshot)
async def latest_telemetry() -> TelemetrySnapshot:
    """Return the latest MQTT telemetry snapshot for integration debugging."""
    return telemetry_subscriber.snapshot()
