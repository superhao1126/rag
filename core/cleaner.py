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
       -> 去 <script>/<style> 整段；保守剥离常见 HTML 裸标签。
       -> fenced code / 表格 / 公式块等结构化片段先保护，避免被标签和空白规则破坏。
S4. URL / Email / 手机号脱敏（可选）+ 长串噪声过滤
       -> 默认过滤长 hash/base64/JWT/OCR 替换字符等噪声，隐私脱敏需显式开启。
S5. 行级清洗
       -> 去页眉页脚（PyMuPDF 常见 "第 X 页 / Page X of Y"）
       -> 去重复行（连续 >=3 行完全相同视为水印 / 装订残留）
       -> 去过短无意义行（行内仅由标点 / 数字 / 单字符组成）
S6. 空白规范化
       -> 多个空格合并为 1 个；多余空行合并为单个换行。
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
import html
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
# S3: 其余 HTML 裸标签。只匹配形如 <tag ...> / </tag> 的片段；
# 是否删除由常见 HTML tag 白名单决定，避免误删 List<T>、<token> 等正文。
_RE_HTML_TAG = re.compile(r"</?([A-Za-z][A-Za-z0-9:-]*)(?:\s+[^<>]{0,180})?\s*/?>")
_HTML_TAG_NAMES = {
    "a", "abbr", "address", "area", "article", "aside", "audio",
    "b", "base", "bdi", "bdo", "blockquote", "body", "br", "button",
    "canvas", "caption", "cite", "code", "col", "colgroup",
    "data", "datalist", "dd", "del", "details", "dfn", "dialog", "div",
    "dl", "dt", "em", "embed",
    "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hr", "html",
    "i", "iframe", "img", "input", "ins",
    "kbd", "label", "legend", "li", "link", "main", "map", "mark", "meta",
    "meter", "nav", "noscript", "object", "ol", "optgroup", "option",
    "output", "p", "param", "picture", "pre", "progress", "q",
    "rp", "rt", "ruby", "s", "samp", "section", "select", "slot", "small",
    "source", "span", "strong", "sub", "summary", "sup", "svg",
    "table", "tbody", "td", "template", "textarea", "tfoot", "th",
    "thead", "time", "title", "tr", "track", "u", "ul", "var", "video",
    "wbr",
}
# S1: HTML 实体（&nbsp; &#32; &#x20; …）由 html.unescape 统一处理。
_RE_HTML_ENTITY = re.compile(r"&(?:#[0-9]+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);")

# Markdown / 结构化内容保护
_RE_FENCED_BLOCK = re.compile(
    r"(?ms)^(?:```|~~~)[^\n]*\n.*?^(?:```|~~~)\s*$"
)
_RE_MATH_BLOCK = re.compile(r"(?ms)^\$\$\s*\n.*?^\$\$\s*$")
_RE_TABLE_SEPARATOR = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_PROTECTED_TOKEN_TEMPLATE = "__CLEAN_PROTECTED_BLOCK_{index}__"

# S4: 长串乱码（>=30 个连续非空白且非中日韩字符），常见于 PDF 图层错位
_RE_LONG_GIBBERISH = re.compile(
    "[^\\s\u4e00-\u9fffA-Za-z0-9，。！？；：、\u201c\u201d\u2018\u2019（）《》【】-]{30,}"
)
_RE_JWT_TOKEN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{10,})?\b"
)
_RE_LONG_QUERY_VALUE = re.compile(
    r"([?&][A-Za-z0-9_.~-]+=)[A-Za-z0-9%+/_=-]{48,}"
)
_RE_REPLACEMENT_NOISE = re.compile(r"\uFFFD{3,}")

# S5: 页眉 / 页脚的常见模式
_RE_PAGE_FOOTER = re.compile(
    r"^\s*(?:"
    r"第\s*\d+\s*页(?:\s*/\s*共\s*\d+\s*页)?"     # 第 3 页 / 共 10 页
    r"|Page\s+\d+\s*(?:of\s+\d+)?"                 # Page 3 of 10
    r"|-\s*\d+\s*-"                                # - 12 -
    r")\s*$",
    re.IGNORECASE,
)

