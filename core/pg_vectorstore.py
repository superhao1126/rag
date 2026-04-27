"""
PostgreSQL + pgvector 向量存储（全局，无会话隔离）
所有文件共享同一向量库，支持对话历史持久化。
"""

import hashlib
import time
import uuid
import numpy as np
from typing import List, Dict, Optional
from openai import OpenAI

from core.llm_stats import llm_stats

try:
    import psycopg2
    from psycopg2.extras import execute_batch, Json
    from pgvector.psycopg2 import register_vector

    HAS_PG = True
except ImportError:
    HAS_PG = False

_GLOBAL_KEY = "global"


def file_id_for(source: str) -> str:
    """根据 source 生成稳定的文件 ID（MD5 前 16 位，足够避免冲突且简短）"""
    return hashlib.md5(source.encode("utf-8")).hexdigest()[:16]


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

            # 文件表：持久化上传状态 / 本地路径 / 向量化结果
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rag_files (
                    id                 VARCHAR(64) PRIMARY KEY,
                    source             VARCHAR(512) NOT NULL UNIQUE,
                    local_path         TEXT,
                    status             VARCHAR(32) NOT NULL DEFAULT 'queued',
                    error_message      TEXT,
                    chunk_count        INTEGER NOT NULL DEFAULT 0,
                    total_chars        INTEGER NOT NULL DEFAULT 0,
                    uploaded_at        TIMESTAMPTZ DEFAULT NOW(),
                    last_vectorized_at TIMESTAMPTZ,
                    updated_at         TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_files_status
                ON rag_files(status)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_files_uploaded_at
                ON rag_files(uploaded_at DESC)
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
            # 反馈字段（自动迁移老库）
            cur.execute("""
                ALTER TABLE rag_messages
                ADD COLUMN IF NOT EXISTS feedback SMALLINT
            """)
            cur.execute("""
                ALTER TABLE rag_messages
                ADD COLUMN IF NOT EXISTS feedback_reasons TEXT[]
            """)
            cur.execute("""
                ALTER TABLE rag_messages
                ADD COLUMN IF NOT EXISTS feedback_comment TEXT
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_messages_conv
                ON rag_messages(conversation_id, created_at)
            """)

            # LLM / Embedding 调用流水
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rag_llm_calls (
                    id                 BIGSERIAL PRIMARY KEY,
                    ts                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    model              VARCHAR(128) NOT NULL,
                    call_type          VARCHAR(32)  NOT NULL DEFAULT 'chat',
                    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
                    completion_tokens  INTEGER NOT NULL DEFAULT 0,
                    total_tokens       INTEGER NOT NULL DEFAULT 0,
                    note               TEXT
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_llm_calls_ts
                ON rag_llm_calls(ts DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_llm_calls_type
                ON rag_llm_calls(call_type)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_llm_calls_model
                ON rag_llm_calls(model)
            """)

            # 历史已入库文件补录到状态表，避免旧数据在文件列表中丢失
            cur.execute("""
                INSERT INTO rag_files (
                    id, source, status, chunk_count, total_chars,
                    uploaded_at, last_vectorized_at, updated_at
                )
                SELECT
                    SUBSTRING(MD5(source) FOR 16),
                    source,
                    'vectorized',
                    COUNT(*),
                    COALESCE(SUM(char_count), 0),
                    MIN(created_at),
                    MAX(created_at),
                    MAX(created_at)
                FROM rag_chunks
                WHERE source IS NOT NULL AND source <> ''
                GROUP BY source
                ON CONFLICT (source) DO UPDATE SET
                    chunk_count = EXCLUDED.chunk_count,
                    total_chars = EXCLUDED.total_chars,
                    last_vectorized_at = EXCLUDED.last_vectorized_at,
                    updated_at = CASE
                        WHEN rag_files.status = 'vectorized'
                        THEN EXCLUDED.updated_at
                        ELSE rag_files.updated_at
                    END
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
                    from datetime import datetime
                    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])
                    response = self.client.embeddings.create(
                        model=self.embedding_model,
                        input=batch,
                    )
                    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])
                    all_embeddings.extend([item.embedding for item in response.data])
                    try:
                        usage = getattr(response, "usage", None)
                        if usage:
                            llm_stats.record(
                                model=self.embedding_model,
                                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                                completion_tokens=0,
                                total_tokens=getattr(usage, "total_tokens", None),
                                call_type="embedding",
                                note=f"batch={len(batch)}",
                            )
                    except Exception:
                        pass
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
            cur.execute("SELECT COUNT(*) FROM rag_files")
            total_files = cur.fetchone()[0]
            cur.execute("SELECT source FROM rag_files ORDER BY source")
            sources = [row[0] for row in cur.fetchall()]

        return {
            "total_chunks": total,
            "total_files": total_files,
            "embedding_dimension": self.embedding_dimension,
            "storage_type": "PostgreSQL + pgvector",
            "sources": sources,
        }

    def get_files(
        self,
        page: int = 1,
        page_size: int = 20,
        keyword: str = "",
    ) -> Dict:
        """获取知识库已入库文件列表（按来源聚合，分页）。

        参数:
            page: 第几页（1 起步）
            page_size: 每页条数；传 0 或负数表示返回全部
            keyword: 文件名模糊筛选（不区分大小写，可为空）

        返回:
            {
                files: [...],
                total: 总文件数,
                page: 当前页,
                page_size: 每页大小（0 表示全部）,
            }
        """
        page = max(1, int(page or 1))
        page_size = max(0, int(page_size or 0))
        kw = (keyword or "").strip()

        where_sql = ""
        params: List = []
        if kw:
            where_sql = "WHERE source ILIKE %s"
            params.append(f"%{kw}%")

        with self.conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM rag_files {where_sql}", params)
            total = int(cur.fetchone()[0] or 0)

            sql = f"""
                SELECT id,
                       source,
                       status,
                       error_message,
                       chunk_count,
                       total_chars,
                       uploaded_at
                FROM rag_files
                {where_sql}
                ORDER BY uploaded_at DESC
            """
            limit_params: List = list(params)
            if page_size > 0:
                sql += " LIMIT %s OFFSET %s"
                limit_params.extend([page_size, (page - 1) * page_size])
            cur.execute(sql, limit_params)
            rows = cur.fetchall()

        files = [
            {
                "id": r[0],
                "source": r[1],
                "status": r[2],
                "error_message": r[3],
                "chunk_count": int(r[4] or 0),
                "total_chars": int(r[5] or 0),
                "uploaded_at": r[6].isoformat() if r[6] else None,
            }
            for r in rows
        ]
        return {
            "files": files,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get_source_by_id(self, file_id: str) -> Optional[str]:
        if not file_id:
            return None
        with self.conn.cursor() as cur:
            cur.execute("SELECT source FROM rag_files WHERE id = %s LIMIT 1", (file_id,))
            row = cur.fetchone()
            return row[0] if row else None

    def get_file_record(self, *, file_id: str | None = None, source: str | None = None) -> Optional[Dict]:
        if not file_id and not source:
            return None
        where_sql = "id = %s" if file_id else "source = %s"
        value = file_id or source
        with self.conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, source, local_path, status, error_message, chunk_count, total_chars,
                           uploaded_at, last_vectorized_at, updated_at
                    FROM rag_files
                    WHERE {where_sql}
                    LIMIT 1""",
                (value,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "source": row[1],
            "local_path": row[2],
            "status": row[3],
            "error_message": row[4],
            "chunk_count": int(row[5] or 0),
            "total_chars": int(row[6] or 0),
            "uploaded_at": row[7].isoformat() if row[7] else None,
            "last_vectorized_at": row[8].isoformat() if row[8] else None,
            "updated_at": row[9].isoformat() if row[9] else None,
        }

    def file_exists(self, source: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM rag_files WHERE source = %s LIMIT 1", (source,))
            return cur.fetchone() is not None

    def get_file_info(self, source: str) -> Optional[Dict]:
        """获取单个文件的详细信息（聚合 + 块字符长度统计）"""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT f.id,
                       f.source,
                       f.status,
                       f.error_message,
                       f.uploaded_at,
                       f.updated_at,
                       COALESCE(COUNT(c.id), 0)         AS chunk_count,
                       COALESCE(SUM(c.char_count), 0)   AS total_chars,
                       COALESCE(MIN(c.char_count), 0)   AS min_chars,
                       COALESCE(MAX(c.char_count), 0)   AS max_chars,
                       COALESCE(AVG(c.char_count), 0)   AS avg_chars,
                       COALESCE(MIN(c.chunk_index), 0)  AS first_chunk_index,
                       COALESCE(MAX(c.chunk_index), 0)  AS last_chunk_index,
                       MAX(c.created_at)                AS last_vectorized_at
                FROM rag_files f
                LEFT JOIN rag_chunks c ON c.source = f.source
                WHERE f.source = %s
                GROUP BY f.id, f.source, f.status, f.error_message, f.uploaded_at, f.updated_at
                """,
                (source,),
            )
            row = cur.fetchone()
        if not row:
            return None

        return {
            "id": row[0],
            "source": row[1],
            "status": row[2],
            "error_message": row[3],
            "chunk_count": int(row[6] or 0),
            "total_chars": int(row[7] or 0),
            "min_chars": int(row[8] or 0),
            "max_chars": int(row[9] or 0),
            "avg_chars": round(float(row[10] or 0), 1),
            "uploaded_at": row[4].isoformat() if row[4] else None,
            "last_updated_at": row[5].isoformat() if row[5] else None,
            "last_vectorized_at": row[13].isoformat() if row[13] else None,
            "chunk_index_range": [
                int(row[11] or 0),
                int(row[12] or 0),
            ],
        }

    def delete_file(self, source: str):
        """从库中删除指定来源的所有块"""
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM rag_chunks WHERE source = %s", (source,))
            cur.execute("DELETE FROM rag_files WHERE source = %s", (source,))
        self._invalidate_cache()

    def upsert_file(self, source: str, *, local_path: str | None, status: str, error_message: str | None = None):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rag_files (id, source, local_path, status, error_message, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (source) DO UPDATE SET
                    local_path = EXCLUDED.local_path,
                    status = EXCLUDED.status,
                    error_message = EXCLUDED.error_message,
                    updated_at = NOW()
                """,
                (file_id_for(source), source, local_path, status, error_message),
            )

    def mark_file_processing(self, source: str):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rag_files
                SET status = 'processing',
                    error_message = NULL,
                    updated_at = NOW()
                WHERE source = %s
                """,
                (source,),
            )

    def mark_file_failed(self, source: str, error_message: str):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rag_files
                SET status = 'failed',
                    error_message = %s,
                    updated_at = NOW()
                WHERE source = %s
                """,
                (error_message, source),
            )

    def mark_file_vectorized(self, source: str):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(char_count), 0), MAX(created_at)
                FROM rag_chunks
                WHERE source = %s
                """,
                (source,),
            )
            row = cur.fetchone()
            chunk_count = int(row[0] or 0)
            total_chars = int(row[1] or 0)
            last_vectorized_at = row[2]
            cur.execute(
                """
                UPDATE rag_files
                SET status = 'vectorized',
                    error_message = NULL,
                    chunk_count = %s,
                    total_chars = %s,
                    last_vectorized_at = %s,
                    updated_at = NOW()
                WHERE source = %s
                """,
                (chunk_count, total_chars, last_vectorized_at, source),
            )

    def recover_interrupted_files(self) -> int:
        """服务启动时恢复上次异常退出遗留的任务。

        进程内队列在服务退出后会丢失，因此上一轮遗留在 queued / processing
        的文件不可能继续推进。这里统一改成 failed，让前端展示“可重试”。
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rag_files
                SET status = 'failed',
                    error_message = CASE
                        WHEN error_message IS NULL OR error_message = ''
                        THEN '服务曾异常中断，请点击重新向量化'
                        ELSE error_message
                    END,
                    updated_at = NOW()
                WHERE status IN ('queued', 'processing')
                """
            )
            return cur.rowcount or 0

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
                """SELECT id, role, content, retrieval_info, created_at,
                          feedback, feedback_reasons, feedback_comment
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
                "feedback": r[5],
                "feedback_reasons": r[6] or [],
                "feedback_comment": r[7],
            }
            for r in rows
        ]

    def add_message(
        self,
        conv_id: str,
        role: str,
        content: str,
        retrieval_info: dict = None,
    ) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO rag_messages (conversation_id, role, content, retrieval_info)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (conv_id, role, content, Json(retrieval_info) if retrieval_info else None),
            )
            new_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE rag_conversations SET updated_at = NOW() WHERE id = %s",
                (conv_id,),
            )
        return new_id

    def set_message_feedback(
        self,
        message_id: int,
        rating,
        reasons: list = None,
        comment: str = None,
    ) -> bool:
        """rating: 1 / -1 / None(取消，同时清空 reasons/comment)"""
        with self.conn.cursor() as cur:
            if rating is None:
                cur.execute(
                    """UPDATE rag_messages
                       SET feedback = NULL,
                           feedback_reasons = NULL,
                           feedback_comment = NULL
                       WHERE id = %s""",
                    (message_id,),
                )
            else:
                cur.execute(
                    """UPDATE rag_messages
                       SET feedback = %s,
                           feedback_reasons = %s,
                           feedback_comment = %s
                       WHERE id = %s""",
                    (rating, reasons or None, (comment or None), message_id),
                )
            return cur.rowcount > 0

    def get_feedback_summary(
        self, rating: int = None, limit: int = 100, offset: int = 0
    ) -> Dict:
        """汇总反馈数据：总数、原因统计、详细列表（含 question/answer/对话标题）。"""
        with self.conn.cursor() as cur:
            # 整体计数
            cur.execute(
                """SELECT
                       COUNT(*) FILTER (WHERE feedback = 1)  AS up,
                       COUNT(*) FILTER (WHERE feedback = -1) AS down,
                       COUNT(*) FILTER (WHERE feedback IS NOT NULL) AS total_with_feedback,
                       (SELECT COUNT(*) FROM rag_messages WHERE role = 'assistant') AS total_answers
                   FROM rag_messages"""
            )
            row = cur.fetchone()
            stats = {
                "up": int(row[0] or 0),
                "down": int(row[1] or 0),
                "total_with_feedback": int(row[2] or 0),
                "total_answers": int(row[3] or 0),
            }

            # 原因分布（仅 -1）
            cur.execute(
                """SELECT reason, COUNT(*) AS cnt
                   FROM (
                       SELECT UNNEST(feedback_reasons) AS reason
                       FROM rag_messages
                       WHERE feedback = -1
                   ) t
                   GROUP BY reason
                   ORDER BY cnt DESC"""
            )
            reason_counts = [{"reason": r[0], "count": int(r[1])} for r in cur.fetchall()]

            # 详细列表（最近的反馈，附带问题与对话标题）
            where = "m.feedback IS NOT NULL"
            params: list = []
            if rating in (1, -1):
                where = "m.feedback = %s"
                params.append(rating)

            cur.execute(
                f"""WITH ranked AS (
                       SELECT m.id, m.conversation_id, m.role, m.content,
                              m.feedback, m.feedback_reasons, m.feedback_comment, m.created_at,
                              LAG(m.content) OVER (PARTITION BY m.conversation_id ORDER BY m.created_at) AS prev_content,
                              LAG(m.role) OVER (PARTITION BY m.conversation_id ORDER BY m.created_at) AS prev_role
                       FROM rag_messages m
                   )
                   SELECT r.id, r.conversation_id, c.title, r.content,
                          r.feedback, r.feedback_reasons, r.feedback_comment, r.created_at,
                          CASE WHEN r.prev_role = 'user' THEN r.prev_content ELSE NULL END AS question
                   FROM ranked r
                   LEFT JOIN rag_conversations c ON c.id = r.conversation_id
                   WHERE r.role = 'assistant' AND {where.replace('m.', 'r.')}
                   ORDER BY r.created_at DESC
                   LIMIT %s OFFSET %s""",
                (*params, limit, offset),
            )
            items = [
                {
                    "id": r[0],
                    "conversation_id": r[1],
                    "conversation_title": r[2],
                    "answer": r[3],
                    "rating": r[4],
                    "reasons": r[5] or [],
                    "comment": r[6],
                    "created_at": r[7].isoformat() if r[7] else None,
                    "question": r[8],
                }
                for r in cur.fetchall()
            ]

            # 趋势：近 30 天每日 up/down 数（按 created_at 截断到日）
            cur.execute(
                """WITH days AS (
                       SELECT generate_series(
                           (CURRENT_DATE - INTERVAL '29 days')::date,
                           CURRENT_DATE::date,
                           INTERVAL '1 day'
                       )::date AS d
                   )
                   SELECT d.d::text,
                          COALESCE(SUM(CASE WHEN m.feedback = 1 THEN 1 ELSE 0 END), 0) AS up,
                          COALESCE(SUM(CASE WHEN m.feedback = -1 THEN 1 ELSE 0 END), 0) AS down
                   FROM days d
                   LEFT JOIN rag_messages m
                     ON m.feedback IS NOT NULL
                    AND m.created_at::date = d.d
                   GROUP BY d.d
                   ORDER BY d.d"""
            )
            trend = [
                {"date": r[0], "up": int(r[1]), "down": int(r[2])}
                for r in cur.fetchall()
            ]

        return {
            "stats": stats,
            "reason_counts": reason_counts,
            "items": items,
            "trend": trend,
        }

    def delete_last_assistant_message(self, conv_id: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute(
                """DELETE FROM rag_messages
                   WHERE id = (
                       SELECT id FROM rag_messages
                       WHERE conversation_id = %s AND role = 'assistant'
                       ORDER BY created_at DESC LIMIT 1
                   )""",
                (conv_id,),
            )
            return cur.rowcount > 0

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

    # ---- LLM / Embedding 调用流水持久化 ----

    def insert_llm_call(
        self,
        model: str,
        call_type: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        note: Optional[str] = None,
    ) -> Dict:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rag_llm_calls
                    (model, call_type, prompt_tokens, completion_tokens, total_tokens, note)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, EXTRACT(EPOCH FROM ts)::DOUBLE PRECISION
                """,
                (model, call_type, prompt_tokens, completion_tokens, total_tokens, note),
            )
            row = cur.fetchone()
        return {"id": int(row[0]), "ts": float(row[1])}

    def aggregate_llm_calls(self) -> Dict:
        """重启时用于复原 totals / models / calls_by_type"""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT model,
                       COALESCE(SUM(prompt_tokens), 0),
                       COALESCE(SUM(completion_tokens), 0),
                       COALESCE(SUM(total_tokens), 0),
                       COUNT(*)
                  FROM rag_llm_calls
                 GROUP BY model
                """
            )
            models = {
                r[0]: {
                    "prompt_tokens": int(r[1]),
                    "completion_tokens": int(r[2]),
                    "total_tokens": int(r[3]),
                    "calls": int(r[4]),
                }
                for r in cur.fetchall()
            }
            cur.execute(
                """
                SELECT call_type, COUNT(*) FROM rag_llm_calls GROUP BY call_type
                """
            )
            calls_by_type = {r[0]: int(r[1]) for r in cur.fetchall()}
            cur.execute(
                "SELECT EXTRACT(EPOCH FROM MAX(ts))::DOUBLE PRECISION FROM rag_llm_calls"
            )
            row = cur.fetchone()
            last = float(row[0]) if row and row[0] is not None else None
        return {"models": models, "calls_by_type": calls_by_type, "last_call_at": last}

    def query_llm_calls(
        self,
        page: int = 1,
        page_size: int = 50,
        call_type: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict:
        page = max(1, int(page or 1))
        page_size = max(1, min(500, int(page_size or 50)))
        offset = (page - 1) * page_size
        where = []
        params: List = []
        if call_type:
            where.append("call_type = %s")
            params.append(call_type)
        if model:
            where.append("model = %s")
            params.append(model)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM rag_llm_calls {where_sql}", params)
            total = int(cur.fetchone()[0])
            cur.execute(
                f"""
                SELECT id, EXTRACT(EPOCH FROM ts)::DOUBLE PRECISION,
                       model, call_type, prompt_tokens, completion_tokens, total_tokens, note
                  FROM rag_llm_calls
                  {where_sql}
                 ORDER BY id DESC
                 LIMIT %s OFFSET %s
                """,
                params + [page_size, offset],
            )
            items = [
                {
                    "id": int(r[0]),
                    "ts": float(r[1]) if r[1] is not None else None,
                    "model": r[2],
                    "call_type": r[3],
                    "prompt_tokens": int(r[4] or 0),
                    "completion_tokens": int(r[5] or 0),
                    "total_tokens": int(r[6] or 0),
                    "note": r[7],
                }
                for r in cur.fetchall()
            ]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def reset_llm_calls(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE rag_llm_calls RESTART IDENTITY")

    def trend_llm_calls(self, bucket: str = "day", points: int = 30) -> List[Dict]:
        """按天/小时聚合 token 用量，返回按时间升序的桶列表（缺失桶补零）

        每个桶包含：
        - prompt_tokens / completion_tokens / total_tokens / calls   总量
        - by_type: { chat: {prompt, completion, total, calls}, embedding: {...}, title: {...} }
        """
        bucket = "hour" if bucket == "hour" else "day"
        points = max(1, min(int(points or 30), 365))
        if bucket == "hour":
            interval_sql = "1 hour"
            trunc = "hour"
        else:
            interval_sql = "1 day"
            trunc = "day"
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                WITH series AS (
                    SELECT generate_series(
                        date_trunc('{trunc}', NOW()) - (%s - 1) * INTERVAL '{interval_sql}',
                        date_trunc('{trunc}', NOW()),
                        INTERVAL '{interval_sql}'
                    ) AS bucket
                ),
                agg AS (
                    SELECT date_trunc('{trunc}', ts) AS bucket,
                           COALESCE(call_type, 'chat') AS call_type,
                           COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                           COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                           COUNT(*)                            AS calls
                      FROM rag_llm_calls
                     WHERE ts >= date_trunc('{trunc}', NOW()) - (%s - 1) * INTERVAL '{interval_sql}'
                     GROUP BY 1, 2
                )
                SELECT s.bucket,
                       a.call_type,
                       COALESCE(a.prompt_tokens, 0),
                       COALESCE(a.completion_tokens, 0),
                       COALESCE(a.total_tokens, 0),
                       COALESCE(a.calls, 0)
                  FROM series s
                  LEFT JOIN agg a ON a.bucket = s.bucket
                 ORDER BY s.bucket
                """,
                (points, points),
            )
            rows = cur.fetchall()

        from collections import defaultdict

        buckets: Dict = {}
        for r in rows:
            ts = r[0]
            key = ts.isoformat() if ts else None
            if key not in buckets:
                buckets[key] = {
                    "bucket": key,
                    "ts": ts.timestamp() if ts else None,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "calls": 0,
                    "by_type": defaultdict(
                        lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
                    ),
                }
            ct = r[1]
            if ct is None:
                continue
            p, c, t, n = int(r[2] or 0), int(r[3] or 0), int(r[4] or 0), int(r[5] or 0)
            buckets[key]["prompt_tokens"] += p
            buckets[key]["completion_tokens"] += c
            buckets[key]["total_tokens"] += t
            buckets[key]["calls"] += n
            tg = buckets[key]["by_type"][ct]
            tg["prompt_tokens"] += p
            tg["completion_tokens"] += c
            tg["total_tokens"] += t
            tg["calls"] += n

        result = []
        for b in buckets.values():
            b["by_type"] = dict(b["by_type"])
            result.append(b)
        return result

    # ---- 关闭连接 ----

    def close(self):
        if hasattr(self, "conn") and self.conn and not self.conn.closed:
            self.conn.close()

    def __del__(self):
        self.close()
