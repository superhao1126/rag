"""
OCR 测试脚本
============
独立调用 PaddleOCR 解析指定文件（图片 / 扫描件 PDF / 普通文本类文件），
将识别结果以 JSON 形式落盘到 ./output/ocr/ 并同步打印到控制台。

用法:
    python testOcr.py <file_path> [file_path2 ...]
    python testOcr.py                       # 不带参数则使用下方 DEFAULT_FILES

示例:
    python testOcr.py ./samples/scan.pdf
    python testOcr.py ./samples/a.png ./samples/b.jpg
"""

import json
import sys
import time
from pathlib import Path

# 在这里直接写死要测试的文件路径（不传命令行参数时使用）
DEFAULT_FILES: list[str] = [
    "/Users/zenghao/Downloads/openclaw交互测试/demo_contracts/01_总包合同_星河湾三期_骑缝章.pdf",
    # "/absolute/path/to/your/image.png",
]


def _print_header(title: str) -> None:
    line = "=" * 70
    print(f"\n{line}\n{title}\n{line}")


def run_one(file_path: str) -> int:
    """对单个文件执行 OCR / 文本提取，返回退出码（0 成功）"""
    path = Path(file_path).expanduser().resolve()
    _print_header(f"📄 处理文件: {path}")

    if not path.exists():
        print(f"❌ 文件不存在: {path}")
        return 1

    # 延迟导入，便于看到环境检查信息
    from core.ocr import OCRReader
    from core.reader import FileReader

    suffix = path.suffix.lower()
    is_ocr_target = (
        suffix in OCRReader.IMAGE_EXTENSIONS or suffix == ".pdf"
    )

    if is_ocr_target:
        if OCRReader.is_available():
            print("✅ PaddleOCR 已就绪")
        else:
            print("⚠️  PaddleOCR 不可用：图片将无法识别；扫描件 PDF 也无法回退到 OCR。")
            print("   安装命令: pip install paddleocr paddlepaddle opencv-python")

    t0 = time.time()
    try:
        # FileReader.read 会：
        #  - 文本类文件 → 直接读
        #  - 图片 → OCRReader.read_image_file（落盘 JSON 到 output/ocr/）
        #  - PDF → 先文本层；若为空 → 自动 OCR（落盘 JSON 到 output/ocr/）
        text = FileReader.read(str(path))
    except Exception as e:
        print(f"❌ 解析失败: {e}")
        return 2

    elapsed = time.time() - t0

    # 仅对 OCR 目标手动落盘原始结果（文本类文件不落盘，避免冗余）
    # FileReader 内部对 OCR 路径已经会落盘到 output/ocr/，这里不重复。
    # 但为了"无论什么类型都有 JSON 产物"，这里再统一写一份 result.json。
    out_dir = Path(__file__).resolve().parent / "output" / "ocr"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_stem = "".join(c if c.isalnum() or c in "._-" else "_" for c in path.stem)
    out_path = out_dir / f"{ts}_{safe_stem}.test.json"

    payload = {
        "source": str(path),
        "filename": path.name,
        "suffix": suffix,
        "kind": (
            "image" if suffix in OCRReader.IMAGE_EXTENSIONS
            else "pdf" if suffix == ".pdf"
            else "text"
        ),
        "elapsed_sec": round(elapsed, 3),
        "char_count": len(text),
        "text": text,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _print_header(f"✅ 解析完成 ({len(text)} 字符, 耗时 {elapsed:.2f}s)")
    preview = text[:200]
    suffix_note = "..." if len(text) > 200 else ""
    summary = {**payload, "text": preview + suffix_note}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n💾 完整结果已写入: {out_path}\n", flush=True)
    return 0


def main() -> int:
    files = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_FILES
    if not files:
        print("用法: python testOcr.py <file_path> [file_path2 ...]")
        print("或在脚本顶部 DEFAULT_FILES 中写入要测试的路径。")
        return 1

    fails = 0
    for f in files:
        rc = run_one(f)
        if rc != 0:
            fails += 1
    return 0 if fails == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
