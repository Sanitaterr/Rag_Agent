<script setup>
import * as echarts from 'echarts'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  deleteKnowledgeFile,
  fetchKnowledgeFiles,
  fetchKnowledgePreview,
  fetchVectorizeJob,
  initializeKnowledgeGraph,
  startGraphIndexFile,
  startVectorizeFile,
  uploadKnowledgeFile,
} from '@/services/knowledgeApi'

const props = defineProps({
  open: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['close'])

const files = ref([])
const stats = ref({
  files: 0,
  chunks: 0,
  images: 0,
  image_chunks: 0,
  table_chunks: 0,
  safety_chunks: 0,
  fault_chunks: 0,
  size: 0,
  vectorized_files: 0,
  pending_files: 0,
  graph_enabled: false,
  graph_indexed_files: 0,
  graph_nodes: 0,
  graph_relationships: 0,
  graph_error: '',
})
const selectedFileId = ref('')
const preview = ref(null)
const loading = ref(false)
const previewLoading = ref(false)
const uploading = ref(false)
const graphLoading = ref(false)
const error = ref('')
const vectorJobs = ref({})
const fileInputRef = ref(null)
const chartRef = ref(null)
let chart = null
const pollers = new Map()

const selectedFile = computed(() => files.value.find((file) => file.id === selectedFileId.value))
const pendingFiles = computed(() => files.value.filter((file) => !file.vectorized))
const vectorizedFiles = computed(() => files.value.filter((file) => file.vectorized))

watch(() => props.open, async (open) => {
  if (!open) return
  await loadFiles()
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
  pollers.forEach((timer) => clearInterval(timer))
  pollers.clear()
  chart?.dispose()
})

async function loadFiles() {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchKnowledgeFiles()
    files.value = data.files || []
    stats.value = data.stats || defaultStats()
    if (selectedFileId.value && !files.value.some((file) => file.id === selectedFileId.value)) {
      selectedFileId.value = ''
      preview.value = null
    }
  } catch (err) {
    error.value = err.message || '加载知识库失败'
  } finally {
    loading.value = false
  }
}

async function openPreview(fileId) {
  selectedFileId.value = fileId
  previewLoading.value = true
  error.value = ''
  try {
    preview.value = await fetchKnowledgePreview(fileId)
  } catch (err) {
    error.value = err.message || '加载预览失败'
  } finally {
    previewLoading.value = false
  }
}

async function handleUpload(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return

  uploading.value = true
  error.value = ''
  try {
    const data = await uploadKnowledgeFile(file)
    await loadFiles()
    selectedFileId.value = data.file?.id || ''
    preview.value = null
  } catch (err) {
    error.value = err.message || '上传失败'
  } finally {
    uploading.value = false
  }
}

async function removeFile(file) {
  if (!file || !confirm(`确定删除 ${file.name}？`)) return

  loading.value = true
  error.value = ''
  try {
    const data = await deleteKnowledgeFile(file.id)
    files.value = data.files || []
    stats.value = data.stats || defaultStats()
    if (selectedFileId.value === file.id) {
      selectedFileId.value = ''
      preview.value = null
    }
  } catch (err) {
    error.value = err.message || '删除失败'
  } finally {
    loading.value = false
  }
}

async function vectorizeFile(file) {
  if (!file || activeJob(file.id)) return

  error.value = ''
  try {
    const data = await startVectorizeFile(file.id)
    setJob(data.job)
    pollVectorJob(data.job.id)
  } catch (err) {
    error.value = err.message || '向量化失败'
  }
}

async function initializeGraph() {
  if (graphLoading.value) return

  graphLoading.value = true
  error.value = ''
  try {
    const data = await initializeKnowledgeGraph()
    stats.value = {
      ...stats.value,
      ...(data || {}),
    }
    await loadFiles()
  } catch (err) {
    error.value = err.message || 'Neo4j GraphRAG 初始化失败'
  } finally {
    graphLoading.value = false
  }
}

async function graphIndexSelectedFile() {
  if (!selectedFile.value || graphLoading.value) return

  graphLoading.value = true
  error.value = ''
  try {
    const data = await startGraphIndexFile(selectedFile.value.id)
    stats.value = {
      ...stats.value,
      ...(data.stats || {}),
    }
  } catch (err) {
    error.value = err.message || 'Neo4j GraphRAG 索引失败'
  } finally {
    graphLoading.value = false
  }
}

