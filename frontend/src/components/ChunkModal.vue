<script setup lang="ts">
import { ref } from "vue";
import api from "@/api";
import type { ChunkSource } from "@/api";
import { useToast } from "@/composables/useToast";
import ChunkSourcePanel from "./ChunkSourcePanel.vue";

const toast = useToast();

const visible = ref(false);
const loading = ref(false);
const chunkData = ref<Record<string, ChunkSource>>({});
const activeTab = ref("");
const singleSource = ref<string | null>(null);

function open(arg?: string | { source?: string; fileId?: string }) {
  let source: string | undefined;
  let fileId: string | undefined;
  if (typeof arg === "string") {
    source = arg;
  } else if (arg) {
    source = arg.source;
    fileId = arg.fileId;
  }
  singleSource.value = source ?? null;
  visible.value = true;
  loadChunks({ source, fileId });
}

async function loadChunks(opts: { source?: string; fileId?: string } = {}) {
  loading.value = true;
  try {
    const params: { source?: string; file_id?: string } = {};
    if (opts.source) params.source = opts.source;
    if (opts.fileId) params.file_id = opts.fileId;
    const res = await api.getChunks(Object.keys(params).length ? params : undefined);
    const data = res.data as ChunkSource | Record<string, ChunkSource>;

    if (data["chunks"] && data["stats"]) {
      const label = opts.source ?? "所有来源";
      chunkData.value = { [label]: data as ChunkSource };
    } else {
      chunkData.value = data as Record<string, ChunkSource>;
    }

    const sources = Object.keys(chunkData.value);
    activeTab.value = sources[0] ?? "";
  } catch {
    toast.show("加载分块数据失败", "error");
  } finally {
    loading.value = false;
  }
}

defineExpose({ open });
</script>

<template>
  <a-modal
    v-model:open="visible"
    :title="singleSource ? `📊 分块可视化 · ${singleSource}` : '📊 分块可视化'"
    width="1100px"
    :footer="null"
    :body-style="{ maxHeight: '70vh', overflow: 'auto' }"
  >
    <a-spin :spinning="loading">
      <a-empty v-if="!loading && Object.keys(chunkData).length === 0" description="暂无分块数据" />

      <template v-else-if="!loading">
        <!-- 单文件：直接渲染 -->
        <template v-if="singleSource">
          <template v-for="(source, name) in chunkData" :key="name">
            <ChunkSourcePanel :source="source" />
          </template>
        </template>
        <!-- 多文件：标签页 -->
        <a-tabs v-else v-model:active-key="activeTab">
          <a-tab-pane v-for="(source, name) in chunkData" :key="name" :tab="name">
            <ChunkSourcePanel :source="source" />
          </a-tab-pane>
        </a-tabs>
      </template>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.chunk-card {
  margin-bottom: 8px;
}

.chunk-text {
  font-size: 13px;
  line-height: 1.7;
  color: #333;
  max-height: 150px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
