package com.jzy.gateway.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.jzy.gateway.mapper.GatewayTelemetryRecordMapper;
import com.jzy.gateway.pojo.entity.GatewayTelemetryRecord;
import com.jzy.gateway.service.GatewayTelemetryRecordService;
import org.springframework.stereotype.Service;

/**
 * Default MyBatis Plus service implementation.
 */
@Service
public class GatewayTelemetryRecordServiceImpl
        extends ServiceImpl<GatewayTelemetryRecordMapper, GatewayTelemetryRecord>
        implements GatewayTelemetryRecordService {
}
