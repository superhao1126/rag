"""文件管理：上传、列表、删除、详情、批量删除、分块查询"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from core.reader import FileReader
from core.pg_vectorstore import file_id_for

from ..deps import get_rag, require_pg

router = APIRouter(prefix="/api", tags=["files"])


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传文件并向量化，通过 SSE 实时推送进度。"""
    suffix = Path(file.filename).suffix.lower()
    if suffix not in FileReader.SUPPORTED_EXTENSIONS:
        raise HTTPException(
            400,
            f"不支持的格式: {suffix}，支持: {', '.join(FileReader.SUPPORTED_EXTENSIONS)}",
        )

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
                yield ": keep-alive\n\n"

        try:
            await task
        except Exception as e:
            yield _fmt({"type": "error", "message": str(e)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/files")
async def list_files(page: int = 1, page_size: int = 20, keyword: str = ""):
    rag = get_rag()
    require_pg(rag)
    if page < 1:
        page = 1
    if page_size > 200:
        page_size = 200
    if page_size < 0:
        page_size = 0
    return rag.vector_store.get_files(page=page, page_size=page_size, keyword=keyword)


@router.get("/db/files")
async def list_db_files_compat(page: int = 1, page_size: int = 20, keyword: str = ""):
    """旧接口别名，保持向后兼容"""
    return await list_files(page=page, page_size=page_size, keyword=keyword)


@router.delete("/files/by-id/{file_id}")
async def delete_file_by_id(file_id: str):
    rag = get_rag()
    require_pg(rag)
    source = rag.vector_store.get_source_by_id(file_id)
    if not source:
        raise HTTPException(404, f"文件 ID 不存在: {file_id}")
    rag.vector_store.delete_file(source)
    rag.bm25_index.fit(rag.vector_store.metadata)
    if rag.vector_store.metadata:
        rag.vector_store.save_bm25(rag.bm25_index.to_dict())
    return {"id": file_id, "source": source, "message": f"已删除文件: {source}"}


@router.get("/files/by-id/{file_id}/info")
async def get_file_info_by_id(file_id: str):
    rag = get_rag()
    require_pg(rag)
    source = rag.vector_store.get_source_by_id(file_id)
    if not source:
        raise HTTPException(404, f"文件 ID 不存在: {file_id}")
    info = rag.vector_store.get_file_info(source)
    if not info:
        raise HTTPException(404, f"文件不存在: {source}")
    return info


@router.post("/files/batch-delete")
async def batch_delete_files(payload: dict = Body(...)):
    """批量删除文件，支持按 ids 或 sources 调用。"""
    rag = get_rag()
    require_pg(rag)

    raw_ids = payload.get("ids") or []
    raw_sources = payload.get("sources") or []
    if not isinstance(raw_ids, list) or not isinstance(raw_sources, list):
        raise HTTPException(400, "ids 与 sources 必须为数组")
    if not raw_ids and not raw_sources:
        raise HTTPException(400, "ids 或 sources 至少提供一个非空数组")

    targets: list[tuple[str, str]] = []
    missing: list[str] = []

    for fid in raw_ids:
        if not isinstance(fid, str) or not fid.strip():
            continue
        src = rag.vector_store.get_source_by_id(fid)
        if src:
            targets.append((fid, src))
        else:
            missing.append(fid)

    for src in raw_sources:
        if not isinstance(src, str) or not src.strip():
            continue
        if rag.vector_store.file_exists(src):
            targets.append((file_id_for(src), src))
        else:
            missing.append(src)

    seen = set()
    unique_targets = []
    for fid, src in targets:
        if src in seen:
            continue
        seen.add(src)
        unique_targets.append((fid, src))

    deleted = []
    for fid, src in unique_targets:
        rag.vector_store.delete_file(src)
        deleted.append({"id": fid, "source": src})

    if deleted:
        rag.bm25_index.fit(rag.vector_store.metadata)
        if rag.vector_store.metadata:
            rag.vector_store.save_bm25(rag.bm25_index.to_dict())

    return {"deleted": deleted, "missing": missing, "total": len(deleted)}


# ⚠️ 路径参数包含 :path 的路由必须放在最后，否则会"吃掉"前面更具体的路由
@router.delete("/files/{source:path}")
async def delete_file_by_source(source: str):
    """按 source 路径删除（兼容旧用法，新代码推荐 /files/by-id/{id}）"""
    rag = get_rag()
    require_pg(rag)
    if not rag.vector_store.file_exists(source):
        raise HTTPException(404, f"文件不存在: {source}")
    rag.vector_store.delete_file(source)
    rag.bm25_index.fit(rag.vector_store.metadata)
    if rag.vector_store.metadata:
        rag.vector_store.save_bm25(rag.bm25_index.to_dict())
    return {"message": f"已删除文件: {source}"}
