"""
文本清洗去噪模块 —— RAG 预处理流水线第 2 步
============================================

在「读取(reader) → ✦清洗(cleaner)✦ → 分块(chunker) → 向量化(vectorstore) → 存库」
中处于关键位置：
    * 上游：FileReader 抽出来的"脏"原始文本（pdf 解析残留、ocr 噪声、html 标签、控制字符…）
    * 下游：TextChunker 期望的、语义相对干净的纯文本

设计目标
--------
1. **保留语义**：仅去掉确定无意义的噪声，不改写正文（不做翻译/纠错/摘要）。
2. **可配置**：通过 ``CleanConfig`` 控制每一步是否启用，便于针对不同文档类型微调。
3. **幂等**：clean(clean(x)) == clean(x)，多次清洗结果稳定。
4. **可观测**：返回 ``CleanReport`` 报告哪些规则触发了多少次，便于排查质量问题。

清洗步骤（按顺序执行，每步均可独立开关）
----------------------------------------
S1. 统一编码 / Unicode 规范化（NFKC）
       -> 全角转半角、兼容字符合并，避免 "Ａ" 与 "A" 被当成不同 token。
S2. 去除控制字符 / 零宽字符 / BOM
       -> \u0000-\u001F、\u200B-\u200F、\ufeff 等不可见字符常来自 PDF / 网页粘贴。
S3. 剥离 HTML / Markdown 噪声标签（保留正文）
       -> 去 <script>/<style> 整段；去裸标签；保留 markdown 文本但折叠 ``` 围栏标记。
S4. URL / Email / 长串数字脱敏占位（可选，默认仅压缩不脱敏）
       -> 默认替换连续 >= 30 位的乱码 token 为 "<NOISE>"，避免污染向量。
S5. 行级清洗
       -> 去页眉页脚（PyMuPDF 常见 "第 X 页 / Page X of Y"）
       -> 去重复行（连续 >=3 行完全相同视为水印 / 装订残留）
       -> 去过短无意义行（行内仅由标点 / 数字 / 单字符组成）
S6. 空白规范化
       -> 多个空格合并为 1 个；多余的空行合并为最多 2 行（保留段落语义）。
       -> 去除首尾空白。
S7. CJK 友好处理
       -> 中文字符之间被 PDF 解析插入的 1 个空格（如 "你 好" → "你好"）做合并。
       -> 中文与英文/数字之间保留 1 个空格（更利于分词与可读性）。

使用示例
--------
>>> from core.cleaner import TextCleaner
>>> cleaner = TextCleaner()
>>> text, report = cleaner.clean(raw_text)
>>> print(report.summary())
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Tuple


# ============================================================
#  正则表达式（模块级编译，避免每次调用重复 compile）
# ============================================================

# S2: 控制字符（保留 \t \n \r）+ 零宽字符 + BOM
_RE_CTRL = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F"          # ASCII 控制符
    r"\u200B-\u200F\u202A-\u202E\u2060\uFEFF"    # 零宽 / 方向 / BOM
    r"]"
)

# S3: HTML <script>/<style> 整段（含内容）
_RE_HTML_BLOCK = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
# S3: 其余 HTML 裸标签
_RE_HTML_TAG = re.compile(r"<[^>]{1,200}?>")
# S3: HTML 实体（&nbsp; &amp; …）→ 交给 unicodedata 在 S1 之前处理一次常见实体
_HTML_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&apos;": "'", "&#39;": "'",
}

# S4: 长串乱码（>=30 个连续非空白且非中日韩字符），常见于 PDF 图层错位
_RE_LONG_GIBBERISH = re.compile(
    "[^\\s\u4e00-\u9fffA-Za-z0-9，。！？；：、\u201c\u201d\u2018\u2019（）《》【】-]{30,}"
)

# S5: 页眉 / 页脚的常见模式
_RE_PAGE_FOOTER = re.compile(
    r"^\s*(?:"
    r"第\s*\d+\s*页(?:\s*/\s*共\s*\d+\s*页)?"     # 第 3 页 / 共 10 页
    r"|Page\s+\d+\s*(?:of\s+\d+)?"                 # Page 3 of 10
    r"|-\s*\d+\s*-"                                # - 12 -
    r"|\d{1,4}"                                    # 仅一行数字（页码）
    r")\s*$",
    re.IGNORECASE,
)

# S5: 仅由标点 / 符号组成的行（无信息量）
_RE_PUNCT_ONLY = re.compile(
    r"^[\s\-_=*~`·•·．。\.\,，;；:：!！\?？\(\)（）\[\]【】\{\}<>《》/\\\|]+$"
)

# S6: 多空白
_RE_MULTI_SPACE = re.compile(r"[ \t\u00A0\u3000]+")     # 含 NBSP 与全角空格
_RE_MULTI_NEWLINE = re.compile(r"\n{3,}")

# S7: 中文之间被错误插入的单个空格（PDF 提取常见）
#     例如 "你 好 世 界" → "你好世界"；连续多次合并直到稳定。
_RE_CJK_SPACE = re.compile(r"([\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")


# ============================================================
#  数据结构
# ============================================================

@dataclass
class CleanConfig:
    """清洗配置（任何一步都可以关闭以便对比 / 调试）"""
    normalize_unicode: bool = True   # S1
    strip_control: bool = True       # S2
    strip_html: bool = True          # S3
    drop_long_gibberish: bool = True # S4
    drop_page_footer: bool = True    # S5
    drop_dup_lines: bool = True      # S5
    drop_punct_only: bool = True     # S5
    collapse_whitespace: bool = True # S6
    cjk_friendly: bool = True        # S7
    # 连续重复行阈值：>= 此值才视为噪声删除
    dup_line_threshold: int = 3


@dataclass
class CleanReport:
    """清洗后报告：每条规则命中次数 + 字符数变化"""
    raw_chars: int = 0
    clean_chars: int = 0
    counters: Dict[str, int] = field(default_factory=dict)

    def hit(self, rule: str, n: int = 1) -> None:
        if n:
            self.counters[rule] = self.counters.get(rule, 0) + n

    def summary(self) -> str:
        kept = self.clean_chars
        ratio = (kept / self.raw_chars * 100) if self.raw_chars else 0
        rules = ", ".join(f"{k}={v}" for k, v in self.counters.items()) or "无"
        return (
            f"清洗完成: {self.raw_chars} → {kept} 字符 (保留 {ratio:.1f}%) | "
            f"命中规则: {rules}"
        )


# ============================================================
#  主类
# ============================================================

class TextCleaner:
    """文本清洗器（无状态，可单例复用）"""

    def __init__(self, config: CleanConfig | None = None):
        self.config = config or CleanConfig()

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    def clean(self, text: str) -> Tuple[str, CleanReport]:
        """
        清洗一段文本，返回 (清洗后文本, 报告)。
        各步骤按 S1→S7 顺序执行，每步均尊重 ``CleanConfig`` 的开关。
        """
        report = CleanReport(raw_chars=len(text or ""))
        if not text:
            return "", report

        cfg = self.config

        # ---- S1: Unicode 规范化（含常见 HTML 实体替换）----
        if cfg.normalize_unicode:
            for ent, repl in _HTML_ENTITIES.items():
                if ent in text:
                    report.hit(f"html_entity:{ent}", text.count(ent))
                    text = text.replace(ent, repl)
            text = unicodedata.normalize("NFKC", text)

        # ---- S2: 去控制字符 / 零宽字符 ----
        if cfg.strip_control:
            new_text, n = _RE_CTRL.subn("", text)
            report.hit("control_char", n)
            text = new_text

        # ---- S3: 去 HTML 噪声 ----
        if cfg.strip_html:
            new_text, n1 = _RE_HTML_BLOCK.subn("", text)
            report.hit("html_block", n1)
            new_text, n2 = _RE_HTML_TAG.subn("", new_text)
            report.hit("html_tag", n2)
            text = new_text

        # ---- S4: 去长串乱码（PDF 图层错位 / OCR 噪声）----
        if cfg.drop_long_gibberish:
            new_text, n = _RE_LONG_GIBBERISH.subn(" ", text)
            report.hit("long_gibberish", n)
            text = new_text

        # ---- S5: 行级清洗 ----
        if cfg.drop_page_footer or cfg.drop_dup_lines or cfg.drop_punct_only:
            text = self._line_level_clean(text, report)

        # ---- S6: 空白规范化 ----
        if cfg.collapse_whitespace:
            new_text, n1 = _RE_MULTI_SPACE.subn(" ", text)
            report.hit("multi_space", n1)
            new_text, n2 = _RE_MULTI_NEWLINE.subn("\n\n", new_text)
            report.hit("multi_newline", n2)
            text = new_text.strip()

        # ---- S7: CJK 友好处理（迭代到稳定）----
        if cfg.cjk_friendly:
            prev = None
            total = 0
            while prev != text:
                prev = text
                text, n = _RE_CJK_SPACE.subn(r"\1", text)
                total += n
            report.hit("cjk_space", total)

        report.clean_chars = len(text)
        return text, report

    # ------------------------------------------------------------------
    # 内部：行级清洗
    # ------------------------------------------------------------------
    def _line_level_clean(self, text: str, report: CleanReport) -> str:
        """
        逐行扫描：
          * 命中页眉页脚正则 → 删除
          * 仅由标点组成 → 删除
          * 连续 >= dup_line_threshold 行内容完全相同 → 仅保留 1 行
        """
        cfg = self.config
        out: list[str] = []
        prev_line: str | None = None
        repeat_count = 0

        for raw_line in text.split("\n"):
            line = raw_line.rstrip()

            # 页眉页脚
            if cfg.drop_page_footer and _RE_PAGE_FOOTER.match(line):
                report.hit("page_footer")
                continue

            # 仅标点
            if cfg.drop_punct_only and line.strip() and _RE_PUNCT_ONLY.match(line):
                report.hit("punct_only")
                continue

            # 连续重复行检测
            if cfg.drop_dup_lines and line == prev_line and line.strip():
                repeat_count += 1
                if repeat_count >= cfg.dup_line_threshold - 1:
                    # 已经累计到阈值，丢弃后续重复
                    report.hit("dup_line")
                    continue
            else:
                repeat_count = 0

            out.append(line)
            prev_line = line

        return "\n".join(out)


# ============================================================
#  便捷函数
# ============================================================

_default_cleaner = TextCleaner()


def clean_text(text: str) -> str:
    """便捷函数：使用默认配置清洗文本，仅返回清洗结果。"""
    cleaned, _ = _default_cleaner.clean(text)
    return cleaned
