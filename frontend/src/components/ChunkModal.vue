<script setup lang="ts">
import { ref } from "vue";
import api from "@/api";
import { useToast } from "@/composables/useToast";

const toast = useToast();

const visible = ref(false);
const loading = ref(false);
const chunkData = ref<Record<string, ChunkSource>>({});
const activeTab = ref("");

interface ChunkItem {
  index?: number;
  chunk_index?: number;
  text?: string;
  char_count?: number;
  start?: number;
  end?: number;
}

interface ChunkSource {
  chunks: ChunkItem[];
  stats: Record<string, number>;
}

function open() {
  visible.value = true;
  loadChunks();
}

async function loadChunks() {
  loading.value = true;
  try {
    const res = await api.getChunks();
    const data = res.data as Record<string, unknown>;

    if (data["chunks"] && data["stats"]) {
      chunkData.value = { 所有来源: data as unknown as ChunkSource };
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

function getChunkIndex(c: ChunkItem) {
  return c.index ?? c.chunk_index ?? 0;
}

function getCharCount(c: ChunkItem) {
  return c.char_count ?? c.text?.length ?? 0;
}

defineExpose({ open });
</script>

<template>
  <a-modal
    v-model:open="visible"
    title="📊 分块可视化"
    width="900px"
    :footer="null"
    :body-style="{ maxHeight: '70vh', overflow: 'auto' }"
  >
    <a-spin :spinning="loading">
      <a-empty v-if="!loading && Object.keys(chunkData).length === 0" description="暂无分块数据" />

      <template v-else-if="!loading">
        <a-tabs v-model:active-key="activeTab">
          <a-tab-pane v-for="(source, name) in chunkData" :key="name" :tab="name">
            <!-- 统计卡片 -->
            <a-row :gutter="12" style="margin-bottom: 16px">
              <a-col :span="6">
                <a-statistic
                  title="文本块数"
                  :value="source.stats?.total_chunks ?? source.chunks?.length ?? 0"
                  :value-style="{ color: '#667eea' }"
                />
              </a-col>
              <a-col v-if="source.stats?.avg_chunk_size" :span="6">
                <a-statistic
                  title="平均字符数"
                  :value="source.stats.avg_chunk_size"
                  :value-style="{ color: '#667eea' }"
                />
              </a-col>
              <a-col v-if="source.stats?.max_chunk_size" :span="6">
                <a-statistic
                  title="最大块"
                  :value="source.stats.max_chunk_size"
                  :value-style="{ color: '#667eea' }"
                />
              </a-col>
              <a-col v-if="source.stats?.min_chunk_size" :span="6">
                <a-statistic
                  title="最小块"
                  :value="source.stats.min_chunk_size"
                  :value-style="{ color: '#667eea' }"
                />
              </a-col>
            </a-row>

            <!-- 分块列表 -->
            <a-card
              v-for="c in source.chunks"
              :key="getChunkIndex(c)"
              size="small"
              class="chunk-card"
            >
              <template #title>
                <span style="color: #667eea; font-weight: 600">块 #{{ getChunkIndex(c) }}</span>
              </template>
              <template #extra>
                <span style="color: #888; font-size: 12px">
                  {{ getCharCount(c) }} 字符
                  <template v-if="c.start != null"> · 位置 {{ c.start }}-{{ c.end }}</template>
                </span>
              </template>
              <div class="chunk-text">{{ c.text ?? "" }}</div>
            </a-card>
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
