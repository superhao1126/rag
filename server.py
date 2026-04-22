"""
RAG Web API 服务 — 基于 FastAPI
全局知识库（无会话隔离），对话历史持久化到 PostgreSQL。
"""

import os
import json
import asyncio
import tempfile
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from core.config import Config
from core.reader import FileReader
from core.pipeline import RAGPipeline

# ============================================================
#  全局单例
# ============================================================
_rag: RAGPipeline | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化全局 RAGPipeline"""
    global _rag
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    config = Config()
    config.STREAM = True
    config.HISTORY_TURNS = 5
    _rag = RAGPipeline(api_key=api_key, config=config)
    print("✅ 全局 RAGPipeline 初始化完成")
    yield
    if _rag and _rag._use_pg:
        _rag.vector_store.close()


def get_rag() -> RAGPipeline:
    if _rag is None:
        raise HTTPException(503, "服务尚未初始化")
    return _rag


# ============================================================
#  FastAPI 应用
# ============================================================
app = FastAPI(
    title="RAG 知识问答系统",
    description="上传文件 → 向量化 → 混合检索问答，对话历史持久化",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
#  文件管理
# ============================================================

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传文件并向量化，通过 SSE 实时推送进度。"""
    suffix = Path(file.filename).suffix.lower()
    if suffix not in FileReader.SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"不支持的格式: {suffix}，支持: {', '.join(FileReader.SUPPORTED_EXTENSIONS)}")

    rag = get_rag()
    upload_dir = os.path.join(rag.temp_dir, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, file.filename)

    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def on_progress(event: dict):
        loop.call_soon_threadsafe(queue.put_nowait, event)

    async def event_stream():
        task = asyncio.create_task(
            asyncio.to_thread(rag.add_file_with_progress, file_path, on_progress)
        )

        def _fmt(event: dict) -> str:
            return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.3)
                yield _fmt(event)
                if event.get("type") in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                if task.done():
                    if task.exception():
                        yield _fmt({"type": "error", "message": str(task.exception())})
                    break
                # 发送心跳保持连接
                yield ": keep-alive\n\n"

        try:
            await task
        except Exception as e:
            yield _fmt({"type": "error", "message": str(e)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/files")
async def list_files():
    """列出知识库中所有已向量化的文件"""
    rag = get_rag()
    if not rag._use_pg:
        raise HTTPException(400, "当前未启用 PostgreSQL 存储")
    files = rag.vector_store.get_files()
    return {"files": files}


@app.delete("/api/files/{source:path}")
async def delete_file(source: str):
    """从知识库中删除指定文件"""
    rag = get_rag()
    if not rag._use_pg:
        raise HTTPException(400, "当前未启用 PostgreSQL 存储")
    if not rag.vector_store.file_exists(source):
        raise HTTPException(404, f"文件不存在: {source}")
    rag.vector_store.delete_file(source)
    # 重建 BM25 索引
    rag.bm25_index.fit(rag.vector_store.metadata)
    if rag.vector_store.metadata:
        rag.vector_store.save_bm25(rag.bm25_index.to_dict())
    return {"message": f"已删除文件: {source}"}


# ============================================================
#  对话管理
# ============================================================

@app.get("/api/conversations")
async def list_conversations():
    rag = get_rag()
    if not rag._use_pg:
        raise HTTPException(400, "当前未启用 PostgreSQL 存储")
    conversations = rag.vector_store.get_conversations()
    return {"conversations": conversations}


@app.post("/api/conversations")
async def create_conversation(body: dict = None):
    rag = get_rag()
    if not rag._use_pg:
        raise HTTPException(400, "当前未启用 PostgreSQL 存储")
    title = (body or {}).get("title", "新对话")
    conv_id = rag.vector_store.create_conversation(title)
    return {"id": conv_id, "title": title}


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    rag = get_rag()
    if not rag._use_pg:
        raise HTTPException(400, "当前未启用 PostgreSQL 存储")
    if not rag.vector_store.conversation_exists(conv_id):
        raise HTTPException(404, "对话不存在")
    messages = rag.vector_store.get_conversation_messages(conv_id)
    return {"conversation_id": conv_id, "messages": messages}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    rag = get_rag()
    if not rag._use_pg:
        raise HTTPException(400, "当前未启用 PostgreSQL 存储")
    if not rag.vector_store.conversation_exists(conv_id):
        raise HTTPException(404, "对话不存在")
    rag.vector_store.delete_conversation(conv_id)
    return {"message": f"对话 {conv_id} 已删除"}


@app.get("/api/feedback")
async def get_feedback_summary(
    rating: int = Query(None, description="筛选: 1=赞, -1=踩, 不传=全部"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    rag = get_rag()
    if not rag._use_pg:
        raise HTTPException(400, "当前未启用 PostgreSQL 存储")
    if rating is not None and rating not in (1, -1):
        raise HTTPException(400, "rating 只能是 1 或 -1")
    return rag.vector_store.get_feedback_summary(rating=rating, limit=limit, offset=offset)


@app.post("/api/messages/{message_id}/feedback")
async def set_message_feedback(message_id: int, body: dict):
    """对助手回答打分：rating = 1(👍) / -1(👎) / 0(取消)
    可选 reasons (list[str])、comment (str) —— 仅 -1 时建议提供。
    """
    rag = get_rag()
    if not rag._use_pg:
        raise HTTPException(400, "当前未启用 PostgreSQL 存储")
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


# ============================================================
#  问答（SSE 流式）
# ============================================================

@app.post("/api/query")
async def query_question(body: dict):
    """SSE 流式问答，支持对话持久化"""
    conversation_id = body.get("conversation_id", "").strip()
    question = body.get("question", "").strip()
    mode = body.get("mode", "hybrid")
    enable_thinking = bool(body.get("enable_thinking", False))
    regenerate = bool(body.get("regenerate", False))

    if not question:
        raise HTTPException(400, "问题不能为空")

    rag = get_rag()

    if rag._use_pg:
        if not conversation_id:
            raise HTTPException(400, "conversation_id 不能为空")
        if not rag.vector_store.conversation_exists(conversation_id):
            raise HTTPException(404, "对话不存在，请先创建对话")

    if rag.get_stats()["文本块总数"] == 0:
        raise HTTPException(400, "知识库为空，请先上传文件")

    async def event_stream():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        DONE = object()

        def producer():
            try:
                for chunk in rag.query_with_conversation(
                    question=question,
                    conversation_id=conversation_id,
                    mode=mode,
                    enable_thinking=enable_thinking,
                    regenerate=regenerate,
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as e:
                traceback.print_exc()
                err = f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
                loop.call_soon_threadsafe(queue.put_nowait, err)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, DONE)

        task = asyncio.create_task(asyncio.to_thread(producer))
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is DONE:
                    break
                yield item
        finally:
            await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ============================================================
#  辅助接口
# ============================================================

@app.get("/api/stats")
async def get_stats():
    return get_rag().get_stats()


@app.get("/api/chunks")
async def get_chunks(source: str = Query(None)):
    """获取分块可视化数据"""
    rag = get_rag()
    viz_data = rag.get_chunk_visualization(source)

    if not viz_data:
        chunks = rag.vector_store.metadata
        if source:
            chunks = [c for c in chunks if c.get("source") == source]
        return {"chunks": chunks, "stats": {"total_chunks": len(chunks)}}

    return viz_data


# ---- 保留旧接口别名，避免前端迁移期间 404 ----
@app.get("/api/db/files")
async def list_db_files_compat():
    return await list_files()


# ============================================================
#  前端静态文件
# ============================================================
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")


@app.get("/favicon.ico")
async def favicon():
    ico = FRONTEND_DIST / "favicon.ico"
    if ico.exists():
        return FileResponse(str(ico))
    raise HTTPException(404)


@app.get("/", response_class=HTMLResponse)
async def root():
    vue_index = FRONTEND_DIST / "index.html"
    if vue_index.exists():
        return vue_index.read_text(encoding="utf-8")
    return "<h1>RAG API</h1><p>请先构建前端: cd frontend && pnpm build</p>"


# SPA fallback — 所有未匹配路由返回 index.html
@app.get("/{full_path:path}", response_class=HTMLResponse)
async def spa_fallback(full_path: str):
    vue_index = FRONTEND_DIST / "index.html"
    if vue_index.exists():
        return vue_index.read_text(encoding="utf-8")
    raise HTTPException(404)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    print(f"\n🚀 RAG Web 服务启动中...")
    print(f"   访问地址: http://localhost:{port}")
    print(f"   API 文档: http://localhost:{port}/docs\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
