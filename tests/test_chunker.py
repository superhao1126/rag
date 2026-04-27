from core.chunker import TextChunker


def test_chunker_keeps_api_sections_together():
    text = "\n".join([
        "获取用户",
        "GET /users/profile",
        "请求参数: id",
        "",
        "更新用户",
        "POST /users/update",
        "请求参数: name",
    ])

    chunks = TextChunker(chunk_size=80, chunk_overlap=10).chunk(text, source="api.md")

    assert len(chunks) == 2
    assert chunks[0]["source"] == "api.md"
    assert chunks[0]["chunk_index"] == 0
    assert chunks[0]["text"].startswith("获取用户\nGET /users/profile")
    assert chunks[1]["chunk_index"] == 1
    assert chunks[1]["text"].startswith("更新用户\nPOST /users/update")
    assert chunks[0]["chunk_type"] == "api"


def test_chunker_offsets_are_precise_for_repeated_lines():
    text = "重复段落\n中间内容\n重复段落\n"

    chunks = TextChunker(chunk_size=5, chunk_overlap=0, min_chunk_size=1).chunk(text, source="dup.txt")

    repeated_chunks = [c for c in chunks if c["text"] == "重复段落"]
    assert len(repeated_chunks) == 2
    assert repeated_chunks[0]["start"] == 0
    assert repeated_chunks[1]["start"] == text.rfind("重复段落")


def test_chunker_splits_long_text_on_sentence_boundaries():
    text = "第一句内容比较完整。第二句内容也比较完整。第三句用于测试。"

    chunks = TextChunker(chunk_size=18, chunk_overlap=0, min_chunk_size=1).chunk(text, source="normal.txt")

    assert len(chunks) > 1
    assert all(c["text"].endswith(("。", "！", "？")) for c in chunks)
    assert all(c["chunk_type"] == "normal" for c in chunks)


def test_chunker_merges_tiny_tail_chunks():
    text = "这是一个较长的段落，用来形成第一个文本块。\n短尾"

    chunks = TextChunker(chunk_size=28, chunk_overlap=0, min_chunk_size=10).chunk(text, source="tail.txt")

    assert chunks[-1]["text"].endswith("短尾")
    assert len(chunks[-1]["text"]) >= 10


def test_chunker_detects_markdown_api_headings():
    text = "\n".join([
        "## 获取用户信息",
        "请求路径: /users/profile",
        "请求参数: id",
        "",
        "## 更新用户信息",
        "请求路径: /users/update",
        "请求参数: name",
    ])

    chunks = TextChunker(chunk_size=120, chunk_overlap=0, min_chunk_size=1).chunk(text, source="api.md")

    assert len(chunks) == 2
    assert chunks[0]["text"].startswith("## 获取用户信息")
    assert chunks[1]["text"].startswith("## 更新用户信息")
    assert all(c["chunk_type"] == "api" for c in chunks)


def test_chunker_splits_oversized_api_sections_with_title_context():
    text = "\n".join([
        "## 获取订单详情",
        "GET /orders/detail",
        "请求参数: " + "订单字段说明。" * 20,
        "响应示例: " + "响应字段说明。" * 20,
    ])

    chunks = TextChunker(
        chunk_size=80,
        chunk_overlap=0,
        min_chunk_size=1,
        api_max_chunk_size=120,
    ).chunk(text, source="api.md")

    assert len(chunks) > 1
    assert all(c["text"].startswith("## 获取订单详情") for c in chunks)
    assert all(c["char_count"] <= 140 for c in chunks)
    assert any(c["is_long_split"] for c in chunks)
