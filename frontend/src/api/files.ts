// 文件管理
import { http } from "./http";
import { consumeSSE } from "./sse";
import type { KbFile, KbFileInfo, KbFileListResponse, BatchDeleteResult } from "./types";

/** 上传进度事件（后端 SSE 推送） */
export type UploadEvent =
  | { type: "progress"; percent?: number; message?: string }
  | { type: "done"; chunk_count: number; total_chars: number; [k: string]: unknown }
  | { type: "error"; message: string };

export interface UploadStreamHandlers {
  onProgress?: (percent: number, message: string) => void;
  onDone?: (info: { chunk_count: number; total_chars: number }) => void | Promise<void>;
  onError?: (msg: string) => void;
}

export const filesApi = {
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return http.post("/upload", form);
  },

  /** SSE 流式上传 — 暴露原生 Response（保留作底层入口） */
  uploadStream: (file: File): Promise<Response> => {
    const form = new FormData();
    form.append("file", file);
    return fetch("/api/upload", { method: "POST", body: form });
  },

  /**
   * 高层封装：上传文件并按事件类型回调，自动解析 SSE。
   * 推荐组件使用本方法，避免重复写流读取/JSON 解析样板代码。
   */
  uploadWithProgress: async (file: File, handlers: UploadStreamHandlers = {}) => {
    const form = new FormData();
    form.append("file", file);
    const resp = await fetch("/api/upload", { method: "POST", body: form });
    await consumeSSE<UploadEvent>(resp, {
      onEvent: async (evt) => {
        if (evt.type === "progress") {
          handlers.onProgress?.(evt.percent ?? 0, evt.message ?? "");
        } else if (evt.type === "done") {
          await handlers.onDone?.({
            chunk_count: evt.chunk_count,
            total_chars: evt.total_chars,
          });
        } else if (evt.type === "error") {
          handlers.onError?.(evt.message);
        }
      },
    });
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
