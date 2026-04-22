"""
文件读取器 —— 支持 txt、md、pdf、docx、xlsx/xls 格式。
PDF 读取策略（按优先级）：
  1. PyMuPDF (fitz)  — 首选，支持复杂布局、CJK、嵌入字体
  2. PyPDF2          — 备用，当 PyMuPDF 未安装时使用
  3. 扫描版 PDF       — 当两者均提取不到文本时给出提示
Word 读取使用 python-docx；Excel 读取使用 openpyxl（.xlsx）/ xlrd（.xls）。
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

try:
    import docx as _docx   # type: ignore  # python-docx
    _HAS_DOCX = True
except ImportError:
    _HAS_DOCX = False

try:
    import openpyxl as _openpyxl   # type: ignore
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False

try:
    import xlrd as _xlrd   # type: ignore
    _HAS_XLRD = True
except ImportError:
    _HAS_XLRD = False


class FileReader:
    """从本地文件提取纯文本"""

    SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".xlsx", ".xls"}

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
        elif suffix == ".docx":
            return FileReader._read_docx(path)
        elif suffix in (".xlsx", ".xls"):
            return FileReader._read_excel(path)
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

    @staticmethod
    def _read_docx(path: Path) -> str:
        """使用 python-docx 提取 Word 文档文本（段落 + 表格）"""
        if not _HAS_DOCX:
            raise RuntimeError(
                "未安装 python-docx，无法读取 .docx 文件。请运行: pip install python-docx"
            )
        doc = _docx.Document(str(path))
        parts: list[str] = []

        for para in doc.paragraphs:
            t = para.text.strip()
            if t:
                parts.append(t)

        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                line = " | ".join(c for c in cells if c)
                if line:
                    parts.append(line)

        return "\n\n".join(parts)

    @staticmethod
    def _read_excel(path: Path) -> str:
        """读取 Excel 文件，每个 sheet 转为 Markdown 表格风格的文本"""
        suffix = path.suffix.lower()
        if suffix == ".xlsx":
            if not _HAS_OPENPYXL:
                raise RuntimeError(
                    "未安装 openpyxl，无法读取 .xlsx 文件。请运行: pip install openpyxl"
                )
            return FileReader._read_xlsx(path)
        else:  # .xls
            if not _HAS_XLRD:
                raise RuntimeError(
                    "未安装 xlrd，无法读取 .xls 文件。请运行: pip install xlrd"
                )
            return FileReader._read_xls(path)

    @staticmethod
    def _read_xlsx(path: Path) -> str:
        wb = _openpyxl.load_workbook(str(path), data_only=True, read_only=True)
        parts: list[str] = []
        for sheet in wb.worksheets:
            sheet_lines: list[str] = [f"# 工作表: {sheet.title}"]
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if v is None else str(v).strip() for v in row]
                if any(cells):
                    sheet_lines.append(" | ".join(cells))
            if len(sheet_lines) > 1:
                parts.append("\n".join(sheet_lines))
        wb.close()
        return "\n\n".join(parts)

    @staticmethod
    def _read_xls(path: Path) -> str:
        book = _xlrd.open_workbook(str(path))
        parts: list[str] = []
        for sheet in book.sheets():
            sheet_lines: list[str] = [f"# 工作表: {sheet.name}"]
            for r in range(sheet.nrows):
                row = sheet.row_values(r)
                cells = ["" if v is None else str(v).strip() for v in row]
                if any(cells):
                    sheet_lines.append(" | ".join(cells))
            if len(sheet_lines) > 1:
                parts.append("\n".join(sheet_lines))
        return "\n\n".join(parts)
