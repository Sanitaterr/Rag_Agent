package com.jzy.gateway.protocol;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

class MqttProtocolMessageProcessorTest {

    private final MqttProtocolMessageProcessor processor = new MqttProtocolMessageProcessor(new ObjectMapper());

    @Test
    void normalizeSinglePointPayload() {
        TelemetryBatch batch = processor.normalize(
                """
                        {"point_code":"temperature","point_value":26.5,"unit":"C","quality":"GOOD","sampled_at":"2026-05-21T09:30:00"}
                        """,
                context()
        );

        assertThat(batch.getPoints()).hasSize(1);
        assertThat(batch.getPoints().getFirst().getPointCode()).isEqualTo("temperature");
        assertThat(batch.getPoints().getFirst().getPointValue()).isEqualByComparingTo(new BigDecimal("26.5"));
        assertThat(batch.getNormalizedPayloadJson()).contains("\"deviceId\":\"device-001\"");
    }

    @Test
    void normalizeMultiPointPayload() {
        TelemetryBatch batch = processor.normalize(
                """
                        {"points":[{"point_code":"temperature","point_value":26.5},{"point_code":"pressure","point_value":0.82}]}
                        """,
                context()
        );

        assertThat(batch.getPoints()).hasSize(2);
        assertThat(batch.getPoints())
                .extracting(TelemetryPoint::getPointCode)
                .containsExactly("temperature", "pressure");
    }

    private GatewayMessageContext context() {
        return GatewayMessageContext.builder()
                .messageId("message-001")
                .sourceProtocol("MQTT")
                .sourceTopic("factory/source/device-001/telemetry")
                .deviceId("device-001")
                .receivedAt(Instant.parse("2026-05-21T01:30:00Z"))
                .build();
    }
}
