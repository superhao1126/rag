"""文件管理：上传、列表、删除、详情、批量删除、分块查询"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from core.reader import FileReader
from core.pg_vectorstore import file_id_for

from ..deps import get_rag, get_upload_queue, require_pg

router = APIRouter(prefix="/api", tags=["files"])


@router.post("/upload", status_code=202)
async def upload_file(file: UploadFile = File(...)):
    """
    上传文件 —— 仅做"接收 + 落盘"，处理流程（清洗/分块/向量化/入库）进入任务队列。

    用户视角：HTTP 上传完成即视为"上传成功"。前端通过轮询 `/api/files` 列表
    判断文件是否已处理完毕（处理完后会出现在列表中）。
    """
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

    # 同步把文件落盘 —— 这一步走完，HTTP 请求才会返回，前端进度条达到 100%
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    if rag._use_pg:
        rag.vector_store.upsert_file(
            file.filename,
            local_path=file_path,
            status="queued",
            error_message=None,
        )
    task = get_upload_queue().enqueue(file_path, file.filename)

    return {
        "task_id": task["task_id"],
        "filename": file.filename,
        "size": len(content),
        "message": "上传成功，已进入后台任务队列",
    }


@router.post("/upload/stream")
async def upload_file_stream(file: UploadFile = File(...)):
    """SSE 流式上传（保留旧接口兼容）—— 实时推送清洗/分块/向量化进度。

    新前端使用 `/api/upload` 即可；本接口供需要观察细节的调试场景或外部脚本使用。
    """
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

    if rag._use_pg:
        rag.vector_store.upsert_file(
            file.filename,
            local_path=file_path,
            status="queued",
            error_message=None,
        )

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def on_progress(event: dict):
        loop.call_soon_threadsafe(queue.put_nowait, event)

    async def event_stream():
        def process_upload_stream():
            source = file.filename
            if rag._use_pg:
                rag.vector_store.mark_file_processing(source)
            try:
                rag.add_file_with_progress(file_path, on_progress)
                if rag._use_pg:
                    rag.vector_store.mark_file_vectorized(source)
            except Exception as exc:
                if rag._use_pg:
                    rag.vector_store.mark_file_failed(source, str(exc))
                raise

        task = asyncio.create_task(asyncio.to_thread(process_upload_stream))

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


@router.get("/upload/queue")
async def get_upload_queue_stats():
    return get_upload_queue().stats()


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


@router.post("/files/by-id/{file_id}/revectorize")
async def revectorize_file_by_id(file_id: str):
    rag = get_rag()
    require_pg(rag)
    record = rag.vector_store.get_file_record(file_id=file_id)
    if not record:
        raise HTTPException(404, f"文件 ID 不存在: {file_id}")
    if record["status"] == "vectorized":
        raise HTTPException(400, "文件已向量化，无需重试")
    if record["status"] in {"queued", "processing"}:
        raise HTTPException(409, "文件已在队列中或正在处理")

    local_path = (record.get("local_path") or "").strip()
    if not local_path or not os.path.exists(local_path):
        rag.vector_store.mark_file_failed(record["source"], "原始上传临时文件不存在，请重新上传")
        raise HTTPException(400, "原始上传临时文件不存在，请重新上传")

    rag.vector_store.upsert_file(
        record["source"],
        local_path=local_path,
        status="queued",
        error_message=None,
    )
    task = get_upload_queue().enqueue(local_path, record["source"])
    return {
        "task_id": task["task_id"],
        "id": record["id"],
        "source": record["source"],
        "message": "已重新加入向量化队列",
    }


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
