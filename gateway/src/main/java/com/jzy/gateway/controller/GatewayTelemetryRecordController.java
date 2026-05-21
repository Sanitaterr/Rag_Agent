package com.jzy.gateway.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.jzy.gateway.pojo.entity.GatewayTelemetryRecord;
import com.jzy.gateway.service.GatewayTelemetryRecordService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * Telemetry query API for frontend troubleshooting.
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/gateway/telemetry")
public class GatewayTelemetryRecordController {

    private final GatewayTelemetryRecordService gatewayTelemetryRecordService;

    @GetMapping("/recent")
    public List<GatewayTelemetryRecord> recent(@RequestParam(defaultValue = "20") long limit) {
        long safeLimit = Math.max(1, Math.min(limit, 100));
        Page<GatewayTelemetryRecord> page = Page.of(1, safeLimit);
        LambdaQueryWrapper<GatewayTelemetryRecord> wrapper = new LambdaQueryWrapper<GatewayTelemetryRecord>()
                .orderByDesc(GatewayTelemetryRecord::getCollectedAt);
        return gatewayTelemetryRecordService.page(page, wrapper).getRecords();
    }
}
