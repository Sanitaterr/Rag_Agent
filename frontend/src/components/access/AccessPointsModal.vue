<script setup>
import * as echarts from 'echarts'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchAccessPointDevice, fetchAccessPointDevices } from '@/services/accessPointApi'

const props = defineProps({
  open: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['close'])

const devices = ref([])
const stats = ref({ devices: 0, online: 0, warning: 0, offline: 0, points: 0, warning_points: 0 })
const selectedDeviceId = ref('')
const detail = ref(null)
const loading = ref(false)
const detailLoading = ref(false)
const error = ref('')
const statusFilter = ref('')
const keyword = ref('')
const chartRef = ref(null)
let chart = null

const selectedDevice = computed(() => detail.value?.device || devices.value.find((item) => item.device_id === selectedDeviceId.value))
const detailPoints = computed(() => detail.value?.points || selectedDevice.value?.latest_points || [])

watch(() => props.open, async (open) => {
  if (!open) return
  await loadDevices()
  await nextTick()
  renderChart()
}, { immediate: true })

watch(stats, async () => {
  await nextTick()
  renderChart()
}, { deep: true })

onMounted(() => {
  window.addEventListener('resize', resizeChart)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart)
  chart?.dispose()
})

async function loadDevices() {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchAccessPointDevices({
      status: statusFilter.value,
      keyword: keyword.value.trim(),
    })
    devices.value = data.devices || []
    stats.value = data.stats || { devices: 0, online: 0, warning: 0, offline: 0, points: 0, warning_points: 0 }

    if (!devices.value.length) {
      selectedDeviceId.value = ''
      detail.value = null
      return
    }

    const stillExists = devices.value.some((device) => device.device_id === selectedDeviceId.value)
    await selectDevice(stillExists ? selectedDeviceId.value : devices.value[0].device_id)
  } catch (err) {
    error.value = err.message || '加载接入点失败'
  } finally {
    loading.value = false
  }
}

async function selectDevice(deviceId) {
  if (!deviceId) return
  selectedDeviceId.value = deviceId
  detailLoading.value = true
  error.value = ''
  try {
    detail.value = await fetchAccessPointDevice(deviceId)
  } catch (err) {
    detail.value = null
    error.value = err.message || '加载设备详情失败'
  } finally {
    detailLoading.value = false
  }
}

function clearFilters() {
  statusFilter.value = ''
  keyword.value = ''
  loadDevices()
}

function renderChart() {
  if (!props.open || !chartRef.value) return

  if (!chart) {
    chart = echarts.init(chartRef.value)
  }

  chart.setOption({
    animation: true,
    animationDuration: 700,
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(15, 23, 42, 0.9)',
      borderWidth: 0,
      textStyle: { color: '#ffffff' },
    },
    legend: {
      bottom: 0,
      icon: 'circle',
      textStyle: { color: '#64748b', fontWeight: 700 },
    },
    series: [{
      type: 'pie',
      radius: ['52%', '74%'],
      center: ['50%', '44%'],
      avoidLabelOverlap: true,
      label: { color: '#334155', fontWeight: 800 },
      itemStyle: { borderColor: '#ffffff', borderWidth: 3 },
      data: [
        { name: '在线', value: stats.value.online || 0, itemStyle: { color: '#14b8a6' } },
        { name: '异常', value: stats.value.warning || 0, itemStyle: { color: '#f59e0b' } },
        { name: '离线', value: stats.value.offline || 0, itemStyle: { color: '#94a3b8' } },
      ],
    }],
  })
  resizeChart()
}

function resizeChart() {
  chart?.resize()
}

function statusLabel(status) {
  return {
    online: '在线',
    warning: '异常',
    offline: '离线',
  }[status] || '未知'
}