function activeJob(fileId) {
  return Object.values(vectorJobs.value).find((job) => (
    job.file_id === fileId && ['queued', 'running'].includes(job.status)
  ))
}

function setJob(job) {
  vectorJobs.value = {
    ...vectorJobs.value,
    [job.id]: job,
  }
}

function pollVectorJob(jobId) {
  if (pollers.has(jobId)) return

  const timer = window.setInterval(async () => {
    try {
      const data = await fetchVectorizeJob(jobId)
      const job = data.job
      setJob(job)
      if (['completed', 'failed'].includes(job.status)) {
        clearInterval(timer)
        pollers.delete(jobId)
        if (job.status === 'completed') {
          await loadFiles()
        } else {
          error.value = job.error || '向量化失败'
        }
      }
    } catch (err) {
      clearInterval(timer)
      pollers.delete(jobId)
      error.value = err.message || '获取向量化进度失败'
    }
  }, 1000)

  pollers.set(jobId, timer)
}

function renderChart() {
  if (!props.open || !chartRef.value) return

  if (!chart) {
    chart = echarts.init(chartRef.value)
  }

  chart.setOption({
    animation: true,
    animationDuration: 720,
    animationEasing: 'cubicOut',
    grid: { top: 12, right: 12, bottom: 28, left: 34 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(15, 23, 42, 0.88)',
      borderWidth: 0,
      textStyle: { color: '#ffffff' },
    },
    xAxis: {
      type: 'category',
      data: ['全部文件', '已向量化', '待向量化'],
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#dbe7ef' } },
      axisLabel: { color: '#64748b', fontWeight: 700 },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#94a3b8' },
      splitLine: { lineStyle: { color: '#eef4f8' } },
    },
    series: [{
      type: 'bar',
      data: [
        stats.value.files || 0,
        stats.value.vectorized_files || 0,
        stats.value.pending_files || 0,
      ],
      barWidth: 30,
      showBackground: true,
      backgroundStyle: { color: 'rgba(226, 232, 240, 0.48)', borderRadius: [8, 8, 0, 0] },
      itemStyle: {
        borderRadius: [8, 8, 0, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#2563eb' },
          { offset: 0.52, color: '#14b8a6' },
          { offset: 1, color: '#0f766e' },
        ]),
      },
    }],
  })
  resizeChart()
}

function resizeChart() {
  chart?.resize()
}

