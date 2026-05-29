<script setup>
import MarkdownIt from 'markdown-it'
import { nextTick, ref } from 'vue'
import ChatTraceTimeline from '@/components/chat/ChatTraceTimeline.vue'

defineProps({
  messages: {
    type: Array,
    default: () => [],
  },
  busy: {
    type: Boolean,
    default: false,
  },
})

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})
const defaultImageRenderer = markdown.renderer.rules.image
  ?? ((tokens, index, options, env, self) => self.renderToken(tokens, index, options))
const defaultFenceRenderer = markdown.renderer.rules.fence
  ?? ((tokens, index, options, env, self) => self.renderToken(tokens, index, options))

markdown.renderer.rules.image = (tokens, index, options, env, self) => {
  const token = tokens[index]
  const src = token.attrGet('src')
  if (src) {
    token.attrSet('src', normalizeKnowledgeImageUrl(src))
  }
  return defaultImageRenderer(tokens, index, options, env, self)
}

markdown.renderer.rules.fence = (tokens, index, options, env, self) => {
  const token = tokens[index]
  const info = token.info.trim().split(/\s+/)[0]
  if (info === 'metrics') {
    return renderMetricsBlock(token.content)
  }
  return defaultFenceRenderer(tokens, index, options, env, self)
}

markdown.renderer.rules.table_open = () => (
  '<div class="assistant-table-wrap overflow-x-auto rounded-box border border-base-300 bg-base-100 shadow-sm">'
  + '<table class="table table-zebra table-sm">'
)
markdown.renderer.rules.table_close = () => '</table></div>'

const listRef = ref(null)
const expandedTraceIds = ref(new Set())

async function scrollToBottom() {
  await nextTick()
  listRef.value?.scrollTo({
    top: listRef.value.scrollHeight,
    behavior: 'smooth',
  })
}

function renderAssistantHtml(text) {
  return markdown.render(text || '')
}

