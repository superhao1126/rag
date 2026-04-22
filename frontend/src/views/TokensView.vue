<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import api from "@/api";
import type { LlmCallEntry, LlmStats, LlmTrendPoint } from "@/api";
import { useAppStore } from "@/stores/session";
import { ReloadOutlined, ClearOutlined } from "@ant-design/icons-vue";
import { message as antMessage, Modal } from "ant-design-vue";

const store = useAppStore();

const stats = ref<LlmStats | null>(null);
const calls = ref<LlmCallEntry[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(20);
const callTypeFilter = ref<string | undefined>(undefined);
const modelFilter = ref<string | undefined>(undefined);

const loadingStats = ref(false);
const loadingCalls = ref(false);

const totals = computed(() => stats.value?.totals ?? { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, calls: 0 });
const modelEntries = computed(() =>
  Object.entries(stats.value?.models ?? {}).map(([name, u]) => ({ key: name, name, ...u })),
);
const callTypeEntries = computed(() =>
  Object.entries(stats.value?.calls_by_type ?? {}).map(([k, v]) => ({ key: k, type: k, count: v })),
);

const callTypeOptions = computed(() => {
  const set = new Set<string>(Object.keys(stats.value?.calls_by_type ?? {}));
  return Array.from(set).map((t) => ({ value: t, label: typeLabel(t) }));
});
const modelOptions = computed(() => {
  const set = new Set<string>(Object.keys(stats.value?.models ?? {}));
  return Array.from(set).map((m) => ({ value: m, label: m }));
});

function typeLabel(t: string): string {
  return ({ chat: "对话", title: "标题", embedding: "向量化" } as Record<string, string>)[t] || t;
}

function typeColor(t: string): string {
  return ({ chat: "blue", title: "purple", embedding: "green" } as Record<string, string>)[t] || "default";
}

function fmtTime(ts: number): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

async function loadStats() {
  loadingStats.value = true;
  try {
    const res = await api.getLlmStats();
    stats.value = res.data;
    store.setLlmStats(res.data);
  } catch (e) {
    antMessage.error((e as Error).message || "加载统计失败");
  } finally {
    loadingStats.value = false;
  }
}

async function loadCalls() {
  loadingCalls.value = true;
  try {
    const res = await api.getLlmCalls({
      page: page.value,
      page_size: pageSize.value,
      call_type: callTypeFilter.value,
      model: modelFilter.value,
    });
    calls.value = res.data.items;
    total.value = res.data.total;
  } catch (e) {
    antMessage.error((e as Error).message || "加载调用记录失败");
  } finally {
    loadingCalls.value = false;
  }
}

async function refreshAll() {
  await Promise.all([loadStats(), loadCalls(), loadTrend()]);
}

// ---- 趋势图 ----
const trendBucket = ref<"day" | "hour">("day");
const trendRange = ref<number>(30);
const trendData = ref<LlmTrendPoint[]>([]);
const loadingTrend = ref(false);

const rangeOptions = computed(() =>
  trendBucket.value === "hour"
    ? [
      { label: "近 12 小时", value: 12 },
      { label: "近 24 小时", value: 24 },
      { label: "近 48 小时", value: 48 },
    ]
    : [
      { label: "近 7 天", value: 7 },
      { label: "近 14 天", value: 14 },
      { label: "近 30 天", value: 30 },
    ],
);

const trendMax = computed(() => {
  let m = 1;
  const arr = Array.isArray(trendData.value) ? trendData.value : [];
  for (const p of arr) {
    const s = bucketSegments(p);
    m = Math.max(m, s.chatPrompt + s.chatCompletion, s.embedding);
  }
  return m;
});

const embeddingPolyline = computed(() => {
  const arr = Array.isArray(trendData.value) ? trendData.value : [];
  const n = arr.length;
  if (n === 0) return "";
  const max = trendMax.value;
  return arr
    .map((p, i) => {
      const x = n === 1 ? 50 : ((i + 0.5) / n) * 100;
      const y = 100 - (bucketSegments(p).embedding / max) * 100;
      return `${x.toFixed(3)},${y.toFixed(3)}`;
    })
    .join(" ");
});

const embeddingPoints = computed(() => {
  const arr = Array.isArray(trendData.value) ? trendData.value : [];
  const n = arr.length;
  if (n === 0) return [];
  const max = trendMax.value;
  return arr.map((p, i) => ({
    x: n === 1 ? 50 : ((i + 0.5) / n) * 100,
    y: 100 - (bucketSegments(p).embedding / max) * 100,
    value: bucketSegments(p).embedding,
  }));
});

function bucketSegments(p: LlmTrendPoint) {
  const bt = p.by_type || {};
  const chatPrompt = (bt.chat?.prompt_tokens || 0) + (bt.title?.prompt_tokens || 0);
  const chatCompletion = (bt.chat?.completion_tokens || 0) + (bt.title?.completion_tokens || 0);
  const embedding = (bt.embedding?.prompt_tokens || 0) + (bt.embedding?.completion_tokens || 0);
  // 兼容旧响应（无 by_type）：把全部 prompt 当成 chat
  if (!p.by_type) {
    return {
      chatPrompt: p.prompt_tokens,
      chatCompletion: p.completion_tokens,
      embedding: 0,
    };
  }
  return { chatPrompt, chatCompletion, embedding };
}

const trendTotals = computed(() => {
  let cp = 0, cc = 0, em = 0, calls = 0;
  const arr = Array.isArray(trendData.value) ? trendData.value : [];
  for (const p of arr) {
    const s = bucketSegments(p);
    cp += s.chatPrompt;
    cc += s.chatCompletion;
    em += s.embedding;
    calls += p.calls;
  }
  return { chatPrompt: cp, chatCompletion: cc, embedding: em, calls };
});

async function loadTrend() {
  loadingTrend.value = true;
  try {
    const res = await api.getLlmTrend({ bucket: trendBucket.value, points: trendRange.value });
    const data = res?.data;
    trendData.value = Array.isArray(data) ? data : [];
  } catch (e) {
    trendData.value = [];
    antMessage.error((e as Error).message || "加载趋势失败");
  } finally {
    loadingTrend.value = false;
  }
}

function onBucketChange() {
  trendRange.value = trendBucket.value === "hour" ? 24 : 30;
  loadTrend();
}

function fmtBucket(p: LlmTrendPoint): string {
  if (!p.ts) return "";
  const d = new Date(p.ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return trendBucket.value === "hour"
    ? `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:00`
    : `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function fmtBucketShort(p: LlmTrendPoint): string {
  if (!p.ts) return "";
  const d = new Date(p.ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return trendBucket.value === "hour" ? `${pad(d.getHours())}h` : `${d.getMonth() + 1}/${d.getDate()}`;
}

function handlePageChange(p: number, ps: number) {
  page.value = p;
  pageSize.value = ps;
  loadCalls();
}

function onFilterChange() {
  page.value = 1;
  loadCalls();
}

function confirmReset() {
  Modal.confirm({
    title: "确认清零所有 token 统计？",
    content: "将清除全部累计数据与调用流水，此操作不可恢复。",
    okText: "清零",
    okType: "danger",
    cancelText: "取消",
    async onOk() {
      try {
        const res = await api.resetLlmStats();
        stats.value = res.data;
        store.setLlmStats(res.data);
        await loadCalls();
        antMessage.success("已清零");
      } catch (e) {
        antMessage.error((e as Error).message || "清零失败");
      }
    },
  });
}

const columns = [
  { title: "时间", dataIndex: "ts", key: "ts", width: 180 },
  { title: "类型", dataIndex: "call_type", key: "call_type", width: 100 },
  { title: "模型", dataIndex: "model", key: "model", ellipsis: true },
  { title: "输入", dataIndex: "prompt_tokens", key: "prompt_tokens", width: 110, align: "right" },
  { title: "输出", dataIndex: "completion_tokens", key: "completion_tokens", width: 110, align: "right" },
  { title: "合计", dataIndex: "total_tokens", key: "total_tokens", width: 110, align: "right" },
  { title: "备注", dataIndex: "note", key: "note", width: 140 },
];

const modelColumns = [
  { title: "模型", dataIndex: "name", key: "name" },
  { title: "调用次数", dataIndex: "calls", key: "calls", align: "right", width: 100 },
  { title: "输入", dataIndex: "prompt_tokens", key: "prompt_tokens", align: "right", width: 130 },
  { title: "输出", dataIndex: "completion_tokens", key: "completion_tokens", align: "right", width: 130 },
  { title: "合计", dataIndex: "total_tokens", key: "total_tokens", align: "right", width: 130 },
];

onMounted(refreshAll);
</script>

<template>
  <div class="tokens-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Token 用量统计</h2>
        <div class="page-sub">累计所有 LLM 对话、标题生成和向量化调用的 token 消耗</div>
      </div>
      <div class="page-actions">
        <a-button :loading="loadingStats || loadingCalls" @click="refreshAll">
          <template #icon>
            <ReloadOutlined />
          </template>
          刷新
        </a-button>
        <a-button danger @click="confirmReset">
          <template #icon>
            <ClearOutlined />
          </template>
          清零
        </a-button>
      </div>
    </div>

    <!-- 总览卡片 -->
    <a-row :gutter="16" class="stat-cards">
      <a-col :xs="12" :sm="12" :md="6">
        <a-card>
          <a-statistic title="总输入 tokens" :value="totals.prompt_tokens" />
        </a-card>
      </a-col>
      <a-col :xs="12" :sm="12" :md="6">
        <a-card>
          <a-statistic title="总输出 tokens" :value="totals.completion_tokens" />
        </a-card>
      </a-col>
      <a-col :xs="12" :sm="12" :md="6">
        <a-card>
          <a-statistic title="合计 tokens" :value="totals.total_tokens"
            :value-style="{ color: '#1677ff', fontWeight: 700 }" />
        </a-card>
      </a-col>
      <a-col :xs="12" :sm="12" :md="6">
        <a-card>
          <a-statistic title="总调用次数" :value="totals.calls" />
        </a-card>
      </a-col>
    </a-row>

    <!-- 趋势图 -->
    <a-card class="block-card trend-card">
      <template #title>
        <span>Token 用量趋势</span>
      </template>
      <template #extra>
        <a-space :size="8">
          <span class="trend-summary">
            <span class="dot dot-prompt"></span>对话输入 {{ trendTotals.chatPrompt.toLocaleString() }}
            <span class="dot dot-completion"></span>对话输出 {{ trendTotals.chatCompletion.toLocaleString() }}
            <span class="dot dot-embedding"></span>向量化(线) {{ trendTotals.embedding.toLocaleString() }}
            <span class="muted">· 共 {{ trendTotals.calls }} 次</span>
          </span>
          <a-radio-group v-model:value="trendBucket" size="small" button-style="solid" @change="onBucketChange">
            <a-radio-button value="hour">小时</a-radio-button>
            <a-radio-button value="day">天</a-radio-button>
          </a-radio-group>
          <a-select v-model:value="trendRange" size="small" style="width: 120px" :options="rangeOptions"
            @change="loadTrend" />
          <a-button size="small" :icon="h(ReloadOutlined)" :loading="loadingTrend" @click="loadTrend" />
        </a-space>
      </template>

      <div v-if="trendData.length" class="trend-chart">
        <div class="trend-bars-area">
          <div v-for="(p, i) in trendData" :key="i" class="trend-col">
            <a-tooltip>
              <template #title>
                <div>{{ fmtBucket(p) }}</div>
                <div>对话输入: {{ bucketSegments(p).chatPrompt.toLocaleString() }}</div>
                <div>对话输出: {{ bucketSegments(p).chatCompletion.toLocaleString() }}</div>
                <div>向量化: {{ bucketSegments(p).embedding.toLocaleString() }}</div>
                <div>合计: {{ p.total_tokens.toLocaleString() }}</div>
                <div>调用: {{ p.calls }} 次</div>
              </template>
              <div class="trend-bar-wrap">
                <div class="trend-bar trend-bar-completion"
                  :style="{ height: (bucketSegments(p).chatCompletion / trendMax) * 100 + '%' }"></div>
                <div class="trend-bar trend-bar-prompt"
                  :style="{ height: (bucketSegments(p).chatPrompt / trendMax) * 100 + '%' }"></div>
              </div>
            </a-tooltip>
          </div>
          <svg class="trend-line-overlay" viewBox="0 0 100 100" preserveAspectRatio="none">
            <polyline :points="embeddingPolyline" fill="none" stroke="#fa8c16" stroke-width="1.5"
              vector-effect="non-scaling-stroke" />
          </svg>
          <div v-for="(pt, i) in embeddingPoints" :key="'pt' + i" class="trend-line-dot"
            :style="{ left: pt.x + '%', top: pt.y + '%' }" :title="'向量化: ' + pt.value.toLocaleString()"></div>
        </div>
        <div class="trend-labels">
          <div v-for="(p, i) in trendData" :key="'l' + i" class="trend-label">
            {{ fmtBucketShort(p) }}
          </div>
        </div>
      </div>
      <a-empty v-else description="暂无数据" />
    </a-card>

    <!-- 按调用类型分桶 -->
    <a-card v-if="callTypeEntries.length" title="按调用类型" class="block-card">
      <div class="type-chips">
        <a-tag v-for="t in callTypeEntries" :key="t.key" :color="typeColor(t.type)">
          {{ typeLabel(t.type) }}: {{ t.count }} 次
        </a-tag>
      </div>
    </a-card>

    <!-- 按模型 -->
    <a-card title="按模型聚合" class="block-card">
      <a-table :columns="modelColumns" :data-source="modelEntries" :pagination="false" :loading="loadingStats"
        size="middle" :row-key="(r: { key: string }) => r.key" />
    </a-card>

    <!-- 调用流水 -->
    <a-card title="调用流水" class="block-card">
      <template #extra>
        <a-space>
          <a-select v-model:value="callTypeFilter" placeholder="全部类型" allow-clear style="width: 140px"
            :options="callTypeOptions" @change="onFilterChange" />
          <a-select v-model:value="modelFilter" placeholder="全部模型" allow-clear style="width: 220px"
            :options="modelOptions" @change="onFilterChange" />
        </a-space>
      </template>
      <a-table :columns="columns" :data-source="calls" :loading="loadingCalls" :row-key="(r: { id: number }) => r.id"
        size="middle" :pagination="{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: ['10', '20', '50', '100'],
          showTotal: (t: number) => `共 ${t} 条`,
          onChange: handlePageChange,
        }">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'ts'">
            {{ fmtTime(record.ts) }}
          </template>
          <template v-else-if="column.key === 'call_type'">
            <a-tag :color="typeColor(record.call_type)">{{ typeLabel(record.call_type) }}</a-tag>
          </template>
          <template v-else-if="column.key === 'note'">
            <span class="cell-muted">{{ record.note || "—" }}</span>
          </template>
          <template v-else-if="['prompt_tokens', 'completion_tokens', 'total_tokens'].includes(column.key)">
            <span class="num">{{ (record[column.key] as number).toLocaleString() }}</span>
          </template>
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<style scoped>
.tokens-page {
  padding: 16px 20px 32px;
  max-width: 1400px;
  margin: 0 auto;
  overflow: auto;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 16px;
  gap: 12px;
}

.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
}

.page-sub {
  font-size: 12px;
  color: var(--text-muted, #6b7280);
  margin-top: 4px;
}

.page-actions {
  display: flex;
  gap: 8px;
}

.stat-cards {
  margin-bottom: 16px;
}

.stat-cards :deep(.ant-card-body) {
  padding: 14px 18px;
}

.block-card {
  margin-bottom: 16px;
}

.type-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.num {
  font-variant-numeric: tabular-nums;
}

.cell-muted {
  color: #9ca3af;
}

/* 趋势图 */
.trend-summary {
  font-size: 12px;
  color: var(--text-secondary, #4b5563);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.trend-summary .dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin: 0 4px 0 8px;
}

.trend-summary .dot:first-child {
  margin-left: 0;
}

.trend-summary .dot-prompt {
  background: #1677ff;
}

.trend-summary .dot-completion {
  background: #52c41a;
}

.trend-summary .dot-embedding {
  background: #fa8c16;
}

.trend-summary .muted {
  color: #9ca3af;
  margin-left: 8px;
}

.trend-chart {
  padding: 8px 4px 0;
  overflow-x: auto;
}

.trend-bars-area {
  position: relative;
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 200px;
}

.trend-labels {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}

.trend-labels .trend-label {
  flex: 1 0 24px;
  min-width: 24px;
  text-align: center;
  font-size: 11px;
  color: var(--text-muted, #6b7280);
  white-space: nowrap;
}

.trend-col {
  flex: 1 0 24px;
  min-width: 24px;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  height: 100%;
}

.trend-line-overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  overflow: visible;
}

.trend-line-dot {
  position: absolute;
  width: 8px;
  height: 8px;
  margin-left: -4px;
  margin-top: -4px;
  border-radius: 50%;
  background: #fa8c16;
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.9);
  pointer-events: auto;
}

:root[data-theme="dark"] .trend-line-dot {
  box-shadow: 0 0 0 2px rgba(20, 20, 20, 0.9);
}

.trend-bar-wrap {
  flex: 1;
  width: 100%;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  align-items: stretch;
  background: var(--bar-track, #f5f5f5);
  border-radius: 4px;
  overflow: hidden;
  cursor: pointer;
  transition: filter 0.15s;
}

.trend-col:hover .trend-bar-wrap {
  filter: brightness(0.92);
}

.trend-bar {
  width: 100%;
  transition: height 0.25s ease;
}

.trend-bar-prompt {
  background: #1677ff;
}

.trend-bar-completion {
  background: #52c41a;
}

:root[data-theme="dark"] .trend-bar-wrap {
  background: rgba(255, 255, 255, 0.06);
}
</style>
