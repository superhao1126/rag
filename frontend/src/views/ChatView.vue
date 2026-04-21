<script setup lang="ts">
import { ref, nextTick, onMounted, watch } from "vue";
import { useRouter } from "vue-router";
import { marked } from "marked";
import { Modal } from "ant-design-vue";
import {
  PlusOutlined,
  DeleteOutlined,
  SendOutlined,
  MessageOutlined,
  BarChartOutlined,
} from "@ant-design/icons-vue";
import api from "@/api";
import type { Conversation, Message, RetrievalResult } from "@/api";
import { useAppStore } from "@/stores/session";
import { useToast } from "@/composables/useToast";
import ChunkModal from "@/components/ChunkModal.vue";
import hljs from "highlight.js";
import "highlight.js/styles/atom-one-dark.css";

// 配置 marked：使用 highlight.js 渲染代码块并注入复制按钮
marked.use({
  breaks: true,
  gfm: true,
  renderer: {
    code(token) {
      const lang = token.lang && hljs.getLanguage(token.lang) ? token.lang : "plaintext";
      const highlighted = hljs.highlight(token.text, { language: lang }).value;
      const langLabel = `<span class="code-lang">${lang}</span>`;
      const copyIcon = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
      const copyBtn = `<button class="copy-code-btn">${copyIcon}复制</button>`;
      return (
        `<div class="code-wrapper">` +
        `<div class="code-toolbar">${langLabel}${copyBtn}</div>` +
        `<pre><code class="hljs language-${lang}">${highlighted}</code></pre>` +
        `</div>`
      );
    },
  },
});

const store = useAppStore();
const toast = useToast();
const router = useRouter();

// ── 聊天状态 ──
interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  htmlContent?: string;
  retrieval?: RetrievalResult[] | null;
  retrievalMode?: string;
  streaming?: boolean;
}

const messages = ref<ChatMessage[]>([]);
const isStreaming = ref(false);
const activeAbort = ref<AbortController | null>(null);
const searchMode = ref<"hybrid" | "vector" | "bm25">("hybrid");
const questionText = ref("");
const loadingConvs = ref(false);
const loadingMsgs = ref(false);

const messagesEl = ref<HTMLElement>();
const inputEl = ref<{ focus: () => void } | null>(null);
const chunkModalRef = ref<InstanceType<typeof ChunkModal>>();

// ── 工具函数 ──
function scrollToBottom() {
  nextTick(() => {
    if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight;
  });
}

function renderMd(text: string, withCursor = false): string {
  if (!text) return "";
  try {
    let html = marked.parse(text) as string;
    if (withCursor) html += '<span class="typing-cursor"></span>';
    return html;
  } catch {
    return text.replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");
  }
}

