"""日志监听器测试：覆盖需求 5.2 与测试清单 10、11、12、18。"""

from __future__ import annotations

import os
import time

import pytest

from cs2_translator.log_watcher import LogWatcher


def wait_for(predicate, timeout=3.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def start_watcher(path, **kwargs):
    lines, events = [], []
    watcher = LogWatcher(path, on_line=lines.append, on_event=events.append, **kwargs)
    watcher.start()
    return watcher, lines, events


def stop_watcher(watcher):
    watcher.stop(timeout=3.0)
    assert not watcher.is_alive(), "监听线程应已停止"


class TestIncrementalReading:
    def test_tail_then_append(self, tmp_path):
        log = tmp_path / "console.log"
        log.write_text("[ALL] old: message\n", encoding="utf-8")
        watcher, lines, events = start_watcher(log, poll_interval=0.02)

        def opened():
            return any(e["type"] == "opened" for e in events)

        assert wait_for(opened)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("[ALL] new1: hello\n")
            fh.write("[ALL] new2: world\n")
        assert wait_for(lambda: len(lines) == 2)
        assert lines == ["[ALL] new1: hello", "[ALL] new2: world"]
        stop_watcher(watcher)

    def test_read_from_start(self, tmp_path):
        log = tmp_path / "console.log"
        log.write_text("[ALL] a: 1\n[ALL] b: 2\n", encoding="utf-8")
        watcher, lines, events = start_watcher(log, poll_interval=0.02, read_from_start=True)
        assert wait_for(lambda: len(lines) == 2)
        assert lines == ["[ALL] a: 1", "[ALL] b: 2"]
        stop_watcher(watcher)

    def test_no_reread_of_old_lines(self, tmp_path):
        log = tmp_path / "console.log"
        log.write_text("[ALL] old: message\n", encoding="utf-8")
        watcher, lines, events = start_watcher(log, poll_interval=0.02)
        assert wait_for(lambda: any(e["type"] == "opened" for e in events))
        time.sleep(0.3)  # 多轮轮询
        assert lines == []
        stop_watcher(watcher)


class TestFileLifecycle:
    def test_missing_file_then_created(self, tmp_path):
        log = tmp_path / "console.log"
        watcher, lines, events = start_watcher(log, poll_interval=0.02)
        assert wait_for(lambda: any(e["type"] == "waiting" for e in events))
        log.write_text("[ALL] fresh: hi\n", encoding="utf-8")
        assert wait_for(lambda: lines == ["[ALL] fresh: hi"])
        assert any(e["type"] == "opened" for e in events)
        stop_watcher(watcher)

    def test_truncation(self, tmp_path):
        log = tmp_path / "console.log"
        log.write_text("init\n", encoding="utf-8")
        watcher, lines, events = start_watcher(log, poll_interval=0.02)
        assert wait_for(lambda: any(e["type"] == "opened" for e in events))

        with open(log, "a", encoding="utf-8") as fh:
            fh.write("line1\n")
        assert wait_for(lambda: lines == ["line1"])

        log.write_text("", encoding="utf-8")  # 清空
        assert wait_for(lambda: any(e["type"] == "truncated" for e in events))

        with open(log, "a", encoding="utf-8") as fh:
            fh.write("line2\n")
        assert wait_for(lambda: lines == ["line1", "line2"])
        stop_watcher(watcher)

    def test_delete_and_recreate(self, tmp_path):
        log = tmp_path / "console.log"
        log.write_text("init\n", encoding="utf-8")
        watcher, lines, events = start_watcher(log, poll_interval=0.02)
        assert wait_for(lambda: any(e["type"] == "opened" for e in events))
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("before\n")
        assert wait_for(lambda: lines == ["before"])

        os.remove(log)  # 游戏关闭
        assert wait_for(lambda: any(e["type"] == "missing" for e in events))

        log.write_text("[ALL] after: restart\n", encoding="utf-8")  # 游戏重启
        assert wait_for(lambda: lines == ["before", "[ALL] after: restart"])
        stop_watcher(watcher)


class TestEncodings:
    def test_utf8(self, tmp_path):
        log = tmp_path / "console.log"
        log.write_bytes("init\n".encode("utf-8"))
        watcher, lines, events = start_watcher(log, poll_interval=0.02)
        assert wait_for(lambda: any(e["type"] == "opened" for e in events))
        with open(log, "ab") as fh:
            fh.write("[ALL] Ник: привет\n".encode("utf-8"))
        assert wait_for(lambda: lines == ["[ALL] Ник: привет"])
        stop_watcher(watcher)

    def test_gbk_fallback(self, tmp_path):
        log = tmp_path / "console.log"
        log.write_bytes("init\n".encode("utf-8"))
        watcher, lines, events = start_watcher(log, poll_interval=0.02)
        assert wait_for(lambda: any(e["type"] == "opened" for e in events))
        with open(log, "ab") as fh:
            fh.write("[ALL] 玩家: 你好\n".encode("gbk"))
        assert wait_for(lambda: lines == ["[ALL] 玩家: 你好"])
        stop_watcher(watcher)

    def test_utf8_bom(self, tmp_path):
        log = tmp_path / "console.log"
        log.write_bytes(b"\xef\xbb\xbf[ALL] a: 1\n")
        watcher, lines, events = start_watcher(log, poll_interval=0.02, read_from_start=True)
        assert wait_for(lambda: len(lines) == 1)
        assert lines[0] == "[ALL] a: 1"
        stop_watcher(watcher)

    def test_crlf(self, tmp_path):
        log = tmp_path / "console.log"
        log.write_bytes(b"init\r\n")
        watcher, lines, events = start_watcher(log, poll_interval=0.02)
        assert wait_for(lambda: any(e["type"] == "opened" for e in events))
        with open(log, "ab") as fh:
            fh.write(b"[ALL] a: 1\r\n[ALL] b: 2\r\n")
        assert wait_for(lambda: lines == ["[ALL] a: 1", "[ALL] b: 2"])
        stop_watcher(watcher)

    def test_empty_lines_ignored(self, tmp_path):
        log = tmp_path / "console.log"
        log.write_text("init\n", encoding="utf-8")
        watcher, lines, _ = start_watcher(log, poll_interval=0.02)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("\n\n[ALL] a: 1\n\n")
        assert wait_for(lambda: lines == ["[ALL] a: 1"])
        stop_watcher(watcher)


class TestRobustness:
    def test_overlong_line_dropped(self, tmp_path):
        log = tmp_path / "console.log"
        log.write_text("init\n", encoding="utf-8")
        watcher, lines, events = start_watcher(
            log, poll_interval=0.02, max_line_bytes=100
        )
        assert wait_for(lambda: any(e["type"] == "opened" for e in events))
        with open(log, "ab") as fh:
            fh.write(b"x" * 500)  # 无换行的超长内容
        assert wait_for(lambda: any(e["type"] == "overlong_line" for e in events))
        assert lines == []
        # 之后恢复正常行不受影响
        with open(log, "ab") as fh:
            fh.write(b"\n[ALL] a: ok\n")
        assert wait_for(lambda: lines == ["[ALL] a: ok"])
        stop_watcher(watcher)

    def test_stop_releases_thread(self, tmp_path):
        log = tmp_path / "console.log"
        watcher, _, _ = start_watcher(log, poll_interval=0.02)
        stop_watcher(watcher)

    def test_stats_counting(self, tmp_path):
        log = tmp_path / "console.log"
        log.write_text("init\n", encoding="utf-8")
        watcher, lines, events = start_watcher(log, poll_interval=0.02)

        def opened():
            return any(e["type"] == "opened" for e in events)

        assert wait_for(opened), "watcher 应先完成首次打开再追加"
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("[ALL] a: 1\n\n[ALL] b: 2\n")
        assert wait_for(lambda: len(lines) == 2)
        stop_watcher(watcher)
        assert watcher.stats["lines"] == 2
        assert watcher.stats["empty_lines"] == 1
        assert watcher.stats["bytes_read"] > 0
