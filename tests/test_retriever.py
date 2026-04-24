from core.retriever import HybridRetriever, _expand_query


class FakeVectorStore:
    def __init__(self):
        self.last_query = None

    def search(self, query, top_k=5):
        self.last_query = query
        return [
            {"id": "a", "text": "vector a", "score": 0.9},
            {"id": "b", "text": "vector b", "score": 0.8},
        ][:top_k]


class FakeBM25Index:
    def search(self, query, top_k=5):
        return [
            {"id": "b", "text": "bm25 b", "bm25_score": 8.0},
            {"id": "c", "text": "bm25 c", "bm25_score": 7.5},
        ][:top_k]


def test_expand_query_keeps_original_and_adds_split_tokens():
    expanded = _expand_query("GET /api/getUserProfile")

    assert expanded.startswith("GET /api/getUserProfile")
    assert "api" in expanded
    assert "get" in expanded
    assert "User" in expanded or "user" in expanded


def test_hybrid_retriever_merges_rrf_scores():
    vector_store = FakeVectorStore()
    retriever = HybridRetriever(vector_store=vector_store, bm25_index=FakeBM25Index())

    results = retriever.search("查询用户", top_k=3, mode="hybrid")

    assert [item["id"] for item in results] == ["b", "a", "c"]
    assert results[0]["bm25_score"] == 8.0
    assert "rrf_score" in results[0]