function escapeHtml(s: string): string {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ── 代码复制（事件委托） ──
function handleMessagesClick(e: MouseEvent) {
  const btn = (e.target as HTMLElement).closest(".copy-code-btn") as HTMLElement | null;
  if (!btn) return;
  const codeEl = btn.closest(".code-wrapper")?.querySelector("pre code");
  if (!codeEl) return;

  navigator.clipboard
    .writeText(codeEl.textContent || "")
    .then(() => {
      const orig = btn.innerHTML;
      btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 12 4 10"/></svg>已复制`;
      btn.classList.add("copied");
      setTimeout(() => {
        btn.innerHTML = orig;
        btn.classList.remove("copied");
      }, 2000);
    })
    .catch(() => toast.show("复制失败", "error"));
}

// ── 加载数据 ──
async function loadConversations() {
  loadingConvs.value = true;
  try {
    const res = await api.getConversations();
    store.setConversations(res.data.conversations);
    if (!store.activeConversationId && res.data.conversations.length > 0) {
      store.setActive(res.data.conversations[0]!.id);
    }
  } catch {
    /* ignore */
  } finally {
    loadingConvs.value = false;
  }
}

async function loadStats() {
  try {
    const res = await api.getStats();
    store.setTotalChunks(res.data["文本块总数"] ?? 0);
  } catch {
    /* ignore */
  }
}

async function loadMessages(convId: string) {
  loadingMsgs.value = true;
  messages.value = [];
  try {
    const res = await api.getConversation(convId);
    messages.value = res.data.messages.map((m: Message) => ({
      role: m.role,
      content: m.content,
      htmlContent: m.role === "assistant" ? renderMd(m.content) : undefined,
      retrieval: m.retrieval_info?.results ?? null,
      retrievalMode: m.retrieval_info?.mode,
    }));
    scrollToBottom();
  } catch {
    /* ignore */
  } finally {
    loadingMsgs.value = false;
  }
}

onMounted(async () => {
  await Promise.all([loadConversations(), loadStats()]);
});

watch(
  () => store.activeConversationId,
  (id) => {
    if (id) loadMessages(id);
    else messages.value = [];
  },
);

// ── 对话操作 ──
async function newConversation() {
  try {
    const res = await api.createConversation("新对话");
    const conv: Conversation = {
      id: res.data.id,
      title: res.data.title,
      created_at: null,
      updated_at: null,
      message_count: 0,
    };
    store.addConversation(conv);
    store.setActive(conv.id);
    messages.value = [];
  } catch {
    toast.show("创建对话失败", "error");
  }
}

function selectConversation(id: string) {
  if (id === store.activeConversationId || isStreaming.value) return;
  store.setActive(id);
}

function deleteConversation(conv: Conversation) {
  Modal.confirm({
    title: "确认删除",
    content: `确定删除对话「${conv.title}」？此操作不可撤销。`,
    okText: "删除",
    okType: "danger",
    cancelText: "取消",
    async onOk() {
      try {
        await api.deleteConversation(conv.id);
        store.removeConversation(conv.id);
        toast.show("对话已删除", "success");
      } catch {
        toast.show("删除失败", "error");
      }
    },
  });
}

// ── 发送消息 ──
function handleKeyDown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendQuestion();
  }
}

async function sendQuestion() {
  const question = questionText.value.trim();
  if (!question || isStreaming.value) return;
  if (!store.activeConversationId) {
    toast.show("请先选择或新建一个对话", "error");
    return;
  }

  messages.value.push({ role: "user", content: question });
  questionText.value = "";
  console.log("questionText==========>", questionText.value);
  scrollToBottom();

  const assistantIdx = messages.value.length;
  messages.value.push({ role: "assistant", content: "", htmlContent: "", streaming: true });

  isStreaming.value = true;
  let streamedText = "";
  const abortCtrl = new AbortController();
  activeAbort.value = abortCtrl;

  try {
    const response = await api.queryStream(
      store.activeConversationId!,
      question,
      searchMode.value,
      abortCtrl.signal,
    );
    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail || "请求失败");
    }

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop()!;

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const event = JSON.parse(line.slice(6));
          if (event.type === "retrieval") {
            const msg = messages.value[assistantIdx];
            if (msg) {
              msg.retrieval = event.data.results;
              msg.retrievalMode = event.data.mode;
            }
          } else if (event.type === "content") {
            streamedText += event.content;
            const msg = messages.value[assistantIdx];
            if (msg) {
              msg.content = streamedText;
              // 流式渲染：带打字机光标
              msg.htmlContent = renderMd(streamedText, true);
            }
            scrollToBottom();
          } else if (event.type === "done") {
            await loadConversations();
          } else if (event.type === "title") {
            // 更新当前对话的标题（无需重新拉取对话列表，直接更新 store）
            const conv = store.conversations.find((c) => c.id === store.activeConversationId);
            if (conv) conv.title = event.title;
          } else if (event.type === "error") {
            streamedText += `\n\n❌ 错误: ${event.message}`;
            const msg = messages.value[assistantIdx];
            if (msg) {
              msg.content = streamedText;
              msg.htmlContent = renderMd(streamedText);
            }
            toast.show(event.message, "error");
          }
        } catch {
          /* ignore */
        }
      }
    }

    // 最终渲染（去掉光标）
    const msg = messages.value[assistantIdx];
    if (msg) {
      msg.content = streamedText;
      msg.htmlContent = renderMd(streamedText);
      msg.streaming = false;
    }
    scrollToBottom();
  } catch (e: unknown) {
    const msg = messages.value[assistantIdx];
    if (abortCtrl.signal.aborted) {
      // 用户主动中止
      if (msg) {
        msg.content = streamedText || "（已停止生成）";
        msg.htmlContent = streamedText
          ? renderMd(streamedText)
          : '<p style="color:#9ca3af">（已停止生成）</p>';
        msg.streaming = false;
      }
    } else {
      if (msg) {
        msg.content = `❌ 出错: ${(e as Error).message}`;
        msg.htmlContent = `<p>❌ 出错: ${escapeHtml((e as Error).message)}</p>`;
        msg.streaming = false;
      }
      toast.show((e as Error).message, "error");
    }
  } finally {
    activeAbort.value = null;
    isStreaming.value = false;
    nextTick(() => inputEl.value?.focus());
  }
}