function formatDate(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return '-'
  const numberValue = Number(value)
  return Number.isFinite(numberValue) ? Number(numberValue.toFixed(6)).toString() : String(value)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="ap-fade">
      <div v-if="open" class="ap-overlay" @click.self="emit('close')">
        <Transition name="ap-panel" appear>
          <section class="ap-modal ui-panel">
            <header class="ap-header">
              <div>
                <p class="ap-eyebrow">Gateway Telemetry</p>
                <h2>接入点</h2>
              </div>
              <button class="btn btn-sm btn-circle btn-ghost focus-ring" type="button" aria-label="关闭" @click="emit('close')">×</button>
            </header>

            <Transition name="notice">
              <div v-if="error" class="alert alert-error mb-4 py-2 text-sm">
                <span>{{ error }}</span>
              </div>
            </Transition>

            <div class="ap-stats">
              <div class="stat-card smooth-surface">
                <span>设备</span>
                <strong>{{ stats.devices || 0 }}</strong>
              </div>
              <div class="stat-card online smooth-surface">
                <span>在线</span>
                <strong>{{ stats.online || 0 }}</strong>
              </div>
              <div class="stat-card offline smooth-surface">
                <span>离线</span>
                <strong>{{ stats.offline || 0 }}</strong>
              </div>
              <div class="stat-card warning smooth-surface">
                <span>异常点位</span>
                <strong>{{ stats.warning_points || 0 }}</strong>
              </div>
              <div ref="chartRef" class="ap-chart"></div>
            </div>

            <div class="ap-toolbar">
              <div class="filter-tabs">
                <button class="focus-ring" type="button" :class="{ active: statusFilter === '' }" @click="statusFilter = ''; loadDevices()">全部</button>
                <button class="focus-ring" type="button" :class="{ active: statusFilter === 'online' }" @click="statusFilter = 'online'; loadDevices()">在线</button>
                <button class="focus-ring" type="button" :class="{ active: statusFilter === 'warning' }" @click="statusFilter = 'warning'; loadDevices()">异常</button>
                <button class="focus-ring" type="button" :class="{ active: statusFilter === 'offline' }" @click="statusFilter = 'offline'; loadDevices()">离线</button>
              </div>
              <form class="search-box" @submit.prevent="loadDevices">
                <input v-model="keyword" class="input input-sm input-bordered focus-ring" type="search" placeholder="搜索设备或点位" />
                <button class="btn btn-sm btn-neutral smooth-pop" type="submit">搜索</button>
                <button class="btn btn-sm btn-ghost smooth-pop" type="button" @click="clearFilters">清空</button>
                <button class="btn btn-sm btn-ghost smooth-pop" type="button" :disabled="loading" @click="loadDevices">刷新</button>
              </form>
            </div>

            <div class="ap-body">
              <aside class="device-panel">
                <div v-if="loading && !devices.length" class="ap-empty">
                  <span class="loading loading-spinner loading-sm"></span>
                  正在加载设备
                </div>

                <TransitionGroup v-else name="device-row" tag="div" class="device-list">
                  <button
                    v-for="device in devices"
                    :key="device.device_id"
                    class="device-row smooth-surface focus-ring"
                    :class="{ active: device.device_id === selectedDeviceId }"
                    type="button"
                    @click="selectDevice(device.device_id)"
                  >
                    <span class="device-status" :class="device.status"></span>
                    <span class="device-main">
                      <strong>{{ device.name }}</strong>
                      <small>{{ device.source_protocol }} · {{ device.point_count }} 点位</small>
                    </span>
                    <span class="device-meta">
                      <b :class="device.status">{{ statusLabel(device.status) }}</b>
                      <small>{{ formatDate(device.last_seen_at) }}</small>
                    </span>
                  </button>
                </TransitionGroup>

                <div v-if="!loading && !devices.length" class="ap-empty">暂无接入设备</div>
              </aside>

              <main class="detail-panel">
                <Transition name="detail" mode="out-in">
                  <div v-if="detailLoading" key="loading" class="ap-empty">
                    <span class="loading loading-spinner loading-sm"></span>
                    正在加载设备详情
                  </div>

                  <section v-else-if="selectedDevice" key="detail" class="device-detail">
                    <header class="detail-head">
                      <div>
                        <p>{{ selectedDevice.source_protocol }}</p>
                        <h3>{{ selectedDevice.name }}</h3>
                      </div>
                      <span class="status-badge" :class="selectedDevice.status">
                        {{ statusLabel(selectedDevice.status) }}
                      </span>
                    </header>

                    <div class="detail-metrics">
                      <div>
                        <span>点位数</span>
                        <strong>{{ selectedDevice.point_count }}</strong>
                      </div>
                      <div>
                        <span>异常点位</span>
                        <strong>{{ selectedDevice.warning_point_count }}</strong>
                      </div>
                      <div>
                        <span>最近采集</span>
                        <strong>{{ formatDate(selectedDevice.last_seen_at) }}</strong>
                      </div>
                    </div>

                    <div class="point-table-wrap">
                      <table class="point-table">
                        <thead>
                          <tr>
                            <th>点位编码</th>
                            <th>值</th>
                            <th>单位</th>
                            <th>质量</th>
                            <th>采样时间</th>
                            <th>采集时间</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="point in detailPoints" :key="point.point_code">
                            <td>
                              <strong>{{ point.point_code }}</strong>
                            </td>
                            <td>{{ formatValue(point.point_value) }}</td>
                            <td>{{ point.unit || '-' }}</td>
                            <td>
                              <span class="quality-pill" :class="{ good: point.quality === 'GOOD' }">{{ point.quality }}</span>
                            </td>
                            <td>{{ formatDate(point.sampled_at) }}</td>
                            <td>{{ formatDate(point.collected_at) }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>

                    <div v-if="!detailPoints.length" class="ap-empty compact">暂无点位数据</div>
                  </section>

                  <div v-else key="empty" class="ap-empty">选择左侧设备查看点位</div>
                </Transition>
              </main>
            </div>
          </section>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.ap-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.34);
  backdrop-filter: blur(10px);
}

