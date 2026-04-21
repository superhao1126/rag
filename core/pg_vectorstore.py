"""
PostgreSQL + pgvector 向量存储（全局，无会话隔离）
所有文件共享同一向量库，支持对话历史持久化。
"""

import time
import uuid
import numpy as np
from typing import List, Dict, Optional
from openai import OpenAI

try:
    import psycopg2
    from psycopg2.extras import execute_batch, Json
    from pgvector.psycopg2 import register_vector

    HAS_PG = True
except ImportError:
    HAS_PG = False

_GLOBAL_KEY = "global"


class PgVectorStore:
    """PostgreSQL 向量存储 —— 全局统一存储，不按会话隔离。"""

    def __init__(
        self,
        client: OpenAI,
        db_config: dict,
        embedding_model: str,
        embedding_dimension: int = 1024,
        db_uri: str = None,
        # 保留 session_id 参数仅为向后兼容，内部不再使用
        session_id: str = None,
    ):
        if not HAS_PG:
            raise ImportError(
                "需要安装 psycopg2-binary 和 pgvector:\n"
                "  pip install psycopg2-binary pgvector"
            )

        self.client = client
        self.embedding_model = embedding_model
        self.embedding_dimension = embedding_dimension

        if db_uri:
            self.conn = psycopg2.connect(db_uri)
        else:
            self.conn = psycopg2.connect(**db_config)
        self.conn.autocommit = True

        self._init_tables()
        register_vector(self.conn)
        self._metadata_cache: Optional[List[Dict]] = None

    # ---- 数据库初始化 ----

    def _init_tables(self):
        """创建所需的数据库表和索引（幂等操作）"""
        dim = self.embedding_dimension
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

            # 向量块表（保留 session_id 列以兼容旧数据，新数据写入 'global'）
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    id          VARCHAR(64) PRIMARY KEY,
                    session_id  VARCHAR(64) NOT NULL DEFAULT 'global',
                    text        TEXT NOT NULL,
                    source      VARCHAR(512),
                    chunk_index INTEGER,
                    start_pos   INTEGER,
                    end_pos     INTEGER,
                    char_count  INTEGER,
                    embedding   vector({dim}),
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_chunks_session
                ON rag_chunks(session_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_chunks_source
                ON rag_chunks(source)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding
                ON rag_chunks USING hnsw (embedding vector_cosine_ops)
            """)

            # BM25 索引缓存
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rag_bm25_cache (
                    session_id  VARCHAR(64) PRIMARY KEY,
                    index_data  JSONB NOT NULL,
                    updated_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 对话表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rag_conversations (
                    id         VARCHAR(64) PRIMARY KEY,
                    title      TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 消息表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rag_messages (
                    id              BIGSERIAL PRIMARY KEY,
                    conversation_id VARCHAR(64) NOT NULL
                        REFERENCES rag_conversations(id) ON DELETE CASCADE,
                    role            VARCHAR(20) NOT NULL,
                    content         TEXT NOT NULL,
                    retrieval_info  JSONB,
                    created_at      TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_messages_conv
                ON rag_messages(conversation_id, created_at)
            """)

        print("  ✅ PostgreSQL 数据库表已就绪")

    # ---- metadata 属性 ----

    @property
    def metadata(self) -> List[Dict]:
        if self._metadata_cache is None:
            self._load_metadata()
        return self._metadata_cache

    def _load_metadata(self):
        """加载全库所有文本块的元数据"""
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT id, text, source, chunk_index, start_pos, end_pos, char_count
                   FROM rag_chunks
                   ORDER BY source, chunk_index"""
            )
            rows = cur.fetchall()

        self._metadata_cache = [
            {
                "id": r[0],
                "text": r[1],
                "source": r[2],
                "chunk_index": r[3],
                "start": r[4],
                "end": r[5],
                "char_count": r[6],
            }
            for r in rows
        ]

    def _invalidate_cache(self):
        self._metadata_cache = None

    # ---- Embedding API ----

    def _get_embeddings(self, texts: List[str], on_progress=None) -> np.ndarray:
        """调用阿里云百炼 Embedding API 批量获取向量"""
        all_embeddings: list = []
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
                    if on_progress:
                        on_progress(current, total_batches)
                    elif total_batches > 1:
                        print(f"    向量化进度: {current}/{total_batches} 批")
                    break
                except Exception as e:
                    if attempt < 2:
                        wait = (attempt + 1) * 2
                        print(f"    ⚠️ 第 {current} 批失败，{wait}s 后重试... ({e})")
                        time.sleep(wait)
                    else:
                        raise RuntimeError(
                            f"向量化第 {current} 批失败（已重试 3 次）: {e}"
                        ) from e

        return np.array(all_embeddings, dtype=np.float32)

    # ---- 文档增删查 ----

    def add(self, chunks: List[Dict], on_progress=None):
        """添加文本块到 PostgreSQL（全局，自动去重）"""
        if not chunks:
            return

        chunk_ids = [c["id"] for c in chunks]
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM rag_chunks WHERE id = ANY(%s)", (chunk_ids,))
            existing_ids = {row[0] for row in cur.fetchall()}

        new_chunks = [c for c in chunks if c["id"] not in existing_ids]
        if not new_chunks:
            print("  所有文本块已存在，跳过。")
            return

        texts = [c["text"] for c in new_chunks]
        print(f"  正在为 {len(texts)} 个文本块生成向量...")
        embeddings = self._get_embeddings(texts, on_progress=on_progress)

        data = [
            (
                chunk["id"],
                _GLOBAL_KEY,
                chunk["text"],
                chunk.get("source", ""),
                chunk.get("chunk_index", 0),
                chunk.get("start", 0),
                chunk.get("end", 0),
                chunk.get("char_count", len(chunk["text"])),
                embeddings[i].tolist(),
            )
            for i, chunk in enumerate(new_chunks)
        ]

        with self.conn.cursor() as cur:
            execute_batch(
                cur,
                """INSERT INTO rag_chunks
                   (id, session_id, text, source, chunk_index, start_pos, end_pos, char_count, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO NOTHING""",
                data,
                page_size=100,
            )

        self._invalidate_cache()
        print(f"  ✅ 已将 {len(new_chunks)} 个文本块存储到 PostgreSQL。")

    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        """余弦相似度检索（全库搜索，不限来源）"""
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM rag_chunks")
            if cur.fetchone()[0] == 0:
                return []

        query_vec = self._get_embeddings([query])[0]
        vec_list = query_vec.tolist()

        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT id, text, source, chunk_index, start_pos, end_pos, char_count,
                          1 - (embedding <=> %s::vector) AS similarity
                   FROM rag_chunks
                   ORDER BY embedding <=> %s::vector
                   LIMIT %s""",
                (vec_list, vec_list, top_k),
            )
            rows = cur.fetchall()

        return [
            {
                "id": r[0],
                "text": r[1],
                "source": r[2],
                "chunk_index": r[3],
                "start": r[4],
                "end": r[5],
                "char_count": r[6],
                "similarity": float(r[7]),
            }
            for r in rows
        ]

    def get_stats(self) -> Dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM rag_chunks")
            total = cur.fetchone()[0]
            cur.execute("SELECT DISTINCT source FROM rag_chunks ORDER BY source")
            sources = [row[0] for row in cur.fetchall()]

        return {
            "文本块总数": total,
            "向量维度": self.embedding_dimension,
            "存储方式": "PostgreSQL + pgvector",
            "已加载来源": sources,
        }

    def get_files(self) -> List[Dict]:
        """获取全库所有文件信息（按来源聚合）"""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT source,
                       COUNT(*) AS chunk_count,
                       SUM(char_count) AS total_chars,
                       MIN(created_at) AS uploaded_at
                FROM rag_chunks
                GROUP BY source
                ORDER BY uploaded_at DESC
            """)
            rows = cur.fetchall()

        return [
            {
                "source": r[0],
                "chunk_count": r[1],
                "total_chars": int(r[2]) if r[2] else 0,
                "uploaded_at": r[3].isoformat() if r[3] else None,
            }
            for r in rows
        ]

    def file_exists(self, source: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM rag_chunks WHERE source = %s LIMIT 1", (source,))
            return cur.fetchone() is not None

    def delete_file(self, source: str):
        """从库中删除指定来源的所有块"""
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM rag_chunks WHERE source = %s", (source,))
        self._invalidate_cache()

    # ---- BM25 索引缓存（全局唯一）----

    def save_bm25(self, index_data: dict):
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO rag_bm25_cache (session_id, index_data, updated_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (session_id)
                   DO UPDATE SET index_data = EXCLUDED.index_data, updated_at = NOW()""",
                (_GLOBAL_KEY, Json(index_data)),
            )

    def load_bm25(self) -> Optional[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT index_data FROM rag_bm25_cache WHERE session_id = %s",
                (_GLOBAL_KEY,),
            )
            row = cur.fetchone()
            return row[0] if row else None

    # ---- 对话管理 ----

    def create_conversation(self, title: str = "新对话") -> str:
        conv_id = str(uuid.uuid4())[:16]
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rag_conversations (id, title) VALUES (%s, %s)",
                (conv_id, title),
            )
        return conv_id

    def get_conversations(self) -> List[Dict]:
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       COUNT(m.id) AS message_count
                FROM rag_conversations c
                LEFT JOIN rag_messages m ON m.conversation_id = c.id
                GROUP BY c.id, c.title, c.created_at, c.updated_at
                ORDER BY c.updated_at DESC
            """)
            rows = cur.fetchall()

        return [
            {
                "id": r[0],
                "title": r[1],
                "created_at": r[2].isoformat() if r[2] else None,
                "updated_at": r[3].isoformat() if r[3] else None,
                "message_count": r[4],
            }
            for r in rows
        ]

    def get_conversation_messages(self, conv_id: str) -> List[Dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT id, role, content, retrieval_info, created_at
                   FROM rag_messages
                   WHERE conversation_id = %s
                   ORDER BY created_at""",
                (conv_id,),
            )
            rows = cur.fetchall()

        return [
            {
                "id": r[0],
                "role": r[1],
                "content": r[2],
                "retrieval_info": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
            }
            for r in rows
        ]

    def add_message(
        self,
        conv_id: str,
        role: str,
        content: str,
        retrieval_info: dict = None,
    ):
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO rag_messages (conversation_id, role, content, retrieval_info)
                   VALUES (%s, %s, %s, %s)""",
                (conv_id, role, content, Json(retrieval_info) if retrieval_info else None),
            )
            cur.execute(
                "UPDATE rag_conversations SET updated_at = NOW() WHERE id = %s",
                (conv_id,),
            )

    def update_conversation_title(self, conv_id: str, title: str):
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE rag_conversations SET title = %s WHERE id = %s",
                (title, conv_id),
            )

    def delete_conversation(self, conv_id: str):
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM rag_conversations WHERE id = %s", (conv_id,))

    def conversation_exists(self, conv_id: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM rag_conversations WHERE id = %s", (conv_id,))
            return cur.fetchone() is not None

    # ---- 关闭连接 ----

    def close(self):
        if hasattr(self, "conn") and self.conn and not self.conn.closed:
            self.conn.close()

    def __del__(self):
        self.close()