// ── 辅助 ──
function getModeLabel(mode: string) {
  return { hybrid: "混合检索", vector: "语义向量", bm25: "关键词" }[mode] ?? mode;
}

function getScoreStr(r: RetrievalResult): string {
  if (r.rrf_score != null) return `RRF ${r.rrf_score.toFixed(4)}`;
  if (r.similarity != null) return `cos ${r.similarity.toFixed(4)}`;
  if (r.bm25_score != null) return `BM25 ${r.bm25_score.toFixed(4)}`;
  return "";
}

function goToSource(source: string) {
  store.setHighlightFile(source);
  router.push("/files");
}

function stopGeneration() {
  activeAbort.value?.abort();
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
</script>

<template>
  <a-layout class="chat-layout">
    <!-- ── 左侧：对话列表 ── -->
    <a-layout-sider width="268" theme="light" class="sidebar">
      <div class="sidebar-header">
        <div class="sidebar-brand">
          <MessageOutlined class="brand-icon" />
          <span>对话列表</span>
        </div>
        <a-button type="primary" size="small" class="new-btn" @click="newConversation">
          <template #icon><PlusOutlined /></template>
          新建
        </a-button>
      </div>

      <!-- 知识库状态 -->
      <div class="kb-stat">
        <div class="kb-stat-left">
          <span class="kb-dot" :class="store.totalChunks > 0 ? 'active' : 'inactive'"></span>
          <span>知识库</span>
          <span class="kb-count">{{
            store.totalChunks > 0 ? store.totalChunks + " 块" : "未上传"
          }}</span>
        </div>
        <a-button size="small" type="text" class="kb-btn" @click="chunkModalRef?.open()">
          <template #icon><BarChartOutlined /></template>
        </a-button>
      </div>

      <!-- 对话列表 -->
      <div class="conv-list">
        <a-spin :spinning="loadingConvs" size="small">
          <div
            v-for="conv in store.conversations"
            :key="conv.id"
            :class="['conv-item', { active: conv.id === store.activeConversationId }]"
            @click="selectConversation(conv.id)"
          >
            <div class="conv-icon">💬</div>
            <div class="conv-info">
              <div class="conv-title" :title="conv.title">{{ conv.title }}</div>
              <div class="conv-meta">
                {{ conv.message_count }} 条 · {{ formatTime(conv.updated_at) }}
              </div>
            </div>
            <a-button
              class="conv-delete"
              type="text"
              size="small"
              danger
              @click.stop="deleteConversation(conv)"
            >
              <template #icon><DeleteOutlined /></template>
            </a-button>
          </div>
          <div v-if="!loadingConvs && store.conversations.length === 0" class="conv-empty">
            <div class="conv-empty-icon">🗨️</div>
            <div>点击「新建」开始对话</div>
          </div>
        </a-spin>
      </div>
    </a-layout-sider>

    <!-- ── 右侧：聊天面板 ── -->
    <a-layout class="chat-panel">
      <!-- 消息区域 -->
      <div ref="messagesEl" class="messages" @click="handleMessagesClick">
        <!-- 欢迎页 -->
        <div v-if="!store.activeConversationId" class="welcome">
          <div class="welcome-emoji">🤖</div>
          <h2>RAG 知识问答助手</h2>
          <p>上传文档后，在左侧新建对话，<br />即可开始提问</p>
          <div class="welcome-tips">
            <div class="tip-item">📄 支持 PDF、TXT、Markdown</div>
            <div class="tip-item">🔍 混合检索，精准定位</div>
            <div class="tip-item">💬 多轮对话，上下文记忆</div>
          </div>
        </div>

        <!-- 加载中 -->
        <div v-else-if="loadingMsgs" class="welcome">
          <a-spin size="large" />
          <p style="margin-top: 16px; color: #94a3b8">加载消息中…</p>
        </div>

        <!-- 空对话提示 -->
        <div v-else-if="store.activeConversationId && messages.length === 0" class="welcome">
          <div class="welcome-emoji">💬</div>
          <h2>开始新对话</h2>
          <p>在下方输入你的问题</p>
        </div>

        <!-- 消息流 -->
        <template v-else>
          <template v-for="(msg, idx) in messages" :key="idx">
            <!-- 检索来源折叠面板 -->
            <a-collapse
              v-if="msg.role === 'assistant' && msg.retrieval && msg.retrieval.length"
              ghost
              class="retrieval-collapse"
            >
              <a-collapse-panel key="1">
                <template #header>
                  <span class="retrieval-summary">
                    🔍 检索到 {{ msg.retrieval.length }} 个片段
                    <span class="retrieval-mode-tag">{{
                      getModeLabel(msg.retrievalMode ?? "hybrid")
                    }}</span>
                  </span>
                </template>
                <div class="retrieval-list">
                  <div v-for="(r, ri) in msg.retrieval" :key="ri" class="retrieval-item">
                    <span class="retrieval-idx">{{ ri + 1 }}</span>
                    <button
                      class="retrieval-source"
                      @click="goToSource(r.source)"
                      :title="'跳转到：' + r.source"
                    >
                      {{ r.source }}
                    </button>
                    <span class="retrieval-score">{{ getScoreStr(r) }}</span>
                  </div>
                </div>
              </a-collapse-panel>
            </a-collapse>

            <!-- 用户消息 -->
            <div v-if="msg.role === 'user'" class="message user">
              <div class="bubble user-bubble">{{ msg.content }}</div>
              <div class="avatar user-avatar">我</div>
            </div>

            <!-- 助手消息 -->
            <div v-else class="message assistant">
              <div class="avatar ai-avatar">AI</div>
              <div
                class="bubble ai-bubble"
                :class="{ streaming: msg.streaming }"
                v-html="msg.htmlContent || '<span class=\'typing-cursor\'></span>'"
              ></div>
            </div>
          </template>
        </template>
      </div>

      <!-- 输入区域 -->
      <div class="input-area">
        <div class="input-card">
          <a-textarea
            ref="inputEl"
            v-model:value="questionText"
            placeholder="输入问题…  Enter 发送，Shift+Enter 换行"
            :auto-size="{ minRows: 1, maxRows: 6 }"
            :disabled="isStreaming || !store.activeConversationId"
            class="question-input"
            @keydown="handleKeyDown"
          />
          <div class="input-footer">
            <div class="mode-picker">
              <span class="mode-label">检索模式</span>
              <a-segmented
                v-model:value="searchMode"
                size="small"
                :options="[
                  { label: '混合', value: 'hybrid' },
                  { label: '向量', value: 'vector' },
                  { label: 'BM25', value: 'bm25' },
                ]"
              />
            </div>
            <a-button v-if="isStreaming" class="stop-btn" @click="stopGeneration">
              ⏹ 停止
            </a-button>
            <a-button
              v-else
              type="primary"
              :disabled="!questionText.trim() || !store.activeConversationId"
              class="send-btn"
              @click="sendQuestion"
            >
              <template #icon><SendOutlined /></template>
              发送
            </a-button>
          </div>
        </div>
      </div>
    </a-layout>

    <ChunkModal ref="chunkModalRef" />
  </a-layout>
