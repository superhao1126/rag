-- ============================================================
--  RAG 知识问答系统 — PostgreSQL 数据库初始化脚本
--  说明: 程序启动时会自动创建以下表和索引（幂等操作），
--       此脚本仅供参考或手动初始化使用。
-- ============================================================

-- 启用 pgvector 扩展（需要 PostgreSQL 已安装 pgvector）
CREATE EXTENSION IF NOT EXISTS vector;

-- 文本块 + 向量存储表（全局共享，session_id 保留列兼容旧数据，新数据写入 'global'）
CREATE TABLE IF NOT EXISTS rag_chunks (
    id          VARCHAR(64) PRIMARY KEY,          -- 文本块唯一 ID（MD5）
    session_id  VARCHAR(64) NOT NULL DEFAULT 'global',
    text        TEXT NOT NULL,                    -- 文本块原文
    source      VARCHAR(512),                     -- 来源文件名
    chunk_index INTEGER,                          -- 块在文件中的序号
    start_pos   INTEGER,                          -- 原文起始字符位置
    end_pos     INTEGER,                          -- 原文结束字符位置
    char_count  INTEGER,                          -- 文本块字符数
    embedding   vector(1024),                     -- 向量（text-embedding-v3 为 1024 维）
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 按 session_id / source 查询索引
CREATE INDEX IF NOT EXISTS idx_rag_chunks_session   ON rag_chunks(session_id);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_source    ON rag_chunks(source);

-- HNSW 向量近似最近邻索引（余弦距离）
CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding
    ON rag_chunks USING hnsw (embedding vector_cosine_ops);

-- BM25 索引缓存表（全局唯一，key = 'global'）
CREATE TABLE IF NOT EXISTS rag_bm25_cache (
    session_id  VARCHAR(64) PRIMARY KEY,
    index_data  JSONB NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 对话表
CREATE TABLE IF NOT EXISTS rag_conversations (
    id         VARCHAR(64) PRIMARY KEY,
    title      TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 消息表（与对话级联删除）
CREATE TABLE IF NOT EXISTS rag_messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL
        REFERENCES rag_conversations(id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL,           -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    retrieval_info  JSONB,                          -- 检索片段信息
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rag_messages_conv
    ON rag_messages(conversation_id, created_at);
