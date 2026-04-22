// 统计 + 分块
import { http } from "./http";
import type { RagStats } from "./types";

export const statsApi = {
  getStats: () => http.get<RagStats>("/stats"),

  getChunks: (params?: { source?: string; file_id?: string }) =>
    http.get("/chunks", { params: params || undefined }),
};
