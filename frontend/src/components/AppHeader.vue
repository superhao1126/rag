<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAppStore } from "@/stores/session";
import {
  MessageOutlined,
  DatabaseOutlined,
  BookOutlined,
  LikeOutlined,
} from "@ant-design/icons-vue";

const store = useAppStore();
const route = useRoute();
const router = useRouter();

const currentRoute = computed(() => route.name as string);
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
      </nav>
    </div>

    <div class="header-right">
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
  color: rgba(255, 255, 255, 0.9);
}

/* 深色模式：渐变 Logo */
:root[data-theme="dark"] .logo-icon {
  background: linear-gradient(135deg, #a5b4fc, #c084fc);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.logo-text {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.2px;
  color: rgba(255, 255, 255, 0.95);
}

:root[data-theme="dark"] .logo-text {
  background: linear-gradient(135deg, #e0e7ff 0%, #c4b5fd 60%, #a78bfa 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.divider {
  width: 1px;
  height: 18px;
  background: linear-gradient(to bottom, transparent, var(--header-divider), transparent);
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
