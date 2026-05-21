package com.jzy.gateway.protocol;

/**
 * 协议消息处理接口，后续接 Apache PLC4X 时只需新增实现。
 */
public interface ProtocolMessageProcessor {

    /**
     * Converts an upstream protocol message into records and agent-side JSON.
     */
    TelemetryBatch normalize(String rawPayload, GatewayMessageContext context);
}
