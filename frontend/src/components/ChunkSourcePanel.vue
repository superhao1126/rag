<script setup lang="ts">
import { computed } from "vue";
import type { ChunkItem, ChunkSource } from "@/api";

const props = defineProps<{ source: ChunkSource }>();

function getChunkIndex(c: ChunkItem) {
  return c.index ?? c.chunk_index ?? 0;
}

function getCharCount(c: ChunkItem) {
  return c.char_count ?? c.text?.length ?? 0;
}

function chunkTypeLabel(type?: string) {
  if (type === "api") return "API";
  if (type === "normal") return "普通";
  return type || "未知";
}

function chunkTypeColor(type?: string) {
  if (type === "api") return "blue";
  if (type === "normal") return "green";
  return "default";
}

function getChunkKey(c: ChunkItem) {
  return `${getChunkIndex(c)}-${c.start ?? 0}-${c.end ?? 0}`;
}

const typeCounts = computed(() => {
  const counts: Record<string, number> = {};
  for (const chunk of props.source.chunks ?? []) {
    const type = chunk.chunk_type || "unknown";
    counts[type] = (counts[type] ?? 0) + 1;
  }
  return counts;
});

const longSplitCount = computed(
  () => (props.source.chunks ?? []).filter((chunk) => chunk.is_long_split).length,
);

const settingTags = computed(() => {
  const stats = props.source.stats ?? {};
  return [
    ["chunk", stats.chunk_size_setting],
    ["overlap", stats.overlap_setting],
    ["min", stats.min_chunk_size_setting],
    ["api_max", stats.api_max_chunk_size_setting],
  ].filter((item): item is [string, number] => typeof item[1] === "number");
});
</script>

<template>
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

  <div class="chunk-summary">
    <a-space wrap>
      <a-tag v-for="(count, type) in typeCounts" :key="type" :color="chunkTypeColor(type)">
        {{ chunkTypeLabel(type) }} {{ count }}
      </a-tag>
      <a-tag v-if="longSplitCount" color="orange">长文切分 {{ longSplitCount }}</a-tag>
      <a-tag v-for="[label, value] in settingTags" :key="label" color="purple">
        {{ label }}={{ value }}
      </a-tag>
      <a-tag v-if="source.stats?.overlap_chars" color="cyan">
        overlap≈{{ source.stats.overlap_chars }}
      </a-tag>
    </a-space>
  </div>

  <a-card v-for="c in source.chunks" :key="getChunkKey(c)" size="small" class="chunk-card">
    <template #title>
      <a-space wrap>
        <span class="chunk-index">块 #{{ getChunkIndex(c) }}</span>
        <a-tag :color="chunkTypeColor(c.chunk_type)">{{ chunkTypeLabel(c.chunk_type) }}</a-tag>
        <a-tag v-if="c.is_long_split" color="orange">长文切分</a-tag>
        <a-tag v-if="c.title" color="geekblue" class="chunk-title">{{ c.title }}</a-tag>
      </a-space>
    </template>
    <template #extra>
      <span style="color: #888; font-size: 12px">
        {{ getCharCount(c) }} 字符
        <template v-if="c.start != null"> · 位置 {{ c.start }}-{{ c.end }}</template>
      </span>
    </template>
    <div class="chunk-text">{{ c.text ?? "" }}</div>
  </a-card>
</template>

<style scoped>
.chunk-card {
  margin-bottom: 8px;
}

.chunk-summary {
  margin: -4px 0 12px;
  padding: 8px 10px;
  border: 1px solid var(--border-color, #f0f0f0);
  border-radius: 8px;
  background: var(--bg-soft, #fafafa);
}

.chunk-index {
  color: #667eea;
  font-weight: 600;
}

.chunk-title {
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chunk-text {
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-primary, #333);
  max-height: 150px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
