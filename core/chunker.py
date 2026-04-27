"""
文本分块器 —— 面向 RAG 检索的语义分块。

核心策略：
- 普通文档按“段落 > 句子/分号 > 逗号/空格 > 强制字符”优先级寻找断点；
- overlap 以语义单元为单位回补，避免半句话重叠；
- API 文档按接口边界切分，超长接口再做语义二次切分；
- start/end 使用逐行绝对偏移，不使用 text.find，重复文本也能准确定位。
"""

import hashlib
import re
from typing import Dict, List, Tuple


LineOffset = Tuple[str, int, int]
Unit = Dict[str, object]


_HTTP_METHOD_RE = r"(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)"

_API_BOUNDARY_RE = re.compile(
    r"^\s*(?:"
    rf"{_HTTP_METHOD_RE}\s+[/\w{{]"                         # GET /path
    r"|(?:接口路径|接口地址|请求路径|Path|URL)\s*[:：]\s*/"     # 接口路径: /path
    r"|/[a-zA-Z][a-zA-Z0-9_/-]*(?:\s|$)"                    # /api/path
    r")",
    re.IGNORECASE,
)

_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_API_HEADING_HINT_RE = re.compile(
    r"(接口|API|新增|创建|获取|查询|更新|修改|删除|上传|下载|登录|注册|"
    rf"{_HTTP_METHOD_RE}\s+/|/[a-zA-Z][a-zA-Z0-9_/-]*)",
    re.IGNORECASE,
)
_API_SUBSECTION_RE = re.compile(
    r"^\s{0,3}(?:#{2,6}\s*)?"
    r"(?:请求参数|请求头|请求体|响应参数|响应示例|返回参数|错误码|示例|字段说明)"
    r"\s*[:：]?\s*$",
    re.IGNORECASE,
)

_LOW_INFO_LINE_RE = re.compile(r"^\s*[-_=*~`·•]{3,}\s*$")


def _is_api_boundary(line: str) -> bool:
    """判断一行是否是 API 接口的起始边界。"""
    stripped = line.strip()
    if not stripped:
        return False
    if _API_BOUNDARY_RE.search(stripped):
        return True

    heading = _MARKDOWN_HEADING_RE.match(stripped)
    if not heading:
        return False
    title = heading.group(2).strip()
    if _API_SUBSECTION_RE.match(title):
        return False
    return bool(_API_HEADING_HINT_RE.search(title))


def _has_api_content(text: str) -> bool:
    """检测文本是否包含 API 接口内容（决定是否启用 API 感知分块）。"""
    api_indicators = [
        r"/[a-zA-Z][a-zA-Z0-9_/-]*",
        rf"{_HTTP_METHOD_RE}\s+/",
        r"(?:接口|请求|响应|参数|字段|错误码)\s*[:：]",
        r"application/json",
        r"Authorization",
        r"#{1,6}\s+.*(?:接口|API|获取|查询|新增|更新|删除)",
    ]
    count = sum(1 for p in api_indicators if re.search(p, text, re.IGNORECASE))
    return count >= 2


def _is_low_info_line(line: str) -> bool:
    stripped = line.strip()
    return not stripped or bool(_LOW_INFO_LINE_RE.match(stripped))


