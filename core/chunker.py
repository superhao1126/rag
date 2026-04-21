"""
文本分块器 —— 将长文本切分为固定大小的片段，
相邻片段保留重叠区域以维持上下文连贯性。
同时提供分块可视化数据（记录每个块在原文中的起止位置）。
支持 API 感知分块：识别接口边界，每个 API 接口作为独立 chunk。
"""

import re
import hashlib
from typing import List, Dict, Tuple

# 匹配 API 接口"硬边界"行（URL 路径或 HTTP 方法，这才是真正的接口分隔）
_API_BOUNDARY_RE = re.compile(
    r"(?:"
    r"(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+[/\w]"   # HTTP 方法 + 路径
    r"|(?:接口路径|接口地址|请求路径|Path|URL)\s*[:：]\s*/"   # 路径标签行
    r"|^/[a-z][a-zA-Z0-9]*/[a-z]"                           # 纯 URL 路径行
    r")",
    re.IGNORECASE,
)


def _is_api_boundary(line: str) -> bool:
    """判断一行是否是 API 接口的起始边界。"""
    return bool(_API_BOUNDARY_RE.search(line))


def _has_api_content(text: str) -> bool:
    """检测文本是否包含 API 接口内容（决定是否启用 API 感知分块）。"""
    api_indicators = [
        r"/[a-z][a-zA-Z0-9]*/[a-z]",          # URL 路径
        r"(?:GET|POST|PUT|DELETE)\s+/",          # HTTP 方法
        r"(?:接口|请求|响应|参数|字段)\s*[:：]", # 中文 API 词汇
        r"application/json",
        r"Authorization",
    ]
    count = sum(1 for p in api_indicators if re.search(p, text, re.IGNORECASE))
    return count >= 2


class TextChunker:
    """文本分块器（支持 API 感知分块）"""

    def __init__(self, chunk_size: int = 300, chunk_overlap: int = 60):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    # ------------------------------------------------------------------
    # 内部：记录段落在原文中的偏移
    # ------------------------------------------------------------------
    def _para_offsets(self, text: str) -> List[Tuple[str, int, int]]:
        offsets = []
        pos = 0
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped:
                real_start = text.find(stripped, pos)
                offsets.append((stripped, real_start, real_start + len(stripped)))
                pos = real_start + len(stripped)
        return offsets

    # ------------------------------------------------------------------
    # API 感知分块：识别接口边界，把每个接口段落聚合成一个 chunk
    # ------------------------------------------------------------------
    def _chunk_api_aware(self, text: str, source: str) -> List[Dict]:
        para_offsets = self._para_offsets(text)
        if not para_offsets:
            return []

        # 找到所有接口硬边界（URL/HTTP方法行）的索引
        boundary_indices = {
            i for i, (para, _, _) in enumerate(para_offsets) if _is_api_boundary(para)
        }

        # 把段落按接口边界分组，边界前的短描述行（接口名称）并入本节
        sections: List[List[Tuple[str, int, int]]] = []
        current_section: List[Tuple[str, int, int]] = []

        for i, item in enumerate(para_offsets):
            if i in boundary_indices and current_section:
                # 检查当前节末尾是否是短描述行（< 30 chars，可能是"接口名称"）
                # 若是，将其归属到新节（向前借一行）
                if len(current_section[-1][0]) < 30:
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

        # 将每个 section 合并为文本，再按 chunk_size 细分（防止超长）
        raw_chunks: List[Tuple[str, int, int]] = []
        for section in sections:
            section_text = "\n".join(p for p, _, _ in section)
            sec_start = section[0][1]
            sec_end = section[-1][2]

            if len(section_text) <= self.chunk_size * 2:
                # 接口段落不超过 2 倍 chunk_size，整体保留
                raw_chunks.append((section_text, sec_start, sec_end))
            else:
                # 超长时按字符滑动窗口切分（保留接口路径行在每个子块中）
                header = section[0][0]  # 第一行（含接口路径）作为前缀
                body = section_text[len(header):]
                step = self.chunk_size - self.chunk_overlap
                for i in range(0, len(body), step):
                    sub_body = body[i: i + self.chunk_size - len(header) - 1]
                    sub_text = (header + "\n" + sub_body).strip()
                    if sub_text:
                        chunk_start = sec_start + len(header) + 1 + i
                        raw_chunks.append((sub_text, chunk_start, chunk_start + len(sub_text)))

        return self._build_result(raw_chunks, source)

    # ------------------------------------------------------------------
    # 通用分块：按字符数切分，段落为基本单位
    # ------------------------------------------------------------------
    def _chunk_generic(self, text: str, source: str) -> List[Dict]:
        para_offsets = self._para_offsets(text)
        raw_chunks: List[Tuple[str, int, int]] = []
        current_chunk = ""
        current_start = 0

        for para, p_start, p_end in para_offsets:
            if len(para) > self.chunk_size:
                if current_chunk:
                    raw_chunks.append((current_chunk, current_start, p_start))
                    current_chunk = ""
                for i in range(0, len(para), self.chunk_size - self.chunk_overlap):
                    sub = para[i: i + self.chunk_size]
                    if sub.strip():
                        raw_chunks.append((sub, p_start + i, p_start + i + len(sub)))
                current_start = p_end

            elif len(current_chunk) + len(para) + 1 > self.chunk_size:
                chunk_end = p_start
                raw_chunks.append((current_chunk, current_start, chunk_end))
                overlap = current_chunk[-self.chunk_overlap:] if len(current_chunk) > self.chunk_overlap else ""
                current_chunk = (overlap + "\n" + para).strip() if overlap else para
                current_start = chunk_end - len(overlap) if overlap else p_start
            else:
                if not current_chunk:
                    current_start = p_start
                current_chunk = (current_chunk + "\n" + para).strip() if current_chunk else para

        if current_chunk.strip():
            raw_chunks.append((current_chunk, current_start, len(text)))

        return self._build_result(raw_chunks, source)

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    def chunk(self, text: str, source: str = "") -> List[Dict]:
        """
        将文本分块，返回带有元数据的块列表。
        自动检测是否为 API 文档，若是则使用接口边界感知分块，否则使用通用分块。
        每个块包含: id, text, source, chunk_index, start, end
        """
        if _has_api_content(text):
            return self._chunk_api_aware(text, source)
        return self._chunk_generic(text, source)

    @staticmethod
    def _build_result(raw_chunks: List[Tuple[str, int, int]], source: str) -> List[Dict]:
        result = []
        for i, (chunk_text, start, end) in enumerate(raw_chunks):
            chunk_id = hashlib.md5(f"{source}_{i}_{chunk_text[:50]}".encode()).hexdigest()
            result.append({
                "id": chunk_id,
                "text": chunk_text,
                "source": source,
                "chunk_index": i,
                "start": start,
                "end": end,
                "char_count": len(chunk_text),
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
            },
        }