# S5: 仅由标点 / 符号组成的行（无信息量）
_RE_PUNCT_ONLY = re.compile(
    r"^[\s\-_=*~`·•·．。\.\,，;；:：!！\?？\(\)（）\[\]【】\{\}<>《》/\\\|]+$"
)

# S6: 多空白
_RE_MULTI_SPACE = re.compile(r"[ \t\u00A0\u3000]+")     # 含 NBSP 与全角空格
_RE_MULTI_NEWLINE = re.compile(r"\n{2,}")

# S7: 中文之间被错误插入的单个空格（PDF 提取常见）
#     例如 "你 好 世 界" → "你好世界"；连续多次合并直到稳定。
_RE_CJK_SPACE = re.compile(r"([\u4e00-\u9fff])[ \t\u00A0\u3000]+(?=[\u4e00-\u9fff])")
_RE_CJK_ALNUM_LEFT = re.compile(r"([\u4e00-\u9fff])([A-Za-z0-9])")
_RE_CJK_ALNUM_RIGHT = re.compile(r"([A-Za-z0-9])([\u4e00-\u9fff])")
_CJK_SPACE_MAX_ITERATIONS = 20

# 隐私脱敏（默认关闭）
_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_RE_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>()，,。；;、]+")
_RE_CN_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")


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
    protect_structured: bool = True  # 保护 Markdown 表格/代码/公式块
    normalize_cjk_alnum_spacing: bool = True
    drop_noise_tokens: bool = True
    redact_privacy: bool = False
    long_token_threshold: int = 48
    # 连续重复行阈值：>= 此值才视为噪声删除
    dup_line_threshold: int = 3


