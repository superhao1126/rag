<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAppStore } from "@/stores/session";
import api from "@/api";
import {
  MessageOutlined,
  DatabaseOutlined,
  BookOutlined,
  LikeOutlined,
  ThunderboltOutlined,
  ReloadOutlined,
  BarChartOutlined,
} from "@ant-design/icons-vue";
import { Popover, message as antMessage } from "ant-design-vue";

const store = useAppStore();
const route = useRoute();
const router = useRouter();

const currentRoute = computed(() => route.name as string);

const llmStats = computed(() => store.llmStats);
const totalTokens = computed(() => llmStats.value?.totals.total_tokens ?? 0);
const promptTokens = computed(() => llmStats.value?.totals.prompt_tokens ?? 0);
const completionTokens = computed(() => llmStats.value?.totals.completion_tokens ?? 0);
const calls = computed(() => llmStats.value?.totals.calls ?? 0);
const defaultModel = computed(() => llmStats.value?.default_model ?? "—");
const modelEntries = computed(() =>
  Object.entries(llmStats.value?.models ?? {}).map(([name, u]) => ({ name, ...u })),
);

function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return (n / 1000).toFixed(n < 10_000 ? 2 : 1) + "K";
  return (n / 1_000_000).toFixed(2) + "M";
}

const refreshing = ref(false);
async function refreshLlmStats() {
  refreshing.value = true;
  try {
    const res = await api.getLlmStats();
    store.setLlmStats(res.data);
  } catch {
    /* ignore */
  } finally {
    refreshing.value = false;
  }
}

async function resetLlmStats() {
  try {
    const res = await api.resetLlmStats();
    store.setLlmStats(res.data);
    antMessage.success("已重置 token 统计");
  } catch (e) {
    antMessage.error((e as Error).message || "重置失败");
  }
}

onMounted(() => {
  refreshLlmStats();
});
</script>

<template>
  <header class="header">
    <div class="header-glow-line" />

    <div class="header-left">
      <div class="logo" @click="router.push('/')">
        <BookOutlined class="logo-icon" />
        <span class="logo-text">RAG 知识问答</span>
      </div>

      <div class="divider" />

      <nav class="header-nav">
        <button
          class="nav-btn"
          :class="{ active: currentRoute === 'chat' }"
          @click="router.push('/')"
        >
          <MessageOutlined />
          问答
        </button>
        <button
          class="nav-btn"
          :class="{ active: currentRoute === 'files' }"
          @click="router.push('/files')"
        >
          <DatabaseOutlined />
          文件管理
        </button>
        <button
          class="nav-btn"
          :class="{ active: currentRoute === 'feedback' }"
          @click="router.push('/feedback')"
        >
          <LikeOutlined />
          反馈分析
        </button>
        <button
          class="nav-btn"
          :class="{ active: currentRoute === 'tokens' }"
          @click="router.push('/tokens')"
        >
          <BarChartOutlined />
          Token 用量
        </button>
      </nav>
    </div>

    <div class="header-right">
      <Popover trigger="click" placement="bottomRight" overlay-class-name="token-popover">
        <button class="token-badge" :class="{ active: totalTokens > 0 }">
          <ThunderboltOutlined class="token-icon" />
          <span class="token-model">{{ defaultModel }}</span>
          <span class="token-sep">·</span>
          <span class="token-num">{{ formatTokens(totalTokens) }}</span>
        </button>
        <template #title>
          <div class="popover-title">
            <span>LLM Token 用量</span>
            <span class="popover-actions">
              <ReloadOutlined
                :spin="refreshing"
                class="popover-icon"
                title="刷新"
                @click="refreshLlmStats"
              />
              <a class="popover-reset" @click="resetLlmStats">清零</a>
            </span>
          </div>
        </template>
        <template #content>
          <div class="token-detail">
            <div class="td-row">
              <span class="td-label">默认模型</span>
              <span class="td-value strong">{{ defaultModel }}</span>
            </div>
            <div class="td-row">
              <span class="td-label">输入 tokens</span>
              <span class="td-value">{{ promptTokens.toLocaleString() }}</span>
            </div>
            <div class="td-row">
              <span class="td-label">输出 tokens</span>
              <span class="td-value">{{ completionTokens.toLocaleString() }}</span>
            </div>
            <div class="td-row total-row">
              <span class="td-label">合计</span>
              <span class="td-value strong">{{ totalTokens.toLocaleString() }}</span>
            </div>
            <div class="td-row">
              <span class="td-label">调用次数</span>
              <span class="td-value">{{ calls }}</span>
            </div>
            <div v-if="modelEntries.length > 0" class="td-models">
              <div class="td-models-title">按模型</div>
              <div v-for="m in modelEntries" :key="m.name" class="td-model-row">
                <span class="td-model-name">{{ m.name }}</span>
                <span class="td-model-stats">
                  in {{ m.prompt_tokens.toLocaleString() }} / out
                  {{ m.completion_tokens.toLocaleString() }} ({{ m.calls }} 次)
                </span>
              </div>
            </div>
          </div>
        </template>
      </Popover>

      <span class="stat-badge" :class="{ lit: store.totalChunks > 0 }">
        {{ store.totalChunks }} 块
      </span>
      <span class="stat-badge">{{ store.conversations.length }} 对话</span>

      <div class="divider" />

      <button
        class="theme-toggle"
        :title="store.isDark ? '切换到亮色' : '切换到深色'"
        @click="store.toggleTheme()"
      >
        {{ store.isDark ? "☀️" : "🌙" }}
      </button>
    </div>
  </header>
