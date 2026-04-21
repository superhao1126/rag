<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { Modal } from 'ant-design-vue'
import {
  ReloadOutlined,
  DeleteOutlined,
  CloudUploadOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  LoadingOutlined,
} from '@ant-design/icons-vue'
import api from '@/api'
import type { KbFile } from '@/api'
import { useToast } from '@/composables/useToast'
import { useAppStore } from '@/stores/session'

const toast = useToast()
const store = useAppStore()

const highlightSource = ref<string | null>(store.highlightFile)

watch(
  () => store.highlightFile,
  (v) => { if (v) { highlightSource.value = v; store.setHighlightFile(null) } },
  { immediate: true },
)

function isHighlighted(source: string) {
  return source === highlightSource.value
}

const files = ref<KbFile[]>([])
const loading = ref(false)

interface UploadTask {
  id: string
  filename: string
  status: 'uploading' | 'done' | 'error'
  percent: number
  message: string
}
const tasks = ref<UploadTask[]>([])

const totalChunks = computed(() => files.value.reduce((s, f) => s + f.chunk_count, 0))

async function loadFiles() {
  loading.value = true
  try {
    const res = await api.getFiles()
    files.value = res.data.files
    store.setTotalChunks(totalChunks.value)
  } catch (e: any) {
    toast.show(e.response?.data?.detail ?? e.message ?? '加载失败', 'error')
  } finally {
    loading.value = false
  }
}

function fileIcon(name: string) {
  if (name.endsWith('.pdf')) return '📕'
  if (name.endsWith('.md'))  return '📘'
  return '📄'
}

function formatDate(iso: string | null): string {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

function formatChars(n: number | null): string {
  if (n == null) return '-'
  return n > 10000 ? (n / 10000).toFixed(1) + ' 万字' : n + ' 字'
}

async function uploadFile(file: File) {
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!['txt', 'md', 'pdf'].includes(ext)) {
    toast.show(`不支持的格式: .${ext}`, 'error')
    return
  }

  const task: UploadTask = {
    id: Math.random().toString(36).slice(2),
    filename: file.name,
    status: 'uploading',
    percent: 0,
    message: '准备上传…',
  }
  tasks.value.push(task)

  try {
    const resp = await api.uploadStream(file)
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`)

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const parts = buf.split('\n\n')
      buf = parts.pop() ?? ''
      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data:')) continue
        try {
          const evt = JSON.parse(line.slice(5).trim())
          if (evt.type === 'progress') {
            task.percent = evt.percent ?? task.percent
            task.message = evt.message ?? task.message
          } else if (evt.type === 'done') {
            task.status = 'done'
            task.percent = 100
            task.message = `完成 · ${evt.chunk_count} 块 · ${formatChars(evt.total_chars)}`
            await loadFiles()
          } else if (evt.type === 'error') {
            task.status = 'error'
            task.message = evt.message ?? '处理失败'
          }
        } catch { /* ignore */ }
      }
    }
    if (task.status === 'uploading') {
      task.status = 'done'
      task.percent = 100
      task.message = '完成'
      await loadFiles()
    }
  } catch (e: any) {
    task.status = 'error'
    task.message = e.message ?? '上传失败'
  }
}

function handleCustomRequest(options: any) {
  uploadFile(options.file as File)
}

function beforeUpload(_file: File): boolean {
  return false // 阻止 antd 自动上传，由 custom-request 处理
}

function dismissTask(id: string) {
  tasks.value = tasks.value.filter(t => t.id !== id)
}

function confirmDelete(record: KbFile) {
  Modal.confirm({
    title: '确认删除',
    content: `删除「${record.source}」及其 ${record.chunk_count} 个文本块？此操作不可撤销。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await api.deleteFile(record.source)
        toast.show(`已删除 ${record.source}`, 'success')
        await loadFiles()
      } catch (e: any) {
        toast.show(e.response?.data?.detail ?? '删除失败', 'error')
      }
    },
  })
}

const columns = [
  { title: '文件名', key: 'source', ellipsis: true },
  { title: '文本块数', dataIndex: 'chunk_count', key: 'chunk_count', width: 100, align: 'center' as const },
  { title: '总字符数', key: 'total_chars', width: 120, align: 'center' as const },
  { title: '上传时间', key: 'uploaded_at', width: 180 },
  { title: '操作', key: 'action', width: 80, align: 'center' as const },
]

