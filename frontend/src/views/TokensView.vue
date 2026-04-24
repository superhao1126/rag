<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import api from "@/api";
import type { LlmCallEntry, LlmStats, LlmTrendPoint } from "@/api";
import { useAppStore } from "@/stores/session";
import { ReloadOutlined, ClearOutlined } from "@ant-design/icons-vue";
import { message as antMessage, Modal } from "ant-design-vue";
import EChart from "@/components/EChart.vue";
import type { EChartsOption } from "echarts";

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

// ---- ECharts 配置：堆叠柱(对话输入/输出) + 折线(向量化) ----
const trendOption = computed<EChartsOption>(() => {
  const arr = Array.isArray(trendData.value) ? trendData.value : [];
  const isDark = document.documentElement.dataset.theme === "dark";
  const axisColor = isDark ? "rgba(255,255,255,0.55)" : "rgba(0,0,0,0.55)";
  const splitColor = isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)";

  const xLabels = arr.map((p) => fmtBucket(p));
  const chatPrompt = arr.map((p) => bucketSegments(p).chatPrompt);
  const chatCompletion = arr.map((p) => bucketSegments(p).chatCompletion);
  const embedding = arr.map((p) => bucketSegments(p).embedding);
  const calls = arr.map((p) => p.calls);
  const totals = arr.map((p) => p.total_tokens);

  return {
    grid: { left: 50, right: 30, top: 30, bottom: 40, containLabel: true },
    legend: {
      data: ["对话输入", "对话输出", "向量化"],
      textStyle: { color: axisColor },
      top: 0,
      right: 0,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const list = params as Array<{ axisValue: string; dataIndex: number }>;
        if (!list?.length) return "";
        const idx = list[0]!.dataIndex;
        const cp = chatPrompt[idx] ?? 0;
        const cc = chatCompletion[idx] ?? 0;
        const em = embedding[idx] ?? 0;
        const tt = totals[idx] ?? 0;
        const ca = calls[idx] ?? 0;
        return [
          `<div style="font-weight:600;margin-bottom:4px">${list[0]!.axisValue}</div>`,
          `<div>对话输入: <b>${cp.toLocaleString()}</b></div>`,
          `<div>对话输出: <b>${cc.toLocaleString()}</b></div>`,
          `<div>向量化: <b>${em.toLocaleString()}</b></div>`,
          `<div style="margin-top:4px;color:#999">合计 ${tt.toLocaleString()} · 调用 ${ca} 次</div>`,
        ].join("");
      },
    },
    xAxis: {
      type: "category",
      data: xLabels,
      axisLine: { lineStyle: { color: splitColor } },
      axisTick: { show: false },
      axisLabel: {
        color: axisColor,
        fontSize: 11,
        interval: arr.length > 16 ? Math.floor(arr.length / 12) : 0,
      },
    },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: axisColor,
        fontSize: 11,
        formatter: (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : `${v}`),
      },
      splitLine: { lineStyle: { color: splitColor } },
    },
    series: [
      {
        name: "对话输入",
        type: "bar",
        stack: "chat",
        barMaxWidth: 22,
        itemStyle: { color: "#3b82f6", borderRadius: [0, 0, 0, 0] },
        data: chatPrompt,
      },
      {
        name: "对话输出",
        type: "bar",
        stack: "chat",
        barMaxWidth: 22,
        itemStyle: { color: "#10b981", borderRadius: [4, 4, 0, 0] },
        data: chatCompletion,
      },
      {
        name: "向量化",
        type: "line",
        smooth: true,
        symbol: "circle",
        symbolSize: 6,
        lineStyle: { color: "#f59e0b", width: 2 },
        itemStyle: { color: "#f59e0b" },
        data: embedding,
      },
    ],
  };
});

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
            :value-style="{ color: 'var(--primary)', fontWeight: 600 }" />
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
        <EChart :option="trendOption" :loading="loadingTrend" class="trend-echart" />
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
  background: #3b82f6;
}

.trend-summary .dot-completion {
  background: #10b981;
}

.trend-summary .dot-embedding {
  background: #f59e0b;
}

.trend-summary .muted {
  color: #9ca3af;
  margin-left: 8px;
}

.trend-chart {
  padding: 8px 4px 0;
}

.trend-echart {
  width: 100%;
  height: 260px;
}
</style>
