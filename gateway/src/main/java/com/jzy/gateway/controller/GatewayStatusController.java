package com.jzy.gateway.controller;

import com.jzy.gateway.config.GatewayProperties;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 网关运行状态接口，用于前端或运维侧快速确认配置。
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/gateway")
public class GatewayStatusController {

    private final GatewayProperties gatewayProperties;

    @GetMapping("/status")
    public Map<String, Object> status() {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("name", "gateway");
        result.put("mqttEnabled", gatewayProperties.getMqtt().isEnabled());
        result.put("sourceProtocol", gatewayProperties.getProcessing().getSourceProtocol());
        result.put("inboundBroker", gatewayProperties.getMqtt().getInbound().getUrl());
        result.put("inboundTopics", gatewayProperties.getMqtt().getInbound().getTopics());
        result.put("outboundBroker", gatewayProperties.getMqtt().getOutbound().getUrl());
        result.put("outputTopicTemplate", gatewayProperties.getProcessing().getOutputTopicTemplate());
        return result;
    }
}
