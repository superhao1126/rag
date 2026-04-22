"""
PaddleOCR 包装层
================
- 单例懒加载，避免重复初始化（首次调用约需数秒）
- 图像预处理：灰度 → 去噪 → CLAHE 对比度增强 → 倾斜校正（deskew）
- 置信度过滤：低于阈值的识别结果不进入后续分块/向量化流程
- 兼容多版本 PaddleOCR 返回结构（list / dict）
"""

import warnings
from pathlib import Path
from typing import List, Optional

try:
    import numpy as np  # type: ignore
    _HAS_NP = True
except ImportError:
    _HAS_NP = False

try:
    import cv2  # type: ignore  # opencv-python
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False


_OCR_INSTANCE = None
_OCR_INIT_FAILED = False


def _get_ocr():
    """懒加载 PaddleOCR 单例。失败后不再重试，返回 None。"""
    global _OCR_INSTANCE, _OCR_INIT_FAILED
    if _OCR_INIT_FAILED:
        return None
    if _OCR_INSTANCE is not None:
        return _OCR_INSTANCE
    try:
        from core.config import Config  # 延迟导入避免循环
        if not getattr(Config, "OCR_ENABLED", True):
            _OCR_INIT_FAILED = True
            return None
        lang = getattr(Config, "OCR_LANG", "ch")
    except Exception:
        lang = "ch"
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except ImportError:
        warnings.warn(
            "未安装 paddleocr，扫描件/图片将无法 OCR。"
            "请运行: pip install paddleocr paddlepaddle",
            UserWarning,
        )
        _OCR_INIT_FAILED = True
        return None
    try:
        # PaddleOCR 各版本参数差异：依次尝试旧版→新版→最简
        attempts = [
            {"use_angle_cls": True, "lang": lang, "show_log": False},  # 2.x
            {"use_angle_cls": True, "lang": lang},                      # 2.7
            {"use_textline_orientation": True, "lang": lang},          # 3.x
            {"lang": lang},                                            # 兜底
            {},
        ]
        last_err = None
        for kwargs in attempts:
            try:
                _OCR_INSTANCE = PaddleOCR(**kwargs)
                last_err = None
                break
            except Exception as e:
                last_err = e
                _OCR_INSTANCE = None
                continue
        if _OCR_INSTANCE is None:
            raise last_err if last_err else RuntimeError("PaddleOCR 初始化失败")
    except Exception as e:
        warnings.warn(f"PaddleOCR 初始化失败: {e}", UserWarning)
        _OCR_INIT_FAILED = True
        return None
    return _OCR_INSTANCE


# ---------- 图像预处理 ----------

def _deskew(gray):
    """估计文本倾斜角度并校正（小幅倾斜 < 0.5° 跳过）"""
    try:
        thr = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
        )[1]
        coords = np.column_stack(np.where(thr > 0))
        if len(coords) < 200:
            return gray
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        if abs(angle) < 0.5:
            return gray
        h, w = gray.shape[:2]
        M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        return cv2.warpAffine(
            gray, M, (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
    except Exception:
        return gray


def _preprocess(img_bgr):
    """灰度 + 去噪 + CLAHE + 倾斜校正，返回 BGR 三通道"""
    if not _HAS_CV2 or not _HAS_NP:
        return img_bgr
    if len(img_bgr.shape) == 3:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    else:
        gray = img_bgr
    # 非局部均值去噪
    try:
        denoised = cv2.fastNlMeansDenoising(
            gray, None, h=10, templateWindowSize=7, searchWindowSize=21
        )
    except Exception:
        denoised = gray
    # CLAHE 自适应直方图均衡，增强弱对比度文本
    try:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(denoised)
    except Exception:
        enhanced = denoised
    deskewed = _deskew(enhanced)
    return cv2.cvtColor(deskewed, cv2.COLOR_GRAY2BGR)


# ---------- 结果解析 ----------

def _parse_result(result, conf_threshold: float) -> List[str]:
    """兼容多版本 PaddleOCR 输出，按置信度筛选行文本"""
    if not result:
        return []
    page = result[0]
    lines: List[str] = []

    # 新版 PaddleOCR (>=2.7 / paddlex)：dict 结构
    if isinstance(page, dict):
        texts = page.get("rec_texts") or page.get("texts") or []
        scores = page.get("rec_scores") or page.get("scores") or []
        for t, s in zip(texts, scores):
            try:
                if t and float(s) >= conf_threshold:
                    lines.append(str(t).strip())
            except (TypeError, ValueError):
                continue
        return lines

    # 旧版：[ [box, (text, conf)], ... ]
    if isinstance(page, list):
        for item in page:
            try:
                payload = item[1]
                text, conf = payload[0], payload[1]
                if text and float(conf) >= conf_threshold:
                    lines.append(str(text).strip())
            except Exception:
                continue
    return lines


class OCRReader:
    """统一 OCR 入口：识别图片文件 / 直接识别图像数组"""

    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
    DEFAULT_CONF_THRESHOLD = 0.6

    @classmethod
    def is_available(cls) -> bool:
        return _HAS_CV2 and _HAS_NP and _get_ocr() is not None

    @classmethod
    def read_image_file(cls, path: Path, conf_threshold: Optional[float] = None) -> str:
        if not _HAS_CV2 or not _HAS_NP:
            raise RuntimeError("OCR 依赖未安装，请运行: pip install opencv-python numpy")
        # 支持中文路径：用 numpy 字节流方式读取
        data = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError(f"无法解码图片文件: {path}")
        return cls.recognize_array(img, conf_threshold)

    @classmethod
    def recognize_array(cls, img_bgr, conf_threshold: Optional[float] = None) -> str:
        ocr = _get_ocr()
        if ocr is None:
            return ""
        if conf_threshold is None:
            try:
                from core.config import Config
                conf_threshold = float(getattr(Config, "OCR_CONF_THRESHOLD", cls.DEFAULT_CONF_THRESHOLD))
            except Exception:
                conf_threshold = cls.DEFAULT_CONF_THRESHOLD
        processed = _preprocess(img_bgr)
        result = None
        # 兼容旧版 .ocr(img, cls=True) / .ocr(img) / 新版 .predict(img)
        for fn_name, kwargs in (
            ("ocr", {"cls": True}),
            ("ocr", {}),
            ("predict", {}),
        ):
            fn = getattr(ocr, fn_name, None)
            if fn is None:
                continue
            try:
                result = fn(processed, **kwargs) if kwargs else fn(processed)
                break
            except TypeError:
                continue
            except Exception as e:
                warnings.warn(f"PaddleOCR 识别失败({fn_name}): {e}")
                return ""
        if result is None:
            return ""
        lines = _parse_result(result, conf_threshold)
        return "\n".join(lines)
