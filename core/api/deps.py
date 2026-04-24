"""全局依赖：RAGPipeline 单例"""
from __future__ import annotations

from fastapi import HTTPException

from core.pipeline import RAGPipeline
from core.task_queue import UploadTaskQueue

_rag: RAGPipeline | None = None
_upload_queue: UploadTaskQueue | None = None


def set_rag(rag: RAGPipeline) -> None:
    global _rag
    _rag = rag


def get_rag_instance() -> RAGPipeline | None:
    return _rag


def set_upload_queue(upload_queue: UploadTaskQueue | None) -> None:
    global _upload_queue
    _upload_queue = upload_queue


def get_upload_queue() -> UploadTaskQueue:
    if _upload_queue is None:
        raise HTTPException(503, "上传任务队列尚未初始化")
    return _upload_queue


def get_rag() -> RAGPipeline:
    if _rag is None:
        raise HTTPException(503, "服务尚未初始化")
    return _rag


def require_pg(rag: RAGPipeline) -> None:
    if not rag._use_pg:
        raise HTTPException(400, "当前未启用 PostgreSQL 存储")
