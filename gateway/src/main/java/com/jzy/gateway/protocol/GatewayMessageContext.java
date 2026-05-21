package com.jzy.gateway.protocol;

import lombok.Builder;
import lombok.Data;

import java.time.Instant;

/**
 * 协议适配时携带的上下文，避免处理器依赖 Spring Integration 的消息细节。
 */
@Data
@Builder
public class GatewayMessageContext {

    private String messageId;

    private String sourceProtocol;

    private String sourceTopic;

    private String deviceId;

    private Instant receivedAt;
}
