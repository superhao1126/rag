"""对话管理"""
from fastapi import APIRouter, HTTPException

from ..deps import get_rag, require_pg

router = APIRouter(prefix="/api", tags=["conversations"])


@router.get("/conversations")
async def list_conversations():
    rag = get_rag()
    require_pg(rag)
    return {"conversations": rag.vector_store.get_conversations()}


@router.post("/conversations")
async def create_conversation(body: dict = None):
    rag = get_rag()
    require_pg(rag)
    title = (body or {}).get("title", "新对话")
    conv_id = rag.vector_store.create_conversation(title)
    return {"id": conv_id, "title": title}


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    rag = get_rag()
    require_pg(rag)
    if not rag.vector_store.conversation_exists(conv_id):
        raise HTTPException(404, "对话不存在")
    messages = rag.vector_store.get_conversation_messages(conv_id)
    return {"conversation_id": conv_id, "messages": messages}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    rag = get_rag()
    require_pg(rag)
    if not rag.vector_store.conversation_exists(conv_id):
        raise HTTPException(404, "对话不存在")
    rag.vector_store.delete_conversation(conv_id)
    return {"message": f"对话 {conv_id} 已删除"}
