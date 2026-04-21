# 📚 RAG 知识问答系统

基于阿里云百炼大模型（qwen-plus）的 **检索增强生成（RAG）** 知识问答系统。  
支持上传本地文件，自动分块、向量化、BM25 索引，通过混合检索 + 大模型生成高质量回答。  
前端采用 Vue 3 + Ant Design Vue，后端 FastAPI + PostgreSQL + pgvector。

---

## 🏗️ 项目结构

```
rag/
├── core/                    # 核心模块包
│   ├── __init__.py          # 包入口
│   ├── config.py            # 全局配置（模型、分块参数、检索权重、数据库等）
│   ├── reader.py            # 文件读取器（txt / md / pdf，PyMuPDF + PyPDF2 双引擎）
│   ├── chunker.py           # 智能文本分块（API 边界感知 + 滑动窗口）
│   ├── vectorstore.py       # 向量存储 — 本地模式（numpy）
│   ├── pg_vectorstore.py    # 向量存储 — PostgreSQL 模式（pgvector）
│   ├── bm25.py              # BM25 稀疏检索（jieba 中文分词）
│   ├── retriever.py         # 混合检索器（RRF 倒数排名融合 + 查询扩展）
│   └── pipeline.py          # RAG 主流程（串联所有组件，对话管理，SSE 进度）
├── frontend/                # Vue 3 前端
│   ├── src/
│   │   ├── views/
│   │   │   ├── ChatView.vue      # 对话页（流式输出、代码高亮、停止生成）
│   │   │   └── DbFilesView.vue   # 文件管理页（SSE 上传进度、卡片视图）
│   │   ├── api.ts                # API 封装（axios + fetch SSE）
│   │   └── stores/session.ts     # Pinia 状态管理
│   └── dist/                # 构建产物（server.py 静态托管）
├── main.py                  # CLI 命令行入口
├── server.py                # Web API 服务（FastAPI + SSE）
├── init_db.sql              # PostgreSQL 建表参考脚本（程序会自动建表）
├── requirements.txt         # Python 依赖
└── 项目说明.md               # 本文件
```

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 📄 多格式文件读取 | 支持 `.txt`、`.md`、`.pdf`；PDF 使用 PyMuPDF 优先，自动回退 PyPDF2 |
| ✂️ 智能文本分块 | API 边界感知分块（检测 URL / HTTP 方法），普通文档滑动窗口分块 |
| 🧮 向量化存储 | 阿里云 `text-embedding-v3`（1024 维），支持本地 numpy 或 PostgreSQL + pgvector |
| 🔍 混合检索 | BM25 关键词 + 语义向量，RRF 倒数排名融合；支持 URL / camelCase 查询扩展 |
| 🤖 大模型问答 | 阿里云 `qwen-plus`，流式 SSE 输出，多轮对话记忆 |
| 📈 SSE 上传进度 | 文件上传时实时推送进度（读取→分块→向量化→索引），前端显示进度条 |
| ⏹ 停止生成 | 流式输出中可随时中止，保留已接收内容 |
| 🏷️ AI 标题命名 | 第一条消息后用 LLM 自动生成对话标题（≤15 字），实时更新侧边栏 |
| 🔗 检索来源跳转 | 点击检索来源文件名直接跳转文件管理页并高亮对应卡片 |
| 💻 代码高亮 | 使用 highlight.js（atom-one-dark 主题），支持一键复制代码块 |
| 🗄️ 文件管理页 | 卡片式文件列表，支持拖拽上传和删除 |

---

## 🔧 环境要求

