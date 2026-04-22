// 通用 SSE 流读取辅助
//
// 用法：
//   await consumeSSE(response, {
//     onEvent: (evt) => { ... },
//     signal: ctrl.signal,
//   });
//
// 协议假设：后端按 `data: <json>\n\n` 推送，每个 event 是合法 JSON。
// 非 JSON 行 / 心跳行 (`: keep-alive`) 自动忽略。

export interface SSEHandlers<T = Record<string, unknown>> {
  onEvent?: (evt: T) => void | Promise<void>;
  /** 解析失败的原始 chunk（debug 用） */
  onParseError?: (raw: string, err: unknown) => void;
}

export class SSEHttpError extends Error {
  status: number;
  body?: unknown;
  constructor(status: number, msg: string, body?: unknown) {
    super(msg);
    this.status = status;
    this.body = body;
  }
}

export async function consumeSSE<T = Record<string, unknown>>(
  response: Response,
  handlers: SSEHandlers<T> = {},
): Promise<void> {
  if (!response.ok || !response.body) {
    let body: unknown;
    let msg = `HTTP ${response.status}`;
    try {
      body = await response.json();
      const b = body as { msg?: string; detail?: string };
      msg = b?.msg || b?.detail || msg;
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new SSEHttpError(response.status, msg, body);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";

    for (const part of parts) {
      const line = part.trim();
      if (!line || line.startsWith(":")) continue; // 心跳
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload) continue;
      try {
        const evt = JSON.parse(payload) as T;
        await handlers.onEvent?.(evt);
      } catch (err) {
        handlers.onParseError?.(payload, err);
      }
    }
  }
}
