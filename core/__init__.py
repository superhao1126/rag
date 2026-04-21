"""
RAG 核心模块包
提供文件读取、文本分块、向量存储、BM25 检索、混合检索、RAG 流程等功能。
支持本地 numpy 存储和 PostgreSQL + pgvector 两种向量持久化方式。
"""

from core.config import Config
from core.reader import FileReader
from core.chunker import TextChunker
from core.vectorstore import VectorStore
from core.bm25 import BM25Index
from core.retriever import HybridRetriever
from core.pipeline import RAGPipeline

__all__ = [
    "Config",
    "FileReader",
    "TextChunker",
    "VectorStore",
    "BM25Index",
    "HybridRetriever",
    "RAGPipeline",
]

# PgVectorStore 仅在安装了 psycopg2 + pgvector 时可用
try:
    from core.pg_vectorstore import PgVectorStore
    __all__.append("PgVectorStore")
except ImportError:
    pass
