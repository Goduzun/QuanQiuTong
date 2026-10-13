"""有界翻译队列：处理期间去重、保序、可取消、可暂停、可跳过过期消息。

架构：日志读取 -> 消息解析 -> 去重 -> 有界队列 -> 翻译工作线程 -> 结果展示。

去重语义：只丢弃“尚未处理完成”的重复消息（防刷屏）；处理完成后
相同消息再次出现时会进入翻译链路，由翻译记忆库决定是否命中缓存。
"""

from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger("cs2_translator.queue_manager")


@dataclass
class QueuedMessage:
    seq: int
    message: object  # ChatMessage
    digest: str = ""  # 去重摘要（提交时计算，处理完成后释放）
    enqueued_at: float = field(default_factory=time.monotonic)


class TranslationQueueManager:
    """有界队列 + N 个工作线程 + 保序输出。

    去重策略：仅在消息“尚未处理完成”期间去重（同一发送者的相同内容
    正在排队或处理中时，后续重复直接丢弃，防止刷屏）；处理完成后
    摘要即释放，之后再次出现的相同消息会正常进入翻译链路——
    此时通常由翻译记忆库直接命中缓存，不会产生 API 请求。
    """

    def __init__(
        self,
        handler,
        max_size: int = 200,
        workers: int = 2,
        skip_expired_seconds: int = 0,
        on_event=None,
        reorder_timeout: float = 3.0,
    ):
        self._handler = handler
        self._max_size = max(1, int(max_size))
        self._workers = max(1, int(workers))
        self._skip_expired = max(0, int(skip_expired_seconds))
        self._on_event = on_event
        self._queue: "queue.Queue[QueuedMessage]" = queue.Queue(maxsize=self._max_size)
        self._threads = []
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 默认未暂停

        self._seq_counter = 0
        self._seq_lock = threading.Lock()
        self._next_emit_seq = 1
        self._reorder_buffer = {}
        self._reorder_deadline = None
        self._reorder_timeout = reorder_timeout

        # 处理期间去重：尚未处理完成的消息摘要
        self._pending_hashes: set[str] = set()

        self._lock = threading.Lock()
        self._stats = {
            "submitted": 0,
            "dropped_full": 0,
            "dropped_duplicate": 0,
            "processed": 0,
            "failed": 0,
            "skipped_expired": 0,
            "cancelled": 0,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _hash_message(message) -> str:
        payload = f"{getattr(message, 'sender', '')}\x00{getattr(message, 'content', '')}"
        return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()

    def stats(self) -> dict:
        with self._lock:
            snapshot = dict(self._stats)
        snapshot["queue_length"] = self._queue.qsize()
        snapshot["max_size"] = self._max_size
        return snapshot

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._threads:
            return
        self._stop_event.clear()
        # 保留当前暂停状态（允许“先暂停再启动”）
        for index in range(self._workers):
            thread = threading.Thread(
                target=self._worker_loop, name=f"translate-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        self._emit_event({"type": "started", "workers": self._workers, "max_size": self._max_size})

    def stop(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        self._pause_event.set()  # 解除暂停以便线程退出
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(timeout=max(0.1, deadline - time.monotonic()))
        self._threads = []
        with self._lock:
            self._pending_hashes.clear()
        self._emit_event({"type": "stopped", "stats": self.stats()})

    # ------------------------------------------------------------------
    def submit(self, message) -> bool:
        """提交消息；队列满或（处理期间的）重复返回 False。"""
        digest = self._hash_message(message)
        with self._lock:
            if digest in self._pending_hashes:
                self._stats["dropped_duplicate"] += 1
                duplicate = True
            else:
                duplicate = False
                self._pending_hashes.add(digest)
        if duplicate:
            self._emit_event({"type": "duplicate", "sender": getattr(message, "sender", "")})
            return False

        with self._seq_lock:
            self._seq_counter += 1
            seq = self._seq_counter

        item = QueuedMessage(seq=seq, message=message, digest=digest)
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._lock:
                self._stats["dropped_full"] += 1
                self._pending_hashes.discard(digest)
            self._emit_event(
                {"type": "dropped", "reason": "queue_full", "sender": getattr(message, "sender", "")}
            )
            return False
        with self._lock:
            self._stats["submitted"] += 1
        return True

    def _release_digest(self, digest: str) -> None:
        """消息处理完成（或取消/跳过），释放去重摘要。"""
        if not digest:
            return
        with self._lock:
            self._pending_hashes.discard(digest)

    def cancel_pending(self) -> int:
        """取消尚未开始处理的任务，返回取消数量。"""
        cancelled = 0
        drained = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            drained.append(item)
            cancelled += 1
            self._queue.task_done()
        for item in drained:
            self._release_digest(item.digest)
        with self._lock:
            self._stats["cancelled"] += cancelled
        # 同时丢弃保序缓冲区中未输出的结果
        with self._seq_lock:
            pending = len(self._reorder_buffer)
            self._reorder_buffer.clear()
            self._reorder_deadline = None
        if cancelled or pending:
            self._emit_event({"type": "cancelled", "count": cancelled + pending})
        return cancelled + pending

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._pause_event.clear()
        else:
            self._pause_event.set()
        self._emit_event({"type": "paused" if paused else "resumed"})

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    # ------------------------------------------------------------------
    def _emit_event(self, event: dict) -> None:
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:
                logger.exception("队列事件回调出错")

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            self._pause_event.wait()
            if self._stop_event.is_set():
                break
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                self._flush_reorder_if_stale()
                continue
            try:
                if self._skip_expired:
                    age = time.monotonic() - item.enqueued_at
                    if age > self._skip_expired:
                        with self._lock:
                            self._stats["skipped_expired"] += 1
                        self._emit_event(
                            {"type": "skipped", "reason": "expired", "seq": item.seq}
                        )
                        continue
                self._handler(item.message, item.seq)
                with self._lock:
                    self._stats["processed"] += 1
            except Exception as exc:  # 单条失败不影响后续消息
                with self._lock:
                    self._stats["failed"] += 1
                self._emit_event({"type": "handler_error", "seq": item.seq, "error": str(exc)})
                logger.exception("翻译处理失败")
            finally:
                self._release_digest(item.digest)
                self._queue.task_done()

    # ------------------------------------------------------------------
    # 保序输出：结果按 seq 排序后交给 handler 的上层回调
    # ------------------------------------------------------------------
    def submit_result(self, seq: int, callback) -> None:
        """由引擎在处理完一条消息后调用，按到达顺序输出结果。"""
        with self._seq_lock:
            self._reorder_buffer[seq] = callback
            if self._reorder_deadline is None:
                self._reorder_deadline = time.monotonic() + self._reorder_timeout
        self._flush_reorder()

    def _flush_reorder(self) -> None:
        with self._seq_lock:
            while self._next_emit_seq in self._reorder_buffer:
                callback = self._reorder_buffer.pop(self._next_emit_seq)
                self._next_emit_seq += 1
                try:
                    callback()
                except Exception:
                    logger.exception("保序输出回调失败")
            if not self._reorder_buffer:
                self._reorder_deadline = None

    def _flush_reorder_if_stale(self) -> None:
        with self._seq_lock:
            if (
                self._reorder_buffer
                and self._reorder_deadline is not None
                and time.monotonic() >= self._reorder_deadline
            ):
                max_seq = max(self._reorder_buffer)
                for seq in sorted(self._reorder_buffer):
                    callback = self._reorder_buffer.pop(seq)
                    try:
                        callback()
                    except Exception:
                        logger.exception("保序输出回调失败(超时刷新)")
                self._next_emit_seq = max(self._next_emit_seq, max_seq + 1)
                self._reorder_deadline = None
