"""
BM25 稀疏检索 —— 基于 Okapi BM25 算法。
中文使用 jieba 分词，英文按空格分词，同时处理 URL 路径、camelCase、下划线等技术标识符。
"""

import re
import math
import json
from collections import Counter
from pathlib import Path
from typing import List, Dict

import jieba


class BM25Index:
    """
    BM25 稀疏检索索引。
    构建倒排索引，支持中英文混合分词检索。
    可持久化到磁盘，也可从已有元数据重建。
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

        self.corpus: List[List[str]] = []
        self.doc_len: List[int] = []
        self.avgdl: float = 0
        self.N: int = 0
        self.df: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self.metadata: List[Dict] = []

    # ---- 分词 ----

    _CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
    # 识别 camelCase 边界：小写或数字 → 大写
    _CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

    @classmethod
    def _split_technical(cls, word: str) -> List[str]:
        """
        将技术标识符拆分为子词，同时保留原词：
        - URL 路径：按 / 拆分
        - camelCase：按大小写边界拆分
        - snake_case / kebab-case：按 _ 或 - 拆分
        返回原词 + 所有子词，均转小写。
        """
        tokens = set()
        tokens.add(word.lower())

        # 按 / 拆分（URL 路径）
        parts = [p for p in re.split(r"[/]", word) if p]

        for part in parts:
            p_lower = part.lower()
            tokens.add(p_lower)

            # 按 _ - . 拆分
            sub_parts = [s for s in re.split(r"[_\-\.]", part) if s]
            for sp in sub_parts:
                tokens.add(sp.lower())
                # camelCase 拆分
                camel_parts = [c for c in cls._CAMEL_RE.split(sp) if c]
                for cp in camel_parts:
                    if len(cp) > 1:
                        tokens.add(cp.lower())

        return list(tokens)

    @classmethod
    def tokenize(cls, text: str) -> List[str]:
        """
        中英文混合分词，支持 URL/camelCase/下划线等技术标识符。
        每个技术词会同时保留原词和拆分后的子词，增加召回率。
        """
        tokens = []
        text_lower = text.lower()

        last_end = 0
        for match in cls._CJK_RE.finditer(text):
            before = text[last_end: match.start()]
            if before.strip():
                tokens.extend(cls._tokenize_non_cjk(before))
            tokens.extend(cls._tokenize_cjk(match.group()))
            last_end = match.end()

        tail = text[last_end:]
        if tail.strip():
            tokens.extend(cls._tokenize_non_cjk(tail))

        return tokens

    @staticmethod
    def _tokenize_cjk(text: str) -> List[str]:
        if hasattr(jieba, 'lcut'):
            words = jieba.lcut(text)
        else:
            words = list(jieba.cut(text))
        return [w for w in words if w.strip()]

    @classmethod
    def _tokenize_non_cjk(cls, text: str) -> List[str]:
        """
        英文分词：先按空格分词，再对每个词做技术标识符展开。
        含 / 的词（URL路径）会被拆开，camelCase 会被分解。
        """
        tokens = []
        for word in text.split():
            if not re.search(r"\w", word):
                continue
            # 如果包含技术特殊字符，展开成多个 token
            if re.search(r"[/_\-\.]|[a-z][A-Z]|[A-Z]{2,}[a-z]", word):
                tokens.extend(cls._split_technical(word))
            else:
                tokens.append(word.lower())
        return tokens

    # ---- 构建索引 ----

    def fit(self, chunks: List[Dict]):
        self.metadata = chunks
        self.corpus = []
        self.doc_len = []

        print("  正在构建 BM25 索引（jieba 分词中）...")
        for chunk in chunks:
            tokens = self.tokenize(chunk["text"])
            self.corpus.append(tokens)
            self.doc_len.append(len(tokens))

        self.N = len(self.corpus)
        self.avgdl = sum(self.doc_len) / self.N if self.N > 0 else 0

        self.df = {}
        for tokens in self.corpus:
            for token in set(tokens):
                self.df[token] = self.df.get(token, 0) + 1

        self.idf = {}
        for token, freq in self.df.items():
            self.idf[token] = math.log((self.N - freq + 0.5) / (freq + 0.5) + 1)

        print(f"  BM25 索引构建完成: {self.N} 个文档, {len(self.df)} 个唯一词")

    # ---- 检索 ----

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        if not self.corpus:
            return []

        query_tokens = self.tokenize(query)
        scores = []

        for i, doc_tokens in enumerate(self.corpus):
            score = 0.0
            tf_counter = Counter(doc_tokens)
            dl = self.doc_len[i]

            for qt in query_tokens:
                if qt not in tf_counter:
                    continue
                tf = tf_counter[qt]
                idf = self.idf.get(qt, 0)
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                score += idf * numerator / denominator

            # 精确子串匹配加权：查询词直接出现在文档原文中给予额外加分
            doc_text = self.metadata[i]["text"].lower()
            query_lower = query.lower()
            if query_lower in doc_text:
                score += 5.0  # 精确命中大幅加权
            else:
                # 对查询中每个技术词做子串匹配加权
                for qt in set(query_tokens):
                    if len(qt) >= 4 and qt in doc_text:
                        score += 0.5

            scores.append(score)

        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

        return [
            {**self.metadata[i], "bm25_score": round(scores[i], 4)}
            for i in top_indices
            if scores[i] > 0
        ]

    # ---- 持久化 ----

    def save(self, storage_dir: str):
        path = Path(storage_dir) / "bm25_index.json"
        data = {
            "k1": self.k1, "b": self.b,
            "corpus": self.corpus, "doc_len": self.doc_len,
            "avgdl": self.avgdl, "N": self.N,
            "df": self.df, "idf": self.idf,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def load(self, storage_dir: str, metadata: List[Dict]) -> bool:
        path = Path(storage_dir) / "bm25_index.json"
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.k1 = data["k1"]; self.b = data["b"]
            self.corpus = data["corpus"]; self.doc_len = data["doc_len"]
            self.avgdl = data["avgdl"]; self.N = data["N"]
            self.df = data["df"]; self.idf = data["idf"]
            self.metadata = metadata
            print(f"  从本地加载了 BM25 索引: {self.N} 个文档, {len(self.df)} 个唯一词")
            return True
        except Exception:
            return False

    def to_dict(self) -> dict:
        return {
            "k1": self.k1, "b": self.b,
            "corpus": self.corpus, "doc_len": self.doc_len,
            "avgdl": self.avgdl, "N": self.N,
            "df": self.df, "idf": self.idf,
        }

    def from_dict(self, data: dict, metadata: List[Dict]) -> bool:
        try:
            self.k1 = data["k1"]; self.b = data["b"]
            self.corpus = data["corpus"]; self.doc_len = data["doc_len"]
            self.avgdl = data["avgdl"]; self.N = data["N"]
            self.df = data["df"]; self.idf = data["idf"]
            self.metadata = metadata
            print(f"  从 PostgreSQL 加载了 BM25 索引: {self.N} 个文档, {len(self.df)} 个唯一词")
            return True
        except Exception:
            return False
