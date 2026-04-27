# RAG 知识问答系统

> 本 README 是项目唯一文档入口，包含架构、目录、依赖、启动、数据库、前端、接口与维护说明。

基于 **FastAPI + Vue 3 + PostgreSQL/pgvector + 阿里云百炼** 的 RAG 项目，覆盖：

1. 文件上传与解析
2. 文本清洗与切块
3. 向量化入库与 BM25 检索
4. 混合检索问答与对话持久化
5. 文件管理、反馈分析、Token 统计

---

## 1. 技术栈

### 后端

| 类别 | 选型 | 说明 |
| --- | --- | --- |
| 语言 / 运行时 | Python 3.10.20 | Paddle / pgvector 组合建议固定 3.10 |
| Web 框架 | FastAPI + Uvicorn | 异步接口、自动 OpenAPI |
| LLM / Embedding | 阿里云百炼（OpenAI 兼容） | `qwen3.6-plus`、`text-embedding-v4` |
| 向量库 | PostgreSQL + pgvector | 持久化切块、向量、会话、统计 |
| 检索 | jieba + 自实现 BM25 + RRF | 中文分词、混合检索 |
| 文档解析 | PyMuPDF / PyPDF2 / python-docx / openpyxl / xlrd | 多格式读取 |
| OCR | PaddleOCR + opencv-python | 扫描件 / 图片识别 |
| 数据库驱动 | psycopg2-binary | 当前为同步驱动 |

### 前端

| 类别 | 选型 | 说明 |
| --- | --- | --- |
| 运行时 | Node.js 24 | 前端命令前先执行 `fnm use 24` |
| 包管理 | pnpm 10.30+ | 不要混用 npm / yarn |
| 框架 | Vue 3 + `<script setup>` | 单页应用 |
| 构建 | Vite 8 | 开发 / 构建 / 预览 |
| 语言 | TypeScript 6 + vue-tsc | 严格类型 |
| UI | Ant Design Vue 4 | 表格、表单、弹窗 |
| 状态 | Pinia 3 | 会话状态管理 |
| 路由 | vue-router 5 | hash 模式 |
| 数据请求 | axios + SSE | 普通接口 + 流式问答 / 上传 |
| 图表 | ECharts 5 | 反馈趋势、Token 趋势 |
| Markdown | marked + highlight.js | 回答渲染、代码高亮 |

---

## 2. 目录与文件用途

### 根目录

| 路径 | 用途 |
| --- | --- |
| `README.md` | 当前总入口文档 |
| `server.py` | FastAPI 入口：中间件、路由、静态资源、SPA fallback |
| `main.py` | CLI 入口：批量导入、直接提问、交互问答 |
| `core/` | 后端核心模块 |
| `frontend/` | Vue 3 前端工程 |
| `init_db.sql` | PostgreSQL 初始化脚本 |
| `requirements.txt` | Python 依赖 |
| `.env.example` | 环境变量模板 |
| `testOcr.py` | OCR 调试脚本 |
| `output/` | CLI 输出目录 |

### `core/`

| 路径 | 用途 |
| --- | --- |
| `core/config.py` | 全局配置：模型、检索、OCR、数据库、清洗开关 |
| `core/pipeline.py` | RAG 总编排层 |
| `core/reader.py` | 多格式文件读取 |
| `core/cleaner.py` | 7 步文本清洗流水线 |
| `core/chunker.py` | 切块：API 边界感知 + 滑动窗口 |
| `core/vectorstore.py` | 本地 numpy 存储 fallback |
| `core/pg_vectorstore.py` | PostgreSQL + pgvector 存储与检索 |
| `core/bm25.py` | BM25 稀疏检索缓存 |
| `core/retriever.py` | 向量 + BM25 融合检索 |
| `core/ocr.py` | PaddleOCR 封装 |
| `core/llm_stats.py` | LLM / embedding 调用统计 |

### `core/api/routers/`

| 路径 | 用途 |
| --- | --- |
| `files.py` | 上传、文件列表、删除、分块预览 |
| `query.py` | SSE 流式问答 |
| `conversations.py` | 会话列表、详情、重命名、删除 |
| `feedback.py` | 点赞/点踩/原因与趋势统计 |
| `misc.py` | 健康检查、Token 统计、趋势、分块查询 |

### `frontend/src/`

| 路径 | 用途 |
| --- | --- |
| `views/ChatView.vue` | 主对话页 |
| `views/DbFilesView.vue` | 文件管理页 |
| `views/FeedbackView.vue` | 反馈分析页 |
| `views/TokensView.vue` | Token 统计页 |
| `api/` | 后端接口与 SSE 封装 |
| `components/` | 头部、分块弹窗、图表等组件 |
| `stores/session.ts` | Pinia 会话状态 |

