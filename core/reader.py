"""
文件读取器 —— 支持 txt、md、pdf、docx、xlsx/xls 格式。
PDF 读取策略（按优先级）：
  1. PyMuPDF (fitz)  — 首选，支持复杂布局、CJK、嵌入字体
  2. PyPDF2          — 备用，当 PyMuPDF 未安装时使用
  3. 扫描版 PDF       — 当两者均提取不到文本时给出提示
Word 读取使用 python-docx；Excel 读取使用 openpyxl（.xlsx）/ xlrd（.xls）。
"""

import json
import time
import warnings
from pathlib import Path

from core.ocr import OCRReader

# 尝试导入 PyMuPDF（安装名：pymupdf，导入名：fitz）
try:
    import fitz as _fitz   # type: ignore
    _HAS_FITZ = True
except ImportError:
    _HAS_FITZ = False

try:
    import numpy as _np  # type: ignore
    _HAS_NP = True
except ImportError:
    _HAS_NP = False

try:
    import cv2 as _cv2  # type: ignore
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

# 触发 OCR 回退的最小有效文本字符数（若文本层提取结果短于此值，视为扫描件）
_PDF_OCR_FALLBACK_MIN_CHARS = 20


def _dump_ocr_output(source_path: Path, kind: str, pages: list[dict], full_text: str) -> None:
    """将 OCR 识别结果落盘为 JSON 文件，方便核查与回放。

    输出目录：项目根目录下的 output/ocr/，文件名带时间戳，避免覆盖。
    """
    try:
        out_dir = Path(__file__).resolve().parent.parent / "output" / "ocr"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe_stem = "".join(c if c.isalnum() or c in "._-" else "_" for c in source_path.stem)
        out_path = out_dir / f"{ts}_{safe_stem}.ocr.json"
        payload = {
            "source": str(source_path),
            "filename": source_path.name,
            "kind": kind,                   # "image" | "scanned_pdf"
            "timestamp": ts,
            "page_count": len(pages),
            "char_count": len(full_text),
            "pages": pages,                 # [{page, text}]
            "text": full_text,
        }
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 同步打印到控制台，便于实时核查
        # print(f"\n===== [OCR] {kind} | {source_path.name} → {out_path} =====")
        # print(json.dumps(payload, ensure_ascii=False, indent=2))
        # print(f"===== [OCR] end ({len(full_text)} chars) =====\n", flush=True)
    except Exception as e:
        warnings.warn(f"OCR 结果落盘失败: {e}", UserWarning)

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

    SUPPORTED_EXTENSIONS = {
        ".txt", ".md", ".pdf", ".docx", ".xlsx", ".xls",
        ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
    }

    @staticmethod
    def read(file_path: str) -> str:
        """读取文件并返回纯文本（自动区分文本类 / 图片类 / 扫描件 PDF）"""
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
        elif suffix in OCRReader.IMAGE_EXTENSIONS:
            return FileReader._read_image(path)
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
        text = ""
        if _HAS_FITZ:
            text = FileReader._read_pdf_fitz(path)
            if len(text.strip()) >= _PDF_OCR_FALLBACK_MIN_CHARS:
                return text
            warnings.warn(f"PyMuPDF 未提取到有效文本，尝试 PyPDF2: {path.name}")

        text2 = FileReader._read_pdf_pypdf2(path)
        if len(text2.strip()) >= _PDF_OCR_FALLBACK_MIN_CHARS:
            return text2

        # 文本层为空 → 视为扫描件 PDF，按页栅格化后 OCR
        if OCRReader.is_available() and _HAS_FITZ:
            warnings.warn(
                f"PDF 文本层为空（疑似扫描件），自动调用 OCR: {path.name}",
                UserWarning,
            )
            return FileReader._ocr_pdf(path)

        warnings.warn(
            f"PDF 文本提取失败（疑似扫描件）：{path.name}。"
            "请安装 OCR 依赖：pip install paddleocr paddlepaddle opencv-python",
            UserWarning,
            stacklevel=2,
        )
        return text or text2

    @staticmethod
    def _ocr_pdf(path: Path) -> str:
        """对扫描版 PDF 逐页栅格化并执行 OCR"""
        if not (_HAS_FITZ and _HAS_NP and _HAS_CV2):
            return ""
        doc = _fitz.open(str(path))
        parts: list[str] = []
        page_records: list[dict] = []
        # 配置驱动的栅格化倍率
        try:
            from core.config import Config
            zoom_v = float(getattr(Config, "OCR_PDF_RENDER_ZOOM", 2.0))
        except Exception:
            zoom_v = 2.0
        zoom = _fitz.Matrix(zoom_v, zoom_v)
        for i, page in enumerate(doc):
            try:
                pix = page.get_pixmap(matrix=zoom, alpha=False)
                arr = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(
                    pix.height, pix.width, pix.n
                )
                if pix.n == 4:
                    img_bgr = _cv2.cvtColor(arr, _cv2.COLOR_RGBA2BGR)
                elif pix.n == 3:
                    img_bgr = _cv2.cvtColor(arr, _cv2.COLOR_RGB2BGR)
                else:
                    img_bgr = _cv2.cvtColor(arr, _cv2.COLOR_GRAY2BGR)
                page_text = OCRReader.recognize_array(img_bgr)
                page_records.append({"page": i + 1, "text": page_text})
                if page_text.strip():
                    parts.append(f"# 第 {i + 1} 页\n{page_text.strip()}")
            except Exception as e:
                warnings.warn(f"OCR 第 {i + 1} 页失败: {e}")
                page_records.append({"page": i + 1, "text": "", "error": str(e)})
        doc.close()
        full_text = "\n\n".join(parts)
        _dump_ocr_output(path, "scanned_pdf", page_records, full_text)
        return full_text

    @staticmethod
    def _read_image(path: Path) -> str:
        """图片文件 → OCR 提取文字"""
        if not OCRReader.is_available():
            raise RuntimeError(
                "OCR 不可用，无法解析图片。请安装：pip install paddleocr paddlepaddle opencv-python"
            )
        text = OCRReader.read_image_file(path)
        if not text.strip():
            warnings.warn(f"图片 OCR 未识别到任何高置信度文本: {path.name}", UserWarning)
        _dump_ocr_output(path, "image", [{"page": 1, "text": text}], text)
        return text

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
