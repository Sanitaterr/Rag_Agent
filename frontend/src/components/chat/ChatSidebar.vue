<script setup>
defineProps({
  chats: {
    type: Array,
    default: () => [],
  },
  activeChatId: {
    type: String,
    default: '',
  },
  loading: {
    type: Boolean,
    default: false,
  },
  deletingChatId: {
    type: String,
    default: '',
  },
})

defineEmits(['new-chat', 'select-chat', 'delete-chat', 'open-access-points', 'open-knowledge'])
</script>

<template>
  <aside class="chat-sidebar">
    <div class="sidebar-brand">
      <div class="brand-mark" aria-hidden="true">R</div>
      <div>
        <strong>RAG Agent</strong>
        <span>工业知识助手</span>
      </div>
    </div>

    <nav class="sidebar-nav">
      <button class="sidebar-action smooth-pop focus-ring" type="button" @click="$emit('new-chat')">
        <span aria-hidden="true">+</span>
        新建对话
      </button>
    </nav>

    <section class="sidebar-section">
      <div class="section-title">
        <h2>最近会话</h2>
        <small>{{ chats.length }}</small>
      </div>

      <div v-if="loading" class="sidebar-empty">
        <span class="loading loading-spinner loading-xs"></span>
        加载中
      </div>

      <TransitionGroup name="chat-item" tag="div" class="chat-list">
        <div
          v-for="chat in chats"
          :key="chat.id"
          class="sidebar-chat focus-ring"
          :class="{ active: chat.id === activeChatId }"
          role="button"
          tabindex="0"
          :title="chat.title"
          @click="$emit('select-chat', chat.id)"
          @keydown.enter.prevent="$emit('select-chat', chat.id)"
          @keydown.space.prevent="$emit('select-chat', chat.id)"
        >
          <span class="chat-dot" aria-hidden="true"></span>
          <span class="chat-title">{{ chat.title }}</span>
          <span class="delete-slot">
            <span v-if="deletingChatId === chat.id" class="loading loading-spinner loading-xs"></span>
            <button
              v-else
              class="chat-delete focus-ring"
              type="button"
              title="删除会话"
              aria-label="删除会话"
              @click.stop="$emit('delete-chat', chat.id)"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M9.25 3.5A1.75 1.75 0 0 1 11 1.75h2A1.75 1.75 0 0 1 14.75 3.5V4H19a.75.75 0 0 1 0 1.5h-.86l-.7 13.1A2.75 2.75 0 0 1 14.69 21h-5.38a2.75 2.75 0 0 1-2.75-2.4L5.86 5.5H5A.75.75 0 0 1 5 4h4.25v-.5Zm1.5.5h2.5v-.5a.25.25 0 0 0-.25-.25h-2a.25.25 0 0 0-.25.25V4Zm-3.39 1.5.7 13.02c.04.55.49.98 1.04.98h5.8c.55 0 1-.43 1.04-.98l.7-13.02H7.36Zm2.9 3.25c.41-.03.77.28.8.69l.43 6a.75.75 0 0 1-1.5.12l-.43-6a.75.75 0 0 1 .7-.81Zm4.18.81-.43 6a.75.75 0 1 1-1.5-.12l.43-6a.75.75 0 1 1 1.5.12Z" />
              </svg>
            </button>
          </span>
        </div>
      </TransitionGroup>

      <div v-if="!loading && chats.length === 0" class="sidebar-empty">暂无历史会话</div>
    </section>

    <div class="sidebar-footer">
      <button class="footer-button access-button smooth-pop focus-ring" type="button" @click="$emit('open-access-points')">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M6.5 4.5A2.5 2.5 0 0 1 9 2h6a2.5 2.5 0 0 1 2.5 2.5v2.1a2.5 2.5 0 0 1 0 4.8v2.1a2.5 2.5 0 0 1-2.5 2.5h-2v2.25h3.25a.75.75 0 0 1 0 1.5h-8.5a.75.75 0 0 1 0-1.5H11V16H9a2.5 2.5 0 0 1-2.5-2.5v-2.1a2.5 2.5 0 0 1 0-4.8V4.5Zm2 0v9c0 .28.22.5.5.5h6a.5.5 0 0 0 .5-.5v-9A.5.5 0 0 0 15 4H9a.5.5 0 0 0-.5.5ZM5.75 8.75a.75.75 0 0 0 0 1.5h1v-1.5h-1Zm11.5 0v1.5h1a.75.75 0 0 0 0-1.5h-1Z" />
          <path d="M10.25 6.5h3.5a.75.75 0 0 1 0 1.5h-3.5a.75.75 0 0 1 0-1.5Zm0 3h3.5a.75.75 0 0 1 0 1.5h-3.5a.75.75 0 0 1 0-1.5Z" />
        </svg>
        <span>接入点</span>
      </button>

      <button class="footer-button knowledge-button smooth-pop focus-ring" type="button" @click="$emit('open-knowledge')">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M6.75 3A2.75 2.75 0 0 0 4 5.75v13.5A1.75 1.75 0 0 0 5.75 21h10.5A3.75 3.75 0 0 0 20 17.25V4.75A1.75 1.75 0 0 0 18.25 3H6.75Zm0 2h10.5a.75.75 0 0 1 .75.75v11.5A1.75 1.75 0 0 1 16.25 19H6.75A.75.75 0 0 1 6 18.25V5.75A.75.75 0 0 1 6.75 5Z" />
          <path d="M8.75 8h6.5a.75.75 0 0 1 0 1.5h-6.5a.75.75 0 0 1 0-1.5Zm0 3.25h6.5a.75.75 0 0 1 0 1.5h-6.5a.75.75 0 0 1 0-1.5Zm0 3.25h4.5a.75.75 0 0 1 0 1.5h-4.5a.75.75 0 0 1 0-1.5Z" />
        </svg>
        <span>知识库</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.chat-sidebar {
  display: flex;
  min-height: 0;
  flex-direction: column;
  padding: 16px 14px;
  overflow: hidden;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.78), rgba(241, 245, 249, 0.78)),
    rgba(255, 255, 255, 0.72);
  border-right: 1px solid rgba(226, 232, 240, 0.88);
  backdrop-filter: blur(18px);
  animation: page-in 380ms var(--agent-ease) both;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 8px 16px;
}