- **Python**：3.10+
- **Node.js**：18+（仅需要修改前端时）
- **阿里云百炼 API Key**：[获取地址](https://bailian.console.aliyun.com/)
- **PostgreSQL**（可选）：12+，需安装 [pgvector 扩展](https://github.com/pgvector/pgvector)

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

依赖清单：

| 包 | 用途 |
|----|------|
| `openai` | OpenAI 兼容 API 客户端（阿里云百炼） |
| `numpy` | 本地向量计算 |
| `PyPDF2` | PDF 解析（回退引擎） |
| `pymupdf` | PDF 解析（主引擎，中文 PDF 更可靠） |
| `jieba` | 中文分词（BM25 用） |
| `fastapi` + `uvicorn` | Web 服务框架 |
| `python-multipart` | 文件上传支持 |
| `psycopg2-binary` | PostgreSQL 驱动 |
| `pgvector` | pgvector 扩展 Python 适配器 |

### 2. 配置 API Key

```bash
export DASHSCOPE_API_KEY="你的百炼API Key"
```

### 3. 配置数据库（必须）

编辑 `core/config.py`，填写 PostgreSQL 连接信息：

```python
USE_PG = True
PG_HOST = "localhost"          # 数据库主机
PG_PORT = 5432                 # 端口
PG_DATABASE = "rag_db"         # 数据库名
PG_USER = "your_user"          # 用户名
PG_PASSWORD = "your_password"  # 密码
```

或使用环境变量：

```bash
export PG_HOST=localhost
export PG_PORT=5432
export PG_DATABASE=rag_db
export PG_USER=your_user
export PG_PASSWORD=your_password
# 或用完整 URI
export PG_URI="postgresql://user:password@localhost:5432/rag_db"
```

> 程序启动时会自动创建所需的数据库表和索引（需要有 CREATE TABLE 权限，且数据库中已启用 pgvector 扩展 `CREATE EXTENSION vector`）。也可参考 `init_db.sql` 手动初始化。

### 4. 启动服务

```bash
python server.py
```

启动后访问 **http://localhost:8000** 即可使用 Web 界面。

---

## 💻 使用 PyCharm 启动

### 步骤一：打开项目

1. 打开 PyCharm → **File → Open** → 选择 `rag/` 目录

### 步骤二：配置 Python 解释器

1. **File → Settings → Project: rag → Python Interpreter**
2. 选择 Python 3.10+ 解释器，点击 **OK**

### 步骤三：安装依赖

在 PyCharm Terminal 执行：

```bash
pip install -r requirements.txt
```

### 步骤四：添加运行配置

**Run → Edit Configurations → + → Python**，配置如下：

| 配置项 | 值 |
|--------|----|
| Name | `RAG Web Server` |
| Script path | 选择 `server.py` |
| Environment variables | `DASHSCOPE_API_KEY=你的Key;PG_HOST=localhost;PG_PORT=5432;PG_DATABASE=rag_db;PG_USER=xxx;PG_PASSWORD=xxx` |
| Working directory | 项目根目录（`rag/`） |

点击 **Apply → OK**，然后点击绿色 ▶️ 运行。

---

## 🌐 Web 界面使用说明

### 文件管理页（`/files`）

1. **上传文件**：拖拽或点击上传区，支持 `.txt` `.md` `.pdf`
2. **实时进度**：上传后显示任务卡片，实时展示读取→分块→向量化→索引进度百分比
3. **文件卡片**：显示文件名、文本块数、字符数、上传时间
4. **删除文件**：点击文件卡片右上角 🗑 图标删除文件及其向量数据

### 对话页（`/`）

1. **新建对话**：左侧边栏点击 **+ 新对话**
2. **提问**：输入框输入问题，回车或点击发送；`Shift+Enter` 换行
3. **停止生成**：流式输出中点击红色 **⏹ 停止** 按钮，保留已接收内容
4. **检索模式**：输入框左侧切换 **混合 / 向量 / BM25**
   - **混合**（默认）：向量语义 + BM25 关键词，RRF 融合，效果最佳
   - **向量**：纯语义相似度
   - **BM25**：纯关键词匹配
5. **检索来源**：每条 AI 回复下方可展开检索片段列表，点击文件名跳转文件管理页并高亮对应文件
6. **代码块**：AI 回复中的代码块支持语法高亮 + 一键复制
7. **AI 标题**：第一次提问后，LLM 自动为对话生成简洁标题（≤15 字）

---

## ⚙️ 核心参数说明

编辑 `core/config.py` 调整以下参数：

| 参数 | 当前值 | 说明 |
|------|--------|------|
| `CHUNK_SIZE` | 300 | 文本块最大字符数（API 文档建议 200-400） |
| `CHUNK_OVERLAP` | 60 | 相邻块重叠字符数 |
| `TOP_K` | 5 | 检索返回片段数 |
| `VECTOR_WEIGHT` | 0.6 | 混合检索向量权重 |
| `BM25_WEIGHT` | 0.4 | 混合检索 BM25 权重 |
| `RRF_K` | 60 | RRF 融合常数 |
| `HISTORY_TURNS` | 5 | 对话历史保留轮数（0 = 不记忆） |
| `LLM_MODEL` | qwen-plus | 大语言模型名称 |
| `EMBEDDING_MODEL` | text-embedding-v3 | 向量化模型 |
| `EMBEDDING_DIMENSION` | 1024 | 向量维度 |
| `USE_PG` | True | 是否启用 PostgreSQL |
| `PG_HOST` | localhost | 数据库主机 |
| `PG_PORT` | 5432 | 数据库端口 |
| `PG_DATABASE` | （空） | 数据库名 |
| `PG_USER` | （空） | 数据库用户名 |
| `PG_PASSWORD` | （空） | 数据库密码 |
| `PG_URI` | （空） | 完整连接 URI（优先级高于分项配置） |

---

## 🔑 API 接口一览

启动后访问 **http://localhost:8000/docs** 查看 Swagger 文档。

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/upload` | 上传文件并向量化（SSE 流式进度） |
| `GET` | `/api/files` | 列出知识库所有文件 |
| `DELETE` | `/api/files/{source}` | 删除指定文件及其向量数据 |
| `GET` | `/api/conversations` | 获取所有对话列表 |
| `POST` | `/api/conversations` | 创建新对话 |
| `GET` | `/api/conversations/{id}` | 获取指定对话消息历史 |
| `DELETE` | `/api/conversations/{id}` | 删除对话 |
| `POST` | `/api/query` | 提问（SSE 流式返回，含检索结果和标题） |
| `GET` | `/api/stats` | 获取知识库统计信息 |
| `GET` | `/api/chunks` | 获取分块可视化数据 |

---

## 📁 数据存储

### PostgreSQL 模式（`USE_PG = True`，推荐）

| 表名 | 内容 |
|------|------|
| `rag_chunks` | 文本块 + 向量（pgvector） |
| `rag_bm25_cache` | BM25 索引缓存 |
| `rag_conversations` | 对话记录 |
| `rag_messages` | 消息历史 |

上传的原始文件暂存于临时目录 `/tmp/rag_session_*/uploads/`。

### 本地模式（`USE_PG = False`）

所有数据保存在 `/tmp/rag_session_*/`：`vectors.npy` + `metadata.json` + `bm25_index.json`。

