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
})

defineEmits(['new-chat', 'select-chat'])
</script>

<template>
  <aside class="chat-sidebar">
    <div class="sidebar-header">
      <strong>Chat Agent</strong>
    </div>

    <nav class="sidebar-nav">
      <button class="sidebar-action" type="button" @click="$emit('new-chat')">
        <span aria-hidden="true">+</span>
        新对话
      </button>
    </nav>

    <section class="sidebar-section">
      <h2>最近</h2>
      <div v-if="loading" class="sidebar-empty">加载中...</div>
      <button
        v-for="chat in chats"
        :key="chat.id"
        class="sidebar-chat"
        :class="{ active: chat.id === activeChatId }"
        type="button"
        :title="chat.title"
        @click="$emit('select-chat', chat.id)"
      >
        {{ chat.title }}
      </button>
      <div v-if="!loading && chats.length === 0" class="sidebar-empty">暂无历史会话</div>
    </section>
  </aside>
</template>

<style scoped>
.chat-sidebar {
  display: flex;
  min-height: 0;
  flex-direction: column;
  padding: 12px 8px;
  overflow-y: auto;
  background: #f9f9f9;
  border-right: 1px solid #ececec;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px 14px;
}

.sidebar-header strong {
  font-size: 19px;
  font-weight: 700;
  letter-spacing: 0;
}

.sidebar-action,
.sidebar-chat {
  border: 0;
  background: transparent;
}

.sidebar-nav,
.sidebar-section {
  display: grid;
  gap: 4px;
}

.sidebar-action,
.sidebar-chat {
  display: flex;
  min-height: 40px;
  align-items: center;
  gap: 12px;
  padding: 9px 12px;
  color: #202123;
  text-align: left;
  border-radius: 8px;
  font-size: 14px;
}

.sidebar-action span {
  width: 18px;
  color: #111827;
  font-size: 18px;
  line-height: 1;
}

.sidebar-action:hover,
.sidebar-chat:hover,
.sidebar-chat.active {
  background: #ececec;
}

.sidebar-section {
  margin-top: 28px;
}

.sidebar-section h2 {
  margin: 0 8px 6px;
  color: #5f6368;
  font-size: 13px;
  font-weight: 700;
}

.sidebar-chat {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-empty {
  padding: 8px 12px;
  color: #8a8f98;
  font-size: 13px;
}
</style>
