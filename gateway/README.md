# Gateway

Spring Boot 3 + JDK 21 gateway service. The current flow is MQTT -> normalize telemetry -> MQTT -> MySQL record.

## Topic Rules

Device publishes to the source broker:

```text
factory/source/{device_id}/telemetry
```

Gateway subscribes from the source broker:

```text
factory/source/+/telemetry
```

Gateway publishes to the target broker:

```text
factory/rag/{device_id}/telemetry
```

Agent subscribes:

```text
factory/rag/+/telemetry
```

Gateway extracts `{device_id}` from the source topic and writes it to `gateway_telemetry_record.device_id`.

## Payload Shape

Supported single-point payload:

```json
{
  "point_code": "temperature",
  "point_value": 26.5,
  "unit": "C",
  "quality": "GOOD",
  "sampled_at": "2026-05-21T09:30:00"
}
```

Supported multi-point payload:

```json
{
  "points": [
    {"point_code": "temperature", "point_value": 26.5, "unit": "C"},
    {"point_code": "pressure", "point_value": 0.82, "unit": "MPa"}
  ]
}
```

If `quality` is missing, gateway uses `GOOD`. If `sampled_at` is missing, gateway uses the receive time.

## Database

The gateway uses your existing `gateway_telemetry_record` table. The SQL file is only a reference copy of that schema:

```sql
source src/main/resources/sql/gateway_schema.sql;
```

Default config:

- MySQL: `localhost:3306/rag`
- Source broker: `tcp://localhost:1883`
- Target broker: `tcp://localhost:1884`

## Start

Windows PowerShell:

```powershell
$env:JAVA_HOME='D:\jdk21'
$env:Path="$env:JAVA_HOME\bin;$env:Path"
.\mvnw.cmd spring-boot:run
```

## API

- `GET /api/gateway/status`
- `GET /api/gateway/telemetry/recent?limit=20`
- `GET /actuator/health`
