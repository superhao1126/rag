// 共用 axios 实例 + 统一响应拦截
//
// 后端响应格式: { code, data, msg }
//  - code === 200 → 用 data 替换 response.data，调用方继续按 res.data 取真实数据
//  - 其它 code   → 抛出 Error(msg)，调用方走 catch
//
import axios, { AxiosError } from "axios";

export const http = axios.create({ baseURL: "/api" });

interface UnifiedBody {
  code: number;
  data: unknown;
  msg: string;
}

function isUnified(body: unknown): body is UnifiedBody {
  return (
    !!body &&
    typeof body === "object" &&
    "code" in body &&
    "data" in body &&
    "msg" in body
  );
}

class ApiError extends Error {
  code?: number;
  data?: unknown;
  constructor(msg: string, code?: number, data?: unknown) {
    super(msg);
    this.code = code;
    this.data = data;
  }
}

http.interceptors.response.use(
  (response) => {
    const body = response.data;
    if (isUnified(body)) {
      if (body.code === 200) {
        response.data = body.data;
        return response;
      }
      return Promise.reject(
        new ApiError(body.msg || `请求失败 (code=${body.code})`, body.code, body.data),
      );
    }
    return response;
  },
  (error: AxiosError) => {
    const data = error.response?.data;
    if (isUnified(data)) {
      return Promise.reject(new ApiError(data.msg || error.message, data.code, data.data));
    }
    return Promise.reject(error);
  },
);

export { ApiError };
