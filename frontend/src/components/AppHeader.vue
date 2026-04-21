<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/stores/session'
import { MessageOutlined, DatabaseOutlined } from '@ant-design/icons-vue'

const store = useAppStore()
const route = useRoute()
const router = useRouter()

const selectedKeys = computed(() => [route.name as string])

function onMenuClick({ key }: { key: string }) {
  if (key === 'chat') router.push('/')
  else if (key === 'files') router.push('/files')
}
</script>

<template>
  <a-layout-header class="header">
    <div class="header-left">
      <div class="logo">📚 RAG 知识问答系统</div>
      <a-menu
        mode="horizontal"
        :selected-keys="selectedKeys"
        class="header-menu"
        @click="onMenuClick"
      >
        <a-menu-item key="chat">
          <template #icon><MessageOutlined /></template>
          问答
        </a-menu-item>
        <a-menu-item key="files">
          <template #icon><DatabaseOutlined /></template>
          文件管理
        </a-menu-item>
      </a-menu>
    </div>
    <a-space>
      <a-tag :color="store.totalChunks > 0 ? 'success' : 'default'">
        知识库: {{ store.totalChunks }} 块
      </a-tag>
      <a-tag color="purple">
        {{ store.conversations.length }} 个对话
      </a-tag>
    </a-space>
  </a-layout-header>
</template>

<style scoped>
.header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 56px;
  line-height: 56px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.logo {
  color: white;
  font-size: 18px;
  font-weight: 600;
  white-space: nowrap;
}

.header-menu {
  background: transparent !important;
  border-bottom: none !important;
  line-height: 56px;
}

.header-menu :deep(.ant-menu-item) {
  color: rgba(255, 255, 255, 0.75) !important;
}

.header-menu :deep(.ant-menu-item:hover),
.header-menu :deep(.ant-menu-item-selected) {
  color: white !important;
}

.header-menu :deep(.ant-menu-item-selected::after),
.header-menu :deep(.ant-menu-item:hover::after) {
  border-bottom-color: white !important;
}

.header :deep(.ant-tag) {
  margin: 0;
}
</style>
