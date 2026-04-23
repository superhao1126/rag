"""全局配置"""

import os


class Config:
    """RAG 系统配置参数"""

    # ---- 阿里云百炼 API ----
    BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_MODEL = "qwen3.6-plus"
    EMBEDDING_MODEL = "text-embedding-v3"
    EMBEDDING_DIMENSION = 1024   # 向量维度（text-embedding-v3 为 1024）

    # ---- 文本分块 ----
    CHUNK_SIZE = 300       # 每个文本块的最大字符数（API文档适合更小的chunk）
    CHUNK_OVERLAP = 60     # 相邻文本块的重叠字符数

    # ---- 检索 ----
    TOP_K = 5              # 检索返回的最相关片段数量（提高覆盖率）
    VECTOR_WEIGHT = 0.6    # 混合检索中向量检索的权重
    BM25_WEIGHT = 0.4      # 混合检索中 BM25 检索的权重（提高关键词权重）
    RRF_K = 60             # RRF（倒数排名融合）常数

    # ---- 存储 ----
    TEMP_DIR = None        # 临时存储目录，None 表示自动创建

    # ---- PostgreSQL 数据库（向量持久化存储）----
    # 设为 True 则使用 PostgreSQL + pgvector 存储向量，False 则使用本地 numpy 文件
    USE_PG = True

    # 完整连接 URI（如设置此项，将忽略下方独立配置）
    # 格式: postgresql://user:password@host:port/dbname
    PG_URI = os.getenv("PG_URI", "")

    PG_HOST = os.getenv("PG_HOST", "101.43.38.7")          # 数据库主机地址
    PG_PORT = int(os.getenv("PG_PORT", "5432"))           # 数据库端口
    PG_DATABASE = os.getenv("PG_DATABASE", "rag_db")            # 数据库名称（请填写）
    PG_USER = os.getenv("PG_USER", "rag_user")                    # 数据库用户名（请填写）
    PG_PASSWORD = os.getenv("PG_PASSWORD", "rag_123456")            # 数据库密码（请填写）

    @property
    def PG_CONFIG(self) -> dict:
        """构建 psycopg2 连接参数字典"""
        return {
            "host": self.PG_HOST,
            "port": self.PG_PORT,
            "dbname": self.PG_DATABASE,
            "user": self.PG_USER,
            "password": self.PG_PASSWORD,
        }

    # ---- OCR（扫描件 PDF / 图片识别）----
    OCR_ENABLED = True              # 是否启用 PaddleOCR（依赖未安装时自动跳过）
    OCR_LANG = "ch"                 # 识别语言：ch / en / japan / korean ...
    OCR_CONF_THRESHOLD = 0.6        # 置信度过滤阈值，低于此值的识别行将被丢弃
    OCR_PDF_RENDER_ZOOM = 2.0       # 扫描件 PDF 栅格化倍率（值越大越清晰但越慢）

    # ---- 文本清洗去噪（reader → cleaner → chunker）----
    # 每一步都可独立开关，方便针对不同文档类型微调
    CLEAN_NORMALIZE_UNICODE  = True   # S1: NFKC 规范化 + HTML 实体替换（全角→半角）
    CLEAN_STRIP_CONTROL      = True   # S2: 去控制字符 / 零宽字符 / BOM
    CLEAN_STRIP_HTML         = True   # S3: 去 HTML <script>/<style>/裸标签
    CLEAN_DROP_LONG_GIBBERISH= True   # S4: 去连续 >=30 位的乱码 token（PDF 图层错位）
    CLEAN_DROP_PAGE_FOOTER   = True   # S5: 去页眉页脚 (第 X 页 / Page X of Y / 单数字行)
    CLEAN_DROP_DUP_LINES     = True   # S5: 去连续重复行（水印 / 装订残留）
    CLEAN_DROP_PUNCT_ONLY    = True   # S5: 去仅由标点 / 符号组成的行
    CLEAN_COLLAPSE_WHITESPACE= True   # S6: 多空格→1 个；多空行→最多 2 行
    CLEAN_CJK_FRIENDLY       = True   # S7: 合并中文之间被 PDF 错误插入的空格
    CLEAN_DUP_LINE_THRESHOLD = 3      # 连续 >= N 行完全相同才视为噪声删除

    # ---- 交互 ----
    STREAM = True          # 是否启用流式输出
    HISTORY_TURNS = 5      # 对话历史保留轮数（0 = 不记忆）
