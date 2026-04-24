from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable, Deque, Dict, Optional


@dataclass
class UploadTaskRecord:
    task_id: str
    filename: str
    file_path: str
    status: str
    enqueued_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None

    def to_public_dict(self) -> dict:
        data = asdict(self)
        data.pop("file_path", None)
        return data


class UploadTaskQueue:
    """进程内上传任务队列。

    目标不是做分布式调度，而是把上传后的重活从 FastAPI 请求线程中剥离，
    用单独 worker 串行消费，避免同步数据库/OCR/向量化阻塞接口线程。
    """

    def __init__(
        self,
        processor: Callable[[str], None],
        logger: Optional[logging.Logger] = None,
        history_limit: int = 200,
    ):
        self._processor = processor
        self._logger = logger or logging.getLogger(__name__)
        self._history_limit = history_limit

        self._queue: queue.Queue[Optional[UploadTaskRecord]] = queue.Queue()
        self._tasks: Dict[str, UploadTaskRecord] = {}
        self._task_order: Deque[str] = deque()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._run,
            name="rag-upload-worker",
            daemon=True,
        )
        self._worker.start()
        self._logger.info("[upload-queue] worker started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._queue.put(None)
        if self._worker:
            self._worker.join(timeout=timeout)
        self._logger.info("[upload-queue] worker stopped")

    def enqueue(self, file_path: str, filename: str) -> dict:
        task = UploadTaskRecord(
            task_id=uuid.uuid4().hex,
            filename=filename,
            file_path=file_path,
            status="queued",
            enqueued_at=time.time(),
        )
        with self._lock:
            self._tasks[task.task_id] = task
            self._task_order.append(task.task_id)
            self._prune_locked()
        self._queue.put(task)
        self._logger.info("[upload-queue] queued: %s (%s)", filename, task.task_id)
        return task.to_public_dict()

    def stats(self) -> dict:
        with self._lock:
            tasks = list(self._tasks.values())
            queued = sum(1 for t in tasks if t.status == "queued")
            running = sum(1 for t in tasks if t.status == "running")
            succeeded = sum(1 for t in tasks if t.status == "succeeded")
            failed = sum(1 for t in tasks if t.status == "failed")
            recent = [
                self._tasks[task_id].to_public_dict()
                for task_id in list(self._task_order)[-10:]
                if task_id in self._tasks
            ]

        return {
            "queued": queued,
            "running": running,
            "succeeded": succeeded,
            "failed": failed,
            "recent": recent,
        }

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            try:
                if task is None:
                    if self._stop_event.is_set():
                        return
                    continue

                self._mark_running(task.task_id)
                self._logger.info("[upload-queue] processing: %s", task.filename)
                self._processor(task.file_path)
                self._mark_done(task.task_id)
                self._logger.info("[upload-queue] done: %s", task.filename)
            except Exception as exc:  # noqa: BLE001
                if task is not None:
                    self._mark_failed(task.task_id, str(exc))
                    self._logger.exception("[upload-queue] failed: %s — %s", task.filename, exc)
            finally:
                self._queue.task_done()

    def _mark_running(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.status = "running"
            task.started_at = time.time()

    def _mark_done(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.status = "succeeded"
            task.finished_at = time.time()
            self._prune_locked()

    def _mark_failed(self, task_id: str, error: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.status = "failed"
            task.error = error
            task.finished_at = time.time()
            self._prune_locked()

    def _prune_locked(self) -> None:
        while len(self._task_order) > self._history_limit:
            oldest_id = self._task_order[0]
            oldest = self._tasks.get(oldest_id)
            if oldest is None:
                self._task_order.popleft()
                continue
            if oldest.status in {"queued", "running"}:
                break
            self._task_order.popleft()
            self._tasks.pop(oldest_id, None)
