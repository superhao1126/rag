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
} from "@ant-design/icons-vue";
import api from "@/api";
import type { Conversation, Message, RetrievalResult } from "@/api";
import { useAppStore } from "@/stores/session";
import { useToast } from "@/composables/useToast";
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
  id?: number;
  role: "user" | "assistant";
  content: string;
  htmlContent?: string;
  thinking?: string;
  thinkingHtml?: string;
  thinkingExpanded?: boolean;
  retrieval?: RetrievalResult[] | null;
  retrievalMode?: string;
  streaming?: boolean;
  feedback?: number | null;
  feedbackReasons?: string[];
  feedbackComment?: string;
  feedbackPending?: boolean;
}

const messages = ref<ChatMessage[]>([]);
const isStreaming = ref(false);
const activeAbort = ref<AbortController | null>(null);
const searchMode = ref<"hybrid" | "vector" | "bm25">("hybrid");
const enableThinking = ref(false);
const questionText = ref("");
const loadingConvs = ref(false);
const loadingMsgs = ref(false);

const messagesEl = ref<HTMLElement>();
const inputEl = ref<{ focus: () => void } | null>(null);
const showScrollBtn = ref(false);
const isAtBottom = ref(true);

// ── 工具函数 ──
function scrollToBottom(force = false) {
  const el = messagesEl.value;
  if (!el) return;
  if (!force && !isAtBottom.value) return;
  const doScroll = () => {
    el.scrollTop = el.scrollHeight;
  };
  nextTick(() => {
    doScroll();
    requestAnimationFrame(() => {
      doScroll();
      if (force) requestAnimationFrame(doScroll);
    });
  });
}

function onMessagesScroll() {
  const el = messagesEl.value;
  if (!el) return;
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
  isAtBottom.value = distance < 80;
  showScrollBtn.value = distance > 200;
}

function scrollToBottomSmooth() {
  const el = messagesEl.value;
  if (!el) return;
  el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  isAtBottom.value = true;
  showScrollBtn.value = false;
}

