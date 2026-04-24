import time

from core.task_queue import UploadTaskQueue


def test_upload_task_queue_processes_jobs():
    processed = []

    def processor(file_path: str) -> None:
        processed.append(file_path)

    upload_queue = UploadTaskQueue(processor=processor)
    upload_queue.start()
    try:
        task = upload_queue.enqueue("/tmp/demo.txt", "demo.txt")

        deadline = time.time() + 2
        while time.time() < deadline:
            stats = upload_queue.stats()
            recent = {item["task_id"]: item for item in stats["recent"]}
            current = recent.get(task["task_id"])
            if current and current["status"] == "succeeded":
                break
            time.sleep(0.05)

        stats = upload_queue.stats()
        recent = {item["task_id"]: item for item in stats["recent"]}
        assert recent[task["task_id"]]["status"] == "succeeded"
        assert processed == ["/tmp/demo.txt"]
    finally:
        upload_queue.stop()
