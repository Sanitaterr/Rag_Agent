package com.jzy.gateway.pojo.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * Telemetry record persisted by the gateway. The fields match gateway_telemetry_record.
 */
@Data
@TableName("gateway_telemetry_record")
public class GatewayTelemetryRecord {

    @TableId(type = IdType.AUTO)
    private Long id;

    private String sourceBroker;

    private String sourceTopic;

    private String targetTopic;

    private String deviceId;

    private String pointCode;

    private BigDecimal pointValue;

    private String unit;

    private String quality;

    private String sourceProtocol;

    private LocalDateTime sampledAt;

    private LocalDateTime collectedAt;

    private String rawPayload;

    private String normalizedPayload;
}