function renderMd(text: string, withCursor = false, citeMax = 0): string {
  if (!text) return "";
  try {
    let html = marked.parse(text) as string;
    if (citeMax > 0) {
      html = html.replace(/\[(\d+)\]/g, (m, n) => {
        const i = parseInt(n, 10);
        if (i < 1 || i > citeMax) return m;
        return `<sup class="cite-ref" data-idx="${i}">${i}</sup>`;
      });
    }
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

// ── 代码复制 + 内联引用悬浮（事件委托） ──
const inlineCite = ref<{
  visible: boolean;
  top: number;
  left: number;
  source: string;
  chunkIndex: number;
  text: string;
  idx: number;
}>({ visible: false, top: 0, left: 0, source: "", chunkIndex: 0, text: "", idx: 0 });

function findCiteContext(target: HTMLElement): { msgIdx: number; idx: number } | null {
  const ref = target.closest(".cite-ref") as HTMLElement | null;
  if (!ref) return null;
  const msgEl = ref.closest(".message.assistant") as HTMLElement | null;
  if (!msgEl) return null;
  const msgIdx = parseInt(msgEl.dataset.msgIdx || "-1", 10);
  const idx = parseInt(ref.dataset.idx || "-1", 10);
  if (msgIdx < 0 || idx < 1) return null;
  return { msgIdx, idx };
}

function handleMessagesClick(e: MouseEvent) {
  const target = e.target as HTMLElement;

  // 内联引用点击 → 跳转
  const cite = findCiteContext(target);
  if (cite) {
    const r = messages.value[cite.msgIdx]?.retrieval?.[cite.idx - 1];
    if (r) goToSource(r.source);
    return;
  }

  // 代码复制
  const btn = target.closest(".copy-code-btn") as HTMLElement | null;
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

function handleMessagesMouseOver(e: MouseEvent) {
  const cite = findCiteContext(e.target as HTMLElement);
  if (!cite) return;
  const r = messages.value[cite.msgIdx]?.retrieval?.[cite.idx - 1];
  if (!r) return;
  const ref = (e.target as HTMLElement).closest(".cite-ref") as HTMLElement;
  const rect = ref.getBoundingClientRect();
  const containerRect = messagesEl.value?.getBoundingClientRect();
  if (!containerRect) return;
  // 视口坐标（fixed 定位），并 clamp 在消息容器水平范围内
  const popWidth = 360;
  const margin = 12;
  const minLeft = containerRect.left + popWidth / 2 + margin;
  const maxLeft = containerRect.right - popWidth / 2 - margin;
  let centerX = rect.left + rect.width / 2;
  centerX = Math.min(Math.max(centerX, minLeft), maxLeft);
  inlineCite.value = {
    visible: true,
    top: rect.top - 8,
    left: centerX,
    source: r.source,
    chunkIndex: r.chunk_index,
    text: r.text_preview,
    idx: cite.idx,
  };
}

function handleMessagesMouseOut(e: MouseEvent) {
  const ref = (e.target as HTMLElement).closest(".cite-ref");
  if (!ref) return;
  const related = e.relatedTarget as HTMLElement | null;
  if (related && (related.closest(".cite-ref") || related.closest(".inline-cite-pop"))) return;
  inlineCite.value.visible = false;
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
      id: m.id,
      role: m.role,
      content: m.content,
      htmlContent:
        m.role === "assistant" ? renderMd(m.content, false, m.retrieval_info?.results?.length ?? 0) : undefined,
      retrieval: m.retrieval_info?.results ?? null,
      retrievalMode: m.retrieval_info?.mode,
      thinking: m.role === "assistant" ? m.retrieval_info?.thinking ?? "" : "",
      thinkingHtml: m.role === "assistant" && m.retrieval_info?.thinking
        ? renderMd(m.retrieval_info.thinking, false, 0)
        : "",
      thinkingExpanded: false,
      feedback: m.feedback ?? null,
      feedbackReasons: m.feedback_reasons ?? [],
      feedbackComment: m.feedback_comment ?? "",
    }));
    // 如果有待高亮的消息，定位过去；否则滚到底
    if (store.highlightMessageId) {
      await highlightAndScrollTo(store.highlightMessageId);
      store.setHighlightMessage(null);
    } else {
      scrollToBottom(true);
    }
  } catch {
    /* ignore */
  } finally {
    loadingMsgs.value = false;
  }
}

const highlightMsgIdx = ref<number | null>(null);
async function highlightAndScrollTo(messageId: number) {
  const idx = messages.value.findIndex((m) => m.id === messageId);
  if (idx < 0) {
    scrollToBottom(true);
    return;
  }
  highlightMsgIdx.value = idx;
  await nextTick();
  // 双 rAF 等待布局完成
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      const el = messagesEl.value?.querySelector<HTMLElement>(
        `[data-msg-idx="${idx}"]`,
      );
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  });
  // 3 秒后取消高亮
  window.setTimeout(() => {
    if (highlightMsgIdx.value === idx) highlightMsgIdx.value = null;
  }, 3000);
}

onMounted(async () => {
  store.setActive("");
  await Promise.all([loadConversations(), loadStats()]);
});

watch(
  () => store.activeConversationId,
  (id) => {
    if (id) loadMessages(id);
    else messages.value = [];
  },
);

// 从反馈页跳转过来：若对话本就处于激活态，活动对话不会重载，需要单独高亮
watch(
  () => store.highlightMessageId,
  async (id) => {
    if (id == null) return;
    // 等到消息载入完毕（loadingMsgs 为 false）
    if (loadingMsgs.value) return; // loadMessages 内已处理
    await nextTick();
    await highlightAndScrollTo(id);
    store.setHighlightMessage(null);
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
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && (e as KeyboardEvent & { keyCode: number }).keyCode !== 229) {
    e.preventDefault();
    sendQuestion();
  }
}

