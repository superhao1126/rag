"""SSE 流式问答"""
import asyncio
import json
import traceback

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..deps import get_rag

router = APIRouter(prefix="/api", tags=["query"])


@router.post("/query")
async def query_question(body: dict):
    """SSE 流式问答，支持对话持久化"""
    conversation_id = body.get("conversation_id", "").strip()
    question = body.get("question", "").strip()
    mode = body.get("mode", "hybrid")
    enable_thinking = bool(body.get("enable_thinking", False))
    regenerate = bool(body.get("regenerate", False))

    if not question:
        raise HTTPException(400, "问题不能为空")

    rag = get_rag()

    if rag._use_pg:
        if not conversation_id:
            raise HTTPException(400, "conversation_id 不能为空")
        if not rag.vector_store.conversation_exists(conversation_id):
            raise HTTPException(404, "对话不存在，请先创建对话")

    if rag.get_stats()["total_chunks"] == 0:
        raise HTTPException(400, "知识库为空，请先上传文件")

    async def event_stream():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        DONE = object()

        def producer():
            try:
                for chunk in rag.query_with_conversation(
                    question=question,
                    conversation_id=conversation_id,
                    mode=mode,
                    enable_thinking=enable_thinking,
                    regenerate=regenerate,
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as e:
                traceback.print_exc()
                err = f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
                loop.call_soon_threadsafe(queue.put_nowait, err)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, DONE)

        task = asyncio.create_task(asyncio.to_thread(producer))
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is DONE:
                    break
                yield item
        finally:
            await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
