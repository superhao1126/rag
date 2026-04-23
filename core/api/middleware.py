"""统一响应格式 {code, data, msg} + 全局异常处理"""
from __future__ import annotations

import json
import traceback

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware


class UnifiedResponseMiddleware(BaseHTTPMiddleware):
    """把 /api/* 的 JSON 响应统一包装为 {code, data, msg}

    - 跳过 SSE / 流式 / 文件下载 / 非 JSON 响应
    - 已是统一结构则不重复包装（幂等）
    - HTTP 状态统一 200，业务码看 code
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        path = request.url.path or ""
        if not path.startswith("/api"):
            return response

        media_type = (response.media_type or "").lower()
        content_type = (response.headers.get("content-type") or "").lower()
        if (
            "text/event-stream" in content_type
            or media_type == "text/event-stream"
            or isinstance(response, StreamingResponse)
        ):
            return response
        if "application/json" not in content_type:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        try:
            payload = json.loads(body.decode("utf-8")) if body else None
        except Exception:
            return response

        if (
            isinstance(payload, dict)
            and "code" in payload
            and "data" in payload
            and "msg" in payload
        ):
            new_payload = payload
        else:
            new_payload = {"code": response.status_code, "data": payload, "msg": "ok"}

        return JSONResponse(
            content=new_payload,
            status_code=200,
            headers={
                k: v
                for k, v in response.headers.items()
                if k.lower() not in ("content-length", "content-type")
            },
            background=getattr(response, "background", None),
        )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        detail = (
            exc.detail
            if isinstance(exc.detail, str)
            else json.dumps(exc.detail, ensure_ascii=False)
        )
        return JSONResponse(
            content={"code": exc.status_code, "data": None, "msg": detail},
            status_code=200,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            content={"code": 422, "data": exc.errors(), "msg": "请求参数校验失败"},
            status_code=200,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        traceback.print_exc()
        return JSONResponse(
            content={"code": 500, "data": None, "msg": f"服务内部错误: {exc}"},
            status_code=200,
        )
