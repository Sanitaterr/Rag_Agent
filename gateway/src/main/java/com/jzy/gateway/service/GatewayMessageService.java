package com.jzy.gateway.service;

import org.springframework.messaging.Message;

/**
 * 网关消息主流程入口，负责接收、处理、落库和转发。
 */
public interface GatewayMessageService {

    void handleInboundMessage(Message<?> message);
}