---

## 3. 核心数据流

```text
用户上传文件
    ↓ POST /api/upload
[0] upload queue.enqueue()    快速返回，后台 worker 串行消费
[1] reader.read()             读取解析（PyMuPDF → OCR fallback）
[2] cleaner.clean()           清洗去噪（结构保护 / HTML / 页眉页脚 / 噪声 token / CJK 空格）
[3] chunker.chunk()           切块（chunk_size=300, overlap=60）
[4] vector_store.add()        embedding → 1024 维向量
[5] pg_vectorstore.add_chunks 写入 rag_chunks + 刷新 BM25 缓存

用户提问
    ↓ POST /api/query
[A] embed(query)              问题向量化
[B] retriever.search()        向量 + BM25 + RRF 融合
[C] prompt + history          组装上下文
[D] LLM stream                SSE 流式返回
[E] messages / llm_calls      对话与统计落库
```

### 3.1 文本清理策略

`core/cleaner.py` 的目标是**只降噪、不改写语义**。当前默认行为：

| 能力 | 默认 | 说明 |
| --- | --- | --- |
| Unicode / HTML entity | 开启 | NFKC 归一化，并用 `html.unescape` 处理 `&nbsp;`、`&#32;`、`&#x20;` 等实体 |
| 控制字符清理 | 开启 | 去除控制字符、零宽字符、BOM |
| 结构化内容保护 | 开启 | 保护 fenced code、缩进代码、Markdown 表格、`$$` 公式块，避免后续规则破坏格式 |
| HTML 清理 | 开启 | 删除 `<script>/<style>` 整块内容；仅剥离常见 HTML 标签，保留 `List<T>`、`<token>` 等正文占位符 |
| 噪声 token | 开启 | 过滤长 hash/base64/JWT、超长 query value、OCR `�` 噪声等 |
| 页眉页脚 / 重复行 / 纯标点行 | 开启 | 删除明确页码格式、达到阈值的连续重复行、纯标点行；纯数字行默认保留 |
| 空白与 CJK | 开启 | 多空格合并、多空行压为单换行；中文间错误空格会合并，中英/数字混排会补空格 |
| 隐私脱敏 | 关闭 | 可选替换 URL、Email、手机号为 `<URL>`、`<EMAIL>`、`<PHONE>` |

`CleanReport.summary()` 会返回保留率、规则命中、删除行/字符数和风险提示。若出现 `low_retention`、`many_deleted_lines`、`many_html_tags`、`many_noise_tokens`，优先检查清理规则是否过强。

---

## 4. 依赖清单

### Python 依赖

| 依赖 | 用途 |
| --- | --- |
| `openai` | 阿里云百炼 OpenAI 兼容调用 |
| `numpy` | 本地向量计算与 fallback 存储 |
| `PyPDF2` / `pymupdf` | PDF 解析 |
| `python-docx` | Word 解析 |
| `openpyxl` / `xlrd` | Excel 解析 |
| `jieba` | 中文分词 |
| `fastapi` / `uvicorn` | Web 服务 |
| `python-multipart` | 文件上传 |
| `psycopg2-binary` / `pgvector` | PostgreSQL 与向量支持 |
| `opencv-python` / `paddlepaddle` / `paddleocr` | OCR |
| `pytest` / `httpx` | 测试与 API 冒烟 |

### 前端依赖

| 依赖 | 用途 |
| --- | --- |
| `vue` / `vue-router` / `pinia` | 应用框架、路由、状态 |
| `axios` | HTTP 请求 |
| `ant-design-vue` / `@ant-design/icons-vue` | UI 与图标 |
| `echarts` | 图表 |
| `marked` / `highlight.js` | Markdown 渲染与代码高亮 |
| `vite` / `typescript` / `vue-tsc` | 构建与类型检查 |
| `eslint` / `oxlint` / `oxfmt` | 代码质量工具 |

---

## 5. 快速开始

### 5.1 后端

```bash
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5.2 前端

```bash
fnm use 24
cd frontend
pnpm install
```

### 5.3 环境变量

```bash
cp .env.example .env
```

项目会在启动时**自动读取仓库根目录 `.env`**；如果部署环境已有同名环境变量，则以外部环境变量为准。

最少配置示例：

```bash
export DASHSCOPE_API_KEY="sk-xxxx"

# PostgreSQL 模式（二选一）
export USE_PG=true
export PG_HOST=127.0.0.1
export PG_PORT=5432
export PG_DATABASE=rag_db
export PG_USER=rag_user
export PG_PASSWORD="change_me"

