// SSE 流式问答
import { consumeSSE } from "./sse";
import type { RetrievalResult, LlmStats } from "./types";

export interface UsagePayload {
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

/** 问答事件（与后端 SSE 协议对齐） */
export type QueryEvent =
  | { type: "retrieval"; data: { mode: string; results: RetrievalResult[] } }
  | { type: "thinking"; content: string }
  | { type: "content"; content: string }
  | { type: "usage"; usage: UsagePayload; totals: LlmStats }
  | { type: "done"; message_id?: number; [k: string]: unknown }
  | { type: "title"; title: string }
  | { type: "error"; message: string };

export interface QueryStreamArgs {
  conversationId: string;
  question: string;
  mode: string;
  enableThinking: boolean;
  regenerate?: boolean;
  signal?: AbortSignal;
}

export interface QueryStreamHandlers {
  onRetrieval?: (mode: string, results: RetrievalResult[]) => void;
  onThinking?: (chunk: string) => void;
  onContent?: (chunk: string) => void;
  onTitle?: (title: string) => void;
  onUsage?: (usage: UsagePayload, totals: LlmStats) => void;
  onDone?: (info: { message_id?: number }) => void | Promise<void>;
  onError?: (msg: string) => void;
}

export const queryApi = {
  /** 底层入口：返回原生 Response，组件可自行读取流 */
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

  /** 高层封装：按事件类型分发到回调，避免组件重复写解析逻辑 */
  queryWithEvents: async (args: QueryStreamArgs, handlers: QueryStreamHandlers = {}) => {
    const resp = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: args.conversationId,
        question: args.question,
        mode: args.mode,
        enable_thinking: args.enableThinking,
        regenerate: args.regenerate ?? false,
      }),
      signal: args.signal,
    });

    await consumeSSE<QueryEvent>(resp, {
      onEvent: async (evt) => {
        switch (evt.type) {
          case "retrieval":
            handlers.onRetrieval?.(evt.data.mode, evt.data.results);
            break;
          case "thinking":
            handlers.onThinking?.(evt.content);
            break;
          case "content":
            handlers.onContent?.(evt.content);
            break;
          case "title":
            handlers.onTitle?.(evt.title);
            break;
          case "usage":
            handlers.onUsage?.(evt.usage, evt.totals);
            break;
          case "done":
            await handlers.onDone?.({ message_id: evt.message_id });
            break;
          case "error":
            handlers.onError?.(evt.message);
            break;
        }
      },
    });
  },
};
