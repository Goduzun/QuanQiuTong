"""对局（match）管理：把「当前局消息 / 历史对局 / 长期翻译记忆」三层数据严格隔离。

设计要点：
- 每局一个唯一 ``match_id``；局内消息写入 ``match_messages`` 表并带 ``match_id``。
- ``translation_memory``（长期翻译记忆）**绝不**因换局而被清空；换局只影响局内消息。
- 「清空本局显示」只清理界面视图（可选同时删除本局记录），与长期记忆完全无关。
- 历史对局保留数量可配置；超出后按最旧优先清理，且**只**删除 ``matches`` /
  ``match_messages`` 中的行，不触碰 ``translation_memory`` / ``translation_terms`` /
  ``translation_templates``。

对局边界识别说明（重要）：
真实 CS2 console.log 中可观察到的边界信号为 ``[HostStateManager] Host activate: <state>``
（``Idle (levelload)`` / ``Loading (<map>)`` / ``Remote Connect (...)`` / ``Quitting``）。
这些信号**只在真实日志中被观察到，尚未在真实「多局对战」场景下验证**，
因此本模块默认以 ``manual``（手动开始新一局）为主，自动识别仅在用户显式开启时启用，
并在界面中如实标注为「未验证」。
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .db import Database

logger = logging.getLogger("cs2_translator.memory.match")

# 对局状态
MATCH_ACTIVE = "active"
MATCH_ENDED = "ended"
MATCH_UNKNOWN = "unknown"

# 对局来源
SOURCE_MANUAL = "manual"
SOURCE_AUTO = "auto"

# 单局消息的默认显示上限
DEFAULT_MATCH_MESSAGE_LIMIT = 300
# 历史对局默认保留局数
DEFAULT_HISTORY_MATCH_LIMIT = 10


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _new_match_id() -> str:
    """生成短小、可读、唯一的对局 ID。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


@dataclass
class MatchInfo:
    """一局的元数据。"""

    match_id: str
    started_at: str
    ended_at: str | None = None
    status: str = MATCH_ACTIVE
    source: str = SOURCE_MANUAL
    message_count: int = 0
    note: str = ""

    @property
    def is_active(self) -> bool:
        return self.status == MATCH_ACTIVE

    @property
    def short_id(self) -> str:
        """界面用的简短标识（时间 + 后 6 位随机）。"""
        return self.match_id

    @property
    def status_label(self) -> str:
        return {
            MATCH_ACTIVE: "进行中",
            MATCH_ENDED: "已结束",
            MATCH_UNKNOWN: "未知",
        }.get(self.status, self.status)

    @property
    def source_label(self) -> str:
        return {
            SOURCE_MANUAL: "手动",
            SOURCE_AUTO: "自动识别",
        }.get(self.source, self.source)


@dataclass
class MatchMessage:
    """局内的一条聊天消息（含译文）。"""

    id: int = 0
    match_id: str = ""
    timestamp: str = ""
    sender: str = ""
    channel: str = ""
    content: str = ""
    translated_text: str = ""
    source_language: str = ""
    hit_kind: str = ""
    status: str = "ok"
    latency_ms: float = 0.0
    created_at: str = ""


# ---------------------------------------------------------------------------
# 对局边界识别（基于真实日志中观察到的 Host activate 信号）
# ---------------------------------------------------------------------------

# 进入「真正开始一局」的候选信号。
# 说明：这些模式来自真实 console.log 观察，但「是否等于新的一局」尚未在真实多局
# 对战下验证，因此默认关闭自动识别；开启后界面会标注为未验证。
_AUTO_START_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("remote_connect", re.compile(r"\[HostStateManager\]\s*Host activate:\s*Remote Connect", re.I)),
    ("loading_map", re.compile(r"\[HostStateManager\]\s*Host activate:\s*Loading\s*\(", re.I)),
]

# 明确结束一局的信号。
_AUTO_END_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("quitting", re.compile(r"\[HostStateManager\]\s*Host activate:\s*Quitting", re.I)),
    ("level_load_idle", re.compile(r"\[HostStateManager\]\s*Host activate:\s*Idle\s*\(levelload\)", re.I)),
]

# 从 "Host activate: Loading (<map>)" 中提取地图名，用于识别重复载入。
_MAP_NAME_RE = re.compile(r"Host activate:\s*Loading\s*\(([^)]+)\)", re.I)


def extract_map_name(line: str) -> str | None:
    """从日志行中提取正在载入的地图名；非载入行返回 None。"""
    match = _MAP_NAME_RE.search(line or "")
    return match.group(1).strip().lower() if match else None


