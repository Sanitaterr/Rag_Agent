package com.jzy.gateway.protocol;

import lombok.Builder;
import lombok.Data;

import java.util.List;

/**
 * Normalized telemetry payload and the point records that should be stored.
 */
@Data
@Builder
public class TelemetryBatch {

    private String rawPayloadJson;

    private String normalizedPayloadJson;

    private List<TelemetryPoint> points;
}
