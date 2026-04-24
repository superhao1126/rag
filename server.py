"""
RAG Web API 服务入口 — 基于 FastAPI

职责清单（仅做装配）：
1. 创建 FastAPI 实例 + 生命周期管理
2. 注册 CORS / 统一响应中间件 / 异常处理器
3. 挂载业务路由（routers）
4. 提供前端静态文件 + SPA fallback

业务逻辑全部位于 `core/api/` 下，按职责拆分为独立 router。
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from core.config import Config
from core.pipeline import RAGPipeline
from core.task_queue import UploadTaskQueue
from core.api.deps import set_rag, set_upload_queue, get_rag_instance
from core.api.middleware import UnifiedResponseMiddleware, register_exception_handlers
from core.api.routers import register_routers


# ============================================================
#  生命周期
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    config = Config()
    config.STREAM = True
    config.HISTORY_TURNS = 5
    rag = RAGPipeline(api_key=api_key, config=config)
    if rag._use_pg:
        recovered = rag.vector_store.recover_interrupted_files()
        if recovered:
            print(f"⚠️ 已恢复 {recovered} 个中断的向量化任务，可在文件列表中重新执行")

    def process_upload(file_path: str) -> None:
        source = Path(file_path).name
        if rag._use_pg:
            rag.vector_store.mark_file_processing(source)
        try:
            rag.add_file(file_path)
            if rag._use_pg:
                rag.vector_store.mark_file_vectorized(source)
        except Exception as exc:
            if rag._use_pg:
                rag.vector_store.mark_file_failed(source, str(exc))
            raise

    upload_queue = UploadTaskQueue(processor=process_upload)
    set_rag(rag)
    set_upload_queue(upload_queue)
    upload_queue.start()
    print("✅ 全局 RAGPipeline 初始化完成")
    yield
    rag = get_rag_instance()
    upload_queue.stop()
    set_upload_queue(None)
    if rag and rag._use_pg:
        rag.vector_store.close()


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

# 统一响应包装 + 全局异常处理
app.add_middleware(UnifiedResponseMiddleware)
register_exception_handlers(app)

# 业务路由
register_routers(app)


# ============================================================
#  前端静态文件
# ============================================================
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

if FRONTEND_DIST.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIST / "assets")),
        name="assets",
    )


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


@app.get("/{full_path:path}", response_class=HTMLResponse)
async def spa_fallback(full_path: str):
    """SPA 路由 fallback — 所有未匹配路由返回 index.html"""
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
