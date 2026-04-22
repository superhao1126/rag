<script setup lang="ts">
import { ref, computed, onMounted, watch, reactive } from "vue";
import { Modal } from "ant-design-vue";
import {
  ReloadOutlined,
  DeleteOutlined,
  CloudUploadOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  LoadingOutlined,
  BarChartOutlined,
  InfoCircleOutlined,
} from "@ant-design/icons-vue";
import api from "@/api";
import type { KbFile, KbFileInfo } from "@/api";
import { useToast } from "@/composables/useToast";
import { useAppStore } from "@/stores/session";
import ChunkModal from "@/components/ChunkModal.vue";

const toast = useToast();
const store = useAppStore();
const chunkModalRef = ref<InstanceType<typeof ChunkModal>>();

function openChunkModal(record: KbFile) {
  chunkModalRef.value?.open({ source: record.source, fileId: record.id });
}

const infoVisible = ref(false);
const infoLoading = ref(false);
const infoData = ref<KbFileInfo | null>(null);

async function openInfo(record: KbFile) {
  infoVisible.value = true;
  infoLoading.value = true;
  infoData.value = null;
  try {
    const res = await api.getFileInfo(record.id);
    infoData.value = res.data;
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "获取文件详情失败";
    toast.show(msg, "error");
    infoVisible.value = false;
  } finally {
    infoLoading.value = false;
  }
}

const highlightSource = ref<string | null>(store.highlightFile);

watch(
  () => store.highlightFile,
  (v) => {
    if (v) {
      highlightSource.value = v;
      store.setHighlightFile(null);
    }
  },
  { immediate: true },
);

function isHighlighted(source: string) {
  return source === highlightSource.value;
}

const files = ref<KbFile[]>([]);
const loading = ref(false);

// 服务端分页状态
const pagination = reactive({
  current: 1,
  pageSize: 20,
  total: 0,
});
const keyword = ref("");

interface UploadTask {
  id: string;
  filename: string;
  status: "uploading" | "done" | "error";
  percent: number;
  message: string;
}
const tasks = ref<UploadTask[]>([]);

const totalChunks = computed(() => files.value.reduce((s, f) => s + f.chunk_count, 0));

async function loadFiles() {
  loading.value = true;
  try {
    const res = await api.getFiles({
      page: pagination.current,
      page_size: pagination.pageSize,
      keyword: keyword.value.trim(),
    });
    files.value = res.data.files;
    pagination.total = res.data.total;
    // 当前页超出总页数（删除最后一条后等情况），自动回到上一页
    const maxPage = Math.max(1, Math.ceil(pagination.total / pagination.pageSize));
    if (pagination.current > maxPage) {
      pagination.current = maxPage;
      await loadFiles();
      return;
    }
    store.setTotalChunks(totalChunks.value);
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } }; message?: string };
    toast.show(err.response?.data?.detail ?? err.message ?? "加载失败", "error");
  } finally {
    loading.value = false;
  }
}

function onTableChange(p: { current?: number; pageSize?: number }) {
  if (p.pageSize && p.pageSize !== pagination.pageSize) {
    pagination.pageSize = p.pageSize;
    pagination.current = 1;
  } else if (p.current) {
    pagination.current = p.current;
  }
  loadFiles();
}

let searchTimer: number | null = null;
function onKeywordChange() {
  if (searchTimer) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    pagination.current = 1;
    loadFiles();
  }, 300);
}

function fileIcon(name: string) {
  const n = name.toLowerCase();
  if (n.endsWith(".pdf")) return "📕";
  if (n.endsWith(".md")) return "📘";
  if (n.endsWith(".docx")) return "📝";
  if (n.endsWith(".xlsx") || n.endsWith(".xls")) return "📊";
  if (/\.(png|jpe?g|bmp|tiff?|webp)$/.test(n)) return "🖼️";
  return "📄";
}

function formatDate(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatChars(n: number | null): string {
  if (n == null) return "-";
  return n > 10000 ? (n / 10000).toFixed(1) + " 万字" : n + " 字";
}

async function uploadFile(file: File) {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  const ALLOWED = ["txt","md","pdf","docx","xlsx","xls","png","jpg","jpeg","bmp","tif","tiff","webp"];
  if (!ALLOWED.includes(ext)) {
    toast.show(`不支持的格式: .${ext}`, "error");
    return;
  }

  const task = reactive<UploadTask>({
    id: Math.random().toString(36).slice(2),
    filename: file.name,
    status: "uploading",
    percent: 0,
    message: "准备上传…",
  });
  tasks.value.push(task);

  try {
    const resp = await api.uploadStream(file);
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        try {
          const evt = JSON.parse(line.slice(5).trim());
          if (evt.type === "progress") {
            task.percent = evt.percent ?? task.percent;
            task.message = evt.message ?? task.message;
          } else if (evt.type === "done") {
            task.status = "done";
            task.percent = 100;
            task.message = `完成 · ${evt.chunk_count} 块 · ${formatChars(evt.total_chars)}`;
            await loadFiles();
          } else if (evt.type === "error") {
            task.status = "error";
            task.message = evt.message ?? "处理失败";
          }
        } catch {
          /* ignore */
        }
      }
    }
    if (task.status === "uploading") {
      task.status = "done";
      task.percent = 100;
      task.message = "完成";
      await loadFiles();
    }
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } }; message?: string };
    task.status = "error";
    task.message = err.response?.data?.detail ?? err.message ?? "上传失败";
  }
}

