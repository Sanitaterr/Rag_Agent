<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import ChatComposer from '@/components/chat/ChatComposer.vue'
import ChatMessageList from '@/components/chat/ChatMessageList.vue'
import ChatSidebar from '@/components/chat/ChatSidebar.vue'
import AccessPointsModal from '@/components/access/AccessPointsModal.vue'
import KnowledgeBaseModal from '@/components/knowledge/KnowledgeBaseModal.vue'
import KnowledgeGraphDrawer from '@/components/knowledge/KnowledgeGraphDrawer.vue'
import { EMPTY_CHAT_TITLE, QUICK_PROMPTS } from '@/config/prompts'
import {
  deleteAgentSession,
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
const accessPointsOpen = ref(false)
const knowledgeOpen = ref(false)
const graphDrawerOpen = ref(false)
const deletingSessionId = ref('')
const requestControllers = new Set()

const activeSessionTitle = computed(() => (
  sessions.value.find((session) => session.id === activeSessionId.value)?.title || '新对话'
))

const hasConversation = computed(() => messages.value.length > 0)

onMounted(async () => {
  await refreshSessions()
  if (sessions.value.length) {
    await selectChat(sessions.value[0].id)
  } else {
    newChat()
  }
})

onBeforeUnmount(() => {
  if (scrollFrameId) {
    cancelAnimationFrame(scrollFrameId)
    scrollFrameId = 0
  }
  requestControllers.forEach((controller) => controller.abort())
  requestControllers.clear()
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
  const controller = createRequestController()

  try {
    messages.value = await fetchAgentSessionMessages(sessionId, { signal: controller.signal })
    await scrollToBottom()
  } catch (error) {
    if (isAbortError(error)) return
    messages.value = [{
      id: crypto.randomUUID(),
      role: 'assistant',
      text: `加载会话失败：${error.message || '请求失败'}`,
      status: 'error',
    }]
  } finally {
    releaseRequestController(controller)
    loadingMessages.value = false
  }
}

async function deleteChat(sessionId) {
  if (!sessionId || busy.value || deletingSessionId.value) return

  const target = sessions.value.find((session) => session.id === sessionId)
  const confirmed = confirm(`确定删除会话“${target?.title || '未命名会话'}”？删除后数据库中的历史记录也会同步移除。`)
  if (!confirmed) return

  deletingSessionId.value = sessionId
  const controller = createRequestController()
  try {
    await deleteAgentSession(sessionId, { signal: controller.signal })
    const remainingSessions = sessions.value.filter((session) => session.id !== sessionId)
    sessions.value = remainingSessions

    if (activeSessionId.value !== sessionId) return

    messages.value = []
    input.value = ''
    activeSessionId.value = ''
    if (remainingSessions.length) {
      await selectChat(remainingSessions[0].id)
    } else {
      newChat()
    }
  } catch (error) {
    if (isAbortError(error)) return
    console.error('Failed to delete chat session:', error)
    alert(`删除会话失败：${error.message || '请求失败'}`)
  } finally {
    releaseRequestController(controller)
    deletingSessionId.value = ''
  }
}

async function refreshSessions() {
  loadingSessions.value = true
  const controller = createRequestController()
  try {
    const persistedSessions = await fetchAgentSessions({ signal: controller.signal })
    const localDrafts = sessions.value.filter((session) => (
      session.id === activeSessionId.value && !persistedSessions.some((item) => item.id === session.id)
    ))
    sessions.value = [...localDrafts, ...persistedSessions]
  } catch (error) {
    if (isAbortError(error)) return
    console.error('Failed to load chat sessions:', error)
  } finally {
    releaseRequestController(controller)
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
  const controller = createRequestController()

  try {
    await streamAgentMessage({
      message: content,
      sessionId: activeSessionId.value,
      signal: controller.signal,
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
      onAnswer: (value) => {
        const assistantMessage = messages.value[assistantIndex]
        assistantMessage.text = value || assistantMessage.text
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
    if (isAbortError(error)) return
    const assistantMessage = messages.value[assistantIndex]
    assistantMessage.text = `调用后端 Agent 失败：${error.message || '请求失败'}`
    assistantMessage.status = 'error'
  } finally {
    releaseRequestController(controller)
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
      :deleting-chat-id="deletingSessionId"
      @new-chat="newChat"
      @select-chat="selectChat"
      @delete-chat="deleteChat"
      @open-access-points="accessPointsOpen = true"
      @open-knowledge="knowledgeOpen = true"
    />

    <AccessPointsModal :open="accessPointsOpen" @close="accessPointsOpen = false" />
    <KnowledgeBaseModal :open="knowledgeOpen" @close="knowledgeOpen = false" />

    <div class="workspace-shell" :class="{ 'graph-open': graphDrawerOpen }">
    <main class="chat-main">
      <Transition name="workspace" mode="out-in">
        <section v-if="!hasConversation" key="empty" class="empty-state">
          <div class="empty-shell">
            <div class="empty-status">
              <span class="status-dot" aria-hidden="true"></span>
              Agent 在线
            </div>
            <button class="graph-toggle focus-ring smooth-pop" type="button" @click="graphDrawerOpen = !graphDrawerOpen">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M7 4.5a2.5 2.5 0 1 1 1.96 3.94l2 3.04a2.52 2.52 0 0 1 2.08 0l2-3.04A2.5 2.5 0 1 1 16.3 9.1l-2 3.04a2.5 2.5 0 1 1-4.6 0l-2-3.04A2.5 2.5 0 0 1 7 4.5Zm0 1.5a1 1 0 1 0 0 2 1 1 0 0 0 0-2Zm10 0a1 1 0 1 0 0 2 1 1 0 0 0 0-2Zm-5 7a1 1 0 1 0 0 2 1 1 0 0 0 0-2Z" />
              </svg>
              {{ graphDrawerOpen ? '收起图谱' : '知识图谱' }}
            </button>
            <h1>{{ EMPTY_CHAT_TITLE }}</h1>
            <p>面向文档问答、工业协议知识检索和 LangGraph 工作流调试。</p>

            <ChatComposer v-model="input" :busy="busy || loadingMessages" @send="sendMessage()" />

            <div class="prompt-actions">
              <button
                v-for="prompt in QUICK_PROMPTS"
                :key="prompt.id"
                class="prompt-button smooth-pop focus-ring"
                type="button"
                @click="sendMessage(prompt.message)"
              >
                <span>{{ prompt.label }}</span>
              </button>
            </div>
          </div>
        </section>

        <section v-else key="conversation" class="conversation">
          <header class="conversation-header">
            <div class="title-block">
              <span>{{ activeSessionTitle }}</span>
              <small>{{ busy ? '正在生成回复' : '上下文已同步' }}</small>
            </div>
            <div class="header-actions">
              <button class="graph-toggle compact focus-ring smooth-pop" type="button" @click="graphDrawerOpen = !graphDrawerOpen">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M7 4.5a2.5 2.5 0 1 1 1.96 3.94l2 3.04a2.52 2.52 0 0 1 2.08 0l2-3.04A2.5 2.5 0 1 1 16.3 9.1l-2 3.04a2.5 2.5 0 1 1-4.6 0l-2-3.04A2.5 2.5 0 0 1 7 4.5Zm0 1.5a1 1 0 1 0 0 2 1 1 0 0 0 0-2Zm10 0a1 1 0 1 0 0 2 1 1 0 0 0 0-2Zm-5 7a1 1 0 1 0 0 2 1 1 0 0 0 0-2Z" />
                </svg>
                {{ graphDrawerOpen ? '收起图谱' : '知识图谱' }}
              </button>
              <span class="status-pill" :class="{ active: busy }">
                <span aria-hidden="true"></span>
                {{ busy ? 'Streaming' : 'Ready' }}
              </span>
            </div>
          </header>

          <ChatMessageList ref="scrollRef" :messages="messages" :busy="busy" />

          <footer class="conversation-footer">
            <ChatComposer v-model="input" :busy="busy || loadingMessages" @send="sendMessage()" />
          </footer>
        </section>
      </Transition>
    </main>
    <KnowledgeGraphDrawer :open="graphDrawerOpen" @close="graphDrawerOpen = false" />
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  display: grid;
  width: 100%;
  height: 100vh;
  grid-template-columns: 312px minmax(0, 1fr);
  overflow: hidden;
  color: #172033;
}

.workspace-shell {
  --graph-drawer-width: clamp(760px, 48vw, 1240px);
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 0;
  min-width: 0;
  height: 100vh;
  overflow: hidden;
  transition: grid-template-columns 260ms var(--agent-ease);
}

function createRequestController() {
  const controller = new AbortController()
  requestControllers.add(controller)
  controller.signal.addEventListener('abort', () => requestControllers.delete(controller), { once: true })
  return controller
}

function releaseRequestController(controller) {
  requestControllers.delete(controller)
}

function isAbortError(error) {
  return error?.name === 'AbortError'
}

.workspace-shell.graph-open {
  grid-template-columns: minmax(0, 1fr) minmax(0, var(--graph-drawer-width));
}

.chat-main {
  position: relative;
  min-width: 0;
  height: 100vh;
  overflow: hidden;
  transform: translateX(0);
  transition: transform 260ms var(--agent-ease);
}

.workspace-shell.graph-open .chat-main {
  transform: none;
}

.workspace-shell :deep(.graph-drawer) {
  position: relative;
  width: 100%;
  min-width: 0;
  box-shadow: -18px 0 44px rgba(15, 23, 42, 0.16);
}

.workspace-shell:not(.graph-open) :deep(.graph-drawer) {
  transform: translateX(100%);
}

.chat-main::before {
  position: absolute;
  inset: 0;
  pointer-events: none;
  content: "";
  background:
    linear-gradient(120deg, rgba(20, 184, 166, 0.10), transparent 28%),
    linear-gradient(300deg, rgba(37, 99, 235, 0.09), transparent 34%),
    radial-gradient(circle at 82% 18%, rgba(245, 158, 11, 0.12), transparent 24%);
}

.empty-state {
  position: relative;
  z-index: 1;
  display: grid;
  width: 100%;
  height: 100%;
  place-items: center;
  padding: 32px;
}

.empty-shell {
  display: flex;
  width: min(920px, 100%);
  flex-direction: column;
  align-items: center;
  transform: translateY(-18px);
  animation: float-in 460ms var(--agent-ease-soft) both;
}

.empty-status {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  padding: 8px 12px;
  color: #0f766e;
  background: rgba(255, 255, 255, 0.78);
  border: 1px solid rgba(20, 184, 166, 0.18);
  border-radius: 999px;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.07);
  font-size: 13px;
  font-weight: 800;
}

.status-dot {
  width: 8px;
  height: 8px;
  background: #14b8a6;
  border-radius: 999px;
  animation: soft-pulse 1200ms var(--agent-ease) infinite;
}

.empty-state h1 {
  max-width: 820px;
  margin: 0;
  color: #0f172a;
  font-size: clamp(30px, 5vw, 56px);
  font-weight: 850;
  line-height: 1.08;
  text-align: center;
  letter-spacing: 0;
}

.empty-state p {
  max-width: 640px;
  margin: 14px 0 28px;
  color: #64748b;
  font-size: 15px;
  line-height: 1.7;
  text-align: center;
}

.prompt-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  margin-top: 18px;
}

.prompt-button {
  height: 38px;
  padding: 0 15px;
  color: #334155;
  background: rgba(255, 255, 255, 0.76);
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 999px;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
  font-size: 13px;
  font-weight: 800;
}

.prompt-button:hover {
  color: #0f766e;
  border-color: rgba(20, 184, 166, 0.4);
}

.conversation {
  position: relative;
  z-index: 1;
  display: grid;
  height: 100vh;
  grid-template-rows: auto minmax(0, 1fr) auto;
}

.conversation-header {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 15px 26px;
  background: rgba(255, 255, 255, 0.68);
  border-bottom: 1px solid rgba(226, 232, 240, 0.86);
  backdrop-filter: blur(18px);
}

.title-block {
  min-width: 0;
}

.title-block span,
.title-block small {
  display: block;
}

.title-block span {
  overflow: hidden;
  color: #0f172a;
  font-size: 15px;
  font-weight: 850;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.title-block small {
  margin-top: 3px;
  color: #64748b;
  font-size: 12px;
}

.header-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 10px;
}

.graph-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 0 13px;
  color: #0f766e;
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid rgba(20, 184, 166, 0.24);
  border-radius: 12px;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.07);
  font-size: 13px;
  font-weight: 850;
}

.graph-toggle.compact {
  min-height: 34px;
  padding: 0 11px;
}

.graph-toggle:hover {
  border-color: rgba(20, 184, 166, 0.48);
  box-shadow: 0 16px 34px rgba(20, 184, 166, 0.13);
}

.graph-toggle svg {
  width: 17px;
  height: 17px;
  flex: 0 0 auto;
  fill: currentColor;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 10px;
  color: #475569;
  background: rgba(248, 250, 252, 0.88);
  border: 1px solid rgba(226, 232, 240, 0.96);
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
}

.status-pill span {
  width: 7px;
  height: 7px;
  background: #22c55e;
  border-radius: 999px;
}

.status-pill.active span {
  background: #14b8a6;
  animation: soft-pulse 1100ms var(--agent-ease) infinite;
}

.conversation-footer {
  padding: 16px 24px 22px;
  background: linear-gradient(180deg, rgba(248, 250, 252, 0), rgba(248, 250, 252, 0.94) 30%, #f8fafc);
}

.conversation-footer :deep(.composer) {
  margin: 0 auto;
}

.workspace-enter-active,
.workspace-leave-active {
  transition:
    opacity 260ms var(--agent-ease),
    transform 260ms var(--agent-ease);
}

.workspace-enter-from,
.workspace-leave-to {
  opacity: 0;
  transform: translateY(10px);
}

@media (max-width: 900px) {
  .chat-page {
    grid-template-columns: 1fr;
  }

  .chat-page :deep(.chat-sidebar) {
    display: none;
  }

  .workspace-shell {
    --graph-drawer-width: min(640px, 92vw);
    display: grid;
    grid-template-columns: minmax(0, 1fr) 0;
  }

  .workspace-shell.graph-open {
    grid-template-columns: 0 minmax(0, var(--graph-drawer-width));
  }

  .workspace-shell.graph-open .chat-main {
    transform: none;
  }

  .workspace-shell :deep(.graph-drawer) {
    width: var(--graph-drawer-width);
    box-shadow: -18px 0 44px rgba(15, 23, 42, 0.18);
  }

  .empty-state {
    padding: 22px 16px;
  }

  .conversation-header {
    padding: 12px 16px;
  }

  .conversation-footer {
    padding: 12px 16px 18px;
  }
}
</style>
