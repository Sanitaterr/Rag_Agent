package com.jzy.gateway.utils;

/**
 * MQTT topic helper for gateway source and agent-side topic mapping.
 */
public final class GatewayTopicUtils {

    private static final String UNKNOWN_DEVICE_ID = "unknown-device";

    private GatewayTopicUtils() {
    }

    /**
     * Extracts device_id from factory/source/{device_id}/telemetry.
     */
    public static String extractDeviceId(String sourceTopic) {
        if (sourceTopic == null || sourceTopic.isBlank()) {
            return UNKNOWN_DEVICE_ID;
        }

        String[] segments = sourceTopic.split("/");
        if (segments.length == 4
                && "factory".equals(segments[0])
                && "source".equals(segments[1])
                && "telemetry".equals(segments[3])
                && !segments[2].isBlank()) {
            return segments[2];
        }
        return UNKNOWN_DEVICE_ID;
    }

    /**
     * Renders factory/rag/{deviceId}/telemetry style templates.
     */
    public static String renderTargetTopic(String topicTemplate, String deviceId) {
        String safeDeviceId = deviceId == null || deviceId.isBlank() ? UNKNOWN_DEVICE_ID : deviceId;
        return topicTemplate
                .replace("{deviceId}", safeDeviceId)
                .replace("{device_id}", safeDeviceId);
    }
}
