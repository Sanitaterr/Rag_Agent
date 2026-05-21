<script setup>
import MarkdownIt from 'markdown-it'
import { nextTick, ref } from 'vue'

defineProps({
  messages: {
    type: Array,
    default: () => [],
  },
})

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

const listRef = ref(null)

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

function isAssistantThinking(message) {
  return message.role === 'assistant' && message.status === 'streaming' && !message.text && !message.thoughts?.length
}

function hasThoughts(message) {
  return message.role === 'assistant' && Array.isArray(message.thoughts) && message.thoughts.length > 0
}

defineExpose({ scrollToBottom })
</script>

<template>
  <div ref="listRef" class="message-list">
    <article
      v-for="message in messages"
      :key="message.id"
      class="message-row"
      :class="message.role"
    >
      <div class="avatar">{{ message.role === 'user' ? '我' : 'AI' }}</div>
      <div class="message-content">
        <details
          v-if="hasThoughts(message)"
          class="assistant-trace"
          :open="message.status === 'streaming'"
        >
          <summary>思考</summary>
          <ol>
            <li v-for="(thought, index) in message.thoughts" :key="`${message.id}-thought-${index}`">
              {{ thought }}
            </li>
          </ol>
        </details>

        <div
          v-if="message.role === 'assistant' && isAssistantThinking(message)"
          class="assistant-thinking"
        >
          正在思考...
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
  </div>
</template>

<style scoped>
.message-list {
  min-height: 0;
  padding: 40px max(32px, calc((100vw - 260px - 920px) / 2)) 24px;
  overflow-y: auto;
}

.message-row {
  display: flex;
  max-width: 920px;
  gap: 14px;
  margin: 0 auto 28px;
}

.message-row.user {
  justify-content: flex-end;
}

.message-row.user .avatar {
  order: 2;
  background: #202123;
}

.message-row.assistant .avatar {
  background: #10a37f;
}

.avatar {
  display: grid;
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  place-items: center;
  color: #ffffff;
  border-radius: 999px;
  font-size: 12px;
}

.message-content {
  min-width: 0;
  max-width: min(760px, 86%);
  font-size: 15px;
  line-height: 1.7;
}

.user-bubble {
  padding: 10px 15px;
  white-space: pre-wrap;
  background: #f4f4f4;
  border-radius: 18px;
}

.assistant-thinking {
  color: #8a8f98;
}

.assistant-trace {
  margin-bottom: 10px;
  color: #6b7280;
  font-size: 13px;
}

.assistant-trace summary {
  width: fit-content;
  cursor: pointer;
  list-style: none;
}

.assistant-trace summary::-webkit-details-marker {
  display: none;
}

.assistant-trace summary::before {
  content: "▸";
  display: inline-block;
  margin-right: 6px;
  color: #9ca3af;
}

.assistant-trace[open] summary::before {
  transform: rotate(90deg);
}

.assistant-trace ol {
  margin: 6px 0 0;
  padding-left: 20px;
}

.assistant-trace li {
  margin: 2px 0;
}

.assistant-markdown.error {
  color: #b42318;
}

.assistant-markdown :deep(p) {
  margin: 0 0 10px;
}

.assistant-markdown :deep(p:last-child) {
  margin-bottom: 0;
}

@media (max-width: 900px) {
  .message-list {
    padding-right: 16px;
    padding-left: 16px;
  }
}
</style>