class TextChunker:
    """文本分块器（普通语义分块 + API 感知分块）"""

    def __init__(
        self,
        chunk_size: int = 350,
        chunk_overlap: int = 80,
        min_chunk_size: int = 120,
        api_max_chunk_size: int = 800,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = min(chunk_overlap, max(0, chunk_size - 1))
        self.min_chunk_size = min_chunk_size
        self.api_max_chunk_size = max(api_max_chunk_size, chunk_size)

    # ------------------------------------------------------------------
    # 内部：记录非空行在原文中的精确偏移
    # ------------------------------------------------------------------
    def _para_offsets(self, text: str) -> List[LineOffset]:
        offsets: List[LineOffset] = []
        cursor = 0

        for raw_line in text.splitlines(keepends=True):
            line = raw_line.rstrip("\r\n")
            if not _is_low_info_line(line):
                left = len(line) - len(line.lstrip())
                right = len(line.rstrip())
                stripped = line[left:right]
                offsets.append((stripped, cursor + left, cursor + right))
            cursor += len(raw_line)

        if text and not text.endswith(("\n", "\r")) and not text.splitlines():
            stripped = text.strip()
            if stripped:
                start = len(text) - len(text.lstrip())
                offsets.append((stripped, start, start + len(stripped)))

        return offsets

    # ------------------------------------------------------------------
    # 语义切割与合并
    # ------------------------------------------------------------------
    def _best_break(self, text: str, limit: int) -> int:
        window = text[:limit]
        min_break = max(1, min(self.min_chunk_size, limit) // 2)

        for pattern in (r"\n{2,}", r"\n", r"[。！？!?；;]", r"[，,]", r"\s+"):
            matches = list(re.finditer(pattern, window))
            for match in reversed(matches):
                idx = match.end()
                if idx >= min_break:
                    return idx
        return limit

    def _split_long_text(self, text: str, start: int, max_size: int) -> List[Unit]:
        units: List[Unit] = []
        local = 0
        length = len(text)

        while local < length:
            remaining = text[local:]
            if len(remaining) <= max_size:
                cut = len(remaining)
            else:
                cut = self._best_break(remaining, max_size)

            raw_part = remaining[:cut]
            leading = len(raw_part) - len(raw_part.lstrip())
            trailing = len(raw_part.rstrip())
            part = raw_part[leading:trailing]
            if part:
                part_start = start + local + leading
                units.append({
                    "text": part,
                    "start": part_start,
                    "end": part_start + len(part),
                    "is_long_split": True,
                })
            local += max(cut, 1)

        return units

    def _semantic_units(self, para_offsets: List[LineOffset], max_size: int) -> List[Unit]:
        units: List[Unit] = []
        for text, start, end in para_offsets:
            if len(text) <= max_size:
                units.append({
                    "text": text,
                    "start": start,
                    "end": end,
                    "is_long_split": False,
                })
            else:
                units.extend(self._split_long_text(text, start, max_size))
        return units

    @staticmethod
    def _join_units(units: List[Unit]) -> str:
        return "\n".join(str(u["text"]) for u in units if str(u["text"]).strip())

    def _tail_overlap_units(self, units: List[Unit]) -> List[Unit]:
        if not units or self.chunk_overlap <= 0:
            return []

        selected: List[Unit] = []
        total = 0
        for unit in reversed(units):
            unit_len = len(str(unit["text"])) + (1 if selected else 0)
            if selected and total + unit_len > self.chunk_overlap:
                break
            if not selected and unit_len > self.chunk_overlap:
                break
            selected.append(unit)
            total += unit_len
        return list(reversed(selected))

    def _units_to_raw_chunks(
        self,
        units: List[Unit],
        source: str,
        chunk_type: str,
        max_size: int,
        title: str = "",
    ) -> List[Dict]:
        raw_chunks: List[Dict] = []
        current: List[Unit] = []

        def flush() -> None:
            if not current:
                return
            text = self._join_units(current)
            if not text.strip():
                return
            raw_chunks.append({
                "text": text,
                "start": int(current[0]["start"]),
                "end": int(current[-1]["end"]),
                "chunk_type": chunk_type,
                "is_long_split": any(bool(u.get("is_long_split")) for u in current),
                "title": title,
            })

        for unit in units:
            candidate = self._join_units(current + [unit])
            if current and len(candidate) > max_size:
                flush()
                overlap = self._tail_overlap_units(current)
                current = overlap if len(self._join_units(overlap + [unit])) <= max_size else []
            current.append(unit)

        flush()
        merged = self._merge_small_chunks(raw_chunks, max_size)

        if chunk_type == "api" and title:
            for chunk in merged:
                if not str(chunk["text"]).startswith(title):
                    chunk["text"] = f"{title}\n{chunk['text']}"
                    chunk["start"] = min(int(chunk["start"]), int(merged[0]["start"]))

        return self._build_result(merged, source)

    def _merge_small_chunks(self, chunks: List[Dict], max_size: int) -> List[Dict]:
        if len(chunks) <= 1:
            return chunks

        merged: List[Dict] = []
        for chunk in chunks:
            if (
                merged
                and len(str(chunk["text"])) < self.min_chunk_size
                and len(str(merged[-1]["text"])) + 1 + len(str(chunk["text"])) <= max_size + self.chunk_overlap
            ):
                merged[-1]["text"] = f"{merged[-1]['text']}\n{chunk['text']}"
                merged[-1]["end"] = chunk["end"]
                merged[-1]["is_long_split"] = bool(merged[-1].get("is_long_split")) or bool(chunk.get("is_long_split"))
            else:
                merged.append(chunk)
        return merged

    # ------------------------------------------------------------------
    # API 感知分块
    # ------------------------------------------------------------------
    def _chunk_api_aware(self, text: str, source: str) -> List[Dict]:
        para_offsets = self._para_offsets(text)
        if not para_offsets:
            return []

        boundary_indices = {
            i for i, (para, _, _) in enumerate(para_offsets) if _is_api_boundary(para)
        }

        sections: List[List[LineOffset]] = []
        current_section: List[LineOffset] = []

        for i, item in enumerate(para_offsets):
            if i in boundary_indices and current_section:
                # Markdown 接口标题后的 GET/请求路径 属于同一接口，不再拆成新 section。
                if _MARKDOWN_HEADING_RE.match(current_section[-1][0]):
                    current_section.append(item)
                    continue
                if (
                    len(current_section[-1][0]) < 30
                    and not _is_api_boundary(current_section[-1][0])
                    and not _MARKDOWN_HEADING_RE.match(item[0])
                ):
                    trailing = current_section.pop()
                    if current_section:
                        sections.append(current_section)
                    current_section = [trailing]
                else:
                    sections.append(current_section)
                    current_section = []
            current_section.append(item)

        if current_section:
            sections.append(current_section)

        raw_chunks: List[Dict] = []
        for section in sections:
            title = section[0][0]
            section_units = self._semantic_units(section, self.api_max_chunk_size)
            raw_chunks.extend(
                self._units_to_raw_chunks(
                    section_units,
                    source=source,
                    chunk_type="api",
                    max_size=self.api_max_chunk_size,
                    title=title,
                )
            )

        for i, chunk in enumerate(raw_chunks):
            chunk["chunk_index"] = i
        return raw_chunks

    # ------------------------------------------------------------------
    # 通用语义分块
    # ------------------------------------------------------------------
    def _chunk_generic(self, text: str, source: str) -> List[Dict]:
        para_offsets = self._para_offsets(text)
        units = self._semantic_units(para_offsets, self.chunk_size)
        return self._units_to_raw_chunks(units, source, "normal", self.chunk_size)

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    def chunk(self, text: str, source: str = "") -> List[Dict]:
        """
        将文本分块，返回带有元数据的块列表。
        每个块包含: id, text, source, chunk_index, start, end, chunk_type, is_long_split
        """
        if _has_api_content(text):
            return self._chunk_api_aware(text, source)
        return self._chunk_generic(text, source)

    @staticmethod
    def _build_result(raw_chunks: List[Dict], source: str) -> List[Dict]:
        result = []
        for i, chunk in enumerate(raw_chunks):
            chunk_text = str(chunk["text"])
            start = int(chunk["start"])
            end = int(chunk["end"])
            fingerprint = f"{source}:{i}:{start}:{end}:{chunk_text[:80]}"
            chunk_id = hashlib.sha256(fingerprint.encode()).hexdigest()
            result.append({
                "id": chunk_id,
                "text": chunk_text,
                "source": source,
                "chunk_index": i,
                "start": start,
                "end": end,
                "char_count": len(chunk_text),
                "chunk_type": chunk.get("chunk_type", "normal"),
                "is_long_split": bool(chunk.get("is_long_split", False)),
                "title": chunk.get("title", ""),
            })
        return result

    def get_visualization_data(self, text: str, chunks: List[Dict]) -> Dict:
        """
        生成分块可视化所需数据。
        返回原始文本、分块边界信息和统计数据。
        """
        total_chars = sum(c["char_count"] for c in chunks)
        overlap_chars = total_chars - len(text) if total_chars > len(text) else 0

        return {
            "original_text": text,
            "original_length": len(text),
            "chunks": [
                {
                    "index": c["chunk_index"],
                    "text": c["text"],
                    "start": c["start"],
                    "end": c["end"],
                    "char_count": c["char_count"],
                    "source": c["source"],
                    "chunk_type": c.get("chunk_type", "normal"),
                    "is_long_split": c.get("is_long_split", False),
                    "title": c.get("title", ""),
                }
                for c in chunks
            ],
            "stats": {
                "total_chunks": len(chunks),
                "avg_chunk_size": round(total_chars / len(chunks), 1) if chunks else 0,
                "max_chunk_size": max((c["char_count"] for c in chunks), default=0),
                "min_chunk_size": min((c["char_count"] for c in chunks), default=0),
                "overlap_chars": overlap_chars,
                "chunk_size_setting": self.chunk_size,
                "overlap_setting": self.chunk_overlap,
                "min_chunk_size_setting": self.min_chunk_size,
                "api_max_chunk_size_setting": self.api_max_chunk_size,
            },
        }
