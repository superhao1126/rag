// 所有后端返回的领域类型集中在此

export interface KbFile {
  id: string;
  source: string;
  status: "queued" | "processing" | "vectorized" | "failed";
  error_message?: string | null;
  chunk_count: number;
  total_chars: number;
  uploaded_at: string | null;
}

export interface KbFileInfo extends KbFile {
  min_chars: number;
  max_chars: number;
  avg_chars: number;
  last_updated_at: string | null;
  last_vectorized_at?: string | null;
  chunk_index_range: [number, number];
}

export interface KbFileListResponse {
  files: KbFile[];
  total: number;
  page: number;
  page_size: number;
}

export interface BatchDeleteResult {
  deleted: { id: string; source: string }[];
  missing: string[];
  total: number;
}

export interface RagStats {
  total_chunks: number;
  embedding_dimension: number;
  storage_type: string;
  storage_path?: string;
  sources: string[];
  bm25_documents: number;
  bm25_vocabulary: number;
}

export interface ChunkItem {
  index?: number;
  chunk_index?: number;
  text?: string;
  char_count?: number;
  start?: number;
  end?: number;
  source?: string;
  chunk_type?: "api" | "normal" | string;
  is_long_split?: boolean;
  title?: string;
}

export interface ChunkStats {
  total_chunks?: number;
  avg_chunk_size?: number;
  max_chunk_size?: number;
  min_chunk_size?: number;
  overlap_chars?: number;
  chunk_size_setting?: number;
  overlap_setting?: number;
  min_chunk_size_setting?: number;
  api_max_chunk_size_setting?: number;
  original_length?: number;
  [key: string]: number | undefined;
}

export interface ChunkSource {
  original_text?: string;
  original_length?: number;
  chunks: ChunkItem[];
  stats: ChunkStats;
}

export interface LlmModelUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
}

export interface LlmStats {
  default_model: string | null;
  totals: LlmModelUsage;
  models: Record<string, LlmModelUsage>;
  calls_by_type: Record<string, number>;
  last_call_at: number | null;
  persisted?: boolean;
}

export interface LlmCallEntry {
  id: number;
  ts: number;
  model: string;
  call_type: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  note: string | null;
}

export interface LlmCallsPage {
  items: LlmCallEntry[];
  total: number;
  page: number;
  page_size: number;
}

export interface LlmTrendBucketTypeStat {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
}

export interface LlmTrendPoint {
  bucket: string;
  ts: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
  by_type?: Record<string, LlmTrendBucketTypeStat>;
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
