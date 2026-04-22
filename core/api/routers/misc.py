"""辅助接口：统计 / 分块 / LLM token 用量"""
from fastapi import APIRouter, HTTPException, Query

from ..deps import get_rag
from core.llm_stats import llm_stats

router = APIRouter(prefix="/api", tags=["misc"])


@router.get("/stats")
async def get_stats():
    return get_rag().get_stats()


@router.get("/llm/stats")
async def get_llm_stats():
    """LLM 调用 token 用量（按模型聚合）"""
    rag = get_rag()
    default_model = getattr(rag.config, "LLM_MODEL", None)
    return llm_stats.snapshot(default_model)


@router.post("/llm/stats/reset")
async def reset_llm_stats():
    llm_stats.reset()
    rag = get_rag()
    return llm_stats.snapshot(getattr(rag.config, "LLM_MODEL", None))


@router.get("/llm/calls")
async def list_llm_calls(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    call_type: str = Query(None, description="过滤调用类型：chat / title / embedding"),
    model: str = Query(None, description="过滤模型名"),
):
    """LLM / Embedding 调用流水（分页）"""
    return llm_stats.list_calls(
        page=page, page_size=page_size, call_type=call_type, model=model
    )


@router.get("/llm/trend")
async def llm_trend(
    bucket: str = Query("day", pattern="^(day|hour)$"),
    points: int = Query(30, ge=1, le=365),
):
    """按天/按小时聚合的 token 用量趋势"""
    return llm_stats.trend(bucket=bucket, points=points)


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