async function sendQuestion(opts: { regenerate?: boolean; question?: string } = {}) {
  const regenerate = !!opts.regenerate;
  const question = (opts.question ?? questionText.value).trim();
  if (!question || isStreaming.value) return;
  if (!store.activeConversationId) {
    toast.show("请先选择或新建一个对话", "error");
    return;
  }

  if (!regenerate) {
    messages.value.push({ role: "user", content: question });
    questionText.value = "";
    await nextTick();
  }
  scrollToBottom();

  const assistantIdx = messages.value.length;
  messages.value.push({
    role: "assistant",
    content: "",
    htmlContent: "",
    thinking: "",
    thinkingHtml: "",
    thinkingExpanded: enableThinking.value,
    streaming: true,
  });

  isStreaming.value = true;
  let streamedText = "";
  const abortCtrl = new AbortController();
  activeAbort.value = abortCtrl;

  try {
    const response = await api.queryStream(
      store.activeConversationId!,
      question,
      searchMode.value,
      enableThinking.value,
      abortCtrl.signal,
      regenerate,
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
          } else if (event.type === "thinking") {
            const msg = messages.value[assistantIdx];
            if (msg) {
              msg.thinking = (msg.thinking || "") + event.content;
              msg.thinkingHtml = renderMd(msg.thinking, true, 0);
            }
            scrollToBottom();
          } else if (event.type === "content") {
            streamedText += event.content;
            const msg = messages.value[assistantIdx];
            if (msg) {
              msg.content = streamedText;
              // 流式渲染：带打字机光标
              msg.htmlContent = renderMd(streamedText, true, msg.retrieval?.length ?? 0);
            }
            scrollToBottom();
          } else if (event.type === "done") {
            const msg = messages.value[assistantIdx];
            if (msg && event.message_id) msg.id = event.message_id;
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
              msg.htmlContent = renderMd(streamedText, false, msg.retrieval?.length ?? 0);
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
      msg.htmlContent = renderMd(streamedText, false, msg.retrieval?.length ?? 0);
      if (msg.thinking) msg.thinkingHtml = renderMd(msg.thinking, false, 0);
      // 思考结束后默认折叠（保留可展开），避免占据视线
      msg.thinkingExpanded = false;
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
          ? renderMd(streamedText, false, msg.retrieval?.length ?? 0)
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

// ── 重新生成 / 反馈 ──
async function regenerateMessage(idx: number) {
  if (isStreaming.value) return;
  // 找到这条 assistant 消息上方的最近一条 user 消息
  let userIdx = idx - 1;
  while (userIdx >= 0 && messages.value[userIdx]?.role !== "user") userIdx--;
  if (userIdx < 0) return;
  const question = messages.value[userIdx]?.content ?? "";
  if (!question) return;
  // 移除当前 assistant 消息（之后会 push 新的）
  messages.value.splice(idx, 1);
  await sendQuestion({ regenerate: true, question });
}

async function setFeedback(idx: number, rating: 1 | -1 | 0, reasons: string[] = [], comment = "") {
  const msg = messages.value[idx];
  if (!msg || !msg.id || msg.feedbackPending) return;
  msg.feedbackPending = true;
  try {
    await api.setFeedback(msg.id, rating, reasons, comment);
    msg.feedback = rating === 0 ? null : rating;
    msg.feedbackReasons = rating === 0 ? [] : reasons;
    msg.feedbackComment = rating === 0 ? "" : comment;
    toast.show(
      rating === 0 ? "已取消反馈" : rating === 1 ? "感谢反馈 👍" : "已记录，我们会改进 👎",
      "success",
    );
  } catch (e) {
    toast.show("反馈失败：" + (e as Error).message, "error");
  } finally {
    msg.feedbackPending = false;
  }
}

// 点赞：直接记录（再次点击 = 取消）
function toggleThumbUp(idx: number) {
  const msg = messages.value[idx];
  if (!msg) return;
  setFeedback(idx, msg.feedback === 1 ? 0 : 1);
}

// 点踩弹窗状态
const FEEDBACK_REASONS = [
  "回答不准确",
  "信息不完整",
  "与文档不符",
  "答非所问",
  "引用错误",
  "其他",
];
const dislikePopoverOpen = ref<Record<number, boolean>>({});
const dislikeDraft = ref<{ reasons: string[]; comment: string }>({ reasons: [], comment: "" });

function openDislikePopover(idx: number) {
  const msg = messages.value[idx];
  if (!msg) return;
  // 如果已经是踩状态再点 → 直接取消
  if (msg.feedback === -1) {
    setFeedback(idx, 0);
    return;
  }
  dislikeDraft.value = {
    reasons: [...(msg.feedbackReasons || [])],
    comment: msg.feedbackComment || "",
  };
  dislikePopoverOpen.value = { [idx]: true };
}

function cancelDislike(idx: number) {
  dislikePopoverOpen.value[idx] = false;
}

async function submitDislike(idx: number) {
  await setFeedback(idx, -1, dislikeDraft.value.reasons, dislikeDraft.value.comment.trim());
  dislikePopoverOpen.value[idx] = false;
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
      <div
        ref="messagesEl"
        class="messages"
        @click="handleMessagesClick"
        @mouseover="handleMessagesMouseOver"
        @mouseout="handleMessagesMouseOut"
        @scroll.passive="onMessagesScroll"
      >
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
            <div v-else class="message assistant" :data-msg-idx="idx" :data-msg-id="msg.id">
              <div class="avatar ai-avatar">AI</div>
              <div class="ai-content" :class="{ 'msg-highlight': highlightMsgIdx === idx }">
                <!-- 深度思考过程（可折叠） -->
                <div v-if="msg.thinking" class="thinking-block" :class="{ streaming: msg.streaming && !msg.content }">
                  <button
                    class="thinking-header"
                    @click="msg.thinkingExpanded = !msg.thinkingExpanded"
                    type="button"
                  >
                    <span class="thinking-header-icon">💭</span>
                    <span class="thinking-header-text">
                      {{ msg.streaming && !msg.content ? "深度思考中…" : "已深度思考" }}
                    </span>
                    <span class="thinking-header-chevron" :class="{ open: msg.thinkingExpanded }">▾</span>
                  </button>
                  <div
                    v-show="msg.thinkingExpanded"
                    class="thinking-body"
                    v-html="msg.thinkingHtml"
                  ></div>
                </div>
                <div
                  v-if="msg.content || !msg.thinking"
                  class="bubble ai-bubble"
                  :class="{ streaming: msg.streaming }"
                  v-html="msg.htmlContent || '<span class=\'typing-cursor\'></span>'"
                ></div>
                <!-- 反馈工具栏 -->
                <div
                  v-if="!msg.streaming && msg.content"
                  class="msg-actions"
                >
                  <button
                    class="msg-action-btn"
                    :disabled="isStreaming"
                    @click="regenerateMessage(idx)"
                    title="重新生成"
                  >
                    <span class="ic">↻</span>
                    <span>重新生成</span>
                  </button>
                  <button
                    class="msg-action-btn"
                    :class="{ active: msg.feedback === 1 }"
                    :disabled="!msg.id || msg.feedbackPending"
                    @click="toggleThumbUp(idx)"
                    title="对该回答满意"
                  >
                    <span class="ic">👍</span>
                  </button>
                  <a-popover
                    trigger="click"
                    placement="bottomLeft"
                    :open="!!dislikePopoverOpen[idx]"
                    @update:open="(v: boolean) => (dislikePopoverOpen[idx] = v)"
                    overlay-class-name="dislike-popover"
                  >
                    <template #content>
                      <div class="dislike-form">
                        <div class="dislike-title">告诉我们哪里需要改进</div>
                        <div class="dislike-reasons">
                          <a-checkable-tag
                            v-for="r in FEEDBACK_REASONS"
                            :key="r"
                            :checked="dislikeDraft.reasons.includes(r)"
                            @change="(checked: boolean) => {
                              if (checked) dislikeDraft.reasons.push(r);
                              else dislikeDraft.reasons = dislikeDraft.reasons.filter(x => x !== r);
                            }"
                          >{{ r }}</a-checkable-tag>
                        </div>
                        <a-textarea
                          v-model:value="dislikeDraft.comment"
                          placeholder="可补充说明（可选，最多 500 字）"
                          :auto-size="{ minRows: 2, maxRows: 4 }"
                          :maxlength="500"
                          show-count
                          class="dislike-comment"
                        />
                        <div class="dislike-actions">
                          <a-button size="small" @click="cancelDislike(idx)">取消</a-button>
                          <a-button
                            size="small"
                            type="primary"
                            :loading="msg.feedbackPending"
                            @click="submitDislike(idx)"
                          >提交</a-button>
                        </div>
                      </div>
                    </template>
                    <button
                      class="msg-action-btn"
                      :class="{ active: msg.feedback === -1 }"
                      :disabled="!msg.id || msg.feedbackPending"
                      @click="openDislikePopover(idx)"
                      :title="msg.feedback === -1 ? '取消踩' : '对该回答不满意'"
                    >
                      <span class="ic">👎</span>
                    </button>
                  </a-popover>
                </div>
              </div>
            </div>
          </template>
        </template>

        <!-- 内联引用悬浮预览 -->
        <div
          v-show="inlineCite.visible"
          class="inline-cite-pop"
          :style="{ top: inlineCite.top + 'px', left: inlineCite.left + 'px' }"
          @mouseenter="inlineCite.visible = true"
          @mouseleave="inlineCite.visible = false"
        >
          <div class="inline-cite-head">
            <span class="inline-cite-idx">{{ inlineCite.idx }}</span>
            <span class="inline-cite-source">📄 {{ inlineCite.source }}</span>
            <span class="inline-cite-meta">#{{ inlineCite.chunkIndex }}</span>
          </div>
          <div class="inline-cite-text">{{ inlineCite.text }}</div>
        </div>

        <!-- 回到底部按钮 -->
        <transition name="fade-up">
          <button
            v-show="showScrollBtn"
            class="scroll-bottom-btn"
            @click="scrollToBottomSmooth"
            title="回到最新消息"
          >
            <span class="arrow">↓</span>
          </button>
        </transition>
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
            <a-tooltip :title="enableThinking ? '已开启深度思考（响应较慢，但更严谨）' : '开启后模型会先输出推理过程'">
              <button
                class="thinking-toggle"
                :class="{ active: enableThinking }"
                :disabled="isStreaming"
                @click="enableThinking = !enableThinking"
                type="button"
              >
                <span class="thinking-icon">💭</span>
                <span>深度思考</span>
              </button>
            </a-tooltip>
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
  position: relative;
}

.messages::-webkit-scrollbar {
  width: 5px;
}
.messages::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.07);
  border-radius: 4px;
}

