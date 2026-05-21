CREATE TABLE IF NOT EXISTS gateway_telemetry_record
(
    id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
    source_broker      VARCHAR(255)   NOT NULL,
    source_topic       VARCHAR(255)   NOT NULL,
    target_topic       VARCHAR(255)   NOT NULL,
    device_id          VARCHAR(128)   NOT NULL,
    point_code         VARCHAR(128)   NOT NULL,
    point_value        DECIMAL(20, 6) NOT NULL,
    unit               VARCHAR(32)    NULL,
    quality            VARCHAR(32)    NOT NULL,
    source_protocol    VARCHAR(64)    NOT NULL,
    sampled_at         DATETIME       NOT NULL,
    collected_at       DATETIME       NOT NULL,
    raw_payload        JSON           NULL,
    normalized_payload JSON           NULL
);

CREATE INDEX idx_gateway_collected_at
    ON gateway_telemetry_record (collected_at);

CREATE INDEX idx_gateway_device_point_time
    ON gateway_telemetry_record (device_id, point_code, sampled_at);