</template>

<style scoped>
/* ========== 布局 ========== */
.chat-layout {
  flex: 1;
  overflow: hidden;
  height: 100%;
  background: var(--bg-chat);
  transition: background 0.25s ease;
}

/* ========== 左侧面板 ========== */
.sidebar :deep(.ant-layout-sider-children) {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border);
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 14px 12px;
  flex-shrink: 0;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.brand-icon {
  color: var(--primary);
  font-size: 16px;
}

.new-btn {
  background: var(--primary) !important;
  border-color: var(--primary) !important;
  border-radius: 8px !important;
  font-size: 12px !important;
}

/* 知识库状态条 */
.kb-stat {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 14px 10px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.kb-stat-left {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
}

.kb-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.kb-dot.active {
  background: #22c55e;
  box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.15);
}
.kb-dot.inactive {
  background: var(--text-muted);
}

.kb-count {
  font-weight: 600;
  color: var(--text-primary);
}

.kb-btn {
  color: var(--text-muted) !important;
}

/* 对话列表 */
.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px 8px;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.08) transparent;
}

.conv-list::-webkit-scrollbar {
  width: 4px;
}
.conv-list::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.08);
  border-radius: 4px;
}

.conv-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 8px;
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.15s;
  position: relative;
}

.conv-item:hover {
  background: var(--bg-hover);
}

