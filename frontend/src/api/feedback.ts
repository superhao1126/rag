// 反馈
import { http } from "./http";
import type { FeedbackSummary } from "./types";

export const feedbackApi = {
  /** 反馈汇总（看板） */
  getFeedback: (params?: { rating?: 1 | -1; limit?: number; offset?: number }) =>
    http.get<FeedbackSummary>("/feedback", { params }),

  /** 对回答打分：1=赞 / -1=踩 / 0=取消；可选 reasons / comment */
  setFeedback: (
    messageId: number,
    rating: 1 | -1 | 0,
    reasons: string[] = [],
    comment = "",
  ) => http.post(`/messages/${messageId}/feedback`, { rating, reasons, comment }),
};
