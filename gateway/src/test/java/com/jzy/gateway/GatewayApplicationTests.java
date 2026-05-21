package com.jzy.gateway;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;

@SpringBootTest(properties = "gateway.mqtt.enabled=false")
class GatewayApplicationTests {

    @Test
    void contextLoads() {

    }

}