function handleCustomRequest(options: { file: File }) {
  uploadFile(options.file);
}

function beforeUpload(file: File): boolean {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!["txt", "md", "pdf", "docx", "xlsx", "xls"].includes(ext)) {
    toast.show(`不支持的格式: .${ext}`, "error");
    return false; // 仅对不支持格式返回 false，阻止上传
  }
  return true; // 合法文件放行到 custom-request
}

function dismissTask(id: string) {
  tasks.value = tasks.value.filter((t) => t.id !== id);
}

function confirmDelete(record: KbFile) {
  Modal.confirm({
    title: "确认删除",
    content: `删除「${record.source}」及其 ${record.chunk_count} 个文本块？此操作不可撤销。`,
    okText: "删除",
    okType: "danger",
    cancelText: "取消",
    async onOk() {
      try {
        await api.deleteFileById(record.id);
        toast.show(`已删除 ${record.source}`, "success");
        selectedRowKeys.value = selectedRowKeys.value.filter((k) => k !== record.id);
        await loadFiles();
      } catch (e: unknown) {
        const err = e as { response?: { data?: { detail?: string } }; message?: string };
        toast.show(err.response?.data?.detail ?? err.message ?? "删除失败", "error");
      }
    },
  });
}

// ── 批量勾选删除（按 id） ──
const selectedRowKeys = ref<string[]>([]);
const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (string | number)[]) => {
    selectedRowKeys.value = keys.map((k) => String(k));
  },
  preserveSelectedRowKeys: true,
}));

function clearSelection() {
  selectedRowKeys.value = [];
}

async function batchDelete() {
  const ids = [...selectedRowKeys.value];
  if (!ids.length) return;
  Modal.confirm({
    title: `确认批量删除 ${ids.length} 个文件`,
    content: `将删除选中的 ${ids.length} 个文件及其全部文本块，此操作不可撤销。`,
    okText: "全部删除",
    okType: "danger",
    cancelText: "取消",
    async onOk() {
      try {
        const res = await api.batchDeleteFiles({ ids });
        const { deleted = [], missing = [] } = res.data;
        toast.show(
          `成功删除 ${deleted.length} 个${missing.length ? `，${missing.length} 个未找到` : ""}`,
          deleted.length ? "success" : "error",
        );
        selectedRowKeys.value = [];
        await loadFiles();
      } catch (e: unknown) {
        const err = e as { response?: { data?: { detail?: string } }; message?: string };
        toast.show(err.response?.data?.detail ?? err.message ?? "批量删除失败", "error");
      }
    },
  });
}

const columns = [
  { title: "文件名", key: "source", ellipsis: true },
  {
    title: "文本块数",
    dataIndex: "chunk_count",
    key: "chunk_count",
    width: 100,
    align: "center" as const,
  },
  { title: "总字符数", key: "total_chars", width: 120, align: "center" as const },
  { title: "上传时间", key: "uploaded_at", width: 180 },
  { title: "操作", key: "action", width: 160, align: "center" as const },
];

