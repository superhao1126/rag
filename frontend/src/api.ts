import axios from 'axios'

const http = axios.create({ baseURL: '/api' })

export default {
  // ---- 文件管理 ----
  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return http.post('/upload', form)
  },

  /** SSE 流式上传，返回 Response（供前端逐事件解析进度） */
  uploadStream: (file: File): Promise<Response> => {
    const form = new FormData()
    form.append('file', file)
    return fetch('/api/upload', { method: 'POST', body: form })
  },

  getFiles: () => http.get<{ files: KbFile[] }>('/files'),

  deleteFile: (source: string) => http.delete(`/files/${encodeURIComponent(source)}`),

  // ---- 对话管理 ----
  getConversations: () => http.get<{ conversations: Conversation[] }>('/conversations'),

  createConversation: (title?: string) =>
    http.post<{ id: string; title: string }>('/conversations', { title: title ?? '新对话' }),

  getConversation: (id: string) =>
    http.get<{ conversation_id: string; messages: Message[] }>(`/conversations/${id}`),

  deleteConversation: (id: string) => http.delete(`/conversations/${id}`),

  // ---- 统计 ----
  getStats: () => http.get('/stats'),

  getChunks: (source?: string) => http.get('/chunks', { params: source ? { source } : undefined }),

  /** SSE 流式问答 */
  queryStream: (conversationId: string, question: string, mode: string, signal?: AbortSignal) =>
    fetch('/api/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_id: conversationId, question, mode }),
      signal,
    }),
}

export interface KbFile {
  source: string
  chunk_count: number
  total_chars: number
  uploaded_at: string | null
}

export interface Conversation {
  id: string
  title: string
  created_at: string | null
  updated_at: string | null
  message_count: number
}

export interface Message {
  id: number
  role: 'user' | 'assistant'
  content: string
  retrieval_info: RetrievalInfo | null
  created_at: string | null
}

export interface RetrievalInfo {
  mode: string
  results: RetrievalResult[]
}

export interface RetrievalResult {
  source: string
  chunk_index: number
  similarity?: number
  bm25_score?: number
  rrf_score?: number
  text_preview: string
}
