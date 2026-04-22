<script setup lang="ts">
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

defineProps<{ source: ChunkSource }>();

function getChunkIndex(c: ChunkItem) {
  return c.index ?? c.chunk_index ?? 0;
}

function getCharCount(c: ChunkItem) {
  return c.char_count ?? c.text?.length ?? 0;
}
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

  <a-card v-for="c in source.chunks" :key="getChunkIndex(c)" size="small" class="chunk-card">
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
</template>

<style scoped>
.chunk-card {
  margin-bottom: 8px;
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