.conv-item.active {
  background: var(--primary-light);
  box-shadow: inset 0 0 0 1px rgba(99, 102, 241, 0.2);
}

.conv-icon {
  font-size: 16px;
  flex-shrink: 0;
  opacity: 0.5;
}

.conv-item.active .conv-icon {
  opacity: 0.9;
}

.conv-info {
  flex: 1;
  min-width: 0;
}

.conv-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-item.active .conv-title {
  color: #a5b4fc;
}

.conv-meta {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 2px;
}

.conv-delete {
  opacity: 0;
  transition: opacity 0.15s;
  flex-shrink: 0;
}

.conv-item:hover .conv-delete {
  opacity: 1;
}

.conv-empty {
  text-align: center;
  padding: 40px 12px;
  color: var(--text-muted);
}

.conv-empty-icon {
  font-size: 32px;
  margin-bottom: 8px;
  opacity: 0.4;
}
.conv-empty div {
  font-size: 12px;
}

/* ========== 右侧聊天面板 ========== */
.chat-panel {
  background: var(--bg-chat) !important;
  flex-direction: column !important;
  overflow: hidden;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 28px 32px 20px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.07) transparent;
}

.messages::-webkit-scrollbar {
  width: 5px;
}
.messages::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.07);
  border-radius: 4px;
}

/* ========== 欢迎页 ========== */
.welcome {
  text-align: center;
  margin: auto;
  padding: 48px 32px;
  max-width: 420px;
}

.welcome-emoji {
  font-size: 52px;
  margin-bottom: 16px;
  opacity: 0.8;
}

.welcome h2 {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.welcome p {
  font-size: 14px;
  color: var(--text-secondary);
  line-height: 1.8;
  margin-bottom: 20px;
}

.welcome-tips {
  display: flex;
  flex-direction: column;
  gap: 8px;
  text-align: left;
}

.tip-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-secondary);
  background: var(--bg-card);
  padding: 9px 14px;
  border-radius: 10px;
  border: 1px solid var(--border);
}

/* ========== 消息气泡 ========== */
.message {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  animation: msg-in 0.2s ease-out;
}

@keyframes msg-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.message.user {
  flex-direction: row-reverse;
  align-self: flex-end;
  max-width: 72%;
}

.message.assistant {
  align-self: flex-start;
  max-width: 84%;
}

/* 头像 */
.avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
  letter-spacing: 0;
}

