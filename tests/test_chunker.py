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
