package com.jzy.gateway.mqtt;

/**
 * Spring Integration 通道名称，避免各处硬编码字符串。
 */
public final class MqttChannelNames {

    public static final String INBOUND = "mqttInboundChannel";

    public static final String OUTBOUND = "mqttOutboundChannel";

    private MqttChannelNames() {
    }
}
