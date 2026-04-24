from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.api.deps as deps
from core.api.middleware import UnifiedResponseMiddleware, register_exception_handlers
from core.api.routers import register_routers


class FakeRag:
    def __init__(self):
        self._use_pg = False
        self.config = SimpleNamespace(LLM_MODEL="qwen3.6-plus")
        self.vector_store = SimpleNamespace(
            metadata=[{"id": "chunk-1", "source": "doc.txt", "text": "hello"}]
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
    client = _create_client()

    response = client.get("/api/stats")
    body = response.json()

    assert response.status_code == 200
    assert body["code"] == 200
    assert body["msg"] == "ok"
    assert body["data"] == {"total_chunks": 1, "total_files": 1}


def test_query_endpoint_rejects_empty_question():
    deps.set_rag(None)
    client = _create_client()

    response = client.post("/api/query", json={"question": ""})
    body = response.json()

    assert response.status_code == 200
    assert body["code"] == 400
    assert body["msg"] == "问题不能为空"
