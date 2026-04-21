"""
文件读取器 —— 支持 txt、md、pdf 格式。
PDF 读取策略（按优先级）：
  1. PyMuPDF (fitz)  — 首选，支持复杂布局、CJK、嵌入字体
  2. PyPDF2          — 备用，当 PyMuPDF 未安装时使用
  3. 扫描版 PDF       — 当两者均提取不到文本时给出提示
"""

import warnings
from pathlib import Path

# 尝试导入 PyMuPDF（安装名：pymupdf，导入名：fitz）
try:
    import fitz as _fitz   # type: ignore
    _HAS_FITZ = True
except ImportError:
    _HAS_FITZ = False

import PyPDF2


class FileReader:
    """从本地文件提取纯文本"""

    SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}

    @staticmethod
    def read(file_path: str) -> str:
        """读取文件并返回纯文本"""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        suffix = path.suffix.lower()
        if suffix in (".txt", ".md"):
            return FileReader._read_text(path)
        elif suffix == ".pdf":
            return FileReader._read_pdf(path)
        else:
            raise ValueError(
                f"不支持的文件格式: {suffix}，仅支持 {FileReader.SUPPORTED_EXTENSIONS}"
            )

    @staticmethod
    def _read_text(path: Path) -> str:
        # 尝试 utf-8，失败则用 gbk（兼容 Windows 中文文本文件）
        for enc in ("utf-8", "gbk", "utf-8-sig"):
            try:
                return path.read_text(encoding=enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _read_pdf(path: Path) -> str:
        if _HAS_FITZ:
            text = FileReader._read_pdf_fitz(path)
            if text.strip():
                return text
            # fitz 没提取到内容，回退到 PyPDF2 再试
            warnings.warn(f"PyMuPDF 提取为空，尝试 PyPDF2: {path.name}")

        text = FileReader._read_pdf_pypdf2(path)
        if text.strip():
            return text

        # 两者均无法提取 —— 很可能是扫描版 PDF（图片化）
        warnings.warn(
            f"PDF 文本提取失败（可能是扫描版/图片 PDF）：{path.name}。"
            "如需处理扫描版，请安装 OCR 工具（如 tesseract + pytesseract）。",
            UserWarning,
            stacklevel=2,
        )
        return ""

    @staticmethod
    def _read_pdf_fitz(path: Path) -> str:
        """使用 PyMuPDF 提取文本（支持中文、复杂布局、嵌入字体）"""
        doc = _fitz.open(str(path))
        parts = []
        for page in doc:
            # "blocks" 模式保留段落结构，比 get_text() 更清晰
            text = page.get_text("text")
            if text.strip():
                parts.append(text.strip())
        doc.close()
        return "\n\n".join(parts)

    @staticmethod
    def _read_pdf_pypdf2(path: Path) -> str:
        """使用 PyPDF2 提取文本（备用）"""
        parts = []
        try:
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        parts.append(text)
        except Exception as e:
            warnings.warn(f"PyPDF2 读取失败: {e}")
        return "\n".join(parts)
