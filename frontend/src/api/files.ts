// 文件管理
import { http } from "./http";
import type { KbFile, KbFileInfo, KbFileListResponse, BatchDeleteResult } from "./types";

export const filesApi = {
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return http.post("/upload", form);
  },

  /** SSE 流式上传，返回原生 Response（供前端逐事件解析进度） */
  uploadStream: (file: File): Promise<Response> => {
    const form = new FormData();
    form.append("file", file);
    return fetch("/api/upload", { method: "POST", body: form });
  },

  getFiles: (params?: { page?: number; page_size?: number; keyword?: string }) =>
    http.get<KbFileListResponse>("/files", { params }),

  getFileInfo: (id: string) =>
    http.get<KbFileInfo>(`/files/by-id/${encodeURIComponent(id)}/info`),

  /** 推荐：按 ID 删除，避免 source 路径编码问题 */
  deleteFileById: (id: string) => http.delete(`/files/by-id/${encodeURIComponent(id)}`),

  /** 兼容入口：可按 ID 或 source 删除 */
  deleteFile: (idOrSource: string, byId = false) =>
    byId
      ? http.delete(`/files/by-id/${encodeURIComponent(idOrSource)}`)
      : http.delete(`/files/${encodeURIComponent(idOrSource)}`),

  batchDeleteFiles: (payload: { ids?: string[]; sources?: string[] }) =>
    http.post<BatchDeleteResult>("/files/batch-delete", payload),
};

export type { KbFile, KbFileInfo, KbFileListResponse, BatchDeleteResult };
