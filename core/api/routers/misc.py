"""辅助接口：统计 / 分块"""
from fastapi import APIRouter, HTTPException, Query

from ..deps import get_rag

router = APIRouter(prefix="/api", tags=["misc"])


@router.get("/stats")
async def get_stats():
    return get_rag().get_stats()


@router.get("/chunks")
async def get_chunks(
    source: str = Query(None),
    file_id: str = Query(None, description="文件 ID（与 source 二选一，优先使用）"),
):
    """获取分块可视化数据"""
    rag = get_rag()
    if file_id and not source:
        if rag._use_pg:
            source = rag.vector_store.get_source_by_id(file_id)
        if not source:
            raise HTTPException(404, f"文件 ID 不存在: {file_id}")
    viz_data = rag.get_chunk_visualization(source)

    if not viz_data:
        chunks = rag.vector_store.metadata
        if source:
            chunks = [c for c in chunks if c.get("source") == source]
        return {"chunks": chunks, "stats": {"total_chunks": len(chunks)}}

    return viz_data