.user-avatar {
  background: linear-gradient(135deg, #6366f1, #818cf8);
  color: white;
  box-shadow: 0 2px 8px rgba(99, 102, 241, 0.4);
}

.ai-avatar {
  background: linear-gradient(135deg, #0ea5e9, #6366f1);
  color: white;
  box-shadow: 0 2px 8px rgba(14, 165, 233, 0.3);
}

/* 气泡 */
.bubble {
  padding: 11px 15px;
  border-radius: 16px;
  font-size: 14px;
  line-height: 1.75;
  word-break: break-word;
  position: relative;
}

.user-bubble {
  background: linear-gradient(135deg, #6366f1, #818cf8);
  color: white;
  border-bottom-right-radius: 4px;
  white-space: pre-wrap;
  box-shadow: 0 2px 12px rgba(99, 102, 241, 0.35);
}

.ai-bubble {
  background: var(--bg-card);
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-bottom-left-radius: 4px;
  box-shadow: var(--shadow-md);
}

/* 打字机光标 */
:deep(.typing-cursor) {
  display: inline-block;
  width: 2px;
  height: 1em;
  background: #a5b4fc;
  margin-left: 2px;
  vertical-align: text-bottom;
  border-radius: 1px;
  animation: blink 0.9s infinite;
}

@keyframes blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0;
  }
}

/* ========== Markdown 内容样式 ========== */
.ai-bubble :deep(h1),
.ai-bubble :deep(h2),
.ai-bubble :deep(h3) {
  font-weight: 700;
  margin: 14px 0 6px;
  color: var(--text-primary);
}
.ai-bubble :deep(h1) {
  font-size: 1.2em;
}
.ai-bubble :deep(h2) {
  font-size: 1.1em;
}
.ai-bubble :deep(h3) {
  font-size: 1em;
}

.ai-bubble :deep(p) {
  margin: 6px 0;
}
.ai-bubble :deep(> p:first-child) {
  margin-top: 0;
}
.ai-bubble :deep(> p:last-child) {
  margin-bottom: 0;
}

.ai-bubble :deep(ul),
.ai-bubble :deep(ol) {
  margin: 6px 0;
  padding-left: 20px;
}
.ai-bubble :deep(li) {
  margin: 3px 0;
}

.ai-bubble :deep(code) {
  background: rgba(99, 102, 241, 0.12);
  padding: 2px 6px;
  border-radius: 5px;
  font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.87em;
  color: #7c3aed;
}

:root[data-theme='dark'] .ai-bubble :deep(code) {
  background: rgba(99, 102, 241, 0.15);
  color: #f472b6;
}

/* ── 代码块容器 ── */
.ai-bubble :deep(.code-wrapper) {
  position: relative;
  margin: 10px 0;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid #2d3748;
}

.ai-bubble :deep(.code-toolbar) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #1a1f2e;
  padding: 6px 14px;
}

.ai-bubble :deep(.code-lang) {
  font-size: 11px;
  color: #7c8db5;
  font-family: "JetBrains Mono", monospace;
  text-transform: lowercase;
}

.ai-bubble :deep(.copy-code-btn) {
  display: flex;
  align-items: center;
  gap: 5px;
  background: transparent;
  border: 1px solid #374151;
  color: #9ca3af;
  font-size: 11px;
  padding: 3px 9px;
  border-radius: 5px;
  cursor: pointer;
  transition: all 0.15s;
  line-height: 1.5;
}

.ai-bubble :deep(.copy-code-btn:hover) {
  background: #374151;
  color: #e5e7eb;
  border-color: #4b5563;
}

.ai-bubble :deep(.copy-code-btn.copied) {
  background: #064e3b;
  border-color: #065f46;
  color: #6ee7b7;
}

.ai-bubble :deep(pre) {
  margin: 0;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.6;
  scrollbar-width: thin;
  scrollbar-color: #374151 transparent;
}

.ai-bubble :deep(pre code) {
  background: none;
  color: inherit;
  padding: 0;
  font-size: inherit;
  border-radius: 0;
}

.ai-bubble :deep(blockquote) {
  border-left: 3px solid var(--primary);
  margin: 8px 0;
  padding: 6px 14px;
  color: var(--text-secondary);
  background: var(--primary-light);
  border-radius: 0 8px 8px 0;
}

.ai-bubble :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
  font-size: 13px;
}

.ai-bubble :deep(th),
.ai-bubble :deep(td) {
  border: 1px solid rgba(255, 255, 255, 0.08);
  padding: 6px 10px;
  text-align: left;
}

.ai-bubble :deep(th) {
  background: rgba(255, 255, 255, 0.05);
  font-weight: 600;
  color: var(--text-primary);
}

.ai-bubble :deep(strong) {
  font-weight: 700;
  color: #e2e8f0;
}
.ai-bubble :deep(a) {
  color: #818cf8;
  text-decoration: none;
}
.ai-bubble :deep(a:hover) {
  text-decoration: underline;
}
.ai-bubble :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 10px 0;
}

/* ========== 检索来源 ========== */
.retrieval-collapse {
  align-self: flex-start;
  max-width: 76%;
  margin-left: 44px;
}

.retrieval-collapse :deep(.ant-collapse-header) {
  padding: 6px 12px !important;
  background: var(--bg-card) !important;
  border-radius: 8px !important;
  color: var(--text-secondary) !important;
  border: 1px solid var(--border) !important;
}

