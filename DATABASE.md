# 数据库 Schema & 示例 SQL

> RAG 系统使用 **PostgreSQL 14+ + pgvector** 作为唯一持久化存储。共 5 张业务表，所有表名以 `rag_` 前缀。
> 启动时由 `pg_vectorstore.py` 幂等创建（建表 + 索引），`init_db.sql` 为参考脚本。

---

## 1. 环境要求

| 项 | 版本 / 说明 |
| --- | --- |
| PostgreSQL | **14+**（推荐 15/16） |
| 扩展 | `pgvector >= 0.5`（必须） |
| 字符集 | `UTF-8` |
| 时区 | 建表使用 `TIMESTAMPTZ`，对客户端时区无要求 |
| 默认连接 | `postgresql://rag_user:rag_123456@101.43.38.7:5432/rag_db` |

```bash
# 安装 pgvector（Linux）
sudo apt install postgresql-server-dev-16
git clone https://github.com/pgvector/pgvector.git && cd pgvector
make && sudo make install

# 在目标库内启用
psql -U rag_user -d rag_db -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 一键初始化
psql -U rag_user -d rag_db -f init_db.sql
```

---

## 2. 表结构总览

| 表名 | 行数级别 | 用途 |
| --- | --- | --- |
| `rag_chunks` | **大**（10w~100w+） | 文本块 + 1024 维向量；检索主表 |
| `rag_bm25_cache` | 小（≤ 数行） | BM25 倒排索引缓存（JSON 整块） |
| `rag_conversations` | 中 | 会话元信息 |
| `rag_messages` | 大 | 对话消息（含检索引用 JSON） |
| `rag_llm_calls` | **大**（每问答数次） | LLM/embedding 调用流水，用于 token 统计 |

---

## 3. 详细 Schema

### 3.1 `rag_chunks` — 文本块 + 向量

```sql
CREATE TABLE rag_chunks (
    id          VARCHAR(64) PRIMARY KEY,        -- 块 ID（MD5(text+source+chunk_index)）
    session_id  VARCHAR(64) NOT NULL DEFAULT 'global',
    text        TEXT        NOT NULL,           -- 切块原文
    source      VARCHAR(512),                   -- 来源文件名（含扩展名）
    chunk_index INTEGER,                        -- 块在文件内的序号（0-based）
    start_pos   INTEGER,                        -- 原文起始字符偏移
    end_pos     INTEGER,                        -- 原文结束字符偏移
    char_count  INTEGER,                        -- 块字符数（≈ end-start）
    embedding   vector(1024),                   -- text-embedding-v4 / 1024 维
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_rag_chunks_session ON rag_chunks(session_id);
CREATE INDEX idx_rag_chunks_source  ON rag_chunks(source);
-- HNSW 近似最近邻（余弦距离）— 推荐用于 5w 行以上
CREATE INDEX idx_rag_chunks_embedding
    ON rag_chunks USING hnsw (embedding vector_cosine_ops);
```

**注意**
- `embedding` 维度必须与 `Config.EMBEDDING_DIMENSION` 一致；改维度需 `DROP INDEX → ALTER COLUMN → 重建索引`。
- `session_id` 当前固定 `'global'`（保留列以便未来多知识库隔离）。
- 前端 `KbFile.total_chars` 是聚合别名 `SUM(char_count) AS total_chars`，不是表字段名。

---

### 3.2 `rag_bm25_cache` — BM25 倒排缓存

```sql
CREATE TABLE rag_bm25_cache (
    session_id  VARCHAR(64) PRIMARY KEY,
    index_data  JSONB NOT NULL,           -- 倒排表 + 文档长度等结构
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
```

- 整体序列化为 JSON 后存一行；增删 chunk 后由 `bm25.py` 全量重写。
- 表很小（单行），不需要额外索引。

---

### 3.3 `rag_conversations` — 会话

