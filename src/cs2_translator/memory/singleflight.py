"""单飞（single-flight）辅助：并发相同请求只执行一次。

用于“并发查询同一句子时尽量避免重复 API 请求”：
- 第一个调用者成为 leader，执行实际工作；
- 后续相同 key 的调用者成为 follower，等待并共享 leader 的结果
  （包括失败：不会出现一个失败一个成功的不一致状态）。
"""

from __future__ import annotations

import threading


class _Call:
    __slots__ = ("event", "result", "error")

    def __init__(self):
        self.event = threading.Event()
        self.result = None
        self.error = None


class SingleFlight:
    def __init__(self):
        self._lock = threading.Lock()
        self._calls: dict[str, _Call] = {}

    def run(self, key: str, fn):
        """执行 fn()；相同 key 的并发调用共享同一次执行。

        返回 (result, error)：成功时 error 为 None，失败时 result 为 None。
        """
        with self._lock:
            call = self._calls.get(key)
            if call is not None:
                leader = False
            else:
                call = _Call()
                self._calls[key] = call
                leader = True

        if leader:
            try:
                call.result = fn()
                call.error = None
            except BaseException as exc:  # noqa: BLE001 - 需要原样传递给 follower
                call.result = None
                call.error = exc
            finally:
                with self._lock:
                    self._calls.pop(key, None)
                call.event.set()
            return call.result, call.error

        # follower：等待 leader 完成并共享结果
        call.event.wait()
        return call.result, call.error

    def in_flight(self, key: str) -> bool:
        with self._lock:
            return key in self._calls
