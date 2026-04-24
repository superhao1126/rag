# 后端项目说明（Backend Overview）

> 一个基于 **FastAPI + 阿里云百炼大模型 + PostgreSQL/pgvector** 的 RAG 知识问答系统后端。
>
> 提供文件上传 → 清洗 → 切块 → 向量化 → 入库 → 混合检索（向量 + BM25） → LLM 流式问答 全链路服务。

---

## 1. 技术栈

| 类别 | 选型 | 说明 |
| --- | --- | --- |
| 语言 / 运行时 | **Python 3.10.20** | 严格遵守，避免 Paddle / pgvector 二进制兼容性问题 |
| Web 框架 | FastAPI 0.110+ + Uvicorn | 异步路由、自动 OpenAPI 文档 |
| LLM / Embedding | 阿里云百炼 (`dashscope` 兼容 OpenAI SDK) | `qwen3.6-plus` 对话；`text-embedding-v4` 1024 维向量 |
| 向量库 | PostgreSQL + **pgvector** | 远程 PG，存切块、向量、BM25 缓存、对话记录、token 统计 |
| 关键词检索 | **jieba** + 自实现 BM25 | 中文分词 + RRF 倒数排名融合 |
| 文档解析 | PyMuPDF / PyPDF2 / python-docx / openpyxl / xlrd | 多格式读取，PyMuPDF 优先 |
| OCR | PaddleOCR 2.7+ + opencv-python | 扫描件 PDF / 图片回退识别 |
| 数据库驱动 | psycopg2-binary 2.9+ | 同步驱动，连接池由 pipeline 管理 |
| 静态托管 | FastAPI StaticFiles + SPA fallback | 直接挂载 `frontend/dist` |

---

## 2. 目录结构

```
rag/
├── server.py              # FastAPI 入口：装配中间件、路由、静态文件、SPA fallback
├── main.py                # CLI 入口：命令行交互式问答 / 批量构建
├── init_db.sql            # PG 库初始化（建表、pgvector 扩展、索引）
├── requirements.txt       # 依赖清单
├── testOcr.py             # OCR 调试脚本
├── output/                # CLI 模式产物目录
│
├── core/                  # 业务核心
│   ├── config.py          # 全局配置（一切开关入口）
│   ├── pipeline.py        # ★ RAGPipeline：装配 reader/cleaner/chunker/vectorstore/retriever
│   ├── reader.py          # 多格式文件读取（PDF/DOCX/XLSX/TXT/MD/HTML）
│   ├── cleaner.py         # ★ 7 步文本清洗去噪流水线
│   ├── chunker.py         # 文本切块（API 边界感知 + 滑动窗口）
│   ├── ocr.py             # PaddleOCR 封装（扫描件兜底）
│   ├── vectorstore.py     # 本地 numpy 向量存储（fallback）
│   ├── pg_vectorstore.py  # ★ PostgreSQL + pgvector 持久化
│   ├── bm25.py            # 中文 BM25（jieba 分词 + 缓存）
│   ├── retriever.py       # 混合检索：向量 + BM25 + RRF 融合
│   ├── llm_stats.py       # Token / 调用次数 统计与持久化
│   │
│   └── api/               # FastAPI 应用层
│       ├── deps.py        # 全局 RAGPipeline 依赖注入
│       ├── middleware.py  # ★ 统一响应包装 + 异常处理器
│       └── routers/
│           ├── files.py         # 文件上传（异步后台处理）/ 列表 / 删除
│           ├── query.py         # 问答接口 + SSE 流式
│           ├── conversations.py # 会话列表 / 历史 / 重命名 / 删除
│           ├── feedback.py      # 点赞/点踩 + 原因 + 趋势统计
│           └── misc.py          # token 统计、分块预览、健康检查
│
└── frontend/              # Vue 3 前端（详见 FRONTEND.md）
```

---

## 3. 核心数据流

```
用户上传文件
    ↓ POST /api/upload (BackgroundTasks 后台异步)
[1] reader.read_file()           读取解析（PyMuPDF → OCR fallback）
    ↓ 原始文本
[2] cleaner.clean(text)          7 步清洗去噪（NFKC/控制字符/HTML/乱码/页眉/空白/CJK）
    ↓ 清洗后文本
[3] chunker.chunk()              切块（CHUNK_SIZE=300, OVERLAP=60）
    ↓ List[chunk]
[4] vectorstore.embed_batch()    批量调用百炼 embedding API → 1024 维向量
    ↓ vectors
[5] pg_vectorstore.add_chunks()  写入 rag_chunks 表（含 vector(1024) + 元信息）
    ↓ 同步刷新 BM25 缓存表
完成

用户提问
    ↓ POST /api/query (SSE)
[A] embed(query)                 向量化问题
[B] retriever.hybrid_search()    向量 cosine + BM25 → RRF 融合 → top_k=5
[C] 拼接 prompt + history (5 轮)
[D] LLM 流式生成 → 持续 SSE 推送
[E] 落库 messages / llm_calls
```

