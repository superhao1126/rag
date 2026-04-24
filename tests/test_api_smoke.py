import os
import tempfile
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.api.deps as deps
from core.api.middleware import UnifiedResponseMiddleware, register_exception_handlers
from core.api.routers import register_routers


class FakeUploadQueue:
    def __init__(self):
        self.enqueued = []

    def stats(self):
        return {"queued": 1, "running": 0, "succeeded": 2, "failed": 0, "recent": []}

    def enqueue(self, file_path: str, filename: str):
        self.enqueued.append((file_path, filename))
        return {"task_id": "task-1", "filename": filename}


class FakeRag:
    def __init__(self):
        self._use_pg = False
        self.config = SimpleNamespace(LLM_MODEL="qwen3.6-plus")
        self.vector_store = SimpleNamespace(
            metadata=[{"id": "chunk-1", "source": "doc.txt", "text": "hello"}],
            get_file_record=lambda **_: None,
            mark_file_failed=lambda *args, **kwargs: None,
            upsert_file=lambda *args, **kwargs: None,
        )

    def get_stats(self):
        return {"total_chunks": 1, "total_files": 1}

    def get_chunk_visualization(self, source=None):
        return {
            "chunks": [{"index": 0, "source": source or "doc.txt", "text": "hello"}],
            "stats": {"total_chunks": 1},
        }


def _create_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(UnifiedResponseMiddleware)
    register_exception_handlers(app)
    register_routers(app)
    return TestClient(app)


def test_stats_endpoint_uses_unified_response():
    deps.set_rag(FakeRag())
    deps.set_upload_queue(FakeUploadQueue())
    client = _create_client()

    response = client.get("/api/stats")
    body = response.json()

    assert response.status_code == 200
    assert body["code"] == 200
    assert body["msg"] == "ok"
    assert body["data"] == {"total_chunks": 1, "total_files": 1}


def test_query_endpoint_rejects_empty_question():
    deps.set_rag(None)
    deps.set_upload_queue(FakeUploadQueue())
    client = _create_client()

    response = client.post("/api/query", json={"question": ""})
    body = response.json()

    assert response.status_code == 200
    assert body["code"] == 400
    assert body["msg"] == "问题不能为空"


def test_upload_queue_endpoint_uses_unified_response():
    deps.set_rag(FakeRag())
    deps.set_upload_queue(FakeUploadQueue())
    client = _create_client()

    response = client.get("/api/upload/queue")
    body = response.json()

    assert response.status_code == 200
    assert body["code"] == 200
    assert body["data"]["queued"] == 1


def test_revectorize_endpoint_requeues_failed_file():
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(b"hello")
        local_path = tmp.name

    queue = FakeUploadQueue()
    rag = FakeRag()
    rag._use_pg = True
    rag.vector_store = SimpleNamespace(
        metadata=[],
        get_file_record=lambda **_: {
            "id": "file-1",
            "source": "demo.txt",
            "status": "failed",
            "local_path": local_path,
        },
        mark_file_failed=lambda *args, **kwargs: None,
        upsert_file=lambda *args, **kwargs: None,
    )

    deps.set_rag(rag)
    deps.set_upload_queue(queue)
    client = _create_client()
    try:
        response = client.post("/api/files/by-id/file-1/revectorize")
        body = response.json()
        assert response.status_code == 200
        assert body["code"] == 200
        assert body["data"]["source"] == "demo.txt"
        assert queue.enqueued == [(local_path, "demo.txt")]
    finally:
        os.unlink(local_path)