function formatSize(bytes) {
  if (!bytes) return '0 B'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function formatDate(timestamp) {
  if (!timestamp) return '-'
  return new Date(timestamp * 1000).toLocaleString()
}

function defaultStats() {
  return {
    files: 0,
    chunks: 0,
    images: 0,
    image_chunks: 0,
    table_chunks: 0,
    safety_chunks: 0,
    fault_chunks: 0,
    size: 0,
    vectorized_files: 0,
    pending_files: 0,
    graph_enabled: false,
    graph_indexed_files: 0,
    graph_nodes: 0,
    graph_relationships: 0,
    graph_error: '',
  }
}

function imageUrlFor(chunk) {
  const imageId = chunk.image_id || chunk.images?.[0]
  const image = preview.value?.images?.find((item) => item.id === imageId)
  return image?.url || ''
}

function chunkLabel(chunk) {
  return chunk.chunk_type || chunk.kind
}

function structuredText(chunk) {
  if (!chunk.structured_json) return ''
  try {
    return JSON.stringify(JSON.parse(chunk.structured_json), null, 2)
  } catch {
    return chunk.structured_json
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="kb-fade">
      <div v-if="open" class="kb-overlay" @click.self="emit('close')">
        <Transition name="kb-panel" appear>
          <section class="kb-modal ui-panel">
            <header class="kb-header">
              <div>
                <p class="kb-eyebrow">Local DOCX RAG</p>
                <h2>知识库</h2>
              </div>
              <button class="btn btn-sm btn-circle btn-ghost focus-ring" type="button" aria-label="关闭" @click="emit('close')">×</button>
            </header>

            <Transition name="notice">
              <div v-if="error" class="alert alert-error mb-4 py-2 text-sm">
                <span>{{ error }}</span>
              </div>
            </Transition>

            <div class="kb-stats">
              <div class="stat-card smooth-surface">
                <span>文件</span>
                <strong>{{ stats.files || 0 }}</strong>
              </div>
              <div class="stat-card smooth-surface">
                <span>已向量化</span>
                <strong>{{ stats.vectorized_files || 0 }}</strong>
              </div>
              <div class="stat-card smooth-surface">
                <span>待处理</span>
                <strong>{{ stats.pending_files || 0 }}</strong>
              </div>
              <div class="stat-card smooth-surface">
                <span>Images</span>
                <strong>{{ stats.image_chunks || 0 }}</strong>
              </div>
              <div class="stat-card smooth-surface">
                <span>Tables</span>
                <strong>{{ stats.table_chunks || 0 }}</strong>
              </div>
              <div class="stat-card smooth-surface">
                <span>Safety</span>
                <strong>{{ stats.safety_chunks || 0 }}</strong>
              </div>
              <div class="stat-card smooth-surface">
                <span>Faults</span>
                <strong>{{ stats.fault_chunks || 0 }}</strong>
              </div>
              <div class="stat-card smooth-surface graph-stat">
                <span>Neo4j Nodes</span>
                <strong>{{ stats.graph_nodes || 0 }}</strong>
              </div>
              <div ref="chartRef" class="kb-chart"></div>
            </div>

            <div class="graph-panel smooth-surface" :class="{ warning: stats.graph_error }">
              <div>
                <strong>GraphRAG / Neo4j</strong>
                <span>
                  {{ stats.graph_enabled ? 'enabled' : 'disabled' }}
                  · docs {{ stats.graph_indexed_files || 0 }}
                  · rels {{ stats.graph_relationships || 0 }}
                </span>
                <small v-if="stats.graph_error">{{ stats.graph_error }}</small>
              </div>
              <div class="graph-actions">
                <button class="btn btn-sm btn-primary smooth-pop" type="button" :disabled="graphLoading" @click="initializeGraph">
                  <span v-if="graphLoading" class="loading loading-spinner loading-xs"></span>
                  初始化图谱
                </button>
                <button class="btn btn-sm btn-outline smooth-pop" type="button" :disabled="graphLoading || !selectedFile" @click="graphIndexSelectedFile">
                  重建当前文件图谱
                </button>
              </div>
            </div>

            <div class="kb-body">
              <aside class="kb-files">
                <div class="kb-files-toolbar">
                  <button class="btn btn-sm btn-neutral smooth-pop" type="button" :disabled="uploading" @click="fileInputRef?.click()">
                    <span v-if="uploading" class="loading loading-spinner loading-xs"></span>
                    {{ uploading ? '上传中' : '上传 docx' }}
                  </button>
                  <button class="btn btn-sm btn-ghost smooth-pop" type="button" :disabled="loading" @click="loadFiles">刷新</button>
                  <input ref="fileInputRef" class="hidden" type="file" accept=".docx" @change="handleUpload" />
                </div>

                <div v-if="loading && !files.length" class="kb-empty">
                  <span class="loading loading-spinner loading-sm"></span>
                  正在加载文件
                </div>

                <div v-else class="kb-file-columns">
                  <section class="file-section pending">
                    <header class="file-section-head">
                      <span>待向量化</span>
                      <strong>{{ pendingFiles.length }}</strong>
                    </header>

                    <TransitionGroup name="file-row" tag="div" class="file-list">
                      <article
                        v-for="file in pendingFiles"
                        :key="file.id"
                        class="file-row focus-ring"
                        :class="{ active: file.id === selectedFileId }"
                      >
                        <button class="file-select" type="button" @click="openPreview(file.id)">
                          <span class="file-icon">DOCX</span>
                          <span class="file-main">
                            <strong>{{ file.name }}</strong>
                            <small>{{ formatSize(file.size) }}</small>
                          </span>
                        </button>
                        <div class="file-actions">
                          <button class="btn btn-xs btn-primary smooth-pop" type="button" :disabled="!!activeJob(file.id)" @click="vectorizeFile(file)">
                            <span v-if="activeJob(file.id)" class="loading loading-spinner loading-xs"></span>
                            {{ activeJob(file.id) ? '处理中' : '向量化' }}
                          </button>
                          <div v-if="activeJob(file.id)" class="vector-progress">
                            <progress class="progress progress-primary" :value="activeJob(file.id).progress" max="100"></progress>
                            <small>
                              {{ activeJob(file.id).stage }} · {{ activeJob(file.id).progress }}%
                              <template v-if="activeJob(file.id).total">
                                · {{ activeJob(file.id).processed }}/{{ activeJob(file.id).total }}
                              </template>
                            </small>
                          </div>
                        </div>
                      </article>
                    </TransitionGroup>

                    <div v-if="!pendingFiles.length" class="kb-empty compact">暂无待向量化文件</div>
                  </section>

                  <section class="file-section indexed">
                    <header class="file-section-head">
                      <span>已写入 Chroma</span>
                      <strong>{{ vectorizedFiles.length }}</strong>
                    </header>

                    <TransitionGroup name="file-row" tag="div" class="file-list">
                      <article
                        v-for="file in vectorizedFiles"
                        :key="file.id"
                        class="file-row focus-ring"
                        :class="{ active: file.id === selectedFileId }"
                      >
                        <button class="file-select" type="button" @click="openPreview(file.id)">
                          <span class="file-icon ready">RAG</span>
                          <span class="file-main">
                            <strong>{{ file.name }}</strong>
                            <small>{{ file.chunks }} 块 · {{ formatSize(file.size) }}</small>
                          </span>
                        </button>
                      </article>
                    </TransitionGroup>

                    <div v-if="!vectorizedFiles.length" class="kb-empty compact">暂无 Chroma 文件</div>
                  </section>
                </div>

                <div v-if="!loading && !files.length" class="kb-empty">暂无 docx 文件</div>
              </aside>

              <main class="kb-preview">
                <Transition name="preview" mode="out-in">
                  <div v-if="previewLoading" key="loading" class="kb-empty">
                    <span class="loading loading-spinner loading-sm"></span>
                    正在生成预览
                  </div>

                  <template v-else-if="preview">
                    <div key="preview" class="preview-wrap">
                      <div class="preview-head">
                        <div>
                          <h3>{{ preview.name }}</h3>
                          <p>{{ formatDate(preview.modified_at) }} · {{ formatSize(preview.size) }}</p>
                        </div>
                        <button class="btn btn-sm btn-error btn-outline smooth-pop" type="button" @click="removeFile(selectedFile)">删除</button>
                      </div>

                      <div v-if="preview.images?.length" class="image-strip">
                        <img
                          v-for="image in preview.images.slice(0, 8)"
                          :key="image.id"
                          :src="image.url"
                          :alt="image.filename"
                        />
                      </div>

                      <div class="preview-chunks">
                        <article v-for="chunk in preview.preview" :key="chunk.id" class="chunk-card smooth-surface">
                          <div class="chunk-head">
                            <span>{{ chunkLabel(chunk) }} #{{ chunk.index }}</span>
                            <small v-if="chunk.visual_type">{{ chunk.visual_type }}</small>
                            <small v-if="chunk.risk_level">risk: {{ chunk.risk_level }}</small>
                          </div>
                          <img
                            v-if="imageUrlFor(chunk)"
                            class="chunk-image"
                            :src="imageUrlFor(chunk)"
                            :alt="chunk.image_id || chunk.id"
                          />
                          <dl v-if="chunk.ocr_text || chunk.description || chunk.context_before || chunk.context_after" class="chunk-meta">
                            <template v-if="chunk.context_before">
                              <dt>Context before</dt>
                              <dd>{{ chunk.context_before }}</dd>
                            </template>
                            <template v-if="chunk.ocr_text">
                              <dt>OCR</dt>
                              <dd>{{ chunk.ocr_text }}</dd>
                            </template>
                            <template v-if="chunk.description">
                              <dt>Description</dt>
                              <dd>{{ chunk.description }}</dd>
                            </template>
                            <template v-if="chunk.context_after">
                              <dt>Context after</dt>
                              <dd>{{ chunk.context_after }}</dd>
                            </template>
                          </dl>
                          <p v-if="chunk.parse_error" class="chunk-error">{{ chunk.parse_error }}</p>
                          <pre v-if="structuredText(chunk)" class="chunk-json">{{ structuredText(chunk) }}</pre>
                          <p>{{ chunk.text }}</p>
                        </article>
                      </div>
                    </div>
                  </template>

                  <div v-else key="empty" class="kb-empty">选择左侧文件查看预览</div>
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
.kb-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.34);
  backdrop-filter: blur(10px);
}

.kb-modal {
  display: grid;
  width: min(1320px, calc(100vw - 48px));
  height: min(800px, calc(100vh - 48px));
  grid-template-rows: auto auto auto auto minmax(0, 1fr);
  padding: 22px;
  overflow: hidden;
  border-radius: 18px;
  box-shadow: var(--agent-shadow-strong);
}

.kb-header,
.preview-head,
.kb-files-toolbar,
.file-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.kb-eyebrow {
  margin: 0 0 4px;
  color: #0f766e;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.kb-header h2,
.preview-head h3 {
  margin: 0;
  color: #0f172a;
}

.kb-header h2 {
  font-size: 24px;
  font-weight: 900;
}

.kb-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(104px, 1fr));
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
.preview-head p,
.file-main small,
.chunk-card span,
.chunk-card small,
.vector-progress small {
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

.kb-chart {
  grid-column: span 2;
  min-height: 104px;
  background: rgba(255, 255, 255, 0.76);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 12px;
}

.graph-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
  margin: 0 0 14px;
  padding: 12px 14px;
  background: rgba(248, 250, 252, 0.86);
  border: 1px solid rgba(20, 184, 166, 0.24);
  border-radius: 12px;
}

.graph-panel.warning {
  border-color: rgba(245, 158, 11, 0.42);
}

.graph-panel strong,
.graph-panel span,
.graph-panel small {
  display: block;
}

.graph-panel strong {
  color: #0f172a;
  font-size: 13px;
  font-weight: 900;
}

.graph-panel span,
.graph-panel small {
  color: #64748b;
  font-size: 12px;
}

.graph-panel small {
  color: #b45309;
}

.graph-actions {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.kb-body {
  display: grid;
  min-height: 0;
  grid-template-columns: minmax(580px, 0.95fr) minmax(0, 1fr);
  gap: 18px;
}

.kb-files,
.kb-preview {
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 14px;
}

.kb-files {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 14px;
  padding: 14px;
}

.kb-preview {
  padding: 18px;
}

.kb-file-columns {
  display: grid;
  min-height: 0;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.file-section {
  min-height: 0;
  overflow-y: auto;
  padding: 12px;
  background: rgba(248, 250, 252, 0.68);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 12px;
}

.file-section.pending {
  border-color: rgba(37, 99, 235, 0.18);
}

.file-section.indexed {
  border-color: rgba(20, 184, 166, 0.24);
}

.file-section-head {
  margin-bottom: 10px;
  color: #172033;
  font-size: 13px;
  font-weight: 900;
}

.file-section-head strong {
  min-width: 28px;
  padding: 2px 8px;
  color: #0f766e;
  text-align: center;
  background: rgba(20, 184, 166, 0.12);
  border-radius: 999px;
}

.file-list {
  display: grid;
  gap: 8px;
  min-width: 0;
  padding-right: 2px;
}

.file-row {
  display: grid;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  gap: 10px;
  padding: 11px;
  text-align: left;
  background: rgba(255, 255, 255, 0.78);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 12px;
  transition:
    transform 180ms var(--agent-ease),
    border-color 180ms var(--agent-ease),
    box-shadow 180ms var(--agent-ease),
    background-color 180ms var(--agent-ease);
}

.file-row:hover,
.file-row.active {
  background: #ffffff;
  border-color: rgba(20, 184, 166, 0.48);
  box-shadow: 0 14px 34px rgba(20, 184, 166, 0.13);
  transform: translateY(-1px);
}

.file-select {
  display: flex;
  width: 100%;
  min-width: 0;
  align-items: center;
  gap: 10px;
  padding: 0;
  text-align: left;
  background: transparent;
  border: 0;
}

.file-icon {
  flex: 0 0 auto;
  padding: 6px 8px;
  color: #1d4ed8;
  background: rgba(37, 99, 235, 0.10);
  border-radius: 8px;
  font-size: 11px;
  font-weight: 900;
}

.file-icon.ready {
  color: #0f766e;
  background: rgba(20, 184, 166, 0.12);
}

.file-main {
  flex: 1 1 auto;
  min-width: 0;
}

.file-main strong {
  display: block;
  overflow: hidden;
  color: #172033;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-actions,
.vector-progress {
  display: grid;
  gap: 6px;
}

.vector-progress .progress {
  height: 6px;
}

.image-strip {
  display: flex;
  gap: 10px;
  margin: 16px 0;
  overflow-x: auto;
}

.image-strip img {
  width: 104px;
  height: 76px;
  object-fit: cover;
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 10px;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
  transition: transform 180ms var(--agent-ease), box-shadow 180ms var(--agent-ease);
}

.image-strip img:hover {
  box-shadow: 0 16px 34px rgba(15, 23, 42, 0.14);
  transform: translateY(-2px) scale(1.02);
}

.preview-wrap {
  animation: float-in 280ms var(--agent-ease) both;
}

.preview-chunks {
  display: grid;
  gap: 10px;
}

.chunk-card {
  padding: 13px;
  background: rgba(248, 250, 252, 0.86);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 12px;
}

.chunk-card:hover {
  border-color: rgba(37, 99, 235, 0.22);
  transform: translateY(-1px);
}

.chunk-card p {
  margin: 7px 0 0;
  color: #1f2937;
  font-size: 13px;
  line-height: 1.7;
}

.chunk-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.chunk-head span {
  color: #0f172a;
  font-weight: 900;
}

.chunk-head small {
  padding: 2px 7px;
  background: rgba(37, 99, 235, 0.08);
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: 999px;
}

.chunk-image {
  width: min(320px, 100%);
  max-height: 220px;
  object-fit: contain;
  margin-top: 10px;
  background: #ffffff;
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 10px;
}

.chunk-meta {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  gap: 6px 10px;
  margin: 10px 0 0;
  font-size: 12px;
}

.chunk-meta dt {
  color: #64748b;
  font-weight: 800;
}

.chunk-meta dd {
  min-width: 0;
  margin: 0;
  color: #1f2937;
  word-break: break-word;
}

.chunk-json {
  max-height: 180px;
  overflow: auto;
  margin: 10px 0 0;
  padding: 10px;
  color: #334155;
  background: #f8fafc;
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 8px;
  font-size: 11px;
}

.chunk-error {
  margin-top: 10px;
  padding: 8px 10px;
  color: #991b1b;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 8px;
  font-size: 12px;
}

.kb-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 20px;
  color: #64748b;
  font-size: 14px;
}

.kb-empty.compact {
  padding: 12px 4px;
  font-size: 12px;
}

.kb-fade-enter-active,
.kb-fade-leave-active {
  transition: opacity 200ms var(--agent-ease);
}

.kb-fade-enter-from,
.kb-fade-leave-to {
  opacity: 0;
}

.kb-panel-enter-active,
.kb-panel-leave-active,
.preview-enter-active,
.preview-leave-active,
.notice-enter-active,
.notice-leave-active,
.file-row-enter-active,
.file-row-leave-active {
  transition:
    opacity 240ms var(--agent-ease),
    transform 240ms var(--agent-ease);
}

.kb-panel-enter-from,
.kb-panel-leave-to {
  opacity: 0;
  transform: translateY(18px) scale(0.985);
}

.preview-enter-from,
.preview-leave-to,
.notice-enter-from,
.notice-leave-to,
.file-row-enter-from,
.file-row-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

.file-row-move {
  transition: transform 240ms var(--agent-ease);
}

@media (max-width: 1100px) {
  .kb-body,
  .kb-file-columns {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 900px) {
  .kb-overlay {
    padding: 14px;
  }

  .kb-modal {
    width: calc(100vw - 28px);
    height: calc(100vh - 28px);
  }

  .kb-stats {
    grid-template-columns: 1fr;
  }

  .kb-chart {
    grid-column: auto;
  }

  .graph-panel {
    align-items: stretch;
    flex-direction: column;
  }

  .graph-actions {
    justify-content: flex-start;
  }
}
</style>