# 或直接提供连接串
export PG_URI="postgresql://rag_user:change_me@127.0.0.1:5432/rag_db"

# 可选
export PORT=8000
```

默认是本地模式；如果只想显式指定本地模式：

```bash
export USE_PG=false
```

### 5.4 初始化数据库

```bash
psql -h "$PG_HOST" -U "$PG_USER" -d "$PG_DATABASE" -f init_db.sql
```

### 5.5 启动

```bash
# Web
python server.py

# CLI
python main.py --files doc1.pdf --interactive
python main.py --dir ./documents -q "主要内容是什么？"
```

访问：

- Web：<http://localhost:8000>
- Swagger：<http://localhost:8000/docs>

---

## 6. API 速览

| Method | Path | 说明 |
| --- | --- | --- |
| `POST` | `/api/upload` | 快速上传，返回 202 后后台处理 |
| `POST` | `/api/upload/stream` | SSE 上传进度 |
| `GET` | `/api/upload/queue` | 上传任务队列状态 |
| `GET` | `/api/files` | 已索引文件列表 |
| `POST` | `/api/files/by-id/{id}/revectorize` | 对失败文件重新加入向量化队列 |
| `DELETE` | `/api/files/{id}` | 删除文件 |
| `GET` | `/api/chunks` | 分块预览 |
| `POST` | `/api/query` | SSE 流式问答 |
| `GET` | `/api/conversations` | 会话列表 |
| `GET` | `/api/conversations/{id}` | 会话详情 |
| `POST` | `/api/feedback` | 反馈提交 |
| `GET` | `/api/feedback` | 反馈聚合 |
| `GET` | `/api/llm/stats` | Token 聚合 |
| `GET` | `/api/llm/calls` | LLM 调用流水 |
| `GET` | `/api/llm/trend` | Token 趋势 |

统一响应格式为：

```json
{ "code": 200, "message": "ok", "data": {} }
```

前端拦截器按 **2xx** 范围判断成功。

---

## 7. 前端页面与交互

### ChatView

- 左侧会话列表，右侧流式消息区
- 支持点赞/点踩、引用 chunk 预览
- Markdown + 代码高亮

### DbFilesView

- 上传后立即进入 `processing`
- 标准上传走后台任务队列，前端每 2 秒轮询 `/api/files` 判断处理完成
- 文件列表持久化显示 `排队中 / 向量化中 / 已向量化 / 向量化失败`
- 向量化失败的文件支持一键重新加入队列
- 服务异常重启后，遗留的 `排队中 / 向量化中` 文件会自动恢复为可重试状态
- 支持删除、分块预览

### FeedbackView

- 满意度卡片
- 点赞/点踩趋势图
- 原因分布和明细列表

### TokensView

- 输入 / 输出 / 合计 / 调用次数统计
- 按模型、按调用类型聚合
- 趋势图与分页流水

---

## 8. 数据库设计

### 8.1 表结构总览

| 表名 | 用途 |
| --- | --- |
| `rag_chunks` | 文本块 + `vector(1024)`，检索主表 |
| `rag_bm25_cache` | BM25 倒排缓存 |
| `rag_conversations` | 会话元信息 |
| `rag_messages` | 对话消息与检索引用 |
| `rag_llm_calls` | LLM / embedding 调用流水 |

### 8.2 关键点

1. `embedding` 维度必须与 `Config.EMBEDDING_DIMENSION` 一致
2. `rag_chunks` 当前按 `source` 聚合为“文件”
3. `rag_messages` 通过 `conversation_id` 关联 `rag_conversations`
4. `rag_llm_calls` 用于趋势和模型聚合统计

### 8.3 常用 SQL

#### 已索引文件列表

```sql
SELECT
    MD5(source) AS id,
    source,
    COUNT(*) AS chunk_count,
    SUM(char_count) AS total_chars,
    MIN(created_at) AS uploaded_at
FROM rag_chunks
WHERE session_id = 'global'
GROUP BY source
ORDER BY uploaded_at DESC;
```

#### 向量近似检索

```sql
SELECT id, source, chunk_index,
       1 - (embedding <=> :query_vector) AS cosine_sim,
       text
FROM rag_chunks
WHERE session_id = 'global'
ORDER BY embedding <=> :query_vector
LIMIT 5;
```

#### Token 用量趋势

```sql
SELECT
    DATE_TRUNC('day', ts) AS day,
    call_type,
    SUM(prompt_tokens) AS prompt,
    SUM(completion_tokens) AS completion,
    SUM(total_tokens) AS total,
    COUNT(*) AS calls
