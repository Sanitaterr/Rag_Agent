package com.jzy.gateway.protocol;

import lombok.Builder;
import lombok.Data;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * One telemetry point extracted from a device MQTT payload.
 */
@Data
@Builder
public class TelemetryPoint {

    private String pointCode;

    private BigDecimal pointValue;

    private String unit;

    private String quality;

    private LocalDateTime sampledAt;
}