.retrieval-collapse :deep(.ant-collapse-content) {
  background: var(--bg-card) !important;
  border-top: 1px solid var(--border) !important;
}

.retrieval-collapse :deep(.ant-collapse-content-box) {
  padding: 0 12px 10px !important;
}

.retrieval-summary {
  font-size: 12px;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
}

.retrieval-mode-tag {
  background: var(--primary-light);
  color: #a5b4fc;
  padding: 1px 7px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 500;
}

.retrieval-list {
  padding-top: 4px;
}

.retrieval-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

.retrieval-item:last-child {
  border-bottom: none;
}

.retrieval-idx {
  width: 18px;
  height: 18px;
  background: var(--primary);
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 700;
  flex-shrink: 0;
}

.retrieval-source {
  flex: 1;
  background: none;
  border: none;
  padding: 0;
  text-align: left;
  font: inherit;
  font-size: 0.82rem;
  color: #818cf8;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-decoration: underline;
  text-underline-offset: 2px;
  transition: color 0.15s;
}
.retrieval-source:hover {
  color: #a5b4fc;
}

.retrieval-score {
  color: var(--text-muted);
  font-family: monospace;
  font-size: 11px;
  flex-shrink: 0;
}

/* ========== 输入区域 ========== */
.input-area {
  padding: 12px 24px 18px;
  background: var(--bg-chat);
  flex-shrink: 0;
  border-top: 1px solid var(--border);
}

.input-card {
  background: var(--bg-card);
  border-radius: var(--radius);
  border: 1px solid rgba(255, 255, 255, 0.09);
  box-shadow: 0 -2px 20px rgba(0, 0, 0, 0.3), 0 4px 20px rgba(0, 0, 0, 0.3);
  padding: 12px 14px 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.question-input :deep(textarea) {
  border: none !important;
  box-shadow: none !important;
  resize: none;
  font-size: 14px;
  line-height: 1.6;
  padding: 0 !important;
  background: transparent !important;
  color: var(--text-primary) !important;
}

.question-input :deep(.ant-input:focus) {
  box-shadow: none !important;
}

.question-input :deep(textarea::placeholder) {
  color: var(--text-muted) !important;
}

.input-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.mode-picker {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mode-label {
  font-size: 12px;
  color: var(--text-muted);
}

/* 检索模式 segmented 深色适配 */
.mode-picker :deep(.ant-segmented) {
  background: rgba(255, 255, 255, 0.06) !important;
  border-radius: 8px !important;
}
.mode-picker :deep(.ant-segmented-item-label) {
  color: var(--text-muted) !important;
  font-size: 12px !important;
}
.mode-picker :deep(.ant-segmented-item-selected .ant-segmented-item-label) {
  color: #a5b4fc !important;
}
.mode-picker :deep(.ant-segmented-item-selected) {
  background: rgba(99, 102, 241, 0.25) !important;
}
.mode-picker :deep(.ant-segmented-thumb) {
  background: rgba(99, 102, 241, 0.25) !important;
}

.send-btn {
  background: linear-gradient(135deg, #6366f1, #818cf8) !important;
  border: none !important;
  border-radius: 9px !important;
  height: 36px !important;
  padding: 0 18px !important;
  font-weight: 500;
  box-shadow: 0 2px 12px rgba(99, 102, 241, 0.4) !important;
  transition:
    opacity 0.15s,
    transform 0.1s !important;
}

.send-btn:not(:disabled):hover {
  opacity: 0.9;
  transform: translateY(-1px);
}

.send-btn:not(:disabled):active {
  transform: translateY(0);
}

.send-btn:disabled {
  opacity: 0.35 !important;
  box-shadow: none !important;
}

.stop-btn {
  border-color: rgba(248, 113, 113, 0.5) !important;
  color: #f87171 !important;
  background: rgba(239, 68, 68, 0.08) !important;
  border-radius: 9px !important;
  height: 36px !important;
  padding: 0 18px !important;
  font-weight: 500;
  transition: background 0.15s !important;
}
.stop-btn:hover {
  background: rgba(239, 68, 68, 0.15) !important;
}
</style>
