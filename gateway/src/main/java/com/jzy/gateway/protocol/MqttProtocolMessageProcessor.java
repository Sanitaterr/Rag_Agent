package com.jzy.gateway.protocol;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import lombok.RequiredArgsConstructor;
import lombok.SneakyThrows;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

/**
 * MQTT telemetry processor. It supports one point, a points array, or a numeric map payload.
 */
@Component
@RequiredArgsConstructor
public class MqttProtocolMessageProcessor implements ProtocolMessageProcessor {

    private static final String DEFAULT_QUALITY = "GOOD";

    private final ObjectMapper objectMapper;

    @Override
    @SneakyThrows
    public TelemetryBatch normalize(String rawPayload, GatewayMessageContext context) {
        JsonNode rawNode = parsePayload(rawPayload);
        LocalDateTime collectedAt = LocalDateTime.ofInstant(context.getReceivedAt(), ZoneId.systemDefault());
        List<TelemetryPoint> points = extractPoints(rawNode, collectedAt);
        if (points.isEmpty()) {
            throw new IllegalArgumentException("MQTT payload does not contain telemetry points");
        }

        ObjectNode normalized = objectMapper.createObjectNode();
        normalized.put("messageId", context.getMessageId());
        normalized.put("sourceProtocol", context.getSourceProtocol());
        normalized.put("sourceTopic", context.getSourceTopic());
        normalized.put("deviceId", context.getDeviceId());
        normalized.put("collectedAt", collectedAt.format(DateTimeFormatter.ISO_LOCAL_DATE_TIME));
        normalized.set("points", toPointsNode(points));

        return TelemetryBatch.builder()
                .rawPayloadJson(objectMapper.writeValueAsString(rawNode))
                .normalizedPayloadJson(objectMapper.writeValueAsString(normalized))
                .points(points)
                .build();
    }

    private JsonNode parsePayload(String rawPayload) {
        try {
            return objectMapper.readTree(rawPayload);
        } catch (Exception ignored) {
            return objectMapper.getNodeFactory().textNode(rawPayload);
        }
    }

    private List<TelemetryPoint> extractPoints(JsonNode payload, LocalDateTime defaultSampledAt) {
        List<TelemetryPoint> points = new ArrayList<>();
        if (payload.isArray()) {
            payload.forEach(node -> addPointIfValid(points, node, defaultSampledAt));
            return points;
        }
        if (!payload.isObject()) {
            return points;
        }

        JsonNode pointsNode = firstExisting(payload, "points", "data", "telemetry");
        if (pointsNode != null && pointsNode.isArray()) {
            pointsNode.forEach(node -> addPointIfValid(points, node, defaultSampledAt));
            return points;
        }

        if (hasPointShape(payload)) {
            addPointIfValid(points, payload, defaultSampledAt);
            return points;
        }

        Iterator<Map.Entry<String, JsonNode>> fields = payload.fields();
        while (fields.hasNext()) {
            Map.Entry<String, JsonNode> field = fields.next();
            if (field.getValue().isNumber()) {
                points.add(TelemetryPoint.builder()
                        .pointCode(field.getKey())
                        .pointValue(field.getValue().decimalValue())
                        .quality(DEFAULT_QUALITY)
                        .sampledAt(defaultSampledAt)
                        .build());
            }
        }
        return points;
    }

    private void addPointIfValid(List<TelemetryPoint> points, JsonNode node, LocalDateTime defaultSampledAt) {
        if (!node.isObject()) {
            return;
        }
        JsonNode codeNode = firstExisting(node, "point_code", "pointCode", "code", "name");
        JsonNode valueNode = firstExisting(node, "point_value", "pointValue", "value");
        if (codeNode == null || valueNode == null || !valueNode.isNumber()) {
            return;
        }

        points.add(TelemetryPoint.builder()
                .pointCode(codeNode.asText())
                .pointValue(valueNode.decimalValue())
                .unit(textOrNull(firstExisting(node, "unit")))
                .quality(textOrDefault(firstExisting(node, "quality"), DEFAULT_QUALITY))
                .sampledAt(parseSampledAt(firstExisting(node, "sampled_at", "sampledAt", "timestamp", "time"), defaultSampledAt))
                .build());
    }

    private boolean hasPointShape(JsonNode payload) {
        return firstExisting(payload, "point_code", "pointCode", "code", "name") != null
                && firstExisting(payload, "point_value", "pointValue", "value") != null;
    }

    private JsonNode firstExisting(JsonNode node, String... names) {
        for (String name : names) {
            JsonNode value = node.get(name);
            if (value != null && !value.isNull()) {
                return value;
            }
        }
        return null;
    }

    private String textOrNull(JsonNode node) {
        return node == null || node.isNull() ? null : node.asText();
    }

    private String textOrDefault(JsonNode node, String defaultValue) {
        String value = textOrNull(node);
        return value == null || value.isBlank() ? defaultValue : value;
    }

    private LocalDateTime parseSampledAt(JsonNode node, LocalDateTime defaultSampledAt) {
        if (node == null || node.isNull()) {
            return defaultSampledAt;
        }
        if (node.isNumber()) {
            long epochMillis = node.asLong();
            return LocalDateTime.ofInstant(Instant.ofEpochMilli(epochMillis), ZoneId.systemDefault());
        }
        String value = node.asText();
        try {
            return LocalDateTime.parse(value, DateTimeFormatter.ISO_LOCAL_DATE_TIME);
        } catch (Exception ignored) {
            try {
                return LocalDateTime.ofInstant(Instant.parse(value), ZoneId.systemDefault());
            } catch (Exception ignoredAgain) {
                return defaultSampledAt;
            }
        }
    }

    private ArrayNode toPointsNode(List<TelemetryPoint> points) {
        ArrayNode arrayNode = objectMapper.createArrayNode();
        for (TelemetryPoint point : points) {
            ObjectNode pointNode = objectMapper.createObjectNode();
            pointNode.put("pointCode", point.getPointCode());
            pointNode.put("pointValue", point.getPointValue());
            pointNode.put("quality", point.getQuality());
            pointNode.put("sampledAt", point.getSampledAt().format(DateTimeFormatter.ISO_LOCAL_DATE_TIME));
            if (point.getUnit() != null) {
                pointNode.put("unit", point.getUnit());
            }
            arrayNode.add(pointNode);
        }
        return arrayNode;
    }
}
