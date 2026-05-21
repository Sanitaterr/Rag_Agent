package com.jzy.gateway.service.impl;

import com.jzy.gateway.config.GatewayProperties;
import com.jzy.gateway.mqtt.MqttChannelNames;
import com.jzy.gateway.pojo.entity.GatewayTelemetryRecord;
import com.jzy.gateway.protocol.GatewayMessageContext;
import com.jzy.gateway.protocol.ProtocolMessageProcessor;
import com.jzy.gateway.protocol.TelemetryBatch;
import com.jzy.gateway.protocol.TelemetryPoint;
import com.jzy.gateway.service.GatewayMessageService;
import com.jzy.gateway.service.GatewayTelemetryRecordService;
import com.jzy.gateway.utils.GatewayTopicUtils;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.integration.mqtt.support.MqttHeaders;
import org.springframework.messaging.Message;
import org.springframework.messaging.MessageChannel;
import org.springframework.messaging.MessageHeaders;
import org.springframework.messaging.support.MessageBuilder;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;
import java.util.UUID;

/**
 * Orchestrates MQTT receive, telemetry normalization, MQTT forwarding, and persistence.
 */
@Slf4j
@Service
public class GatewayMessageServiceImpl implements GatewayMessageService {

    private final GatewayProperties gatewayProperties;

    private final ProtocolMessageProcessor protocolMessageProcessor;

    private final GatewayTelemetryRecordService gatewayTelemetryRecordService;

    @Qualifier(MqttChannelNames.OUTBOUND)
    private final MessageChannel mqttOutboundChannel;

    public GatewayMessageServiceImpl(
            GatewayProperties gatewayProperties,
            ProtocolMessageProcessor protocolMessageProcessor,
            GatewayTelemetryRecordService gatewayTelemetryRecordService,
            @Qualifier(MqttChannelNames.OUTBOUND) MessageChannel mqttOutboundChannel
    ) {
        this.gatewayProperties = gatewayProperties;
        this.protocolMessageProcessor = protocolMessageProcessor;
        this.gatewayTelemetryRecordService = gatewayTelemetryRecordService;
        this.mqttOutboundChannel = mqttOutboundChannel;
    }

    @Override
    public void handleInboundMessage(Message<?> message) {
        String messageId = UUID.randomUUID().toString();
        Instant receivedAt = Instant.now();
        LocalDateTime collectedAt = LocalDateTime.ofInstant(receivedAt, ZoneId.systemDefault());
        String sourceTopic = readSourceTopic(message.getHeaders());
        String deviceId = GatewayTopicUtils.extractDeviceId(sourceTopic);
        String rawPayload = toRawPayload(message.getPayload());
        String targetTopic = GatewayTopicUtils.renderTargetTopic(
                gatewayProperties.getProcessing().getOutputTopicTemplate(),
                deviceId
        );

        try {
            GatewayMessageContext context = GatewayMessageContext.builder()
                    .messageId(messageId)
                    .sourceProtocol(gatewayProperties.getProcessing().getSourceProtocol())
                    .sourceTopic(sourceTopic)
                    .deviceId(deviceId)
                    .receivedAt(receivedAt)
                    .build();
            TelemetryBatch telemetryBatch = protocolMessageProcessor.normalize(rawPayload, context);
            publishToAgentBroker(targetTopic, telemetryBatch.getNormalizedPayloadJson());
            saveTelemetryRecords(sourceTopic, targetTopic, deviceId, collectedAt, telemetryBatch);
        } catch (Exception ex) {
            log.warn("MQTT telemetry route failed, messageId={}, sourceTopic={}", messageId, sourceTopic, ex);
        }
    }

    private void publishToAgentBroker(String targetTopic, String normalizedPayload) {
        Message<String> outboundMessage = MessageBuilder.withPayload(normalizedPayload)
                .setHeader(MqttHeaders.TOPIC, targetTopic)
                .setHeader(MqttHeaders.QOS, firstQos(gatewayProperties.getMqtt().getOutbound().getQos()))
                .setHeader(MqttHeaders.RETAINED, gatewayProperties.getMqtt().getOutbound().isRetained())
                .build();
        mqttOutboundChannel.send(outboundMessage);
    }

    private void saveTelemetryRecords(
            String sourceTopic,
            String targetTopic,
            String deviceId,
            LocalDateTime collectedAt,
            TelemetryBatch telemetryBatch
    ) {
        List<GatewayTelemetryRecord> records = telemetryBatch.getPoints().stream()
                .map(point -> toRecord(sourceTopic, targetTopic, deviceId, collectedAt, telemetryBatch, point))
                .toList();
        gatewayTelemetryRecordService.saveBatch(records);
    }

    private GatewayTelemetryRecord toRecord(
            String sourceTopic,
            String targetTopic,
            String deviceId,
            LocalDateTime collectedAt,
            TelemetryBatch telemetryBatch,
            TelemetryPoint point
    ) {
        GatewayTelemetryRecord record = new GatewayTelemetryRecord();
        record.setSourceBroker(gatewayProperties.getMqtt().getInbound().getUrl());
        record.setSourceTopic(sourceTopic);
        record.setTargetTopic(targetTopic);
        record.setDeviceId(deviceId);
        record.setPointCode(point.getPointCode());
        record.setPointValue(point.getPointValue());
        record.setUnit(point.getUnit());
        record.setQuality(point.getQuality());
        record.setSourceProtocol(gatewayProperties.getProcessing().getSourceProtocol());
        record.setSampledAt(point.getSampledAt());
        record.setCollectedAt(collectedAt);
        record.setRawPayload(telemetryBatch.getRawPayloadJson());
        record.setNormalizedPayload(telemetryBatch.getNormalizedPayloadJson());
        return record;
    }

    private String readSourceTopic(MessageHeaders headers) {
        String topic = headers.get(MqttHeaders.RECEIVED_TOPIC, String.class);
        return topic == null ? "unknown" : topic;
    }

    private String toRawPayload(Object payload) {
        if (payload instanceof byte[] bytes) {
            return new String(bytes, StandardCharsets.UTF_8);
        }
        return String.valueOf(payload);
    }

    private int firstQos(List<Integer> qos) {
        return qos.isEmpty() ? 1 : qos.getFirst();
    }
}
