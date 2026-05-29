<script setup>
import { computed } from 'vue'

const props = defineProps({
  thoughts: {
    type: Array,
    default: () => [],
  },
  active: {
    type: Boolean,
    default: false,
  },
})

const ragStages = [
  { label: '问题预处理', pattern: /启动知识库|问题预处理/i },
  { label: '向量召回', pattern: /向量召回/i },
  { label: '标题关键词召回', pattern: /标题关键词召回|关键词召回/i },
  { label: '候选片段重排', pattern: /候选片段重排|重排/i },
  { label: '来源整理', pattern: /来源整理|调用完成/i },
]

const steps = computed(() => {
  const events = props.thoughts.map((thought, index) => ({
    id: `${index}-${thought}`,
    text: thought,
    type: eventType(thought),
  }))
  return events.length ? events : [{ id: 'idle', text: '正在分析问题', type: 'analysis' }]
})

const hasRag = computed(() => props.thoughts.some((thought) => /知识库|RAG|Chroma|search_docs/i.test(thought)))

const ragStageIndex = computed(() => props.thoughts.reduce((latestIndex, thought) => {
  const matchedIndex = ragStages.findIndex((stage) => stage.pattern.test(thought))
  return matchedIndex >= 0 ? Math.max(latestIndex, matchedIndex) : latestIndex
}, -1))

function eventType(text) {
  if (/知识库|RAG|Chroma|search_docs/i.test(text)) return 'rag'
  if (/联网|web|搜索/i.test(text)) return 'web'
  if (/工具|调用/i.test(text)) return 'tool'
  if (/最终|整理|回答/i.test(text)) return 'answer'
  return 'analysis'
}
</script>

<template>
  <div class="trace-timeline" :class="{ active }">
    <div class="trace-track" aria-hidden="true">
      <span class="track-glow"></span>
    </div>

    <ol class="trace-steps">
      <li
        v-for="(step, index) in steps"
        :key="step.id"
        class="trace-step"
        :class="[step.type, { current: active && index === steps.length - 1 }]"
        :style="{ '--delay': `${index * 70}ms` }"
      >
        <span class="step-node" aria-hidden="true"></span>
        <span class="step-main">
          <strong>{{ step.text }}</strong>
          <small>{{ step.type === 'rag' ? '本地知识库链路' : step.type === 'web' ? '外部检索' : 'Agent 流程' }}</small>
        </span>
      </li>
    </ol>

    <div v-if="hasRag" class="rag-flow">
      <span
        v-for="(stage, index) in ragStages"
        :key="stage.label"
        :class="{
          done: ragStageIndex > index || (!active && ragStageIndex >= index),
          current: active && ragStageIndex === index,
        }"
      >
        {{ stage.label }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.trace-timeline {
  position: relative;
  display: grid;
  gap: 10px;
  padding: 10px 12px;
  overflow: hidden;
  background: rgba(248, 250, 252, 0.82);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 12px;
}

.trace-track {
  position: absolute;
  top: 17px;
  bottom: 17px;
  left: 21px;
  width: 1px;
  overflow: hidden;
  background: #dbe7ef;
}

.track-glow {
  display: block;
  width: 100%;
  height: 34px;
  background: linear-gradient(180deg, transparent, #14b8a6, transparent);
  transform: translateY(-36px);
}

.trace-timeline.active .track-glow {
  animation: trace-scan 1500ms var(--agent-ease) infinite;
}

.trace-steps {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.trace-step {
  position: relative;
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
  opacity: 0;
  transform: translateY(5px);
  animation: trace-step-in 240ms var(--agent-ease) var(--delay) both;
}

.step-node {
  position: relative;
  z-index: 1;
  width: 9px;
  height: 9px;
  flex: 0 0 auto;
  background: #94a3b8;
  border: 2px solid #ffffff;
  border-radius: 999px;
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.22);
}

.trace-step.current .step-node {
  background: #14b8a6;
  box-shadow: 0 0 0 5px rgba(20, 184, 166, 0.12);
}

.trace-step.rag .step-node {
  background: #2563eb;
}

.trace-step.web .step-node {
  background: #f59e0b;
}

.trace-step.answer .step-node {
  background: #0f766e;
}

.step-main {
  display: grid;
  min-width: 0;
}

.step-main strong {
  overflow: hidden;
  color: #334155;
  font-size: 12px;
  font-weight: 800;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.step-main small {
  color: #94a3b8;
  font-size: 11px;
}

.rag-flow {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-left: 18px;
}

.rag-flow span {
  position: relative;
  padding: 4px 8px;
  color: #1d4ed8;
  background: rgba(37, 99, 235, 0.08);
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 800;
}

.rag-flow span::after {
  position: absolute;
  top: 50%;
  right: -7px;
  width: 7px;
  height: 1px;
  background: rgba(37, 99, 235, 0.28);
  content: "";
}

.rag-flow span:last-child::after {
  display: none;
}

.rag-flow span.done {
  color: #0f766e;
  background: rgba(20, 184, 166, 0.08);
  border-color: rgba(20, 184, 166, 0.22);
}

.trace-timeline.active .rag-flow span.current {
  color: #ffffff;
  background: #2563eb;
  border-color: #2563eb;
  box-shadow: 0 0 0 5px rgba(37, 99, 235, 0.12);
  animation: rag-chip-pulse 1050ms var(--agent-ease) infinite;
}

.trace-timeline.active .rag-flow span.current::before {
  position: absolute;
  inset: -4px;
  border: 1px solid rgba(37, 99, 235, 0.22);
  border-radius: inherit;
  content: "";
  animation: rag-chip-ring 1050ms var(--agent-ease) infinite;
}

.trace-timeline.active .rag-flow span.current::after {
  background: rgba(37, 99, 235, 0.56);
}

.trace-timeline.active .rag-flow span.done::after {
  background: rgba(20, 184, 166, 0.42);
}

@keyframes trace-scan {
  from {
    transform: translateY(-36px);
  }

  to {
    transform: translateY(150px);
  }
}

@keyframes trace-step-in {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes rag-chip-pulse {
  0%,
  100% {
    transform: translateY(0);
  }

  50% {
    transform: translateY(-1px);
  }
}

@keyframes rag-chip-ring {
  from {
    opacity: 0.7;
    transform: scale(0.92);
  }

  to {
    opacity: 0;
    transform: scale(1.22);
  }
}
</style>
