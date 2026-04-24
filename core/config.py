"""全局配置"""

import os
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


_load_dotenv()


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """RAG 系统配置参数"""

    # ---- 阿里云百炼 API ----
    BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_MODEL = "qwen3.6-plus"
    EMBEDDING_MODEL = "text-embedding-v4"
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
    USE_PG = _env_flag("USE_PG", False)

    # 完整连接 URI（如设置此项，将忽略下方独立配置）
    # 格式: postgresql://user:password@host:port/dbname
    PG_URI = os.getenv("PG_URI", "").strip()

    PG_HOST = os.getenv("PG_HOST", "127.0.0.1").strip()   # 数据库主机地址
    PG_PORT = int(os.getenv("PG_PORT", "5432"))           # 数据库端口
    PG_DATABASE = os.getenv("PG_DATABASE", "").strip()    # 数据库名称
    PG_USER = os.getenv("PG_USER", "").strip()            # 数据库用户名
    PG_PASSWORD = os.getenv("PG_PASSWORD", "").strip()    # 数据库密码

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

    @property
    def PG_READY(self) -> bool:
        """是否已提供完整的 PostgreSQL 连接信息"""
        if self.PG_URI:
            return True
        return all([
            self.PG_HOST,
            self.PG_DATABASE,
            self.PG_USER,
            self.PG_PASSWORD,
        ])

    # ---- OCR（扫描件 PDF / 图片识别）----
    OCR_ENABLED = _env_flag("OCR_ENABLED", True)  # 是否启用 PaddleOCR（依赖未安装时自动跳过）
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