@dataclass
class CleanReport:
    """清洗后报告：每条规则命中次数 + 字符数变化"""
    raw_chars: int = 0
    clean_chars: int = 0
    counters: Dict[str, int] = field(default_factory=dict)
    removed_lines: int = 0
    removed_chars: int = 0
    warnings: list[str] = field(default_factory=list)

    def hit(self, rule: str, n: int = 1) -> None:
        if n:
            self.counters[rule] = self.counters.get(rule, 0) + n

    def remove_line(self, rule: str, line: str) -> None:
        self.hit(rule)
        self.removed_lines += 1
        self.removed_chars += len(line)

    def remove_lines(self, rule: str, lines: list[str]) -> None:
        if not lines:
            return
        self.hit(rule, len(lines))
        self.removed_lines += len(lines)
        self.removed_chars += sum(len(line) for line in lines)

    def warn(self, warning: str) -> None:
        if warning and warning not in self.warnings:
            self.warnings.append(warning)

    def summary(self) -> str:
        kept = self.clean_chars
        ratio = (kept / self.raw_chars * 100) if self.raw_chars else 0
        rules = ", ".join(f"{k}={v}" for k, v in self.counters.items()) or "无"
        extra = ""
        if self.removed_lines or self.removed_chars:
            extra += f" | 删除 {self.removed_lines} 行 / {self.removed_chars} 字符"
        if self.warnings:
            extra += f" | 风险提示: {', '.join(self.warnings)}"
        return (
            f"清洗完成: {self.raw_chars} → {kept} 字符 (保留 {ratio:.1f}%) | "
            f"命中规则: {rules}{extra}"
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
    def clean(self, text: str | None) -> Tuple[str, CleanReport]:
        """
        清洗一段文本，返回 (清洗后文本, 报告)。
        各步骤按 S1→S7 顺序执行，每步均尊重 ``CleanConfig`` 的开关。
        """
        if not isinstance(text, str):
            if text is None:
                return "", CleanReport(raw_chars=0, clean_chars=0)
            raise TypeError("TextCleaner.clean() expects str or None")
        report = CleanReport(raw_chars=len(text))
        if not text:
            return "", report

        cfg = self.config
        protected_blocks: list[tuple[str, str]] = []

        # ---- S1: Unicode 规范化（含常见 HTML 实体替换）----
        if cfg.normalize_unicode:
            entity_matches = _RE_HTML_ENTITY.findall(text)
            if entity_matches:
                report.hit("html_entity", len(entity_matches))
                for ent in entity_matches:
                    report.hit(f"html_entity:{ent}")
                text = html.unescape(text)
            text = unicodedata.normalize("NFKC", text)

        # ---- S2: 去控制字符 / 零宽字符 ----
        if cfg.strip_control:
            new_text, n = _RE_CTRL.subn("", text)
            report.hit("control_char", n)
            text = new_text

        if cfg.redact_privacy:
            text = self._redact_privacy(text, report)

        # ---- 结构化片段保护：避免后续 HTML / 行级 / 空白规则破坏代码、表格、公式 ----
        if cfg.protect_structured:
            text, protected_blocks = self._protect_structured_blocks(text)
            report.hit("protected_block", len(protected_blocks))

        # ---- S3: 去 HTML 噪声 ----
        if cfg.strip_html:
            new_text, n1 = _RE_HTML_BLOCK.subn("", text)
            report.hit("html_block", n1)
            new_text, n2 = self._strip_known_html_tags(new_text)
            report.hit("html_tag", n2)
            text = new_text

        # ---- S4: 去长串乱码（PDF 图层错位 / OCR 噪声）----
        if cfg.drop_long_gibberish:
            new_text, n = _RE_LONG_GIBBERISH.subn(" ", text)
            report.hit("long_gibberish", n)
            text = new_text

        if cfg.drop_noise_tokens:
            text = self._drop_noise_tokens(text, report, cfg.long_token_threshold)

        # ---- S5: 行级清洗 ----
        if cfg.drop_page_footer or cfg.drop_dup_lines or cfg.drop_punct_only:
            text = self._line_level_clean(text, report)

        # ---- S6: 空白规范化 ----
        if cfg.collapse_whitespace:
            new_text, n1 = _RE_MULTI_SPACE.subn(" ", text)
            report.hit("multi_space", n1)
            new_text, n2 = _RE_MULTI_NEWLINE.subn("\n", new_text)
            report.hit("multi_newline", n2)
            text = new_text.strip()

        # ---- S7: CJK 友好处理（迭代到稳定）----
        if cfg.cjk_friendly:
            total = 0
            for _ in range(_CJK_SPACE_MAX_ITERATIONS):
                text, n = _RE_CJK_SPACE.subn(r"\1", text)
                if not n:
                    break
                total += n
            else:
                report.warn("cjk_space_iteration_limit")
            report.hit("cjk_space", total)

        if cfg.normalize_cjk_alnum_spacing:
            text, n1 = _RE_CJK_ALNUM_LEFT.subn(r"\1 \2", text)
            text, n2 = _RE_CJK_ALNUM_RIGHT.subn(r"\1 \2", text)
            report.hit("cjk_alnum_space", n1 + n2)

        if protected_blocks:
            text = self._restore_protected_blocks(text, protected_blocks)

        report.clean_chars = len(text)
        self._add_risk_warnings(report)
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
        run_lines: list[str] = []
        run_key: str | None = None
        dup_threshold = max(2, cfg.dup_line_threshold)

        def flush_run() -> None:
            nonlocal run_lines, run_key
            if not run_lines:
                return
            if cfg.drop_dup_lines and run_key and len(run_lines) >= dup_threshold:
                out.append(run_lines[0])
                report.remove_lines("dup_line", run_lines[1:])
            else:
                out.extend(run_lines)
            run_lines = []
            run_key = None

        for raw_line in text.split("\n"):
            line = raw_line.rstrip()

            # 页眉页脚
            if cfg.drop_page_footer and _RE_PAGE_FOOTER.match(line):
                flush_run()
                report.remove_line("page_footer", line)
                continue

            # 仅标点
            if cfg.drop_punct_only and line.strip() and _RE_PUNCT_ONLY.match(line):
                flush_run()
                report.remove_line("punct_only", line)
                continue

            # 连续重复行检测
            line_key = line.strip()
            if not line_key:
                flush_run()
                out.append(line)
            elif cfg.drop_dup_lines and line_key == run_key:
                run_lines.append(line)
            else:
                flush_run()
                run_key = line_key
                run_lines = [line]

        flush_run()

        return "\n".join(out)

    @staticmethod
    def _strip_known_html_tags(text: str) -> tuple[str, int]:
        count = 0

        def repl(match: re.Match) -> str:
            nonlocal count
            tag_name = match.group(1).lower()
            if tag_name in _HTML_TAG_NAMES:
                count += 1
                return ""
            return match.group(0)

        return _RE_HTML_TAG.sub(repl, text), count

    @staticmethod
    def _redact_privacy(text: str, report: CleanReport) -> str:
        text, n = _RE_EMAIL.subn("<EMAIL>", text)
        report.hit("privacy_email", n)
        text, n = _RE_URL.subn("<URL>", text)
        report.hit("privacy_url", n)
        text, n = _RE_CN_PHONE.subn("<PHONE>", text)
        report.hit("privacy_phone", n)
        return text

    @staticmethod
    def _drop_noise_tokens(text: str, report: CleanReport, long_token_threshold: int) -> str:
        threshold = max(24, long_token_threshold)

        text, n = _RE_JWT_TOKEN.subn(" ", text)
        report.hit("jwt_token", n)

        text, n = _RE_LONG_QUERY_VALUE.subn(r"\1<NOISE>", text)
        report.hit("long_query_value", n)

        long_token_re = re.compile(
            rf"(?<![A-Za-z0-9_+/=-])[A-Za-z0-9_+/=-]{{{threshold},}}(?![A-Za-z0-9_+/=-])"
        )
        text, n = long_token_re.subn(" ", text)
        report.hit("long_noise_token", n)

        text, n = _RE_REPLACEMENT_NOISE.subn(" ", text)
        report.hit("replacement_noise", n)

        return text

    @staticmethod
    def _protect_structured_blocks(text: str) -> tuple[str, list[tuple[str, str]]]:
        protected: list[tuple[str, str]] = []

        def stash(block: str) -> str:
            token = _PROTECTED_TOKEN_TEMPLATE.format(index=len(protected))
            protected.append((token, block))
            return token

        for pattern in (_RE_FENCED_BLOCK, _RE_MATH_BLOCK):
            text = pattern.sub(lambda match: stash(match.group(0)), text)

        text = TextCleaner._protect_line_structures(text, stash)
        return text, protected

    @staticmethod
    def _protect_line_structures(text: str, stash) -> str:
        lines = text.split("\n")
        out: list[str] = []
        i = 0

        while i < len(lines):
            line = lines[i]
            next_line = lines[i + 1] if i + 1 < len(lines) else ""

            if "|" in line and _RE_TABLE_SEPARATOR.match(next_line):
                start = i
                end = i + 2
                while end < len(lines) and "|" in lines[end] and lines[end].strip():
                    end += 1
                out.append(stash("\n".join(lines[start:end])))
                i = end
                continue

            if line.startswith("    ") or line.startswith("\t"):
                start = i
                end = i + 1
                while end < len(lines):
                    candidate = lines[end]
                    if candidate.startswith("    ") or candidate.startswith("\t"):
                        end += 1
                        continue
                    break
                out.append(stash("\n".join(lines[start:end])))
                i = end
                continue

            out.append(line)
            i += 1

        return "\n".join(out)

    @staticmethod
    def _restore_protected_blocks(text: str, protected: list[tuple[str, str]]) -> str:
        for token, block in protected:
            text = text.replace(token, block)
        return text

    @staticmethod
    def _add_risk_warnings(report: CleanReport) -> None:
        if not report.raw_chars:
            return
        retention = report.clean_chars / report.raw_chars
        if retention < 0.3:
            report.warn("low_retention")
        if report.removed_lines >= 20:
            report.warn("many_deleted_lines")
        if report.counters.get("html_tag", 0) >= 50:
            report.warn("many_html_tags")
        if (
            report.counters.get("long_noise_token", 0)
            + report.counters.get("jwt_token", 0)
            + report.counters.get("replacement_noise", 0)
        ) >= 20:
            report.warn("many_noise_tokens")


# ============================================================
#  便捷函数
# ============================================================

_default_cleaner = TextCleaner()


def clean_text(text: str) -> str:
    """便捷函数：使用默认配置清洗文本，仅返回清洗结果。"""
    cleaned, _ = _default_cleaner.clean(text)
    return cleaned