FROM rag_llm_calls
WHERE ts >= NOW() - INTERVAL '30 days'
GROUP BY day, call_type
ORDER BY day, call_type;
```

---

## 9. 配置与注意事项

### 配置原则

1. **不再内置默认数据库凭据**
2. 默认 `USE_PG=false`，未配置数据库也可直接启动
3. `USE_PG=true` 时，必须提供 `PG_URI` 或完整 `PG_*`

### 文本清理配置

清理规则支持通过 `.env` 或外部环境变量开关。未配置时使用默认值。

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `CLEAN_NORMALIZE_UNICODE` | `true` | NFKC 与 HTML entity 解码 |
| `CLEAN_STRIP_CONTROL` | `true` | 控制字符、零宽字符、BOM |
| `CLEAN_PROTECT_STRUCTURED` | `true` | 保护代码块、Markdown 表格、公式块 |
| `CLEAN_STRIP_HTML` | `true` | 删除 script/style 与常见 HTML 标签 |
| `CLEAN_DROP_LONG_GIBBERISH` | `true` | 删除长特殊符号乱码 |
| `CLEAN_DROP_NOISE_TOKENS` | `true` | 删除长 hash/base64/JWT/OCR 噪声 |
| `CLEAN_LONG_TOKEN_THRESHOLD` | `48` | 长 token 判定阈值 |
| `CLEAN_REDACT_PRIVACY` | `false` | URL/Email/手机号脱敏 |
| `CLEAN_DROP_PAGE_FOOTER` | `true` | 删除明确页码格式 |
| `CLEAN_DROP_DUP_LINES` | `true` | 删除达到阈值的连续重复行 |
| `CLEAN_DUP_LINE_THRESHOLD` | `3` | 连续重复行阈值 |
| `CLEAN_DROP_PUNCT_ONLY` | `true` | 删除纯标点/符号行 |
| `CLEAN_COLLAPSE_WHITESPACE` | `true` | 多空格、多空行规范化 |
| `CLEAN_CJK_FRIENDLY` | `true` | 合并中文之间错误空格 |
| `CLEAN_CJK_ALNUM_SPACING` | `true` | 中文与英文/数字之间补空格 |

### 注意事项

- 标准上传接口已改为进程内任务队列，避免重活阻塞 FastAPI 请求线程
- `/api/upload/stream` 仍保留直连处理，便于调试实时进度
- `UnifiedResponseMiddleware` 会把原 HTTP 状态写进 body 的 `code`
- `paddleocr` 体积较大，CI / 离线环境建议关闭 OCR
- 清理报告出现低保留率或大量删除行时，应先降低相关 `CLEAN_*` 强度再重新导入文档
- `EMBEDDING_DIMENSION` 变更时，需同步数据库 `vector(N)`
- `CORS` 当前为 `*`，上线必须收紧
- 前端命令前请先执行 `fnm use 24`

---

## 10. 调试速查

| 现象 | 排查方向 |
| --- | --- |
| 上传 202 但前端报错 | 检查前端是否接受 2xx 范围 |
| 上传长时间不完成 | 检查 `/api/upload/queue` 是否堆积，或看服务日志中的 upload-queue |
| 检索召回为空 | 看 `rag_chunks` 是否有数据，BM25 缓存是否刷新 |
| 上传后召回内容缺字段 | 查看清理 summary 的删除行/字符数和风险提示，必要时关闭 `CLEAN_DROP_PAGE_FOOTER` / `CLEAN_DROP_NOISE_TOKENS` |
| Markdown 表格或代码被破坏 | 确认 `CLEAN_PROTECT_STRUCTURED=true`，并补充对应回归样例 |
| OCR ImportError | 关闭 `OCR_ENABLED` 或补装 OCR 依赖 |
| pgvector 维度不匹配 | 检查模型维度与 `vector(N)` |
| 前端 lint 报 engine/lifecycle | 确认已 `fnm use 24` 且使用 pnpm |

---

## 11. 质量保障

### 后端

- `pytest`
- 纯函数测试：cleaner / chunker / retriever
- API 冒烟：统一响应与基础错误路径

### 前端

- `pnpm run type-check`
- `pnpm run lint`
- `pnpm run build`

### CI

- GitHub Actions：`.github/workflows/ci.yml`
- 后端与前端分作业执行

---

## 12. 改进建议

### 高优先级

1. 把 OCR 拆成可选安装，降低首次部署成本
2. 继续演进为独立进程 / 外部任务队列，降低单进程 worker 风险
3. 完善多知识库隔离（如 `kb_id`）
4. 为检索链路增加 rerank 或质量评估

### 产品体验

1. ChatView 可加虚拟列表，提升长会话性能
2. 上传队列可进一步演进为 Redis / Celery / RQ 等外部任务系统
3. 可以考虑补 i18n 与更细的错误码映射