/* 回到底部浮动按钮 */
.scroll-bottom-btn {
  position: sticky;
  bottom: 16px;
  margin-left: auto;
  margin-right: 8px;
  align-self: flex-end;
  width: 38px;
  height: 38px;
  border-radius: 50%;
  border: 1px solid var(--border);
  background: var(--bg-card);
  color: var(--text-primary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.18);
  transition: all 0.18s ease;
  z-index: 5;
  flex-shrink: 0;
}
.scroll-bottom-btn:hover {
  background: var(--primary);
  color: #fff;
  border-color: transparent;
  transform: translateY(-2px);
  box-shadow: 0 10px 24px rgba(99, 102, 241, 0.35);
}
.scroll-bottom-btn .arrow {
  font-size: 18px;
  font-weight: 700;
  line-height: 1;
}
.fade-up-enter-active,
.fade-up-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.fade-up-enter-from,
.fade-up-leave-to {
  opacity: 0;
  transform: translateY(8px);
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

/* AI 消息内容容器（气泡 + 引用） */
.ai-content {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  flex: 1;
}

/* 引用上标专用悬浮预览见 .inline-cite-pop */

/* 内联引用上标（在 AI 回答正文中渲染） */
:deep(.cite-ref) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 16px;
  padding: 0 5px;
  margin: 0 2px;
  border-radius: 4px;
  background: var(--primary-light, rgba(99, 102, 241, 0.14));
  color: var(--primary, #6366f1);
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
  vertical-align: super;
  transition: all 0.15s ease;
  font-variant-numeric: tabular-nums;
  text-decoration: none;
}

:deep(.cite-ref:hover) {
  background: linear-gradient(135deg, #6366f1, #818cf8);
  color: white;
  transform: translateY(-1px);
  box-shadow: 0 3px 8px rgba(99, 102, 241, 0.4);
}

/* 内联引用悬浮预览框 */
.inline-cite-pop {
  position: fixed;
  transform: translate(-50%, -100%);
  z-index: 1000;
  width: 360px;
  max-width: calc(100vw - 60px);
  background: var(--bg-card);
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
  padding: 10px 12px;
  pointer-events: auto;
  animation: fadeInUp 0.15s ease;
}

.inline-cite-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}

.inline-cite-idx {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  background: linear-gradient(135deg, #6366f1, #818cf8);
  color: white;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
}

.inline-cite-source {
  font-weight: 600;
  color: var(--primary, #6366f1);
  flex: 1;
  text-overflow: ellipsis;
  overflow: hidden;
  white-space: nowrap;
}

.inline-cite-meta {
  color: var(--text-secondary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.inline-cite-text {
  max-height: 200px;
  overflow-y: auto;
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translate(-50%, calc(-100% + 6px));
  }
  to {
    opacity: 1;
    transform: translate(-50%, -100%);
  }
}

/* 打字机光标 - 平滑呼吸 */
:deep(.typing-cursor) {
  display: inline-block;
  width: 8px;
  height: 1em;
  margin-left: 3px;
  vertical-align: text-bottom;
  border-radius: 2px;
  background: linear-gradient(180deg, #818cf8, #6366f1);
  box-shadow: 0 0 6px rgba(99, 102, 241, 0.55);
  animation: cursorPulse 1.1s ease-in-out infinite;
  transform-origin: center;
  will-change: opacity, transform;
}

@keyframes cursorPulse {
  0% {
    opacity: 0.25;
    transform: scaleY(0.85);
  }
  50% {
    opacity: 1;
    transform: scaleY(1);
  }
  100% {
    opacity: 0.25;
    transform: scaleY(0.85);
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

:root[data-theme="dark"] .ai-bubble :deep(code) {
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
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: left;
}

.ai-bubble :deep(th) {
  background: var(--bg-hover);
  font-weight: 600;
  color: var(--text-primary);
}

.ai-bubble :deep(strong) {
  font-weight: 700;
  color: var(--text-primary);
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
  box-shadow:
    0 -2px 20px rgba(0, 0, 0, 0.3),
    0 4px 20px rgba(0, 0, 0, 0.3);
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

/* 反馈跳转高亮 */
.ai-content.msg-highlight {
  position: relative;
}
.ai-content.msg-highlight::before {
  content: "";
  position: absolute;
  inset: -8px -10px;
  border-radius: 14px;
  background: linear-gradient(135deg, rgba(139, 92, 246, 0.15), rgba(99, 102, 241, 0.1));
  border: 1px solid rgba(139, 92, 246, 0.45);
  pointer-events: none;
  animation: msgHighlightPulse 1.4s ease-out 2;
  z-index: 0;
}
.ai-content.msg-highlight > * {
  position: relative;
  z-index: 1;
}
@keyframes msgHighlightPulse {
  0% { box-shadow: 0 0 0 0 rgba(139, 92, 246, 0.4); }
  70% { box-shadow: 0 0 0 12px rgba(139, 92, 246, 0); }
  100% { box-shadow: 0 0 0 0 rgba(139, 92, 246, 0); }
}

/* 反馈工具栏（重新生成 / 👍 / 👎） */
.msg-actions {
  display: flex;
  gap: 6px;
  margin-top: 6px;
  padding-left: 2px;
  opacity: 0.65;
  transition: opacity 0.15s ease;
}
.msg-actions:hover {
  opacity: 1;
}
.msg-action-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 10px;
  border-radius: 13px;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
}
.msg-action-btn .ic {
  font-size: 13px;
}
.msg-action-btn:hover:not(:disabled) {
  background: var(--bg-hover);
  color: var(--text-primary);
  border-color: var(--border-strong, var(--border));
}
.msg-action-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.msg-action-btn.active {
  background: linear-gradient(135deg, rgba(139, 92, 246, 0.18), rgba(99, 102, 241, 0.18));
  border-color: rgba(139, 92, 246, 0.5);
  color: #a78bfa;
}

/* 点踩反馈弹窗 */
:global(.dislike-popover .ant-popover-inner) {
  border-radius: 12px;
  padding: 0;
}
.dislike-form {
  width: 320px;
  padding: 14px 14px 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.dislike-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}
.dislike-reasons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.dislike-reasons :deep(.ant-tag-checkable) {
  border-radius: 12px;
  padding: 2px 10px;
  font-size: 12px;
  background: var(--bg-hover);
  color: var(--text-secondary);
  border: 1px solid var(--border);
  margin: 0;
  cursor: pointer;
  transition: all 0.15s ease;
}
.dislike-reasons :deep(.ant-tag-checkable-checked) {
  background: linear-gradient(135deg, rgba(139, 92, 246, 0.22), rgba(99, 102, 241, 0.22));
  border-color: rgba(139, 92, 246, 0.55);
  color: #a78bfa;
}
.dislike-comment :deep(textarea) {
  font-size: 12px;
  border-radius: 8px;
}
.dislike-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 2px;
}

/* 深度思考开关按钮 */
.thinking-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 12px;
  border-radius: 14px;
  background: var(--bg-hover);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.18s ease;
  margin-left: auto;
  margin-right: 10px;
}
.thinking-toggle:hover:not(:disabled) {
  border-color: rgba(99, 102, 241, 0.45);
  color: var(--text-primary);
}
.thinking-toggle.active {
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.14), rgba(168, 85, 247, 0.14));
  border-color: rgba(99, 102, 241, 0.55);
  color: var(--primary);
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.08);
}
.thinking-toggle:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.thinking-icon {
  font-size: 13px;
}

/* 深度思考过程区块（DeepSeek 风格，紧凑） */
.thinking-block {
  border-left: 2px solid rgba(99, 102, 241, 0.35);
  background: transparent;
  border-radius: 0;
  font-size: 12px;
  overflow: hidden;
  position: relative;
  padding-left: 0;
  margin: 0 0 4px 0;
}
.thinking-block.streaming::before {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: -2px;
  width: 2px;
  background: linear-gradient(180deg, transparent, rgba(99, 102, 241, 0.85), transparent);
  background-size: 100% 200%;
  animation: shimmerY 1.4s linear infinite;
}
@keyframes shimmerY {
  0% {
    background-position: 0 200%;
  }
  100% {
    background-position: 0 -200%;
  }
}
.thinking-header {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 10px;
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
  color: var(--text-muted);
  font-size: 11.5px;
  font-weight: 500;
  line-height: 1.4;
}
.thinking-header:hover {
  color: var(--text-secondary);
}
.thinking-header-icon {
  font-size: 11px;
  opacity: 0.75;
}
.thinking-header-text {
  letter-spacing: 0.2px;
}
.thinking-header-chevron {
  font-size: 9px;
  transition: transform 0.18s ease;
  opacity: 0.55;
  margin-left: 2px;
}
.thinking-header-chevron.open {
  transform: rotate(180deg);
}
.thinking-body {
  padding: 2px 12px 6px;
  color: var(--text-muted);
  line-height: 1.55;
  font-size: 12px;
  white-space: pre-wrap;
  max-height: 280px;
  overflow-y: auto;
  font-style: normal;
  opacity: 0.85;
}
.thinking-body :deep(p) {
  margin: 2px 0;
}
.thinking-body :deep(ul),
.thinking-body :deep(ol) {
  margin: 2px 0;
  padding-left: 16px;
}
.thinking-body :deep(li) {
  margin: 1px 0;
}
.thinking-body :deep(code) {
  background: rgba(99, 102, 241, 0.08);
  padding: 0 4px;
  border-radius: 3px;
  font-size: 0.9em;
}
.thinking-body :deep(pre) {
  margin: 4px 0;
  font-size: 11px;
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