onMounted(async () => {
  await loadFiles()
  if (highlightSource.value) {
    await new Promise(r => setTimeout(r, 100))
    const el = document.querySelector('.highlighted-row') as HTMLElement | null
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
})
</script>

<template>
  <div class="db-files-page">

    <!-- 上传区 -->
    <a-card title="📤 上传文件">
      <a-upload-dragger
        accept=".txt,.md,.pdf"
        :multiple="true"
        :show-upload-list="false"
        :before-upload="beforeUpload"
        :custom-request="handleCustomRequest"
        class="upload-dragger"
      >
        <p class="ant-upload-drag-icon">
          <CloudUploadOutlined style="color: #6366f1; font-size: 40px" />
        </p>
        <p class="ant-upload-text">点击或拖拽文件到此处上传</p>
        <p class="ant-upload-hint">支持 .txt · .md · .pdf，文件将自动向量化入库</p>
      </a-upload-dragger>

      <!-- 上传任务进度列表 -->
      <a-list
        v-if="tasks.length"
        style="margin-top: 16px"
        :data-source="tasks"
        :split="false"
        size="small"
      >
        <template #renderItem="{ item: t }">
          <a-list-item class="task-item" :class="t.status">
            <a-list-item-meta>
              <template #avatar>
                <CheckCircleFilled v-if="t.status === 'done'" style="color: #10b981; font-size: 20px" />
                <CloseCircleFilled v-else-if="t.status === 'error'" style="color: #ef4444; font-size: 20px" />
                <LoadingOutlined v-else style="color: #6366f1; font-size: 20px" />
              </template>
              <template #title>{{ t.filename }}</template>
              <template #description>
                <div>{{ t.message }}</div>
                <a-progress
                  v-if="t.status === 'uploading'"
                  :percent="t.percent"
                  size="small"
                  :show-info="false"
                  stroke-color="#6366f1"
                  style="margin-top: 4px"
                />
              </template>
            </a-list-item-meta>
            <template #actions>
              <a-button
                v-if="t.status !== 'uploading'"
                type="text"
                size="small"
                @click="dismissTask(t.id)"
              >关闭</a-button>
            </template>
          </a-list-item>
        </template>
      </a-list>
    </a-card>

    <!-- 文件列表 -->
    <a-card>
      <template #title>
        🗄️ 知识库文件
        <a-tag v-if="files.length" color="blue" style="margin-left: 8px">
          {{ files.length }} 文件 · {{ totalChunks }} 块
        </a-tag>
      </template>
      <template #extra>
        <a-button :loading="loading" @click="loadFiles">
          <template #icon><ReloadOutlined /></template>
          刷新
        </a-button>
      </template>

      <a-table
        :columns="columns"
        :data-source="files"
        :loading="loading"
        :pagination="{ pageSize: 20, showSizeChanger: true, showTotal: (t: number) => `共 ${t} 条` }"
        row-key="source"
        size="middle"
        :row-class-name="(record: KbFile) => isHighlighted(record.source) ? 'highlighted-row' : ''"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'source'">
            <span>{{ fileIcon(record.source) }} {{ record.source }}</span>
          </template>
          <template v-else-if="column.key === 'total_chars'">
            {{ formatChars(record.total_chars) }}
          </template>
          <template v-else-if="column.key === 'uploaded_at'">
            {{ formatDate(record.uploaded_at) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <a-button type="text" danger size="small" @click="confirmDelete(record)">
              <template #icon><DeleteOutlined /></template>
            </a-button>
          </template>
        </template>

        <template #emptyText>
          <a-empty description="知识库为空，请先上传文件" />
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<style scoped>
.db-files-page {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  background: #f5f6f8;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.upload-dragger :deep(.ant-upload-drag-icon) {
  margin-bottom: 8px;
}

.task-item {
  border-left: 3px solid #e5e7eb;
  margin-bottom: 6px;
  border-radius: 6px;
  background: #fafafa;
  padding: 4px 12px !important;
}
.task-item.uploading { border-left-color: #6366f1; }
.task-item.done      { border-left-color: #10b981; }
.task-item.error     { border-left-color: #ef4444; }
</style>

<style>
/* 高亮行（全局，因为 antd 表格行不在 scoped 内） */
.highlighted-row td {
  background: #eef2ff !important;
  animation: highlight-fade 2s ease forwards;
}
@keyframes highlight-fade {
  0%   { background: #c7d2fe !important; }
  100% { background: #eef2ff !important; }
}
</style>
