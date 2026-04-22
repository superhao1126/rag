"""向量存储 —— 基于 numpy + 阿里云百炼 Embedding API"""

import json
import time
import numpy as np
from pathlib import Path
from typing import List, Dict
from openai import OpenAI


class VectorStore:
    """
    轻量级向量存储。
    向量和元数据持久化到本地磁盘，支持增量添加和余弦相似度检索。
    """

    def __init__(self, client: OpenAI, storage_dir: str, embedding_model: str):
        self.client = client
        self.storage_dir = Path(storage_dir)
        self.embedding_model = embedding_model

        self.vectors_file = self.storage_dir / "vectors.npy"
        self.metadata_file = self.storage_dir / "metadata.json"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self._load()

    # ---- 持久化 ----

    def _load(self):
        """从磁盘加载已有的向量与元数据"""
        if self.vectors_file.exists() and self.metadata_file.exists():
            self.vectors = np.load(str(self.vectors_file))
            with open(self.metadata_file, "r", encoding="utf-8") as f:
                self.metadata = json.load(f)
            print(f"  从本地加载了 {len(self.metadata)} 个已有文本块。")
        else:
            self.vectors = None
            self.metadata = []

    def _save(self):
        """将数据保存到磁盘"""
        if self.vectors is not None:
            np.save(str(self.vectors_file), self.vectors)
        with open(self.metadata_file, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, ensure_ascii=False, indent=2)

    # ---- Embedding API ----

    def _get_embeddings(self, texts: List[str]) -> np.ndarray:
        """
        调用阿里云百炼 Embedding API 批量获取向量。
        每批最多 10 条，内置重试机制。
        """
        all_embeddings = []
        batch_size = 10
        total_batches = (len(texts) + batch_size - 1) // batch_size

        for batch_idx in range(0, len(texts), batch_size):
            batch = texts[batch_idx : batch_idx + batch_size]
            current = batch_idx // batch_size + 1

            for attempt in range(3):
                try:
                    response = self.client.embeddings.create(
                        model=self.embedding_model,
                        input=batch,
                    )
                    all_embeddings.extend([item.embedding for item in response.data])
                    if total_batches > 1:
                        print(f"    向量化进度: {current}/{total_batches} 批")
                    break
                except Exception as e:
                    if attempt < 2:
                        wait = (attempt + 1) * 2
                        print(f"    ⚠️ 第 {current} 批失败，{wait}s 后重试... ({e})")
                        time.sleep(wait)
                    else:
                        raise RuntimeError(f"向量化第 {current} 批失败（已重试 3 次）: {e}") from e

        return np.array(all_embeddings, dtype=np.float32)

    # ---- 增删查 ----

    def add(self, chunks: List[Dict]):
        """添加文本块到向量存储（自动去重）"""
        if not chunks:
            return

        existing_ids = {m["id"] for m in self.metadata}
        new_chunks = [c for c in chunks if c["id"] not in existing_ids]

        if not new_chunks:
            print("  所有文本块已存在，跳过。")
            return

        texts = [c["text"] for c in new_chunks]
        print(f"  正在为 {len(texts)} 个文本块生成向量...")
        new_vectors = self._get_embeddings(texts)

        if self.vectors is not None and len(self.vectors) > 0:
            self.vectors = np.vstack([self.vectors, new_vectors])
        else:
            self.vectors = new_vectors

        self.metadata.extend(new_chunks)
        self._save()
        print(f"  已成功存储 {len(new_chunks)} 个文本块。")

    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        """余弦相似度检索"""
        if self.vectors is None or len(self.metadata) == 0:
            return []

        query_vec = self._get_embeddings([query])[0]
        sims = self._cosine_similarity(query_vec, self.vectors)
        top_idx = np.argsort(sims)[::-1][:top_k]

        return [
            {**self.metadata[i], "similarity": float(sims[i])}
            for i in top_idx
        ]

    @staticmethod
    def _cosine_similarity(query: np.ndarray, vectors: np.ndarray) -> np.ndarray:
        q = query / (np.linalg.norm(query) + 1e-10)
        v = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-10)
        return np.dot(v, q)

    def get_stats(self) -> Dict:
        return {
            "total_chunks": len(self.metadata),
            "embedding_dimension": self.vectors.shape[1] if self.vectors is not None else 0,
            "storage_type": "Local FAISS",
            "storage_path": str(self.storage_dir),
            "sources": list(set(m.get("source", "") for m in self.metadata)),
        }