```sql
CREATE TABLE rag_conversations (
    id         VARCHAR(64) PRIMARY KEY,   -- uuid
    title      TEXT,                      -- 自动用首问生成（title 模型）
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

### 3.4 `rag_messages` — 对话消息

```sql
CREATE TABLE rag_messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL
        REFERENCES rag_conversations(id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL,  -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    retrieval_info  JSONB,                 -- 引用片段：[{id, source, score, text_snippet}, ...]
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_rag_messages_conv
    ON rag_messages(conversation_id, created_at);
```

- 删会话级联删消息。
- 反馈相关字段（`rating / reasons / comment`）若启用，会写入 `retrieval_info` JSONB 内的 `feedback` 子对象，便于扩展不改表结构。

---

### 3.5 `rag_llm_calls` — LLM 调用流水

```sql
CREATE TABLE rag_llm_calls (
    id                 BIGSERIAL PRIMARY KEY,
    ts                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model              VARCHAR(128) NOT NULL,        -- qwen3.6-plus / text-embedding-v4 ...
    call_type          VARCHAR(32)  NOT NULL DEFAULT 'chat',
                                                    -- chat | title | embedding
    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
    completion_tokens  INTEGER NOT NULL DEFAULT 0,
    total_tokens       INTEGER NOT NULL DEFAULT 0,
    note               TEXT
);

CREATE INDEX idx_rag_llm_calls_ts    ON rag_llm_calls(ts DESC);
CREATE INDEX idx_rag_llm_calls_type  ON rag_llm_calls(call_type);
CREATE INDEX idx_rag_llm_calls_model ON rag_llm_calls(model);
```

---

## 4. ER 关系

```
rag_conversations 1 ──< N rag_messages
        (CASCADE delete)

rag_chunks         独立（按 source 聚合归属"文件"，但表中无 file 实体）
rag_bm25_cache     与 rag_chunks 一一对应（按 session_id）
rag_llm_calls      独立（仅审计）
```

---

## 5. 常用查询

### 5.1 已索引文件列表（驱动 `GET /api/files`）

```sql
SELECT
    MD5(source)                         AS id,
    source,
    COUNT(*)                            AS chunk_count,
    SUM(char_count)                     AS total_chars,
    MIN(created_at)                     AS uploaded_at
FROM rag_chunks
WHERE session_id = 'global'
GROUP BY source
ORDER BY uploaded_at DESC;
```

### 5.2 删除某个文件的全部分块

```sql
DELETE FROM rag_chunks
WHERE session_id = 'global' AND source = '签证服务协议.pdf';

-- 别忘了同步刷新 BM25
DELETE FROM rag_bm25_cache WHERE session_id = 'global';
-- 后端下一次检索时会自动重建
```

### 5.3 向量近似检索（top-K，余弦距离）

```sql
-- 注意 :query_vector 是 1024 维 float 数组
SELECT id, source, chunk_index,
       1 - (embedding <=> :query_vector) AS cosine_sim,   -- 余弦相似度
       text
FROM rag_chunks
WHERE session_id = 'global'
ORDER BY embedding <=> :query_vector                       -- 距离升序
LIMIT 5;
```

> `<=>` 是 pgvector 的"余弦距离"运算符；对应 `vector_cosine_ops` 索引。
> 对应前端命中卡片中的 score 值即 `1 - distance`。

### 5.4 按时间范围统计 token 用量

```sql
SELECT
    DATE_TRUNC('day', ts) AS day,
    call_type,
    SUM(prompt_tokens)     AS prompt,
    SUM(completion_tokens) AS completion,
    SUM(total_tokens)      AS total,
    COUNT(*)               AS calls
FROM rag_llm_calls
WHERE ts >= NOW() - INTERVAL '30 days'
GROUP BY day, call_type
ORDER BY day, call_type;
```

### 5.5 按模型聚合 token 用量

```sql
SELECT
    model,
    COUNT(*)               AS calls,
    SUM(prompt_tokens)     AS prompt,
    SUM(completion_tokens) AS completion,
    SUM(total_tokens)      AS total
FROM rag_llm_calls
GROUP BY model
ORDER BY total DESC;
```

### 5.6 反馈：按天统计赞 / 踩

```sql
SELECT
    DATE(created_at) AS day,
    SUM(CASE WHEN (retrieval_info->'feedback'->>'rating')::int =  1 THEN 1 ELSE 0 END) AS up,
    SUM(CASE WHEN (retrieval_info->'feedback'->>'rating')::int = -1 THEN 1 ELSE 0 END) AS down
FROM rag_messages
WHERE role = 'assistant'
  AND retrieval_info ? 'feedback'
  AND created_at >= NOW() - INTERVAL '30 days'
GROUP BY day
ORDER BY day;
```

### 5.7 找出"被踩"消息及其原因

```sql
SELECT
    m.id,
    m.conversation_id,
    c.title,
    LEFT(m.content, 120) AS answer_preview,
    m.retrieval_info->'feedback'->>'reasons' AS reasons,
    m.retrieval_info->'feedback'->>'comment' AS comment,
    m.created_at
FROM rag_messages m
JOIN rag_conversations c ON c.id = m.conversation_id
WHERE (m.retrieval_info->'feedback'->>'rating')::int = -1
ORDER BY m.created_at DESC
LIMIT 50;
```

### 5.8 某次会话的完整对话上下文

```sql
SELECT id, role, LEFT(content, 200) AS preview, created_at
FROM rag_messages
WHERE conversation_id = 'xxxx-uuid'
ORDER BY created_at;
```

### 5.9 查看分块内容（按文件）

```sql
SELECT chunk_index, char_count, LEFT(text, 80) AS preview
FROM rag_chunks
WHERE source = '签证服务协议.pdf'
ORDER BY chunk_index;
```

### 5.10 数据库体积统计

```sql
SELECT
    relname AS table_name,
    pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
    pg_size_pretty(pg_relation_size(relid))       AS table_size,
    pg_size_pretty(pg_indexes_size(relid))        AS index_size,
    n_live_tup                                    AS rows
FROM pg_stat_user_tables
WHERE relname LIKE 'rag_%'
ORDER BY pg_total_relation_size(relid) DESC;
```

---

## 6. 索引策略

| 索引 | 类型 | 何时建议调整 |
| --- | --- | --- |
| `idx_rag_chunks_embedding` (HNSW) | 近似最近邻 | 默认即可；超 100w 行可调 `m=16, ef_construction=64`（CREATE 时指定） |
| `idx_rag_chunks_source` | B-tree | 按文件过滤 / 删除使用，必须保留 |
| `idx_rag_messages_conv` | B-tree 复合 | 按会话拉历史使用，必须保留 |
| `idx_rag_llm_calls_ts` | B-tree DESC | 趋势/最新流水用，必须保留 |

**HNSW 索引参数（如需自定义）**
```sql
CREATE INDEX idx_rag_chunks_embedding
    ON rag_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
-- 查询前可调 ef_search（越大越准但越慢）：
SET hnsw.ef_search = 100;
```

**何时改用 IVFFlat**
- 数据量 < 5w：简单数据集 IVFFlat 足够，构建快、内存小。
- 数据量 > 50w 且 RAM 充足：HNSW 召回率更高。

---

## 7. 维护与运维

### 7.1 备份

```bash
# 完整备份（含 vector 数据）
pg_dump -U rag_user -d rag_db -F c -f rag_$(date +%F).dump

# 仅 schema
pg_dump -U rag_user -d rag_db -s -f rag_schema.sql

# 恢复
pg_restore -U rag_user -d rag_db_new rag_2024-01-15.dump
```

### 7.2 清空（重建索引）

```sql
-- 危险操作！清空所有切块，对话保留
TRUNCATE TABLE rag_chunks RESTART IDENTITY;
DELETE FROM rag_bm25_cache;

-- 完全重置（含会话/反馈）
TRUNCATE TABLE rag_messages, rag_conversations,
              rag_chunks, rag_bm25_cache, rag_llm_calls
       RESTART IDENTITY CASCADE;
```

### 7.3 重建向量索引

```sql
DROP INDEX IF EXISTS idx_rag_chunks_embedding;
CREATE INDEX idx_rag_chunks_embedding
    ON rag_chunks USING hnsw (embedding vector_cosine_ops);
-- HNSW 构建较慢，10w 行约 1~3 分钟
```

### 7.4 VACUUM / 统计信息

```sql
-- 大量删除/更新后回收空间
VACUUM (ANALYZE, VERBOSE) rag_chunks;
VACUUM (ANALYZE, VERBOSE) rag_messages;
```

---

## 8. 常见问题

| 现象 | 原因 / 排查 |
| --- | --- |
| `ERROR: type "vector" does not exist` | 未启用 pgvector：`CREATE EXTENSION vector;` |
| `ERROR: expected 1024 dimensions, not N` | 模型维度变更未同步 schema；DROP 列后重建 |
| 检索召回随机 / 很慢 | HNSW 索引未建或被破坏；`REINDEX` 或重建 |
| `rag_bm25_cache` 越来越大 | 倒排表整块 JSON，正常；超 50MB 时考虑改增量结构 |
| `rag_llm_calls` 行数爆炸 | 加分区表（按月）或定期归档 6 个月前的数据 |
| 删除文件后仍能检索到 | BM25 缓存未刷新；`DELETE FROM rag_bm25_cache;` |
| 中文分词召回差 | jieba 自定义词典是否加载（`bm25.py` 启动日志） |

---

## 9. 推荐扩展

```sql
-- 启用查询监控（可选）
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- 启用 trigram 支持模糊文本搜索（可选，用于"按 source 模糊搜文件"）
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_rag_chunks_source_trgm
    ON rag_chunks USING gin (source gin_trgm_ops);
```

---

## 10. 与代码的对应关系

| 文件 | 职责 |
| --- | --- |
| `core/pg_vectorstore.py` | 建表、写 chunk、向量检索（pgvector cosine） |
| `core/bm25.py` | 维护 `rag_bm25_cache` 倒排索引 |
| `core/api/routers/conversations.py` | 操作 `rag_conversations` / `rag_messages` |
| `core/api/routers/feedback.py` | 写入/读取 `rag_messages.retrieval_info.feedback` |
| `core/llm_stats.py` | 写入 `rag_llm_calls`，提供按天/小时聚合查询 |
| `init_db.sql` | 一键初始化脚本（程序启动时也会幂等创建） |
