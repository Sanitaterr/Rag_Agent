package com.jzy.gateway.config;

import lombok.Data;
import lombok.EqualsAndHashCode;
import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.ArrayList;
import java.util.List;

/**
 * 网关配置入口，集中维护 MQTT 输入、输出 Broker 和处理规则。
 */
@Data
@ConfigurationProperties(prefix = "gateway")
public class GatewayProperties {

    private Mqtt mqtt = new Mqtt();

    private Processing processing = new Processing();

    @Data
    public static class Mqtt {

        /**
         * 本地测试或无 Broker 环境可关闭 MQTT 连接。
         */
        private boolean enabled = true;

        private Inbound inbound = new Inbound();

        private Outbound outbound = new Outbound();
    }

    @Data
    @EqualsAndHashCode(callSuper = true)
    public static class Inbound extends Broker {

        /**
         * 网关订阅的上游主题，支持 MQTT 通配符。
         */
        private List<String> topics = new ArrayList<>(List.of("factory/source/+/telemetry"));
    }

    @Data
    @EqualsAndHashCode(callSuper = true)
    public static class Outbound extends Broker {

        /**
         * 处理后默认发往 agent 侧 Broker 的主题。
         */
        private String defaultTopic = "factory/rag/unknown-device/telemetry";

        private boolean retained = false;

        private boolean async = true;
    }

    @Data
    public static class Broker {

        private String url = "tcp://localhost:1883";

        private String clientId = "rag-gateway";

        private String username;

        private String password;

        private List<Integer> qos = new ArrayList<>(List.of(1));

        private int connectionTimeoutSeconds = 10;

        private int keepAliveSeconds = 30;

        private boolean automaticReconnect = true;

        private boolean cleanSession = true;
    }

    @Data
    public static class Processing {

        /**
         * 当前只接 MQTT；后续接 OPC UA、S7、Modbus 时可通过适配器扩展。
         */
        private String sourceProtocol = "MQTT";

        private String outputTopicTemplate = "factory/rag/{deviceId}/telemetry";
    }
}