---

## 4. 数据库表（PostgreSQL）

| 表 | 用途 |
| --- | --- |
| `rag_chunks` | 文本块 + embedding `vector(1024)` + source / chunk_index / char_count（前端用 `SUM(char_count) AS total_chars` 聚合） |
| `rag_bm25_cache` | BM25 倒排缓存（整块 JSONB，按 session_id 单行） |
| `rag_conversations` | 会话元信息（id, title, created_at, updated_at） |
| `rag_messages` | 对话消息（含 role, content, retrieval_info JSONB；反馈 rating/reasons/comment 写入 `retrieval_info.feedback` 子对象，不占独立字段） |
| `rag_llm_calls` | 每次 LLM 调用流水（model, call_type, prompt/completion/total tokens, ts） |

> 连接信息默认走环境变量 `PG_HOST/PG_PORT/PG_DATABASE/PG_USER/PG_PASSWORD`，回退到 `core/config.py` 内置值。
> 远程库建议改为 `PG_URI` 一次性传入。

---

## 5. API 速览

| Method | Path | 说明 |
| --- | --- | --- |
| `POST` | `/api/upload` | **快速接收**（落盘后 `202`，后台静默处理） |
| `POST` | `/api/upload/stream` | SSE 上传：实时返回 reading/cleaning/chunking/embedding/storing 阶段进度 |
| `GET`  | `/api/files` | 已索引文件列表（来自 `rag_chunks` 聚合） |
| `DELETE` | `/api/files/{id}` | 删除文件及其全部分块 |
| `GET`  | `/api/chunks` | 分块预览（按 source 或 file_id 过滤） |
| `POST` | `/api/query` | 问答（SSE 流式） |
| `GET`  | `/api/conversations` | 会话列表 |
| `GET`  | `/api/conversations/{id}` | 会话详情 + 消息历史 |
| `POST` | `/api/feedback` | 提交点赞/点踩/原因/评论 |
| `GET`  | `/api/feedback` | 反馈聚合（含趋势） |
| `GET`  | `/api/llm/stats` | Token 用量聚合 |
| `GET`  | `/api/llm/calls` | LLM 调用流水（分页 + 过滤） |
| `GET`  | `/api/llm/trend` | Token 趋势（hour/day 桶） |

> **统一响应格式**：`UnifiedResponseMiddleware` 把所有 JSON 响应包装为 `{ code, message, data }`；2xx 状态都视为成功（前端拦截器同步放行 2xx）。

---

## 6. 关键配置（`core/config.py`）

| 区块 | 关键字段 | 作用 |
| --- | --- | --- |
| 模型 | `LLM_MODEL` / `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION` | 切换百炼模型；维度必须与建表 `vector(1024)` 一致 |
| 切块 | `CHUNK_SIZE=300` / `CHUNK_OVERLAP=60` | 偏小，对中文 API 文档/合同检索友好 |
| 检索 | `TOP_K=5` / `VECTOR_WEIGHT=0.6` / `BM25_WEIGHT=0.4` / `RRF_K=60` | 混合检索权重 |
| PG | `USE_PG=True` / `PG_*` | False 时回退到本地 numpy 文件存储 |
| OCR | `OCR_ENABLED=True` / `OCR_LANG="ch"` / `OCR_CONF_THRESHOLD=0.6` / `OCR_PDF_RENDER_ZOOM=2.0` | 扫描件回退识别参数 |
| 清洗 | 10 个 `CLEAN_*` 开关 + `CLEAN_DUP_LINE_THRESHOLD=3` | 7 步清洗每步可独立开关 |
| 交互 | `STREAM=True` / `HISTORY_TURNS=5` | LLM 流式 + 上下文记忆轮数 |

---

## 7. 环境与启动

### 7.1 环境要求

- **Python 3.10.20**（严格）
  - PaddlePaddle 2.6 / paddleocr 2.7 对 3.11+ 兼容性差，3.10.20 是验证过的稳定组合
- PostgreSQL 14+ 并启用 `pgvector` 扩展（`CREATE EXTENSION vector;`）
- 系统包：libgl / libglib（PaddleOCR 依赖，macOS 通常已自带）

