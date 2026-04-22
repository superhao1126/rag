// API 入口：聚合各模块导出，保持 `import api from "@/api"` 用法不变
//
// 推荐：新代码直接按模块导入，例如:
//   import { filesApi } from "@/api/files";
//   import type { KbFile } from "@/api/types";
//
import { filesApi } from "./files";
import { conversationsApi } from "./conversations";
import { feedbackApi } from "./feedback";
import { queryApi } from "./query";
import { statsApi } from "./stats";

export { http, ApiError } from "./http";
export * from "./types";
export { filesApi, conversationsApi, feedbackApi, queryApi, statsApi };

// 兼容默认导出（旧组件继续 `api.upload(...)` / `api.getFiles(...)` 即可）
const api = {
  ...filesApi,
  ...conversationsApi,
  ...feedbackApi,
  ...queryApi,
  ...statsApi,
};

export default api;