onMounted(async () => {
  await loadFiles();
  if (highlightSource.value) {
    await new Promise((r) => setTimeout(r, 100));
    const el = document.querySelector(".highlighted-row") as HTMLElement | null;
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
});
</script>

<template>
  <div class="db-files-page">
    <!-- 上传区 -->
    <a-card title="📤 上传文件">
      <a-upload-dragger
        accept=".txt,.md,.pdf,.docx,.xlsx,.xls,.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp"
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
        <p class="ant-upload-hint">
          支持 .txt · .md · .pdf · .docx · .xlsx · .xls · 图片(jpg/png/bmp/tif/webp)，扫描件 PDF 与图片自动 OCR 识别
        </p>
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
                <CheckCircleFilled
                  v-if="t.status === 'done'"
                  style="color: #10b981; font-size: 20px"
                />
                <CloseCircleFilled
                  v-else-if="t.status === 'error'"
                  style="color: #ef4444; font-size: 20px"
                />
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
                >关闭</a-button
              >
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
        <a-input-search
          v-model:value="keyword"
          placeholder="搜索文件名"
          allow-clear
          style="width: 220px; margin-right: 8px"
          @input="onKeywordChange"
          @search="onKeywordChange"
        />
        <a-button
          v-if="selectedRowKeys.length"
          danger
          style="margin-right: 8px"
          @click="batchDelete"
        >
          <template #icon><DeleteOutlined /></template>
          批量删除 ({{ selectedRowKeys.length }})
        </a-button>
        <a-button
          v-if="selectedRowKeys.length"
          style="margin-right: 8px"
          @click="clearSelection"
        >
          清空选择
        </a-button>
        <a-button :loading="loading" @click="loadFiles">
          <template #icon><ReloadOutlined /></template>
          刷新
        </a-button>
      </template>

      <a-table
        :columns="columns"
        :data-source="files"
        :loading="loading"
        :row-selection="rowSelection"
        :pagination="{
          current: pagination.current,
          pageSize: pagination.pageSize,
          total: pagination.total,
          showSizeChanger: true,
          pageSizeOptions: ['10', '20', '50', '100'],
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        @change="onTableChange"
        row-key="id"
        size="middle"
        :row-class-name="
          (record: KbFile) => (isHighlighted(record.source) ? 'highlighted-row' : '')
        "
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
            <a-tooltip title="文件详情">
              <a-button
                type="text"
                size="small"
                style="color: #0ea5e9"
                @click="openInfo(record)"
              >
                <template #icon><InfoCircleOutlined /></template>
              </a-button>
            </a-tooltip>
            <a-tooltip title="分块可视化">
              <a-button
                type="text"
                size="small"
                style="color: #6366f1"
                @click="openChunkModal(record)"
              >
                <template #icon><BarChartOutlined /></template>
              </a-button>
            </a-tooltip>
            <a-tooltip title="删除">
              <a-button type="text" danger size="small" @click="confirmDelete(record)">
                <template #icon><DeleteOutlined /></template>
              </a-button>
            </a-tooltip>
          </template>
        </template>

        <template #emptyText>
          <a-empty description="知识库为空，请先上传文件" />
        </template>
      </a-table>
    </a-card>

    <ChunkModal ref="chunkModalRef" />

    <a-modal
      v-model:open="infoVisible"
      title="文件详情"
      :footer="null"
      width="560px"
      destroy-on-close
    >
      <a-spin :spinning="infoLoading">
        <a-descriptions
          v-if="infoData"
          :column="1"
          bordered
          size="small"
          :label-style="{ width: '120px', background: 'var(--bg-secondary)' }"
        >
          <a-descriptions-item label="文件名">
            <span style="word-break: break-all">{{ infoData.source }}</span>
          </a-descriptions-item>
          <a-descriptions-item label="文件 ID">
            <code>{{ infoData.id }}</code>
          </a-descriptions-item>
          <a-descriptions-item label="文本块数">{{ infoData.chunk_count }}</a-descriptions-item>
          <a-descriptions-item label="总字符数">
            {{ formatChars(infoData.total_chars) }}
          </a-descriptions-item>
          <a-descriptions-item label="块字符长度">
            最小 {{ infoData.min_chars }} / 最大 {{ infoData.max_chars }} / 平均
            {{ infoData.avg_chars }}
          </a-descriptions-item>
          <a-descriptions-item label="块索引范围">
            {{ infoData.chunk_index_range[0] }} ~ {{ infoData.chunk_index_range[1] }}
          </a-descriptions-item>
          <a-descriptions-item label="首次上传">
            {{ formatDate(infoData.uploaded_at) }}
          </a-descriptions-item>
          <a-descriptions-item label="最近更新">
            {{ formatDate(infoData.last_updated_at) }}
          </a-descriptions-item>
        </a-descriptions>
      </a-spin>
    </a-modal>
  </div>
</template>

<style scoped>
.db-files-page {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  background: var(--bg-chat);
  display: flex;
  flex-direction: column;
  gap: 16px;
  transition: background 0.25s ease;
}

.upload-dragger :deep(.ant-upload-drag-icon) {
  margin-bottom: 8px;
}

.task-item {
  border-left: 3px solid var(--border);
  margin-bottom: 6px;
  border-radius: 6px;
  background: var(--bg-card);
  padding: 4px 12px !important;
  transition: background 0.25s ease;
}
.task-item.uploading {
  border-left-color: #6366f1;
}
.task-item.done {
  border-left-color: #10b981;
}
.task-item.error {
  border-left-color: #ef4444;
}
</style>

<style>
/* ── 高亮行（全局，antd 表格行不在 scoped 内）── */
.highlighted-row td {
  background: #eef2ff !important;
  animation: highlight-fade 2s ease forwards;
}
@keyframes highlight-fade {
  0% {
    background: #c7d2fe !important;
  }
  100% {
    background: #eef2ff !important;
  }
}

[data-theme="dark"] .highlighted-row td {
  background: rgba(99, 102, 241, 0.2) !important;
  animation: highlight-fade-dark 2s ease forwards;
}
@keyframes highlight-fade-dark {
  0% {
    background: rgba(99, 102, 241, 0.45) !important;
  }
  100% {
    background: rgba(99, 102, 241, 0.2) !important;
  }
}

/* ── antd 组件深色主题覆盖 ── */
[data-theme="dark"] .db-files-page .ant-card {
  background: #161b2e;
  border-color: rgba(255, 255, 255, 0.07);
}
[data-theme="dark"] .db-files-page .ant-card-head {
  background: #111827;
  border-bottom-color: rgba(255, 255, 255, 0.07);
  color: #e2e8f0;
}
[data-theme="dark"] .db-files-page .ant-card-head-title {
  color: #e2e8f0;
}
[data-theme="dark"] .db-files-page .ant-card-body {
  background: #161b2e;
}

[data-theme="dark"] .db-files-page .ant-upload-drag {
  background: rgba(99, 102, 241, 0.05);
  border-color: rgba(99, 102, 241, 0.3);
}
[data-theme="dark"] .db-files-page .ant-upload-drag:hover {
  border-color: rgba(99, 102, 241, 0.6);
}
[data-theme="dark"] .db-files-page .ant-upload-text {
  color: #e2e8f0 !important;
}
[data-theme="dark"] .db-files-page .ant-upload-hint {
  color: #94a3b8 !important;
}

[data-theme="dark"] .db-files-page .ant-list-item {
  background: #1a1f2e;
  border-color: rgba(255, 255, 255, 0.06);
}
[data-theme="dark"] .db-files-page .ant-list-item-meta-title {
  color: #e2e8f0;
}
[data-theme="dark"] .db-files-page .ant-list-item-meta-description {
  color: #94a3b8;
}

[data-theme="dark"] .db-files-page .ant-table {
  background: #161b2e;
  color: #e2e8f0;
}
[data-theme="dark"] .db-files-page .ant-table-thead > tr > th {
  background: #111827 !important;
  color: #94a3b8;
  border-bottom-color: rgba(255, 255, 255, 0.08) !important;
}
[data-theme="dark"] .db-files-page .ant-table-tbody > tr > td {
  background: #161b2e;
  color: #e2e8f0;
  border-bottom-color: rgba(255, 255, 255, 0.05);
}
[data-theme="dark"] .db-files-page .ant-table-tbody > tr:hover > td {
  background: rgba(99, 102, 241, 0.08) !important;
}
[data-theme="dark"] .db-files-page .ant-table-tbody > tr.ant-table-row-selected > td {
  background: rgba(99, 102, 241, 0.15) !important;
}

[data-theme="dark"] .db-files-page .ant-pagination-item a {
  color: #94a3b8;
}
[data-theme="dark"] .db-files-page .ant-pagination-item-active {
  background: rgba(99, 102, 241, 0.15);
  border-color: #6366f1;
}
[data-theme="dark"] .db-files-page .ant-pagination-item-active a {
  color: #a5b4fc;
}
[data-theme="dark"] .db-files-page .ant-pagination-prev button,
[data-theme="dark"] .db-files-page .ant-pagination-next button {
  color: #94a3b8;
  background: transparent;
  border-color: rgba(255, 255, 255, 0.1);
}
[data-theme="dark"] .db-files-page .ant-select-selector {
  background: #1a1f2e !important;
  border-color: rgba(255, 255, 255, 0.1) !important;
  color: #e2e8f0 !important;
}
[data-theme="dark"] .db-files-page .ant-select-arrow {
  color: #64748b;
}
[data-theme="dark"] .db-files-page .ant-select-dropdown {
  background: #1a1f2e;
  border-color: rgba(255, 255, 255, 0.1);
}
[data-theme="dark"] .db-files-page .ant-select-item {
  color: #e2e8f0;
}
[data-theme="dark"] .db-files-page .ant-select-item-option-active {
  background: rgba(99, 102, 241, 0.1);
}

[data-theme="dark"] .db-files-page .ant-empty-description {
  color: #4b5563;
}
[data-theme="dark"] .db-files-page .ant-empty-image path {
  fill: #1e2a3a;
  stroke: #374151;
}

[data-theme="dark"] .db-files-page .ant-btn-text {
  color: #94a3b8;
}
[data-theme="dark"] .db-files-page .ant-btn-text:hover {
  background: rgba(255, 255, 255, 0.06);
  color: #e2e8f0;
}
[data-theme="dark"] .db-files-page .ant-btn-default {
  background: #1a1f2e;
  border-color: rgba(255, 255, 255, 0.1);
  color: #e2e8f0;
}
[data-theme="dark"] .db-files-page .ant-btn-default:hover {
  border-color: rgba(99, 102, 241, 0.5);
  color: #a5b4fc;
}
</style>