### 7.2 安装

```bash
# 1. 创建虚拟环境（推荐 venv / conda）
python3.10 -m venv .venv
source .venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 初始化数据库（首次）
psql -h $PG_HOST -U $PG_USER -d $PG_DATABASE -f init_db.sql
```

### 7.3 环境变量

```bash
export DASHSCOPE_API_KEY="sk-xxxxx"     # 必填
export PG_HOST=101.43.38.7
export PG_PORT=5432
export PG_DATABASE=rag_db
export PG_USER=rag_user
export PG_PASSWORD=rag_123456
# 或一次性：
export PG_URI="postgresql://rag_user:rag_123456@101.43.38.7:5432/rag_db"

export PORT=8000                         # 可选
```

### 7.4 启动

```bash
# Web 服务（生产）
python server.py
# 访问 http://localhost:8000   API 文档 http://localhost:8000/docs

# CLI 模式
python main.py --files doc1.pdf --interactive
python main.py --dir ./documents -q "主要内容是什么？"
```

---

## 8. 注意事项

### 8.1 中间件 / 异步

- `BackgroundTasks` 通过 `Response.background` 传递。**自定义中间件如重建 Response 必须保留 `background=getattr(response, "background", None)`**，否则后台清洗永不执行（已修复）。
- `UnifiedResponseMiddleware` 把原 HTTP status 写入 body `code` 字段（202/204/...），前端拦截器需接受 2xx 范围而非仅 200。

### 8.2 OCR

- `paddleocr` / `paddlepaddle` 安装包大（~500MB），首次运行还会下载模型。**离线/CI 环境建议关闭**：`OCR_ENABLED=False`。
- 仅当 PyMuPDF 提取的文本字符数 < 阈值时才回退 OCR；普通可复制 PDF 不会触发。

### 8.3 清洗

- 7 步清洗按顺序执行，**S7 CJK 友好**对中文 PDF 增益最大（合并 "第 八 条"→"第八条"）。
- 想完全保留原文（如代码块/日志），把 10 个 `CLEAN_*` 全置 `False`。

### 8.4 PG / 向量

- 改 `EMBEDDING_DIMENSION` **必须**同步 `init_db.sql` 中 `vector(1024)` 维度并重建索引。
- pgvector 索引当前使用 **HNSW** + `vector_cosine_ops`（`init_db.sql` 默认）；数据量较小（< 5w）时也可换 `ivfflat (lists=100)`。
- 远程 PG 网络延迟会显著影响批量 embedding 入库速度，建议 `add_chunks` 端用 `execute_values` 批写。
- 详细 schema、运维 SQL、常用查询见 `DATABASE.md`。

### 8.5 大模型 API

- 阿里云百炼：兼容 OpenAI SDK，设置 `BASE_URL` 为 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- 流式调用通过 `stream=True` + SSE 传给前端，注意 `await` 异步 client。

### 8.6 安全

- API Key 仅从环境变量读，**禁止**写入仓库。
- PG 凭据当前默认值是测试库，生产请改用环境变量并删除默认值。
- CORS 当前 `allow_origins=["*"]`，上线务必限定域名。

### 8.7 性能

- BM25 缓存与切块写库在同一事务，文件多时上传明显变慢；可考虑独立 worker 队列。
- LLM 流式输出阶段会持续占用一个 uvicorn worker，务必启动多 worker：`uvicorn server:app --workers 4`。

---

## 9. 调试与排错

| 现象 | 排查方向 |
| --- | --- |
| 上传 202 但前端报错 | 检查 axios 拦截器是否接受 2xx（已修复） |
| 后台任务不执行 | 中间件是否丢失 `response.background`（已修复） |
| 检索召回为空 | 确认 `rag_chunks` 是否有数据；BM25 缓存表是否同步刷新 |
| OCR 报错 ImportError | 关闭 `OCR_ENABLED` 或安装 `paddleocr` 全套 |
| pgvector 报维度不匹配 | 模型维度与建表 `vector(N)` 必须一致 |
| token 统计为 0 | 检查 `llm_stats` 是否被正确调用（`pipeline._record_call`） |

---

## 10. 后续可优化

- 引入异步 PG 驱动（asyncpg）替换 psycopg2-binary 同步调用
- 用 Celery / RQ 把后台清洗+向量化迁出 FastAPI 进程
- 切块策略加入语义边界（句子/标题）
- 检索结果加入 rerank（如 bge-reranker） 提高 top_k 精度
- 多租户 / 知识库隔离（增加 `kb_id` 字段）
