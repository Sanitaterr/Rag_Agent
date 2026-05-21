from __future__ import annotations

from datetime import datetime, timezone
import json
from threading import RLock
from typing import Any

from config.settings import Settings, settings
from schemas.telemetry import TelemetryRecord, TelemetrySnapshot


class TelemetrySubscriber:
    """Background MQTT subscriber that keeps the latest payload per topic."""

    def __init__(self, app_settings: Settings) -> None:
        self._settings = app_settings
        self._lock = RLock()
        self._latest_by_topic: dict[str, TelemetryRecord] = {}
        self._client: Any | None = None

    def start(self) -> None:
        """Start the MQTT network loop; missing broker/dependency should not block local API work."""
        try:
            from paho.mqtt import client as mqtt
        except ImportError:
            print("[MQTT] paho-mqtt is not installed; telemetry subscription skipped.")
            return

        self._client = self._build_client(mqtt)
        try:
            self._client.connect(
                self._settings.mqtt_host,
                self._settings.mqtt_port,
                keepalive=self._settings.mqtt_keepalive,
            )
            self._client.loop_start()
            print(f"[MQTT] Connecting to {self._settings.mqtt_host}:{self._settings.mqtt_port}")
        except Exception as exc:
            print(f"[MQTT] Connection failed: {exc}")

    def stop(self) -> None:
        """Stop the MQTT loop and disconnect the client."""
        if self._client is None:
            return

        try:
            self._client.loop_stop()
            self._client.disconnect()
        finally:
            self._client = None

    def snapshot(self) -> TelemetrySnapshot:
        """Return the current in-memory telemetry snapshot."""
        with self._lock:
            latest = sorted(
                self._latest_by_topic.values(),
                key=lambda item: item.received_at,
                reverse=True,
            )

        return TelemetrySnapshot(
            subscribed_topic=self._settings.mqtt_topic,
            latest=latest,
        )

    def _build_client(self, mqtt: Any) -> Any:
        """Build a paho-mqtt client compatible with both 1.x and 2.x APIs."""
        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self._settings.mqtt_client_id,
            )
        except AttributeError:
            client = mqtt.Client(client_id=self._settings.mqtt_client_id)

        if self._settings.mqtt_username:
            client.username_pw_set(self._settings.mqtt_username, self._settings.mqtt_password or None)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        if self._is_success_reason(reason_code):
            client.subscribe(self._settings.mqtt_topic)
            print(f"[MQTT] Subscribed: {self._settings.mqtt_topic}")
        else:
            print(f"[MQTT] Connection rejected, reason_code={reason_code}")

    def _on_disconnect(self, client: Any, userdata: Any, *args: Any) -> None:
        print(f"[MQTT] Disconnected, args={args}")

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        payload_text = message.payload.decode("utf-8", errors="replace")
        payload: dict[str, Any] | str
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            payload = payload_text

        record = TelemetryRecord(
            topic=message.topic,
            payload=payload,
            received_at=datetime.now(timezone.utc),
        )

        with self._lock:
            self._latest_by_topic[message.topic] = record

        print(f"[MQTT] topic={message.topic} payload={json.dumps(payload, ensure_ascii=False)}")

    @staticmethod
    def _is_success_reason(reason_code: Any) -> bool:
        """Normalize paho-mqtt 1.x int codes and 2.x ReasonCode objects."""
        if isinstance(reason_code, int):
            return reason_code == 0
        return str(reason_code).lower() in {"success", "0"}


telemetry_subscriber = TelemetrySubscriber(settings)
