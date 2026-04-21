import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Conversation } from '../api'

export const useAppStore = defineStore('app', () => {
  const conversations = ref<Conversation[]>([])
  const activeConversationId = ref<string | null>(null)
  const totalChunks = ref(0)
  const highlightFile = ref<string | null>(null)

  const activeConversation = computed(() =>
    conversations.value.find((c) => c.id === activeConversationId.value) ?? null,
  )

  function setConversations(list: Conversation[]) {
    conversations.value = list
  }

  function addConversation(conv: Conversation) {
    conversations.value.unshift(conv)
  }

  function removeConversation(id: string) {
    conversations.value = conversations.value.filter((c) => c.id !== id)
    if (activeConversationId.value === id) {
      activeConversationId.value = conversations.value[0]?.id ?? null
    }
  }

  function setActive(id: string | null) {
    activeConversationId.value = id
  }

  function setTotalChunks(n: number) {
    totalChunks.value = n
  }

  function setHighlightFile(source: string | null) {
    highlightFile.value = source
  }

  return {
    conversations,
    activeConversationId,
    activeConversation,
    totalChunks,
    highlightFile,
    setConversations,
    addConversation,
    removeConversation,
    setActive,
    setTotalChunks,
    setHighlightFile,
  }
})