.ap-modal {
  display: grid;
  width: min(1320px, calc(100vw - 48px));
  height: min(820px, calc(100vh - 48px));
  grid-template-rows: auto auto auto minmax(0, 1fr);
  padding: 22px;
  overflow: hidden;
  border-radius: 18px;
  box-shadow: var(--agent-shadow-strong);
}

.ap-header,
.ap-toolbar,
.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}

.ap-eyebrow {
  margin: 0 0 4px;
  color: #0f766e;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.ap-header h2,
.detail-head h3 {
  margin: 0;
  color: #0f172a;
}

.ap-header h2 {
  font-size: 24px;
  font-weight: 900;
}

.ap-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(96px, 132px)) minmax(240px, 1fr);
  gap: 12px;
  margin: 18px 0;
}

.stat-card {
  padding: 14px;
  background: rgba(248, 250, 252, 0.86);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 12px;
}

.stat-card:hover {
  border-color: rgba(20, 184, 166, 0.34);
  box-shadow: 0 12px 30px rgba(20, 184, 166, 0.10);
  transform: translateY(-1px);
}

.stat-card span,
.device-main small,
.device-meta small,
.detail-head p,
.detail-metrics span {
  color: #64748b;
  font-size: 12px;
}

.stat-card strong {
  display: block;
  margin-top: 4px;
  color: #0f172a;
  font-size: 26px;
  font-weight: 900;
}

.stat-card.online strong {
  color: #0f766e;
}

.stat-card.warning strong {
  color: #d97706;
}

.stat-card.offline strong {
  color: #64748b;
}

.ap-chart {
  min-height: 116px;
  background: rgba(255, 255, 255, 0.76);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 12px;
}

.ap-toolbar {
  margin-bottom: 14px;
}

.filter-tabs {
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  background: rgba(241, 245, 249, 0.86);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 12px;
}

.filter-tabs button {
  height: 30px;
  padding: 0 12px;
  color: #475569;
  background: transparent;
  border: 0;
  border-radius: 9px;
  font-size: 12px;
  font-weight: 850;
}

.filter-tabs button.active {
  color: #0f766e;
  background: #ffffff;
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.08);
}

.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
}

.search-box input {
  width: 220px;
}

.ap-body {
  display: grid;
  min-height: 0;
  grid-template-columns: minmax(360px, 0.42fr) minmax(0, 1fr);
  gap: 18px;
}

.device-panel,
.detail-panel {
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 14px;
}

.device-panel {
  padding: 12px;
}

.detail-panel {
  padding: 18px;
}

.device-list {
  display: grid;
  gap: 8px;
}

.device-row {
  display: flex;
  width: 100%;
  min-width: 0;
  align-items: center;
  gap: 10px;
  padding: 12px;
  text-align: left;
  background: rgba(248, 250, 252, 0.78);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 12px;
}

.device-row:hover,
.device-row.active {
  background: #ffffff;
  border-color: rgba(20, 184, 166, 0.48);
  box-shadow: 0 14px 34px rgba(20, 184, 166, 0.13);
  transform: translateY(-1px);
}