function normalizeKnowledgeImageUrl(src) {
  if (!src) return src
  const normalized = src.replace(/^(\/api)+\/knowledge\//, '/api/knowledge/')
  if (normalized.startsWith('/knowledge/')) {
    return `/api${normalized}`
  }
  return normalized
}

function renderMetricsBlock(content) {
  const metrics = parseMetrics(content)
  if (!metrics.length) {
    return `<pre class="assistant-metric-fallback"><code>${escapeHtml(content)}</code></pre>`
  }
  const cards = metrics.map((metric) => {
    const badge = metric.status === 'neutral' ? 'badge-neutral' : `badge-${metric.status}`
    return `
      <article class="card bg-base-100 border border-base-300 shadow-sm">
        <div class="card-body gap-2 p-4">
          <div class="flex items-start justify-between gap-3">
            <h3 class="card-title text-sm font-semibold text-base-content">${escapeHtml(metric.label)}</h3>
            <span class="badge ${badge} badge-sm">${escapeHtml(metric.status)}</span>
          </div>
          <div class="text-2xl font-bold leading-tight text-base-content">${escapeHtml(metric.value)}</div>
          ${metric.description ? `<p class="text-xs leading-5 text-base-content/65">${escapeHtml(metric.description)}</p>` : ''}
        </div>
      </article>
    `
  }).join('')
  return `<div class="assistant-metrics grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">${cards}</div>`
}

function parseMetrics(content) {
  const allowedStatuses = new Set(['success', 'warning', 'error', 'info', 'neutral'])
  return String(content || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => !/^指标\s*\|/.test(line) && !/^[-\s|:]+$/.test(line))
    .map((line) => {
      const [label = '', value = '', status = 'neutral', description = ''] = line
        .split('|')
        .map((part) => part.trim())
      const normalizedStatus = allowedStatuses.has(status) ? status : 'neutral'
      return { label, value, status: normalizedStatus, description }
    })
    .filter((metric) => metric.label && metric.value)
}

function escapeHtml(value) {
  return markdown.utils.escapeHtml(String(value ?? ''))
}

function isAssistantThinking(message) {
  return message.role === 'assistant' && message.status === 'streaming' && !message.text && !message.thoughts?.length
}

function hasThoughts(message) {
  return message.role === 'assistant' && Array.isArray(message.thoughts) && message.thoughts.length > 0
}

function isTraceOpen(message) {
  return message.status === 'streaming' || expandedTraceIds.value.has(message.id)
}

function toggleTrace(message) {
  const nextIds = new Set(expandedTraceIds.value)
  if (nextIds.has(message.id)) {
    nextIds.delete(message.id)
  } else {
    nextIds.add(message.id)
  }
  expandedTraceIds.value = nextIds
}

function beforeTraceEnter(element) {
  element.style.height = '0'
  element.style.opacity = '0'
  element.style.transform = 'translateY(-4px)'
}

function traceEnter(element) {
  requestAnimationFrame(() => {
    element.style.height = `${element.scrollHeight}px`
    element.style.opacity = '1'
    element.style.transform = 'translateY(0)'
  })
}

function afterTraceTransition(element) {
  element.style.height = ''
  element.style.opacity = ''
  element.style.transform = ''
}

function traceLeave(element) {
  element.style.height = `${element.scrollHeight}px`
  element.style.opacity = '1'
  element.style.transform = 'translateY(0)'

  requestAnimationFrame(() => {
    element.style.height = '0'
    element.style.opacity = '0'
    element.style.transform = 'translateY(-4px)'
  })
}

defineExpose({ scrollToBottom })
</script>

<template>
  <div ref="listRef" class="message-list">
    <TransitionGroup name="message" tag="div" class="message-stack">
      <article
        v-for="message in messages"
        :key="message.id"
        class="message-row"
        :class="[message.role, { streaming: message.status === 'streaming' }]"
      >
        <div class="avatar" aria-hidden="true">
          <span>{{ message.role === 'user' ? '我' : 'AI' }}</span>
        </div>

        <div class="message-content">
          <div
            v-if="hasThoughts(message)"
            class="assistant-trace"
            :class="{ active: message.status === 'streaming' }"
          >
            <button
              class="trace-toggle"
              type="button"
              :aria-expanded="isTraceOpen(message)"
              :aria-controls="`trace-panel-${message.id}`"
              @click="toggleTrace(message)"
            >
              <span class="trace-pulse" aria-hidden="true"></span>
              <span class="trace-label">{{ message.status === 'streaming' ? '正在思考' : '思考过程' }}</span>
              <span class="trace-count">{{ message.thoughts.length }}</span>
              <span class="trace-chevron" aria-hidden="true"></span>
            </button>
            <Transition
              name="trace-panel"
              @before-enter="beforeTraceEnter"
              @enter="traceEnter"
              @after-enter="afterTraceTransition"
              @leave="traceLeave"
              @after-leave="afterTraceTransition"
            >
              <div
                v-if="isTraceOpen(message)"
                :id="`trace-panel-${message.id}`"
                class="trace-body"
              >
                <ChatTraceTimeline :thoughts="message.thoughts" :active="message.status === 'streaming'" />
              </div>
            </Transition>
          </div>

          <div
            v-if="message.role === 'assistant' && isAssistantThinking(message)"
            class="assistant-thinking"
            aria-label="正在思考"
          >
            <span class="thinking-line"></span>
            <span class="thinking-dot"></span>
            <span class="thinking-dot"></span>
            <span class="thinking-dot"></span>
          </div>
          <div
            v-else-if="message.role === 'assistant'"
            class="assistant-markdown"
            :class="{ error: message.status === 'error' }"
            v-html="renderAssistantHtml(message.text)"
          ></div>
          <div v-else class="user-bubble">{{ message.text }}</div>
        </div>
      </article>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.message-list {
  min-height: 0;
  padding: 30px clamp(18px, 4%, 28px) 24px;
  overflow-x: hidden;
  overflow-y: auto;
}

.message-stack {
  min-height: 100%;
}

.message-row {
  display: flex;
  max-width: 820px;
  gap: 12px;
  margin: 0 auto 18px;
  will-change: transform, opacity;
}

.message-row.user {
  justify-content: flex-end;
}

.message-row.user .avatar {
  order: 2;
  background: linear-gradient(135deg, #111827, #334155);
}

.message-row.assistant .avatar {
  background: linear-gradient(135deg, #0f766e, #2563eb);
}

.message-row.streaming .avatar {
  box-shadow: 0 0 0 5px rgba(20, 184, 166, 0.10);
}

.avatar {
  display: grid;
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  place-items: center;
  color: #ffffff;
  border-radius: 12px;
  box-shadow: 0 10px 26px rgba(15, 23, 42, 0.12);
}

.avatar span {
  font-size: 12px;
  font-weight: 800;
  line-height: 1;
}

.message-content {
  min-width: 0;
  max-width: min(680px, 84%);
  color: #172033;
  font-size: 14px;
  line-height: 1.7;
}

.user-bubble {
  padding: 10px 14px;
  white-space: pre-wrap;
  background: linear-gradient(135deg, #ecfeff, #f0f9ff);
  border: 1px solid rgba(14, 116, 144, 0.14);
  border-radius: 18px 6px 18px 18px;
  box-shadow: 0 10px 26px rgba(14, 116, 144, 0.08);
}

.assistant-thinking {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 72px;
  height: 24px;
  color: #64748b;
}

.thinking-line {
  width: 28px;
  height: 1px;
  margin-right: 2px;
  overflow: hidden;
  background: #cbd5e1;
  border-radius: 999px;
}

.thinking-line::after {
  display: block;
  width: 12px;
  height: 1px;
  background: #0f766e;
  content: "";
  animation: thought-scan 1100ms var(--agent-ease) infinite;
}

.thinking-dot {
  width: 4px;
  height: 4px;
  background: currentColor;
  border-radius: 999px;
  animation: thought-dot 900ms var(--agent-ease) infinite;
}

.thinking-dot:nth-child(3) {
  animation-delay: 130ms;
}

.thinking-dot:nth-child(4) {
  animation-delay: 260ms;
}

.assistant-trace {
  margin: 0 0 10px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.6;
}

.trace-toggle {
  display: inline-flex;
  width: fit-content;
  align-items: center;
  gap: 7px;
  min-height: 24px;
  padding: 0;
  color: inherit;
  background: transparent;
  border: 0;
  cursor: pointer;
  outline: none;
  transition: color 180ms var(--agent-ease), opacity 180ms var(--agent-ease);
}

.trace-toggle:hover,
.trace-toggle:focus-visible {
  color: #0f766e;
}

.trace-pulse {
  position: relative;
  width: 7px;
  height: 7px;
  background: #94a3b8;
  border-radius: 999px;
  transition: background-color 180ms var(--agent-ease);
}

.assistant-trace.active .trace-pulse {
  background: #0f766e;
}

.assistant-trace.active .trace-pulse::after {
  position: absolute;
  inset: -5px;
  border: 1px solid rgba(15, 118, 110, 0.24);
  border-radius: inherit;
  content: "";
  animation: thought-ring 1200ms var(--agent-ease) infinite;
}

.trace-label {
  font-weight: 650;
}

.trace-count {
  display: grid;
  min-width: 18px;
  height: 18px;
  place-items: center;
  color: #94a3b8;
  border: 1px solid #e2e8f0;
  border-radius: 999px;
  font-size: 11px;
  line-height: 1;
}

.trace-chevron {
  width: 7px;
  height: 7px;
  border-right: 1.5px solid currentColor;
  border-bottom: 1.5px solid currentColor;
  opacity: 0.68;
  transform: rotate(45deg) translateY(-1px);
  transform-origin: center;
  transition: transform 220ms var(--agent-ease), opacity 180ms var(--agent-ease);
}

.trace-toggle[aria-expanded="true"] .trace-chevron {
  opacity: 1;
  transform: rotate(225deg) translateY(-1px);
}

.trace-body {
  box-sizing: border-box;
  margin-left: 3px;
  overflow: hidden;
  border-left: 1px solid #e2e8f0;
  transition:
    height 260ms var(--agent-ease),
    opacity 220ms var(--agent-ease),
    transform 260ms var(--agent-ease);
}

.trace-panel-enter-active,
.trace-panel-leave-active {
  will-change: height, opacity, transform;
}

.assistant-trace ol {
  min-height: 0;
  margin: 4px 0 0;
  padding: 2px 0 1px 14px;
  list-style: none;
}

.assistant-markdown {
  position: relative;
  padding: 2px 0;
}

.message-row.streaming .assistant-markdown::after {
  display: inline-block;
  width: 7px;
  height: 1.18em;
  margin-left: 3px;
  vertical-align: -0.18em;
  background: #14b8a6;
  border-radius: 999px;
  content: "";
  animation: stream-caret 980ms steps(1, end) infinite;
}

.assistant-markdown.error {
  color: #b42318;
}

.assistant-markdown :deep(p) {
  margin: 0 0 8px;
}

.assistant-markdown :deep(p:last-child) {
  margin-bottom: 0;
}

.assistant-markdown :deep(ul),
.assistant-markdown :deep(ol) {
  margin: 8px 0;
  padding-left: 22px;
}

.assistant-markdown :deep(code) {
  padding: 2px 5px;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 5px;
  font-size: 0.92em;
}

.assistant-markdown :deep(pre) {
  padding: 12px;
  overflow-x: auto;
  background: #0f172a;
  border-radius: 12px;
}

.assistant-markdown :deep(pre code) {
  padding: 0;
  color: #e2e8f0;
  background: transparent;
}

.assistant-markdown :deep(.assistant-metrics) {
  margin: 10px 0 12px;
}

.assistant-markdown :deep(.assistant-table-wrap) {
  margin: 10px 0 12px;
}

.assistant-markdown :deep(.assistant-table-wrap table) {
  width: 100%;
  min-width: 560px;
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
}

.assistant-markdown :deep(.assistant-table-wrap th),
.assistant-markdown :deep(.assistant-table-wrap td) {
  padding: 8px 10px;
  vertical-align: middle;
}

.assistant-markdown :deep(.assistant-table-wrap th) {
  color: #0f172a;
  background: rgba(241, 245, 249, 0.86);
  font-weight: 800;
  text-align: left;
}

.assistant-markdown :deep(.assistant-table-wrap td:first-child) {
  width: 76px;
  text-align: center;
}

.assistant-markdown :deep(img) {
  display: block;
  max-width: min(100%, 560px);
  max-height: 360px;
  margin: 10px 0;
  object-fit: contain;
  background: #ffffff;
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 10px;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.10);
}

.assistant-markdown :deep(td img) {
  display: inline-block;
  width: auto;
  max-width: 56px;
  max-height: 42px;
  margin: 0;
  vertical-align: middle;
  border-radius: 6px;
  box-shadow: none;
}

.message-enter-active,
.message-leave-active {
  transition:
    opacity 260ms var(--agent-ease),
    transform 260ms var(--agent-ease);
}

.message-enter-from,
.message-leave-to {
  opacity: 0;
  transform: translateY(12px) scale(0.985);
}

.message-move {
  transition: transform 260ms var(--agent-ease);
}

@keyframes thought-dot {
  0%,
  100% {
    opacity: 0.28;
    transform: translateY(0);
  }

  50% {
    opacity: 0.9;
    transform: translateY(-2px);
  }
}

@keyframes thought-scan {
  from {
    transform: translateX(-12px);
  }

  to {
    transform: translateX(30px);
  }
}

@keyframes thought-ring {
  from {
    opacity: 0.8;
    transform: scale(0.58);
  }

  to {
    opacity: 0;
    transform: scale(1.42);
  }
}

@keyframes thought-item-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 900px) {
  .message-list {
    padding-right: 16px;
    padding-left: 16px;
  }

  .message-content {
    max-width: min(620px, 88%);
  }
}
</style>
