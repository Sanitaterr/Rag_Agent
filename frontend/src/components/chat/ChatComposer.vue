<script setup>
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  modelValue: {
    type: String,
    default: '',
  },
  busy: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['update:modelValue', 'send'])

const textareaRef = ref(null)
const focused = ref(false)

const canSend = computed(() => props.modelValue.trim().length > 0 && !props.busy)
const messageLength = computed(() => props.modelValue.trim().length)

watch(() => props.modelValue, async () => {
  await nextTick()
  resizeTextarea()
})

function updateValue(event) {
  emit('update:modelValue', event.target.value)
  resizeTextarea()
}

function onEnter(event) {
  if (event.isComposing || !canSend.value) return
  emit('send')
}

function submit() {
  if (!canSend.value) return
  emit('send')
}

function resizeTextarea() {
  const textarea = textareaRef.value
  if (!textarea) return

  // 让输入框随内容平滑增高，同时限制最大高度，避免挤压消息区。
  textarea.style.height = '0px'
  textarea.style.height = `${Math.min(textarea.scrollHeight, 168)}px`
}
</script>

<template>
  <form
    class="composer ui-panel"
    :class="{ 'is-focused': focused, 'is-busy': busy }"
    @submit.prevent="submit"
  >
    <div class="composer-top">
      <span class="composer-badge">RAG Agent</span>
      <span v-if="busy" class="composer-status">
        <span aria-hidden="true"></span>
        正在生成
      </span>
      <span v-else-if="messageLength" class="composer-count">{{ messageLength }} 字</span>
    </div>

    <div class="composer-main">
      <textarea
        ref="textareaRef"
        :value="modelValue"
        rows="1"
        placeholder="输入问题，Enter 发送，Shift + Enter 换行"
        @focus="focused = true"
        @blur="focused = false"
        @input="updateValue"
        @keydown.enter.exact.prevent="onEnter"
      ></textarea>

      <button
        class="composer-send btn btn-circle focus-ring"
        type="submit"
        :disabled="!canSend"
        aria-label="发送消息"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4.6 11.2 18.1 4c.9-.5 1.9.4 1.5 1.4l-5.4 14c-.4 1-1.8 1-2.1 0l-2-5.2-5.3-2c-1-.4-1.1-1.7-.2-2.2Zm2.1-.2 4.2 1.6 1.6 4.2 4.1-10.6L6.7 11Z" />
        </svg>
      </button>
    </div>
  </form>
</template>

<style scoped>
.composer {
  position: relative;
  width: min(880px, calc(100% - 48px));
  min-height: 78px;
  padding: 12px 12px 12px 16px;
  overflow: hidden;
  border-radius: 24px;
  animation: float-in 420ms var(--agent-ease-soft) both;
}

.composer::before {
  position: absolute;
  inset: 0;
  pointer-events: none;
  content: "";
  background:
    linear-gradient(90deg, rgba(20, 184, 166, 0), rgba(20, 184, 166, 0.16), rgba(20, 184, 166, 0)),
    radial-gradient(circle at 82% 16%, rgba(59, 130, 246, 0.12), transparent 34%);
  opacity: 0;
  transform: translateX(-40%);
  transition: opacity 220ms var(--agent-ease);
}

.composer.is-focused {
  border-color: rgba(20, 184, 166, 0.55);
  box-shadow:
    0 0 0 4px rgba(20, 184, 166, 0.10),
    0 22px 62px rgba(15, 23, 42, 0.15);
  transform: translateY(-1px);
}

.composer.is-focused::before {
  opacity: 1;
  animation: rail-shimmer 1500ms var(--agent-ease) infinite;
}

.composer-top,
.composer-main {
  position: relative;
  z-index: 1;
}

.composer-top {
  display: flex;
  min-height: 18px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.composer-badge,
.composer-count,
.composer-status {
  color: #64748b;
  font-size: 12px;
  line-height: 1;
}

.composer-badge {
  color: #0f766e;
  font-weight: 700;
}

.composer-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.composer-status span {
  width: 7px;
  height: 7px;
  background: #14b8a6;
  border-radius: 999px;
  animation: soft-pulse 1100ms var(--agent-ease) infinite;
}

.composer-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 42px;
  align-items: end;
  gap: 10px;
}

.composer textarea {
  width: 100%;
  min-height: 30px;
  max-height: 168px;
  padding: 4px 0 5px;
  resize: none;
  color: #172033;
  background: transparent;
  border: 0;
  outline: none;
  font-size: 15px;
  line-height: 1.55;
}

.composer textarea::placeholder {
  color: #94a3b8;
}

.composer-send {
  width: 42px;
  height: 42px;
  min-height: 42px;
  color: #ffffff;
  background: linear-gradient(135deg, #0f766e, #2563eb);
  border: 0;
  box-shadow: 0 12px 28px rgba(20, 184, 166, 0.24);
  transition:
    transform 180ms var(--agent-ease),
    opacity 180ms var(--agent-ease),
    box-shadow 180ms var(--agent-ease),
    filter 180ms var(--agent-ease);
}

.composer-send:hover:not(:disabled) {
  box-shadow: 0 16px 34px rgba(37, 99, 235, 0.24);
  filter: saturate(1.08);
  transform: translateY(-1px);
}

.composer-send:active:not(:disabled) {
  transform: translateY(0) scale(0.96);
}

.composer-send:disabled {
  color: #94a3b8;
  background: #e2e8f0;
  box-shadow: none;
}

.composer-send svg {
  width: 20px;
  height: 20px;
  fill: currentColor;
}

@media (max-width: 900px) {
  .composer {
    width: calc(100% - 32px);
  }
}
</style>