.brand-mark {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  color: #ffffff;
  background: linear-gradient(135deg, #0f766e, #2563eb 72%, #f59e0b);
  border-radius: 12px;
  box-shadow: 0 14px 34px rgba(20, 184, 166, 0.22);
  font-size: 15px;
  font-weight: 900;
}

.sidebar-brand strong,
.sidebar-brand span {
  display: block;
}

.sidebar-brand strong {
  color: #0f172a;
  font-size: 17px;
  font-weight: 800;
  line-height: 1.1;
}

.sidebar-brand span {
  margin-top: 3px;
  color: #64748b;
  font-size: 12px;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.sidebar-section {
  flex: 1;
  min-height: 0;
  margin-top: 18px;
  padding-right: 2px;
  overflow-x: hidden;
  overflow-y: auto;
}

.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0 8px 8px;
}

.section-title h2 {
  margin: 0;
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.section-title small {
  display: grid;
  min-width: 22px;
  height: 20px;
  place-items: center;
  color: #0f766e;
  background: rgba(20, 184, 166, 0.10);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 800;
}

.sidebar-action,
.sidebar-chat {
  display: flex;
  width: 100%;
  height: 40px;
  min-height: 40px;
  align-items: center;
  gap: 10px;
  padding: 0 12px;
  color: #172033;
  text-align: left;
  border: 1px solid transparent;
  border-radius: 12px;
  font-size: 13px;
  line-height: 1;
  transition:
    border-color 190ms var(--agent-ease),
    background-color 190ms var(--agent-ease),
    box-shadow 190ms var(--agent-ease),
    transform 190ms var(--agent-ease);
}

.sidebar-action {
  background: #ffffff;
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.08);
  font-weight: 700;
}

.sidebar-action span {
  display: grid;
  width: 20px;
  height: 20px;
  place-items: center;
  color: #ffffff;
  background: #0f766e;
  border-radius: 8px;
  font-size: 16px;
  line-height: 1;
}

.chat-list {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.sidebar-chat {
  min-width: 0;
  max-width: 100%;
  background: transparent;
  cursor: pointer;
}

.chat-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.delete-slot {
  display: grid;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  place-items: center;
  margin-left: auto;
}

.chat-delete {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  color: #94a3b8;
  background: transparent;
  border: 0;
  border-radius: 9px;
  opacity: 0.62;
  transform: scale(0.96);
  transition:
    color 180ms var(--agent-ease),
    background-color 180ms var(--agent-ease),
    opacity 180ms var(--agent-ease),
    transform 180ms var(--agent-ease);
}

.sidebar-chat:hover .chat-delete,
.sidebar-chat.active .chat-delete,
.chat-delete:focus-visible {
  opacity: 1;
  transform: scale(1);
}

.chat-delete:hover {
  color: #dc2626;
  background: rgba(220, 38, 38, 0.10);
}

.chat-delete svg {
  width: 15px;
  height: 15px;
  fill: currentColor;
}

.chat-dot {
  width: 7px;
  height: 7px;
  flex: 0 0 7px;
  background: #cbd5e1;
  border-radius: 999px;
  transition: background-color 190ms var(--agent-ease), transform 190ms var(--agent-ease);
}

.sidebar-action:hover,
.sidebar-chat:hover,
.sidebar-chat.active {
  background: rgba(255, 255, 255, 0.9);
  border-color: rgba(20, 184, 166, 0.24);
  box-shadow: 0 12px 26px rgba(15, 23, 42, 0.07);
  transform: translateX(2px);
}

.sidebar-chat.active .chat-dot {
  background: #14b8a6;
  transform: scale(1.32);
}

.sidebar-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px;
  color: #64748b;
  font-size: 12px;
}

.sidebar-footer {
  display: grid;
  gap: 8px;
  padding: 12px 4px 2px;
  border-top: 1px solid rgba(226, 232, 240, 0.86);
}

.footer-button {
  display: flex;
  width: 100%;
  height: 42px;
  align-items: center;
  justify-content: center;
  gap: 9px;
  padding: 0 12px;
  color: #0f172a;
  background: #ffffff;
  border: 1px solid rgba(226, 232, 240, 0.98);
  border-radius: 12px;
  font-size: 13px;
  font-weight: 800;
}

.footer-button:hover {
  border-color: rgba(20, 184, 166, 0.45);
  box-shadow: 0 14px 34px rgba(20, 184, 166, 0.14);
}

.footer-button svg {
  width: 18px;
  height: 18px;
  fill: #0f766e;
}

.access-button svg {
  fill: #2563eb;
}

.chat-item-enter-active,
.chat-item-leave-active {
  transition:
    opacity 220ms var(--agent-ease),
    transform 220ms var(--agent-ease);
}

.chat-item-enter-from,
.chat-item-leave-to {
  opacity: 0;
  transform: translateX(-8px);
}

.chat-item-move {
  transition: transform 220ms var(--agent-ease);
}
</style>