</template>

<style scoped>
.header {
  background: var(--header-bg);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 52px;
  position: relative;
  flex-shrink: 0;
  box-shadow: var(--header-shadow);
  transition:
    background 0.25s ease,
    box-shadow 0.25s ease;
}

.header-glow-line {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent,
    var(--header-glow) 30%,
    var(--header-glow) 70%,
    transparent
  );
  pointer-events: none;
}

/* ── Logo ── */
.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  user-select: none;
}

.logo-icon {
  font-size: 17px;
  color: var(--primary);
}

/* 深色模式下 logo 图标也用 primary，去掉花哨渐变 */
:root[data-theme="dark"] .logo-icon {
  color: var(--primary);
}

.logo-text {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.2px;
  color: var(--text-primary);
}

:root[data-theme="dark"] .logo-text {
  color: var(--text-primary);
}

.divider {
  width: 1px;
  height: 16px;
  background: var(--header-divider);
}

/* ── 导航按钮 ── */
.header-nav {
  display: flex;
  align-items: center;
  gap: 2px;
}

.nav-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--header-nav-color);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
  line-height: 1;
}

.nav-btn:hover {
  background: var(--header-nav-hover-bg);
  color: var(--header-nav-active-color);
}

.nav-btn.active {
  background: var(--header-nav-active-bg);
  color: var(--header-nav-active-color);
  box-shadow: var(--header-nav-active-shadow);
}

/* ── 右侧区域 ── */
.header-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.stat-badge {
  padding: 3px 9px;
  border-radius: 20px;
  border: 1px solid var(--header-stat-border);
  background: var(--header-stat-bg);
  color: var(--header-stat-color);
  font-size: 11px;
}

.stat-badge.lit {
  background: var(--header-stat-lit-bg);
  color: var(--header-stat-lit-color);
}

/* ── Token 徽章 ── */
.token-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border-radius: 20px;
  border: 1px solid var(--header-stat-border);
  background: var(--header-stat-bg);
  color: var(--header-stat-color);
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s ease;
  line-height: 1;
  max-width: 220px;
}
.token-badge:hover {
  background: var(--header-nav-active-bg);
  color: var(--header-nav-active-color);
}
.token-badge.active {
  background: var(--header-stat-lit-bg);
  color: var(--header-stat-lit-color);
}
.token-icon {
  font-size: 12px;
}
.token-model {
  max-width: 110px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.token-sep {
  opacity: 0.55;
}
.token-num {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

/* ── Popover 详情 ── */
.popover-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-width: 240px;
  font-size: 13px;
}
.popover-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}
.popover-icon {
  cursor: pointer;
  color: #6b7280;
}
.popover-reset {
  font-size: 12px;
}
.token-detail {
  min-width: 260px;
  font-size: 12px;
}
.td-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
}
.td-row.total-row {
  border-top: 1px dashed rgba(127, 127, 127, 0.3);
  padding-top: 6px;
  margin-top: 2px;
}
.td-label {
  color: #6b7280;
}
.td-value {
  font-variant-numeric: tabular-nums;
}
.td-value.strong {
  font-weight: 700;
}
.td-models {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed rgba(127, 127, 127, 0.3);
}
.td-models-title {
  font-size: 11px;
  color: #6b7280;
  margin-bottom: 4px;
}
.td-model-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 3px 0;
}
.td-model-name {
  font-weight: 600;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.td-model-stats {
  color: #6b7280;
  font-variant-numeric: tabular-nums;
}

/* ── 主题切换按钮 ── */
.theme-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 7px;
  background: var(--header-nav-hover-bg);
  color: var(--header-nav-color);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.theme-toggle:hover {
  background: var(--header-nav-active-bg);
  color: var(--header-nav-active-color);
}
</style>