.device-status {
  width: 10px;
  height: 10px;
  flex: 0 0 10px;
  background: #94a3b8;
  border-radius: 999px;
}

.device-status.online {
  background: #14b8a6;
  box-shadow: 0 0 0 5px rgba(20, 184, 166, 0.12);
}

.device-status.warning {
  background: #f59e0b;
  box-shadow: 0 0 0 5px rgba(245, 158, 11, 0.14);
}

.device-main {
  min-width: 0;
}

.device-main strong,
.device-main small {
  display: block;
}

.device-main strong {
  overflow: hidden;
  color: #172033;
  font-size: 14px;
  font-weight: 900;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.device-meta {
  display: grid;
  min-width: 132px;
  justify-items: end;
  margin-left: auto;
}

.device-meta b,
.status-badge {
  font-size: 12px;
  font-weight: 900;
}

.device-meta b.online,
.status-badge.online {
  color: #0f766e;
}

.device-meta b.warning,
.status-badge.warning {
  color: #b45309;
}

.device-meta b.offline,
.status-badge.offline {
  color: #64748b;
}

.detail-head p {
  margin: 0 0 4px;
  font-weight: 850;
  text-transform: uppercase;
}

.status-badge {
  padding: 7px 10px;
  background: rgba(248, 250, 252, 0.86);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 999px;
}

.detail-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 16px 0;
}

.detail-metrics div {
  min-width: 0;
  padding: 13px;
  background: rgba(248, 250, 252, 0.74);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 12px;
}

.detail-metrics strong {
  display: block;
  overflow: hidden;
  margin-top: 4px;
  color: #0f172a;
  font-size: 16px;
  font-weight: 900;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.point-table-wrap {
  overflow: auto;
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 12px;
}

.point-table {
  width: 100%;
  min-width: 780px;
  border-collapse: collapse;
  background: rgba(255, 255, 255, 0.78);
}

.point-table th,
.point-table td {
  padding: 12px 13px;
  color: #334155;
  border-bottom: 1px solid rgba(226, 232, 240, 0.82);
  font-size: 13px;
  text-align: left;
}

.point-table th {
  color: #64748b;
  background: rgba(248, 250, 252, 0.96);
  font-size: 12px;
  font-weight: 900;
}

.point-table tbody tr:last-child td {
  border-bottom: 0;
}

.quality-pill {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 8px;
  color: #b45309;
  background: rgba(245, 158, 11, 0.12);
  border-radius: 999px;
  font-size: 12px;
  font-weight: 900;
}

.quality-pill.good {
  color: #0f766e;
  background: rgba(20, 184, 166, 0.12);
}

.ap-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 20px;
  color: #64748b;
  font-size: 14px;
}

.ap-empty.compact {
  padding: 12px 0 0;
  font-size: 12px;
}

.ap-fade-enter-active,
.ap-fade-leave-active {
  transition: opacity 200ms var(--agent-ease);
}

.ap-fade-enter-from,
.ap-fade-leave-to {
  opacity: 0;
}

.ap-panel-enter-active,
.ap-panel-leave-active,
.detail-enter-active,
.detail-leave-active,
.notice-enter-active,
.notice-leave-active,
.device-row-enter-active,
.device-row-leave-active {
  transition:
    opacity 240ms var(--agent-ease),
    transform 240ms var(--agent-ease);
}

.ap-panel-enter-from,
.ap-panel-leave-to {
  opacity: 0;
  transform: translateY(18px) scale(0.985);
}

.detail-enter-from,
.detail-leave-to,
.notice-enter-from,
.notice-leave-to,
.device-row-enter-from,
.device-row-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

.device-row-move {
  transition: transform 240ms var(--agent-ease);
}

@media (max-width: 1100px) {
  .ap-stats,
  .ap-body {
    grid-template-columns: 1fr;
  }

  .ap-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .search-box {
    flex-wrap: wrap;
  }

  .search-box input {
    width: min(100%, 320px);
  }
}

@media (max-width: 900px) {
  .ap-overlay {
    padding: 14px;
  }

  .ap-modal {
    width: calc(100vw - 28px);
    height: calc(100vh - 28px);
  }

  .detail-metrics {
    grid-template-columns: 1fr;
  }

  .device-row {
    align-items: flex-start;
  }

  .device-meta {
    min-width: 96px;
  }
}
</style>
