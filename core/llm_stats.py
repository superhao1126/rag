"""LLM / Embedding 调用 token 统计（线程安全）。

- 累计每个模型的 prompt_tokens / completion_tokens / total_tokens / call 次数
- 按调用类型分桶（chat / title / embedding / 其它）
- 保留最近 N 条调用日志，供"调用流水"页面展示
- 可选挂载持久化后端（duck-typed），重启后从 DB 恢复
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional


class LLMStats:
    def __init__(self, max_log: int = 5000) -> None:
        self._lock = threading.Lock()
        self._models: Dict[str, Dict[str, int]] = {}
        self._calls_by_type: Dict[str, int] = {}
        self._last_call_at: Optional[float] = None
        self._call_log: Deque[Dict] = deque(maxlen=max_log)
        self._next_id: int = 1
        # 持久化后端：需提供 insert_llm_call / aggregate_llm_calls /
        # query_llm_calls / reset_llm_calls
        self._storage: Optional[Any] = None

    def attach_storage(self, storage: Any) -> None:
        """挂载持久化后端并从中恢复聚合数据"""
        self._storage = storage
        try:
            agg = storage.aggregate_llm_calls()
        except Exception as e:
            print(f"  ⚠️ 加载历史 token 统计失败: {e}")
            return
        with self._lock:
            self._models = {k: dict(v) for k, v in (agg.get("models") or {}).items()}
            self._calls_by_type = dict(agg.get("calls_by_type") or {})
            self._last_call_at = agg.get("last_call_at")
        print(
            f"  ✅ 已加载 token 历史统计: 模型 {len(self._models)} 个, "
            f"调用类型 {len(self._calls_by_type)} 类"
        )

    def record(
        self,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: Optional[int] = None,
        call_type: str = "chat",
        note: Optional[str] = None,
    ) -> Dict:
        if total_tokens is None:
            total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        total_tokens = int(total_tokens or 0)

        # 先尝试持久化（拿到 DB 自增 id 与时间戳）
        entry_id: Optional[int] = None
        entry_ts: Optional[float] = None
        if self._storage is not None:
            try:
                row = self._storage.insert_llm_call(
                    model=model,
                    call_type=call_type,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    note=note,
                )
                entry_id = row.get("id")
                entry_ts = row.get("ts")
            except Exception as e:
                print(f"  ⚠️ token 调用持久化失败: {e}")

        with self._lock:
            bucket = self._models.setdefault(
                model,
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0},
            )
            bucket["prompt_tokens"] += prompt_tokens
            bucket["completion_tokens"] += completion_tokens
            bucket["total_tokens"] += total_tokens
            bucket["calls"] += 1
            self._calls_by_type[call_type] = self._calls_by_type.get(call_type, 0) + 1
            if entry_ts is None:
                entry_ts = time.time()
            self._last_call_at = entry_ts
            if entry_id is None:
                entry_id = self._next_id
                self._next_id += 1
            entry = {
                "id": int(entry_id),
                "ts": float(entry_ts),
                "model": model,
                "call_type": call_type,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "note": note,
            }
            self._call_log.appendleft(entry)
            return entry

    def reset(self) -> None:
        if self._storage is not None:
            try:
                self._storage.reset_llm_calls()
            except Exception as e:
                print(f"  ⚠️ token 流水清零失败: {e}")
        with self._lock:
            self._models.clear()
            self._calls_by_type.clear()
            self._last_call_at = None
            self._call_log.clear()
            self._next_id = 1

    def snapshot(self, default_model: Optional[str] = None) -> Dict:
        with self._lock:
            models = {k: dict(v) for k, v in self._models.items()}
            calls_by_type = dict(self._calls_by_type)
            last = self._last_call_at
        totals = {
            "prompt_tokens": sum(m["prompt_tokens"] for m in models.values()),
            "completion_tokens": sum(m["completion_tokens"] for m in models.values()),
            "total_tokens": sum(m["total_tokens"] for m in models.values()),
            "calls": sum(m["calls"] for m in models.values()),
        }
        return {
            "default_model": default_model,
            "totals": totals,
            "models": models,
            "calls_by_type": calls_by_type,
            "last_call_at": last,
            "persisted": self._storage is not None,
        }

    def list_calls(
        self,
        page: int = 1,
        page_size: int = 50,
        call_type: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict:
        # 如有持久化后端 → 走 DB 查询（覆盖全量历史）
        if self._storage is not None:
            try:
                return self._storage.query_llm_calls(
                    page=page, page_size=page_size, call_type=call_type, model=model
                )
            except Exception as e:
                print(f"  ⚠️ token 流水查询失败，降级为内存: {e}")

        page = max(1, int(page or 1))
        page_size = max(1, min(500, int(page_size or 50)))
        with self._lock:
            items: List[Dict] = list(self._call_log)
        if call_type:
            items = [c for c in items if c["call_type"] == call_type]
        if model:
            items = [c for c in items if c["model"] == model]
        total = len(items)
        start = (page - 1) * page_size
        page_items = items[start : start + page_size]
        return {
            "items": page_items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def trend(self, bucket: str = "day", points: int = 30) -> List[Dict]:
        """按天/小时聚合 token 用量趋势"""
        if self._storage is not None:
            try:
                return self._storage.trend_llm_calls(bucket=bucket, points=points)
            except Exception as e:
                print(f"  ⚠️ trend 查询失败，降级为内存: {e}")

        bucket = "hour" if bucket == "hour" else "day"
        points = max(1, min(int(points or 30), 365))
        step = 3600 if bucket == "hour" else 86400
        now = time.time()

        def floor_ts(t: float) -> float:
            if bucket == "hour":
                return t - (t % 3600)
            tm = time.localtime(t)
            day_start = time.mktime((tm.tm_year, tm.tm_mon, tm.tm_mday, 0, 0, 0, 0, 0, -1))
            return day_start

        end_bucket = floor_ts(now)
        buckets_keys = [end_bucket - (points - 1 - i) * step for i in range(points)]
        agg = {
            b: {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "calls": 0,
                "by_type": {},
            }
            for b in buckets_keys
        }
        with self._lock:
            entries = list(self._call_log)
        for e in entries:
            b = floor_ts(float(e["ts"]))
            if b in agg:
                a = agg[b]
                a["prompt_tokens"] += e["prompt_tokens"]
                a["completion_tokens"] += e["completion_tokens"]
                a["total_tokens"] += e["total_tokens"]
                a["calls"] += 1
                ct = e.get("call_type", "chat") or "chat"
                tg = a["by_type"].setdefault(
                    ct, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
                )
                tg["prompt_tokens"] += e["prompt_tokens"]
                tg["completion_tokens"] += e["completion_tokens"]
                tg["total_tokens"] += e["total_tokens"]
                tg["calls"] += 1
        return [
            {
                "bucket": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(b)),
                "ts": b,
                **agg[b],
            }
            for b in buckets_keys
        ]


# 全局单例
llm_stats = LLMStats()
