"""聚合所有路由"""
from fastapi import FastAPI

from . import files, conversations, feedback, query, misc


def register_routers(app: FastAPI) -> None:
    app.include_router(files.router)
    app.include_router(conversations.router)
    app.include_router(feedback.router)
    app.include_router(query.router)
    app.include_router(misc.router)
