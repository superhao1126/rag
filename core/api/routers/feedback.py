"""反馈管理：汇总看板 + 对消息打分"""
from fastapi import APIRouter, HTTPException, Query

from ..deps import get_rag, require_pg

router = APIRouter(prefix="/api", tags=["feedback"])


@router.get("/feedback")
async def get_feedback_summary(
    rating: int = Query(None, description="筛选: 1=赞, -1=踩, 不传=全部"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    rag = get_rag()
    require_pg(rag)
    if rating is not None and rating not in (1, -1):
        raise HTTPException(400, "rating 只能是 1 或 -1")
    return rag.vector_store.get_feedback_summary(rating=rating, limit=limit, offset=offset)


@router.post("/messages/{message_id}/feedback")
async def set_message_feedback(message_id: int, body: dict):
    """对助手回答打分：rating = 1(👍) / -1(👎) / 0(取消)"""
    rag = get_rag()
    require_pg(rag)
    rating = body.get("rating")
    if rating not in (1, -1, 0):
        raise HTTPException(400, "rating 必须是 1 / -1 / 0")
    reasons = body.get("reasons") or []
    if not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons):
        raise HTTPException(400, "reasons 必须是字符串数组")
    comment = body.get("comment") or ""
    if not isinstance(comment, str):
        raise HTTPException(400, "comment 必须是字符串")
    db_val = None if rating == 0 else rating
    if not rag.vector_store.set_message_feedback(
        message_id,
        db_val,
        reasons if db_val is not None else None,
        comment if db_val is not None else None,
    ):
        raise HTTPException(404, "消息不存在")
    return {
        "message_id": message_id,
        "rating": rating,
        "reasons": reasons,
        "comment": comment,
    }
