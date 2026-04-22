// 对话管理
import { http } from "./http";
import type { Conversation, Message } from "./types";

export const conversationsApi = {
  getConversations: () => http.get<{ conversations: Conversation[] }>("/conversations"),

  createConversation: (title?: string) =>
    http.post<{ id: string; title: string }>("/conversations", { title: title ?? "新对话" }),

  getConversation: (id: string) =>
    http.get<{ conversation_id: string; messages: Message[] }>(`/conversations/${id}`),

  deleteConversation: (id: string) => http.delete(`/conversations/${id}`),
};
