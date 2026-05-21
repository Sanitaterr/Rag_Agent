<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import ChatComposer from '@/components/chat/ChatComposer.vue'
import ChatMessageList from '@/components/chat/ChatMessageList.vue'
import ChatSidebar from '@/components/chat/ChatSidebar.vue'
import { EMPTY_CHAT_TITLE, QUICK_PROMPTS } from '@/config/prompts'
import {
  fetchAgentSessionMessages,
  fetchAgentSessions,
  streamAgentMessage,
} from '@/services/agentApi'

const input = ref('')
const busy = ref(false)
const loadingSessions = ref(false)
const loadingMessages = ref(false)
const messages = ref([])
const scrollRef = ref(null)
const activeSessionId = ref('')
const sessions = ref([])

const activeSessionTitle = computed(() => (
  sessions.value.find((session) => session.id === activeSessionId.value)?.title || '新对话'
))

onMounted(async () => {
  await refreshSessions()
  if (sessions.value.length) {
    await selectChat(sessions.value[0].id)
  } else {
    newChat()
  }
})

function newChat() {
  if (busy.value) return

  const sessionId = crypto.randomUUID()
  activeSessionId.value = sessionId
  messages.value = []
  input.value = ''
  upsertSession({ id: sessionId, title: '新对话', updated_at: new Date().toISOString() })
}

async function selectChat(sessionId) {
  if (!sessionId || sessionId === activeSessionId.value || busy.value) return

  activeSessionId.value = sessionId
  input.value = ''
  messages.value = []
  loadingMessages.value = true

  try {
    messages.value = await fetchAgentSessionMessages(sessionId)
    await scrollToBottom()
  } catch (error) {
    messages.value = [{
      id: crypto.randomUUID(),
      role: 'assistant',
      text: `加载会话失败：${error.message || '请求失败'}`,
      status: 'error',
    }]
  } finally {
    loadingMessages.value = false
  }
}

async function refreshSessions() {
  loadingSessions.value = true
  try {
    const persistedSessions = await fetchAgentSessions()
    const localDrafts = sessions.value.filter((session) => (
      session.id === activeSessionId.value && !persistedSessions.some((item) => item.id === session.id)
    ))
    sessions.value = [...localDrafts, ...persistedSessions]
  } catch (error) {
    console.error('Failed to load chat sessions:', error)
  } finally {
    loadingSessions.value = false
  }
}

async function scrollToBottom() {
  await nextTick()
  scrollRef.value?.scrollToBottom()
}

let scrollFrameId = 0
function scheduleScrollToBottom() {
  if (scrollFrameId) return
  scrollFrameId = requestAnimationFrame(async () => {
    scrollFrameId = 0
    await scrollToBottom()
  })
}

async function sendMessage(text = input.value) {
  const content = text.trim()
  if (!content || busy.value) return

  if (!activeSessionId.value) {
    newChat()
  }

  messages.value.push({
    id: crypto.randomUUID(),
    role: 'user',
    text: content,
    status: 'done',
  })
  updateActiveSessionTitle()
  input.value = ''
  busy.value = true
  await scrollToBottom()

  const assistantIndex = messages.value.length
  messages.value.push({
    id: crypto.randomUUID(),
    role: 'assistant',
    text: '',
    thoughts: [],
    status: 'streaming',
  })
  await scrollToBottom()

  try {
    await streamAgentMessage({
      message: content,
      sessionId: activeSessionId.value,
      onSession: (value) => {
        activeSessionId.value = value
      },
      onThought: (value) => {
        appendAssistantThought(assistantIndex, value)
      },
      onTool: (value) => {
        appendAssistantThought(assistantIndex, value)
      },
      onToken: (value) => {
        const assistantMessage = messages.value[assistantIndex]
        assistantMessage.text += value
        scheduleScrollToBottom()
      },
      onDone: (value) => {
        activeSessionId.value = value || activeSessionId.value
        messages.value[assistantIndex].status = 'done'
        updateActiveSessionTitle()
        refreshSessions()
      },
      onError: (value) => {
        const assistantMessage = messages.value[assistantIndex]
        assistantMessage.text = `调用后端 Agent 失败：${value}`
        assistantMessage.status = 'error'
      },
    })
  } catch (error) {
    const assistantMessage = messages.value[assistantIndex]
    assistantMessage.text = `调用后端 Agent 失败：${error.message || '请求失败'}`
    assistantMessage.status = 'error'
  } finally {
    const assistantMessage = messages.value[assistantIndex]
    if (!assistantMessage.text && assistantMessage.status !== 'error') {
      assistantMessage.text = '后端没有返回内容。'
      assistantMessage.status = 'done'
    }
    busy.value = false
    await scrollToBottom()
  }
}

