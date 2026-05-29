<script setup>
import * as echarts from 'echarts'
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { fetchKnowledgeGraph } from '@/services/knowledgeApi'

const props = defineProps({
  open: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['close'])

const chartRef = ref(null)
const loading = ref(false)
const error = ref('')
const graph = ref({ nodes: [], links: [], categories: [] })
let chart = null
const CATEGORY_COLORS = ['#dc2626', '#2563eb', '#0f766e', '#7c3aed', '#d97706', '#64748b', '#16a34a', '#9333ea', '#0891b2', '#f59e0b']
const IMPORTANT_LABEL_LIMIT = 42

watch(() => props.open, async (open) => {
  if (!open) return
  await loadGraph()
  await nextTick()
  renderChart()
  scheduleChartResize()
}, { immediate: true })

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart)
  chart?.dispose()
})

window.addEventListener('resize', resizeChart)

async function loadGraph() {
  loading.value = true
  error.value = ''
  try {
    graph.value = await fetchKnowledgeGraph(160)
    error.value = graph.value.error || ''
  } catch (err) {
    error.value = err.message || '知识图谱加载失败'
    graph.value = { nodes: [], links: [], categories: [] }
  } finally {
    loading.value = false
    await nextTick()
    scheduleChartResize()
  }
}

function renderChart() {
  if (!props.open || !chartRef.value) return
  if (!chart) {
    chart = echarts.init(chartRef.value)
  }

  const categories = graph.value.categories?.length
    ? graph.value.categories
    : ['Alarm', 'Device', 'Parameter', 'System', 'Area', 'Cause', 'Action', 'ResetCondition', 'TableRow', 'Image'].map((name) => ({ name }))

  const { chartNodes, chartLinks } = buildChartModel(graph.value.nodes || [], graph.value.links || [])

  chart.setOption({
    animation: true,
    animationDuration: 720,
    animationDurationUpdate: 880,
    animationEasing: 'cubicOut',
    animationEasingUpdate: 'quarticOut',
    color: CATEGORY_COLORS,
    backgroundColor: 'rgba(255, 255, 255, 0)',
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(15, 23, 42, 0.92)',
      borderWidth: 0,
      padding: [9, 11],
      textStyle: { color: '#ffffff', fontSize: 12, lineHeight: 18 },
      formatter: (params) => {
        if (params.dataType === 'edge') {
          return `${params.data.label}<br/>${params.data.source}<br/>${params.data.target}`
        }
        return `${params.data.category}<br/>${params.data.name}<br/>连接：${params.data.degree || 0}`
      },
    },
    legend: {
      type: 'scroll',
      top: 8,
      left: 8,
      right: 8,
      itemWidth: 9,
      itemHeight: 9,
      textStyle: { color: '#64748b', fontSize: 12 },
      data: categories.map((item) => item.name),
    },
    series: [{
      type: 'graph',
      layout: 'force',
      top: 48,
      bottom: 20,
      left: 28,
      right: 28,
      roam: true,
      draggable: true,
      scaleLimit: {
        min: 0.28,
        max: 3.2,
      },
      categories,
      data: chartNodes,
      links: chartLinks,
      label: {
        show: false,
        position: 'right',
        color: '#172033',
        fontSize: 12,
        fontWeight: 700,
        overflow: 'truncate',
        width: 150,
      },
      edgeLabel: {
        show: false,
      },
      edgeSymbol: ['none', 'arrow'],
      edgeSymbolSize: 6,
      lineStyle: {
        color: 'source',
        curveness: 0.12,
        opacity: 0.34,
        width: 1,
      },
      emphasis: {
        focus: 'adjacency',
        scale: true,
        label: {
          show: true,
          color: '#0f172a',
          fontSize: 13,
          fontWeight: 850,
          width: 190,
          overflow: 'truncate',
        },
        lineStyle: { width: 2.2, opacity: 0.86 },
      },
      force: {
        initLayout: 'circular',
        repulsion: [220, 520],
        edgeLength: [82, 190],
        gravity: 0.045,
        friction: 0.72,
        layoutAnimation: true,
      },
    }],
  }, true)
  resizeChart()
}

function buildChartModel(nodes, links) {
  const degreeMap = new Map()
  links.forEach((link) => {
    degreeMap.set(link.source, (degreeMap.get(link.source) || 0) + 1)
    degreeMap.set(link.target, (degreeMap.get(link.target) || 0) + 1)
  })

  const importantNodeIds = new Set(
    [...degreeMap.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, IMPORTANT_LABEL_LIMIT)
      .map(([id]) => id),
  )

  // 常驻标签只保留核心节点，避免大图谱标签互相遮挡；悬停时仍会显示邻接节点。
  const chartNodes = nodes.map((node) => {
    const degree = degreeMap.get(node.id) || 0
    const baseSize = node.symbolSize || 28
    const softDegreeBoost = Math.min(18, Math.sqrt(degree) * 4)
    const isImportant = importantNodeIds.has(node.id) || ['Alarm', 'Device'].includes(node.category)

    return {
      ...node,
      degree,
      symbolSize: Math.round(baseSize * 1.08 + softDegreeBoost),
      itemStyle: {
        borderColor: '#ffffff',
        borderWidth: 2,
        shadowBlur: isImportant ? 12 : 5,
        shadowColor: 'rgba(15, 23, 42, 0.14)',
      },
      label: {
        show: isImportant,
        color: '#172033',
        fontSize: isImportant ? 12 : 11,
        fontWeight: isImportant ? 800 : 650,
        width: isImportant ? 170 : 120,
        overflow: 'truncate',
      },
    }
  })

  const chartLinks = links.map((link) => ({
    ...link,
    lineStyle: {
      opacity: 0.28,
      curveness: 0.08 + ((degreeMap.get(link.source) || 0) % 4) * 0.025,
    },
  }))

  return { chartNodes, chartLinks }
}

