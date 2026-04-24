<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { ReloadOutlined, LikeFilled, DislikeFilled } from "@ant-design/icons-vue";
import api from "@/api";
import type { FeedbackSummary, FeedbackItem } from "@/api";
import { useToast } from "@/composables/useToast";
import { useAppStore } from "@/stores/session";
import EChart from "@/components/EChart.vue";
import type { EChartsOption } from "echarts";

const toast = useToast();
const router = useRouter();
const store = useAppStore();
const data = ref<FeedbackSummary | null>(null);
const loading = ref(false);
const filter = ref<"all" | "up" | "down">("all");

async function load() {
  loading.value = true;
  try {
    const params: { rating?: 1 | -1; limit: number } = { limit: 200 };
    if (filter.value === "up") params.rating = 1;
    if (filter.value === "down") params.rating = -1;
    const res = await api.getFeedback(params);
    data.value = res.data;
  } catch (e) {
    toast.show("加载失败：" + (e as Error).message, "error");
  } finally {
    loading.value = false;
  }
}

onMounted(load);

const stats = computed(() => data.value?.stats);
const reasonCounts = computed(() => data.value?.reason_counts ?? []);
const items = computed<FeedbackItem[]>(() => data.value?.items ?? []);
const trend = computed(() => data.value?.trend ?? []);
const trendRange = ref<7 | 30>(7);
const trendData = computed(() => trend.value.slice(-trendRange.value));
const trendTotal = computed(() => {
  const up = trendData.value.reduce((s, d) => s + d.up, 0);
  const down = trendData.value.reduce((s, d) => s + d.down, 0);
  return { up, down, total: up + down };
});

function fmtMD(date: string) {
  // "2024-01-15" → "1/15"
  const parts = date.split("-");
  return parts.length === 3 ? `${+(parts[1] ?? 0)}/${+(parts[2] ?? 0)}` : date;
}

// ---- ECharts: 反馈趋势（堆叠柱：👍 + 👎） ----
const trendOption = computed<EChartsOption>(() => {
  const isDark = document.documentElement.dataset.theme === "dark";
  const axisColor = isDark ? "rgba(255,255,255,0.55)" : "rgba(0,0,0,0.55)";
  const splitColor = isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)";
  const xs = trendData.value.map((d) => fmtMD(d.date));
  const ups = trendData.value.map((d) => d.up);
  const downs = trendData.value.map((d) => d.down);
  return {
    grid: { left: 30, right: 16, top: 24, bottom: 30, containLabel: true },
    legend: { data: ["👍 点赞", "👎 点踩"], textStyle: { color: axisColor }, top: 0, right: 0 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: {
      type: "category",
      data: xs,
      axisLine: { lineStyle: { color: splitColor } },
      axisTick: { show: false },
      axisLabel: { color: axisColor, fontSize: 11 },
    },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: axisColor, fontSize: 11 },
      splitLine: { lineStyle: { color: splitColor } },
      minInterval: 1,
    },
    series: [
      {
        name: "👍 点赞",
        type: "bar",
        stack: "fb",
        barMaxWidth: 22,
        itemStyle: { color: "#10b981", borderRadius: [4, 4, 0, 0] },
        data: ups,
      },
      {
        name: "👎 点踩",
        type: "bar",
        stack: "fb",
        barMaxWidth: 22,
        itemStyle: { color: "#ef4444" },
        data: downs,
      },
    ],
  };
});

// ---- ECharts: 原因分布（横向条形图） ----
const reasonOption = computed<EChartsOption>(() => {
  const isDark = document.documentElement.dataset.theme === "dark";
  const axisColor = isDark ? "rgba(255,255,255,0.55)" : "rgba(0,0,0,0.55)";
  const splitColor = isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)";
  const sorted = [...reasonCounts.value].sort((a, b) => a.count - b.count);
  return {
    grid: { left: 16, right: 30, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const list = params as Array<{ name: string; value: number }>;
        if (!list?.length) return "";
        const p = list[0]!;
        const pct = totalReasons.value ? ((p.value / totalReasons.value) * 100).toFixed(1) : "0";
        return `${p.name}<br/><b>${p.value}</b> 次 · ${pct}%`;
      },
    },
    xAxis: {
      type: "value",
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: axisColor, fontSize: 11 },
      splitLine: { lineStyle: { color: splitColor } },
      minInterval: 1,
    },
    yAxis: {
      type: "category",
      data: sorted.map((r) => r.reason),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: axisColor, fontSize: 12 },
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 16,
        itemStyle: {
          color: "rgba(239, 68, 68, 0.75)",
          borderRadius: [0, 4, 4, 0],
        },
        label: {
          show: true,
          position: "right",
          color: axisColor,
          fontSize: 11,
        },
        data: sorted.map((r) => r.count),
      },
    ],
  };
});
const totalReasons = computed(() => reasonCounts.value.reduce((s, r) => s + r.count, 0));
const satisfaction = computed(() => {
  const s = stats.value;
  if (!s || s.total_with_feedback === 0) return null;
  return Math.round((s.up / s.total_with_feedback) * 100);
});