def detect_match_signal(line: str) -> tuple[str, str] | None:
    """检查一行日志是否为对局边界信号。

    返回 ``("start", reason)`` / ``("end", reason)``，非边界返回 ``None``。
    """
    for reason, pattern in _AUTO_START_PATTERNS:
        if pattern.search(line):
            return ("start", reason)
    for reason, pattern in _AUTO_END_PATTERNS:
        if pattern.search(line):
            return ("end", reason)
    return None


class MatchManager:
    """对局生命周期 + 三层数据隔离。

    线程安全：所有公开方法都可从任意线程调用（内部持锁），数据库访问经 Database 串行化。
    """

    def __init__(
        self,
        db: Database,
        *,
        message_limit: int = DEFAULT_MATCH_MESSAGE_LIMIT,
        history_limit: int = DEFAULT_HISTORY_MATCH_LIMIT,
        on_change: Callable[[str], None] | None = None,
    ):
        self.db = db
        self.message_limit = max(10, int(message_limit))
        self.history_limit = max(1, int(history_limit))
        self.on_change = on_change

        self._lock = threading.RLock()
        self._current: MatchInfo | None = None
        # 代际计数：每次换局 +1。用于丢弃上一局的迟到翻译结果。
        self._generation = 0
        self._message_count = 0

        self._current = self._load_or_create_active()

    # ------------------------------------------------------------------
    # 当前对局
    # ------------------------------------------------------------------
    def _load_or_create_active(self) -> MatchInfo:
        row = self.db.query_one(
            "SELECT * FROM matches WHERE status=? ORDER BY started_at DESC LIMIT 1",
            (MATCH_ACTIVE,),
        )
        if row is not None:
            info = _row_to_match(row)
            self._message_count = int(
                self.db.query_one(
                    "SELECT COUNT(*) AS c FROM match_messages WHERE match_id=?",
                    (info.match_id,),
                )["c"]
            )
            return info
        return self._create_match(source=SOURCE_MANUAL, note="启动时自动建立")

    @property
    def current(self) -> MatchInfo | None:
        with self._lock:
            return self._current

    @property
    def current_match_id(self) -> str | None:
        with self._lock:
            return self._current.match_id if self._current else None

    @property
    def generation(self) -> int:
        """当前代际；换局后变化，用于校验迟到的翻译结果。"""
        with self._lock:
            return self._generation

    @property
    def message_count(self) -> int:
        with self._lock:
            return self._message_count

    # ------------------------------------------------------------------
    # 换局
    # ------------------------------------------------------------------
    def _create_match(self, source: str, note: str = "") -> MatchInfo:
        info = MatchInfo(
            match_id=_new_match_id(),
            started_at=_utc_now(),
            status=MATCH_ACTIVE,
            source=source,
            note=note,
        )
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO matches (match_id, started_at, ended_at, status, source,"
                " message_count, note) VALUES (?, ?, NULL, ?, ?, 0, ?)",
                (info.match_id, info.started_at, info.status, info.source, info.note),
            )
        self._message_count = 0
        logger.info("已创建对局 %s (source=%s)", info.match_id, source)
        return info

    def start_new_match(self, source: str = SOURCE_MANUAL, note: str = "") -> MatchInfo:
        """结束当前局并开启新的一局。

        返回新的 ``MatchInfo``。调用方应随后丢弃旧代际的待处理结果。
        """
        with self._lock:
            old = self._current
            if old is not None:
                with self.db.transaction() as conn:
                    conn.execute(
                        "UPDATE matches SET status=?, ended_at=?, message_count=? WHERE match_id=?",
                        (MATCH_ENDED, _utc_now(), self._message_count, old.match_id),
                    )
                logger.info("已结束对局 %s（消息 %d 条）", old.match_id, self._message_count)

            self._generation += 1
            self._current = self._create_match(source=source, note=note)
            self._enforce_history_limit()
            new_info = self._current

        self._notify("started")
        return new_info

    def end_current_match(self) -> None:
        """结束当前局但不创建新局（例如游戏退出）。

        幂等：真实日志里 ``Idle (levelload)`` 会连续出现多次，
        已经结束的对局不应被重复结束（否则会重复写入 ended_at 并重复通知）。
        """
        with self._lock:
            old = self._current
            if old is None or old.status != MATCH_ACTIVE:
                return
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE matches SET status=?, ended_at=?, message_count=? WHERE match_id=?",
                    (MATCH_ENDED, _utc_now(), self._message_count, old.match_id),
                )
            old.status = MATCH_ENDED
            logger.info("已结束对局 %s", old.match_id)
        self._notify("ended")

    # ------------------------------------------------------------------
    # 局内消息
    # ------------------------------------------------------------------
    def add_message(
        self,
        *,
        timestamp: str,
        sender: str,
        channel: str,
        content: str,
        translated_text: str = "",
        source_language: str = "",
        hit_kind: str = "",
        status: str = "ok",
        latency_ms: float = 0.0,
        match_id: str | None = None,
    ) -> int:
        """写入一条局内消息，返回自增 id。"""
        with self._lock:
            target = match_id or (self._current.match_id if self._current else None)
            if target is None:
                return 0
            now = _utc_now()
            with self.db.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO match_messages (match_id, timestamp, sender, channel, content,"
                    " translated_text, source_language, hit_kind, status, latency_ms, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        target,
                        timestamp,
                        sender,
                        channel,
                        content,
                        translated_text,
                        source_language,
                        hit_kind,
                        status,
                        float(latency_ms or 0.0),
                        now,
                    ),
                )
                row_id = int(cur.lastrowid or 0)
                if target == (self._current.match_id if self._current else None):
                    self._message_count += 1
                    conn.execute(
                        "UPDATE matches SET message_count=? WHERE match_id=?",
                        (self._message_count, target),
                    )
            self._trim_match_messages(target)
            return row_id

    def update_message(
        self,
        row_id: int,
        *,
        translated_text: str,
        hit_kind: str = "",
        status: str = "ok",
        latency_ms: float = 0.0,
        source_language: str = "",
    ) -> None:
        """回填某条局内消息的翻译结果。"""
        if not row_id:
            return
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE match_messages SET translated_text=?, hit_kind=?, status=?,"
                    " latency_ms=?, source_language=? WHERE id=?",
                    (
                        translated_text,
                        hit_kind,
                        status,
                        float(latency_ms or 0.0),
                        source_language,
                        int(row_id),
                    ),
                )
        except Exception:
            logger.exception("回填局内消息翻译失败 (id=%s)", row_id)

    def _trim_match_messages(self, match_id: str) -> None:
        """单局消息数超过上限时移除最旧的（数据库层面，防止无限增长）。"""
        try:
            with self.db.transaction() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM match_messages WHERE match_id=?", (match_id,)
                ).fetchone()
                count = int(row["c"]) if row else 0
                if count <= self.message_limit:
                    return
                excess = count - self.message_limit
                conn.execute(
                    "DELETE FROM match_messages WHERE id IN ("
                    " SELECT id FROM match_messages WHERE match_id=? ORDER BY id ASC LIMIT ?)",
                    (match_id, excess),
                )
        except Exception:
            logger.exception("裁剪局内消息失败 (match=%s)", match_id)

    def list_messages(self, match_id: str, limit: int | None = None) -> list[MatchMessage]:
        """按时间正序返回某局的消息。"""
        cap = int(limit or self.message_limit)
        rows = self.db.query_all(
            "SELECT * FROM match_messages WHERE match_id=? ORDER BY id ASC LIMIT ?",
            (match_id, cap),
        )
        return [_row_to_message(r) for r in rows]

    def count_messages(self, match_id: str) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS c FROM match_messages WHERE match_id=?", (match_id,)
        )
        return int(row["c"]) if row else 0

    def clear_current_view(self, *, delete_records: bool = False) -> int:
        """「清空本局显示」。

        ``delete_records=False``（默认）只清理界面视图，数据库记录保留。
        ``delete_records=True`` 同时删除本局消息记录（**不**影响长期翻译记忆）。
        返回被删除的记录数（未删除时为 0）。
        """
        with self._lock:
            match_id = self._current.match_id if self._current else None
        if not delete_records or match_id is None:
            return 0
        with self.db.transaction() as conn:
            removed = conn.execute(
                "DELETE FROM match_messages WHERE match_id=?", (match_id,)
            ).rowcount
            conn.execute("UPDATE matches SET message_count=0 WHERE match_id=?", (match_id,))
        with self._lock:
            self._message_count = 0
        logger.info("已清空对局 %s 的 %d 条记录", match_id, removed)
        self._notify("cleared")
        return int(removed or 0)

    # ------------------------------------------------------------------
    # 啟動重置
    # ------------------------------------------------------------------
    def reset_for_new_session(self) -> dict:
        """程式啟動時重置「對局」資料，僅保留長期翻譯記憶與詞彙表。

        對應需求：每次重新開啟軟體都清空「實時翻譯」與「歷史對局」，
        只保留長期快取與詞彙表。

        刪除：``matches`` 全部、``match_messages`` 全部（含上次殘留的進行中對局）
        保留：``translation_memory`` / ``translation_terms`` / ``translation_templates``
              （這三張表完全不碰）

        回傳實際刪除筆數供介面顯示。
        """
        with self._lock:
            with self.db.transaction() as conn:
                messages = conn.execute("DELETE FROM match_messages").rowcount
                matches = conn.execute("DELETE FROM matches").rowcount
            self._message_count = 0
            self._generation += 1  # 舊代際的待處理結果一律作廢
            self._current = self._create_match(source=SOURCE_MANUAL, note="程式啟動")
        logger.info(
            "啟動重置：已清空對局（%d 局 / %d 條訊息），長期記憶與詞彙表保留",
            matches, messages,
        )
        return {"matches_removed": int(matches or 0), "messages_removed": int(messages or 0)}

    # ------------------------------------------------------------------
    # 历史对局
    # ------------------------------------------------------------------
    def list_matches(self, limit: int = 50, include_active: bool = True) -> list[MatchInfo]:
        sql = "SELECT * FROM matches"
        if not include_active:
            sql += " WHERE status<>'active'"
        sql += " ORDER BY started_at DESC LIMIT ?"
        rows = self.db.query_all(sql, (int(limit),))
        return [_row_to_match(r) for r in rows]

    def count_matches(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS c FROM matches")
        return int(row["c"]) if row else 0

    def count_ended_matches(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS c FROM matches WHERE status<>'active'")
        return int(row["c"]) if row else 0

    def _enforce_history_limit(self) -> int:
        """超出保留上限时清理最旧的历史对局。

        只删除 ``matches`` 与 ``match_messages`` 中的行；
        **绝不**触碰 ``translation_memory`` / ``translation_terms`` / ``translation_templates``。
        """
        try:
            with self.db.transaction() as conn:
                rows = conn.execute(
                    "SELECT match_id FROM matches WHERE status<>'active'"
                    " ORDER BY started_at DESC"
                ).fetchall()
                stale = [r["match_id"] for r in rows[self.history_limit:]]
                if not stale:
                    return 0
                for match_id in stale:
                    conn.execute("DELETE FROM match_messages WHERE match_id=?", (match_id,))
                    conn.execute("DELETE FROM matches WHERE match_id=?", (match_id,))
                logger.info("已清理 %d 个过期历史对局（长期翻译记忆未受影响）", len(stale))
                return len(stale)
        except Exception:
            logger.exception("清理历史对局失败")
            return 0

    def cleanup_history(self, keep: int | None = None) -> int:
        """按保留局数清理历史对局，返回清理局数。"""
        if keep is not None:
            self.history_limit = max(1, int(keep))
        removed = self._enforce_history_limit()
        if removed:
            self._notify("history_cleaned")
        return removed

    def delete_match(self, match_id: str) -> bool:
        """删除单个历史对局（含其消息）；不影响长期翻译记忆。"""
        with self._lock:
            if self._current and match_id == self._current.match_id:
                return False  # 不允许删除进行中的对局
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM match_messages WHERE match_id=?", (match_id,))
            removed = conn.execute("DELETE FROM matches WHERE match_id=?", (match_id,)).rowcount
        return bool(removed)

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    def stats(self) -> dict:
        with self._lock:
            current_id = self._current.match_id if self._current else None
            current_count = self._message_count
        return {
            "current_match_id": current_id,
            "current_message_count": current_count,
            "history_match_count": self.count_ended_matches(),
            "total_match_count": self.count_matches(),
            "message_limit": self.message_limit,
            "history_limit": self.history_limit,
        }

    def _notify(self, event: str) -> None:
        if self.on_change:
            try:
                self.on_change(event)
            except Exception:
                logger.exception("对局变更回调失败")


def _row_to_match(row) -> MatchInfo:
    return MatchInfo(
        match_id=row["match_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        status=row["status"],
        source=row["source"],
        message_count=int(row["message_count"] or 0),
        note=row["note"] or "",
    )


def _row_to_message(row) -> MatchMessage:
    return MatchMessage(
        id=int(row["id"]),
        match_id=row["match_id"],
        timestamp=row["timestamp"] or "",
        sender=row["sender"] or "",
        channel=row["channel"] or "",
        content=row["content"] or "",
        translated_text=row["translated_text"] or "",
        source_language=row["source_language"] or "",
        hit_kind=row["hit_kind"] or "",
        status=row["status"] or "ok",
        latency_ms=float(row["latency_ms"] or 0.0),
        created_at=row["created_at"] or "",
    )