function appendAssistantThought(index, value) {
  if (!value) return

  const assistantMessage = messages.value[index]
  if (!assistantMessage.thoughts) {
    assistantMessage.thoughts = []
  }
  if (assistantMessage.thoughts[assistantMessage.thoughts.length - 1] === value) {
    return
  }

  assistantMessage.thoughts.push(value)
  scheduleScrollToBottom()
}

function updateActiveSessionTitle() {
  const firstUserMessage = messages.value.find((message) => message.role === 'user')?.text
  const title = firstUserMessage ? firstUserMessage.slice(0, 18) : '新对话'
  upsertSession({ id: activeSessionId.value, title, updated_at: new Date().toISOString() })
}

function upsertSession(session) {
  const index = sessions.value.findIndex((item) => item.id === session.id)
  if (index >= 0) {
    sessions.value[index] = { ...sessions.value[index], ...session }
  } else {
    sessions.value.unshift(session)
  }
}
</script>

<template>
  <div class="chat-page">
    <ChatSidebar
      :chats="sessions"
      :active-chat-id="activeSessionId"
      :loading="loadingSessions"
      @new-chat="newChat"
      @select-chat="selectChat"
    />

    <main class="chat-main">
      <section v-if="messages.length === 0" class="empty-state">
        <h1>{{ EMPTY_CHAT_TITLE }}</h1>

        <ChatComposer v-model="input" :busy="busy || loadingMessages" @send="sendMessage()" />

        <div class="prompt-actions">
          <button
            v-for="prompt in QUICK_PROMPTS"
            :key="prompt.id"
            type="button"
            @click="sendMessage(prompt.message)"
          >
            {{ prompt.label }}
          </button>
        </div>
      </section>

      <section v-else class="conversation">
        <header class="conversation-header">
          <span>{{ activeSessionTitle }}</span>
        </header>

        <ChatMessageList ref="scrollRef" :messages="messages" :busy="busy" />

        <footer class="conversation-footer">
          <ChatComposer v-model="input" :busy="busy || loadingMessages" @send="sendMessage()" />
        </footer>
      </section>
    </main>
  </div>
</template>

<style scoped>
.chat-page {
  display: grid;
  width: 100%;
  height: 100vh;
  grid-template-columns: 260px minmax(0, 1fr);
  overflow: hidden;
  color: #202123;
  background: #ffffff;
}

.chat-main {
  min-width: 0;
  height: 100vh;
  background: #ffffff;
}

.empty-state {
  display: flex;
  width: 100%;
  height: 100%;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 24px;
  transform: translateY(-22px);
}

.empty-state h1 {
  margin: 0 0 28px;
  font-size: 30px;
  font-weight: 650;
  line-height: 1.25;
  letter-spacing: 0;
}

.prompt-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  margin-top: 22px;
}

.prompt-actions button {
  height: 36px;
  padding: 0 15px;
  color: #4b5563;
  background: #ffffff;
  border: 1px solid #dedede;
  border-radius: 999px;
  font-size: 14px;
}

.prompt-actions button:hover {
  background: #f7f7f7;
}

.conversation {
  display: grid;
  height: 100vh;
  grid-template-rows: auto minmax(0, 1fr) auto;
}

.conversation-header {
  min-width: 0;
  padding: 14px 24px;
  overflow: hidden;
  color: #4b5563;
  border-bottom: 1px solid #ececec;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conversation-footer {
  padding: 16px 24px 22px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0), #ffffff 28%);
}

.conversation-footer :deep(.composer) {
  margin: 0 auto;
}

@media (max-width: 900px) {
  .chat-page {
    grid-template-columns: 1fr;
  }
}
</style>
