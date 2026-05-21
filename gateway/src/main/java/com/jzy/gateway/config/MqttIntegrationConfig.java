package com.jzy.gateway.config;

import com.jzy.gateway.mqtt.MqttChannelNames;
import com.jzy.gateway.service.GatewayMessageService;
import org.eclipse.paho.client.mqttv3.MqttConnectOptions;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.integration.annotation.ServiceActivator;
import org.springframework.integration.channel.DirectChannel;
import org.springframework.integration.core.MessageProducer;
import org.springframework.integration.mqtt.core.DefaultMqttPahoClientFactory;
import org.springframework.integration.mqtt.core.MqttPahoClientFactory;
import org.springframework.integration.mqtt.inbound.MqttPahoMessageDrivenChannelAdapter;
import org.springframework.integration.mqtt.outbound.MqttPahoMessageHandler;
import org.springframework.integration.mqtt.support.DefaultPahoMessageConverter;
import org.springframework.messaging.MessageChannel;
import org.springframework.messaging.MessageHandler;

import java.util.List;

/**
 * MQTT 入站订阅和出站发布配置，使用 Spring Integration 减少手写连接管理代码。
 */
@Configuration
public class MqttIntegrationConfig {

    @Bean
    public MessageChannel mqttInboundChannel() {
        return new DirectChannel();
    }

    @Bean
    public MessageChannel mqttOutboundChannel() {
        return new DirectChannel();
    }

    @Bean
    public MqttPahoClientFactory inboundMqttClientFactory(GatewayProperties properties) {
        return createClientFactory(properties.getMqtt().getInbound());
    }

    @Bean
    public MqttPahoClientFactory outboundMqttClientFactory(GatewayProperties properties) {
        return createClientFactory(properties.getMqtt().getOutbound());
    }

    @Bean
    @ConditionalOnProperty(prefix = "gateway.mqtt", name = "enabled", havingValue = "true", matchIfMissing = true)
    public MessageProducer mqttInboundAdapter(
            GatewayProperties properties,
            MqttPahoClientFactory inboundMqttClientFactory,
            MessageChannel mqttInboundChannel
    ) {
        GatewayProperties.Inbound inbound = properties.getMqtt().getInbound();
        MqttPahoMessageDrivenChannelAdapter adapter = new MqttPahoMessageDrivenChannelAdapter(
                inbound.getClientId(),
                inboundMqttClientFactory,
                inbound.getTopics().toArray(String[]::new)
        );
        adapter.setCompletionTimeout(5000);
        adapter.setConverter(new DefaultPahoMessageConverter());
        adapter.setQos(toQosArray(inbound.getQos()));
        adapter.setOutputChannel(mqttInboundChannel);
        return adapter;
    }

    @Bean
    @ServiceActivator(inputChannel = MqttChannelNames.INBOUND)
    public MessageHandler mqttInboundHandler(GatewayMessageService gatewayMessageService) {
        return gatewayMessageService::handleInboundMessage;
    }

    @Bean
    @ServiceActivator(inputChannel = MqttChannelNames.OUTBOUND)
    @ConditionalOnProperty(prefix = "gateway.mqtt", name = "enabled", havingValue = "true", matchIfMissing = true)
    public MessageHandler mqttOutboundHandler(
            GatewayProperties properties,
            MqttPahoClientFactory outboundMqttClientFactory
    ) {
        GatewayProperties.Outbound outbound = properties.getMqtt().getOutbound();
        MqttPahoMessageHandler handler = new MqttPahoMessageHandler(outbound.getClientId(), outboundMqttClientFactory);
        handler.setAsync(outbound.isAsync());
        handler.setDefaultTopic(outbound.getDefaultTopic());
        handler.setDefaultQos(firstQos(outbound.getQos()));
        handler.setDefaultRetained(outbound.isRetained());
        return handler;
    }

    private MqttPahoClientFactory createClientFactory(GatewayProperties.Broker broker) {
        MqttConnectOptions options = new MqttConnectOptions();
        options.setServerURIs(new String[]{broker.getUrl()});
        options.setCleanSession(broker.isCleanSession());
        options.setAutomaticReconnect(broker.isAutomaticReconnect());
        options.setConnectionTimeout(broker.getConnectionTimeoutSeconds());
        options.setKeepAliveInterval(broker.getKeepAliveSeconds());
        if (broker.getUsername() != null && !broker.getUsername().isBlank()) {
            options.setUserName(broker.getUsername());
        }
        if (broker.getPassword() != null && !broker.getPassword().isBlank()) {
            options.setPassword(broker.getPassword().toCharArray());
        }

        DefaultMqttPahoClientFactory factory = new DefaultMqttPahoClientFactory();
        factory.setConnectionOptions(options);
        return factory;
    }

    private int[] toQosArray(List<Integer> qos) {
        return qos.stream().mapToInt(Integer::intValue).toArray();
    }

    private int firstQos(List<Integer> qos) {
        return qos.isEmpty() ? 1 : qos.getFirst();
    }
}
