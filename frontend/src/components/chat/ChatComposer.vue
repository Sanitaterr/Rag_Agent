<script setup>
defineProps({
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

function updateValue(event) {
  emit('update:modelValue', event.target.value)
}

function onEnter(event) {
  if (event.isComposing) return
  emit('send')
}
</script>

<template>
  <form class="composer" @submit.prevent="$emit('send')">
    <textarea
      :value="modelValue"
      rows="1"
      placeholder="输入消息"
      @input="updateValue"
      @keydown.enter.exact.prevent="onEnter"
    ></textarea>
    <button class="composer-send" type="submit" :disabled="!modelValue.trim() || busy">
      发送
    </button>
  </form>
</template>

<style scoped>
.composer {
  display: grid;
  width: min(820px, calc(100vw - 340px));
  min-height: 58px;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  padding: 8px 10px 8px 16px;
  background: #ffffff;
  border: 1px solid #dedede;
  border-radius: 30px;
  box-shadow:
    0 1px 2px rgba(0, 0, 0, 0.04),
    0 10px 32px rgba(0, 0, 0, 0.10);
}

.composer textarea {
  width: 100%;
  min-height: 38px;
  max-height: 140px;
  padding: 9px 4px;
  resize: none;
  color: #202123;
  background: transparent;
  border: 0;
  outline: none;
  font-size: 14px;
  line-height: 1.45;
}

.composer textarea::placeholder {
  color: #8a8f98;
}

.composer-send {
  height: 36px;
  min-width: 58px;
  padding: 0 14px;
  color: #ffffff;
  background: #202123;
  border: 0;
  border-radius: 999px;
  font-size: 13px;
}

.composer-send:disabled {
  width: 36px;
  min-width: 36px;
  padding: 0;
  color: transparent;
  background: #e5e5e5;
}

@media (max-width: 900px) {
  .composer {
    width: calc(100vw - 32px);
  }
}
</style>