function fmtTime(iso: string | null) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function truncate(s: string | null, n = 200) {
  if (!s) return "";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function setFilter(f: "all" | "up" | "down") {
  if (filter.value === f) return;
  filter.value = f;
  load();
}

function jumpToConversation(it: FeedbackItem) {
  store.setActive(it.conversation_id);
  store.setHighlightMessage(it.id);
  router.push("/");
}
</script>

<template>
  <div class="feedback-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">反馈分析</h2>
        <p class="page-subtitle">汇总用户对每次回答的满意度，用于持续优化 RAG 效果</p>
      </div>
      <a-button :loading="loading" @click="load">
        <template #icon><ReloadOutlined /></template>
        刷新
      </a-button>
    </div>

    <!-- 顶部统计卡片 -->
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-label">总回答数</div>
        <div class="stat-value">{{ stats?.total_answers ?? "-" }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">已反馈</div>
        <div class="stat-value">{{ stats?.total_with_feedback ?? "-" }}</div>
        <div class="stat-sub" v-if="stats && stats.total_answers">
          {{ Math.round((stats.total_with_feedback / stats.total_answers) * 100) }}% 覆盖率
        </div>
      </div>
      <div class="stat-card stat-up">
        <div class="stat-label"><LikeFilled /> 点赞</div>
        <div class="stat-value">{{ stats?.up ?? "-" }}</div>
      </div>
      <div class="stat-card stat-down">
        <div class="stat-label"><DislikeFilled /> 点踩</div>
        <div class="stat-value">{{ stats?.down ?? "-" }}</div>
      </div>
      <div class="stat-card stat-rate">
        <div class="stat-label">满意度</div>
        <div class="stat-value">
          {{ satisfaction !== null ? satisfaction + "%" : "-" }}
        </div>
        <div class="stat-bar" v-if="satisfaction !== null">
          <div class="stat-bar-fill" :style="{ width: satisfaction + '%' }"></div>
        </div>
      </div>
    </div>

    <!-- 时间趋势 -->
    <section class="panel">
      <div class="panel-head">
        <h3>📈 反馈趋势</h3>
        <div class="trend-controls">
          <span class="trend-summary">
            <span class="dot dot-up"></span>{{ trendTotal.up }} 赞 <span class="dot dot-down"></span
            >{{ trendTotal.down }} 踩
          </span>
          <a-segmented
            :value="trendRange"
            :options="[
              { label: '近 7 天', value: 7 },
              { label: '近 30 天', value: 30 },
            ]"
            @change="(v: string | number) => (trendRange = v as 7 | 30)"
          />
        </div>
      </div>
      <div class="trend-chart" v-if="trendData.length">
        <EChart :option="trendOption" :loading="loading" class="trend-echart" />
      </div>
      <a-empty v-else description="暂无趋势数据" />
    </section>

    <!-- 原因分布 -->
    <section class="panel">
      <div class="panel-head">
        <h3>👎 原因分布</h3>
        <span class="panel-meta">共 {{ totalReasons }} 次原因标注</span>
      </div>
      <div class="reason-chart" v-if="reasonCounts.length">
        <EChart
          :option="reasonOption"
          class="reason-echart"
          :style="{ height: Math.max(160, reasonCounts.length * 32) + 'px' }"
        />
      </div>
      <a-empty v-else description="暂无点踩原因数据" />
    </section>

    <!-- 反馈明细列表 -->
    <section class="panel">
      <div class="panel-head">
        <h3>📋 反馈明细</h3>
        <a-segmented
          :value="filter"
          :options="[
            { label: '全部', value: 'all' },
            { label: '👍 仅赞', value: 'up' },
            { label: '👎 仅踩', value: 'down' },
          ]"
          @change="(v: string | number) => setFilter(v as 'all' | 'up' | 'down')"
        />
      </div>
      <div class="item-list" v-if="items.length">
        <div
          v-for="it in items"
          :key="it.id"
          class="fb-item clickable"
          :class="it.rating === 1 ? 'up' : 'down'"
          @click="jumpToConversation(it)"
          :title="'点击跳转到该对话'"
        >
          <div class="fb-item-head">
            <span class="fb-rating">
              <LikeFilled v-if="it.rating === 1" />
              <DislikeFilled v-else />
            </span>
            <span class="fb-conv-title">{{ it.conversation_title || "（无标题对话）" }}</span>
            <span class="fb-time">{{ fmtTime(it.created_at) }}</span>
          </div>
          <div class="fb-q" v-if="it.question">
            <span class="fb-q-tag">问</span>
            <span>{{ truncate(it.question, 150) }}</span>
          </div>
          <div class="fb-a">
            <span class="fb-a-tag">答</span>
            <span>{{ truncate(it.answer, 280) }}</span>
          </div>
          <div class="fb-reasons" v-if="it.reasons && it.reasons.length">
            <a-tag v-for="r in it.reasons" :key="r" color="purple">{{ r }}</a-tag>
          </div>
          <div class="fb-comment" v-if="it.comment">💬 {{ it.comment }}</div>
        </div>
      </div>
      <a-empty v-else :description="loading ? '加载中…' : '暂无反馈数据'" />
    </section>
  </div>