function resizeChart() {
  chart?.resize()
}

function scheduleChartResize() {
  window.setTimeout(resizeChart, 80)
  window.setTimeout(resizeChart, 280)
}
</script>

<template>
  <aside class="graph-drawer" :class="{ open }" aria-label="知识图谱抽屉">
    <div class="drawer-shell">
      <header class="drawer-header">
        <div>
          <span>Neo4j GraphRAG</span>
          <strong>知识图谱</strong>
        </div>
        <div class="drawer-actions">
          <button class="icon-button focus-ring" type="button" title="刷新" :disabled="loading" @click="loadGraph">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M17.65 6.35A7.95 7.95 0 0 0 12 4a8 8 0 1 0 7.45 10.92.75.75 0 0 0-1.4-.54A6.5 6.5 0 1 1 12 5.5c1.8 0 3.43.73 4.61 1.91L14.5 9.5H20V4l-2.35 2.35Z" />
            </svg>
          </button>
          <button class="icon-button focus-ring" type="button" title="关闭" @click="emit('close')">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6.47 5.47a.75.75 0 0 1 1.06 0L12 9.94l4.47-4.47a.75.75 0 1 1 1.06 1.06L13.06 11l4.47 4.47a.75.75 0 1 1-1.06 1.06L12 12.06l-4.47 4.47a.75.75 0 0 1-1.06-1.06L10.94 11 6.47 6.53a.75.75 0 0 1 0-1.06Z" />
            </svg>
          </button>
        </div>
      </header>

      <div class="drawer-stats">
        <div>
          <span>节点</span>
          <strong>{{ graph.nodes?.length || 0 }}</strong>
        </div>
        <div>
          <span>关系</span>
          <strong>{{ graph.links?.length || 0 }}</strong>
        </div>
      </div>

      <div v-if="error" class="drawer-notice">{{ error }}</div>

      <div class="drawer-chart-wrap">
        <div v-if="loading" class="drawer-empty">
          <span class="loading loading-spinner loading-sm"></span>
          加载中
        </div>
        <div v-else-if="!(graph.nodes?.length)" class="drawer-empty">暂无图谱数据</div>
        <div ref="chartRef" class="drawer-chart" :class="{ hidden: loading || !(graph.nodes?.length) }"></div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.graph-drawer {
  position: relative;
  z-index: 2;
  min-width: 0;
  height: 100vh;
  overflow: hidden;
  background: rgba(248, 250, 252, 0.82);
  border-left: 1px solid rgba(226, 232, 240, 0.94);
  backdrop-filter: blur(18px);
  transform: translateX(14px);
  opacity: 0;
  pointer-events: none;
  transition:
    opacity 220ms var(--agent-ease),
    transform 220ms var(--agent-ease);
}

.graph-drawer.open {
  transform: translateX(0);
  opacity: 1;
  pointer-events: auto;
}

.drawer-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 12px;
  padding: 16px;
}

.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.drawer-header span,
.drawer-header strong {
  display: block;
}

.drawer-header span,
.drawer-stats span {
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.drawer-header strong {
  color: #0f172a;
  font-size: 18px;
  font-weight: 900;
}

.drawer-actions {
  display: flex;
  gap: 8px;
}

.icon-button {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  color: #334155;
  background: #ffffff;
  border: 1px solid rgba(226, 232, 240, 0.98);
  border-radius: 10px;
}

.icon-button:hover {
  color: #0f766e;
  border-color: rgba(20, 184, 166, 0.42);
}

.icon-button svg {
  width: 18px;
  height: 18px;
  fill: currentColor;
}

.drawer-stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.drawer-stats div {
  padding: 10px 12px;
  background: #ffffff;
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 10px;
}

.drawer-stats strong {
  display: block;
  margin-top: 2px;
  color: #0f172a;
  font-size: 22px;
  font-weight: 900;
}

.drawer-notice {
  padding: 9px 11px;
  color: #92400e;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 10px;
  font-size: 12px;
  line-height: 1.5;
}

.drawer-chart-wrap {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
  background:
    radial-gradient(circle at 18% 20%, rgba(20, 184, 166, 0.08), transparent 26%),
    radial-gradient(circle at 82% 8%, rgba(37, 99, 235, 0.07), transparent 24%),
    rgba(255, 255, 255, 0.86);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 12px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.86);
}

.drawer-chart {
  width: 100%;
  height: 100%;
  min-height: 620px;
  cursor: grab;
  pointer-events: auto;
}

.drawer-chart:active {
  cursor: grabbing;
}

.drawer-chart.hidden {
  visibility: hidden;
}

.drawer-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #64748b;
  font-size: 13px;
}
</style>
