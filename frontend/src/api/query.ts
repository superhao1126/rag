// SSE 流式问答 — 直接用 fetch 以便逐 chunk 解析
export const queryApi = {
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
};
