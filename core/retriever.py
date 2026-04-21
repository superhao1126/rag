"""
混合检索器 —— 融合向量检索与 BM25 稀疏检索。
使用 RRF（Reciprocal Rank Fusion，倒数排名融合）合并两路检索结果。
"""

import re
from typing import List, Dict
from core.vectorstore import VectorStore
from core.bm25 import BM25Index


def _expand_query(query: str) -> str:
    """
    对包含 URL/camelCase 的技术查询词做扩展，
    在原始查询后附加拆分后的子词，增强向量检索对精确技术名词的召回。
    """
    # 检测是否包含 URL 路径或 camelCase 标识符
    if not re.search(r"[/]|[a-z][A-Z]", query):
        return query

    extra_tokens = set()
    for word in re.split(r"\s+", query):
        if re.search(r"[/_\-\.]|[a-z][A-Z]", word):
            # URL 路径拆分
            parts = re.split(r"[/]", word)
            for part in parts:
                if part:
                    extra_tokens.add(part)
                    # camelCase + 下划线拆分
                    sub = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", part)
                    extra_tokens.update(re.split(r"[_\-\.]", sub))

    extra = " ".join(t for t in extra_tokens if len(t) > 1)
    return f"{query} {extra}" if extra else query


class HybridRetriever:
    """
    混合检索器：向量语义检索 + BM25 关键词检索。

    使用 RRF 融合公式:
        RRF_score(d) = Σ weight_i / (k + rank_i(d))
    其中 k 是常数（默认 60），rank 是文档在各路检索中的排名。
    """

    def __init__(
        self,
        vector_store: VectorStore,
        bm25_index: BM25Index,
        vector_weight: float = 0.6,
        bm25_weight: float = 0.4,
        rrf_k: int = 60,
    ):
        self.vector_store = vector_store
        self.bm25_index = bm25_index
        self.vector_weight = vector_weight
        self.bm25_weight = bm25_weight
        self.rrf_k = rrf_k

    def search(self, query: str, top_k: int = 5, mode: str = "hybrid") -> List[Dict]:
        """
        执行检索。

        参数:
            query:  查询文本
            top_k:  返回结果数量
            mode:   检索模式
                    "hybrid"  — RRF 混合检索（默认）
                    "vector"  — 仅向量检索
                    "bm25"    — 仅 BM25 检索
        """
        if mode == "vector":
            expanded = _expand_query(query)
            return self.vector_store.search(expanded, top_k=top_k)

        if mode == "bm25":
            return self.bm25_index.search(query, top_k=top_k)

        # ---- 混合检索（RRF 融合） ----
        fetch_k = top_k * 4  # 多取一些再融合

        expanded = _expand_query(query)
        vector_results = self.vector_store.search(expanded, top_k=fetch_k)
        bm25_results = self.bm25_index.search(query, top_k=fetch_k)

        rrf_scores: Dict[str, float] = {}

        for rank, r in enumerate(vector_results):
            doc_id = r["id"]
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + self.vector_weight / (self.rrf_k + rank + 1)

        for rank, r in enumerate(bm25_results):
            doc_id = r["id"]
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + self.bm25_weight / (self.rrf_k + rank + 1)

        # 汇总候选文档
        all_docs: Dict[str, Dict] = {}
        for r in vector_results:
            all_docs[r["id"]] = r
        for r in bm25_results:
            if r["id"] not in all_docs:
                all_docs[r["id"]] = r
            else:
                all_docs[r["id"]]["bm25_score"] = r.get("bm25_score", 0)

        sorted_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)[:top_k]

        results = []
        for doc_id in sorted_ids:
            result = all_docs[doc_id].copy()
            result["rrf_score"] = round(rrf_scores[doc_id], 6)
            results.append(result)

        return results
