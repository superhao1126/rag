// 统计 + 分块 + LLM token 用量
import { http } from "./http";
import type { RagStats, LlmStats, LlmCallsPage, LlmTrendPoint, ChunkSource } from "./types";

export const statsApi = {
  getStats: () => http.get<RagStats>("/stats"),

  getChunks: (params?: { source?: string; file_id?: string }) =>
    http.get<ChunkSource | Record<string, ChunkSource>>("/chunks", { params: params || undefined }),

  getLlmStats: () => http.get<LlmStats>("/llm/stats"),

  resetLlmStats: () => http.post<LlmStats>("/llm/stats/reset"),

  getLlmCalls: (params: { page?: number; page_size?: number; call_type?: string; model?: string } = {}) =>
    http.get<LlmCallsPage>("/llm/calls", { params }),

  getLlmTrend: (params: { bucket: "day" | "hour"; points: number }) =>
    http.get<LlmTrendPoint[]>("/llm/trend", { params }),
};
