import axios from "axios";

const http = axios.create({ baseURL: "/api" });

export default {
  // ---- 文件管理 ----
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return http.post("/upload", form);
  },

  /** SSE 流式上传，返回 Response（供前端逐事件解析进度） */
  uploadStream: (file: File): Promise<Response> => {
    const form = new FormData();
    form.append("file", file);
    return fetch("/api/upload", { method: "POST", body: form });
  },

  getFiles: () => http.get<{ files: KbFile[] }>("/files"),

  deleteFile: (source: string) => http.delete(`/files/${encodeURIComponent(source)}`),

  // ---- 对话管理 ----
  getConversations: () => http.get<{ conversations: Conversation[] }>("/conversations"),

  createConversation: (title?: string) =>
    http.post<{ id: string; title: string }>("/conversations", { title: title ?? "新对话" }),

  getConversation: (id: string) =>
    http.get<{ conversation_id: string; messages: Message[] }>(`/conversations/${id}`),

  deleteConversation: (id: string) => http.delete(`/conversations/${id}`),

  // ---- 统计 ----
  getStats: () => http.get("/stats"),

  getChunks: (source?: string) => http.get("/chunks", { params: source ? { source } : undefined }),

  /** SSE 流式问答 */
  queryStream: (
    conversationId: string,
    question: string,
    mode: string,
    enableThinking: boolean,
    signal?: AbortSignal,
    regenerate = false,
  ) =>
    fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: conversationId,
        question,
        mode,
        enable_thinking: enableThinking,
        regenerate,
      }),
      signal,
    }),

  /** 反馈汇总（看板） */
  getFeedback: (params?: { rating?: 1 | -1; limit?: number; offset?: number }) =>
    http.get<FeedbackSummary>("/feedback", { params }),

  /** 对回答进行反馈：1=赞 / -1=踩 / 0=取消；可选 reasons / comment */
  setFeedback: (messageId: number, rating: 1 | -1 | 0, reasons: string[] = [], comment = "") =>
    http.post(`/messages/${messageId}/feedback`, { rating, reasons, comment }),
};

export interface KbFile {
  source: string;
  chunk_count: number;
  total_chars: number;
  uploaded_at: string | null;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string | null;
  updated_at: string | null;
  message_count: number;
}

export interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  retrieval_info: RetrievalInfo | null;
  created_at: string | null;
  feedback: number | null;
  feedback_reasons?: string[];
  feedback_comment?: string | null;
}

export interface RetrievalInfo {
  mode: string;
  results: RetrievalResult[];
  thinking?: string;
}

export interface RetrievalResult {
  source: string;
  chunk_index: number;
  similarity?: number;
  bm25_score?: number;
  rrf_score?: number;
  text_preview: string;
}

export interface FeedbackSummary {
  stats: {
    up: number;
    down: number;
    total_with_feedback: number;
    total_answers: number;
  };
  reason_counts: { reason: string; count: number }[];
  items: FeedbackItem[];
  trend: { date: string; up: number; down: number }[];
}

export interface FeedbackItem {
  id: number;
  conversation_id: string;
  conversation_title: string | null;
  answer: string;
  rating: 1 | -1;
  reasons: string[];
  comment: string | null;
  created_at: string | null;
  question: string | null;
}