</template>

<style scoped>
.feedback-page {
  padding: 20px 28px 32px;
  width: 100%;
  max-width: 1100px;
  margin: 0 auto;
  height: 100%;
  overflow-y: auto;
  flex: 1;
  min-width: 0;
  box-sizing: border-box;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 20px;
}
.page-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 4px;
}
.page-subtitle {
  margin: 0;
  font-size: 12.5px;
  color: var(--text-secondary);
}

/* 顶部统计 */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}
.stat-card {
  padding: 14px 16px;
  background: var(--bg-card, #fff);
  border: 1px solid var(--border);
  border-radius: 12px;
  position: relative;
  overflow: hidden;
}
.stat-label {
  font-size: 12px;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 6px;
}
.stat-value {
  font-size: 24px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.1;
}
.stat-sub {
  font-size: 11.5px;
  color: var(--text-secondary);
  margin-top: 4px;
}
.stat-card.stat-up .stat-label {
  color: #16a34a;
}
.stat-card.stat-down .stat-label {
  color: #dc2626;
}
.stat-card.stat-rate .stat-value {
  color: var(--primary);
}
.stat-bar {
  margin-top: 8px;
  height: 4px;
  background: var(--bg-hover);
  border-radius: 2px;
  overflow: hidden;
}
.stat-bar-fill {
  height: 100%;
  background: var(--primary);
  transition: width 0.4s ease;
}

/* 面板 */
.panel {
  background: var(--bg-card, var(--bg-hover));
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 18px;
  margin-bottom: 16px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.panel-head h3 {
  font-size: 14px;
  font-weight: 600;
  margin: 0;
  color: var(--text-primary);
}
.panel-meta {
  font-size: 12px;
  color: var(--text-secondary);
}

/* 趋势图 */
.trend-controls {
  display: flex;
  align-items: center;
  gap: 14px;
}
.trend-summary {
  font-size: 12px;
  color: var(--text-secondary);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.trend-summary .dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 2px;
  margin-left: 8px;
  margin-right: 2px;
}
.trend-summary .dot-up {
  background: #22c55e;
}
.trend-summary .dot-down {
  background: #ef4444;
}

.trend-chart {
  width: 100%;
}

.trend-echart {
  width: 100%;
  height: 220px;
}

/* 原因分布 */
.reason-chart {
  width: 100%;
}

.reason-echart {
  width: 100%;
}

/* 明细列表 */
.item-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.fb-item {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  background: var(--bg);
  border-left-width: 3px;
}
.fb-item.clickable {
  cursor: pointer;
  transition:
    background 0.15s ease,
    transform 0.15s ease,
    box-shadow 0.15s ease;
}
.fb-item.clickable:hover {
  background: var(--bg-hover);
  transform: translateX(2px);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
}
.fb-item.up {
  border-left-color: #16a34a;
}
.fb-item.down {
  border-left-color: #dc2626;
}
.fb-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.fb-rating {
  font-size: 13px;
}
.fb-item.up .fb-rating {
  color: #16a34a;
}
.fb-item.down .fb-rating {
  color: #dc2626;
}
.fb-conv-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fb-time {
  font-size: 11.5px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.fb-q,
.fb-a {
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--text-primary);
  margin: 4px 0;
  display: flex;
  gap: 6px;
}
.fb-q-tag,
.fb-a-tag {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 4px;
  font-size: 10.5px;
  font-weight: 600;
  margin-top: 1px;
}
.fb-q-tag {
  background: rgba(99, 102, 241, 0.15);
  color: #818cf8;
}
.fb-a-tag {
  background: rgba(139, 92, 246, 0.15);
  color: #a78bfa;
}
.fb-q span:not(.fb-q-tag),
.fb-a span:not(.fb-a-tag) {
  color: var(--text-secondary);
}
.fb-reasons {
  margin-top: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.fb-comment {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg-hover);
  border-radius: 6px;
  padding: 6px 8px;
}
</style>
