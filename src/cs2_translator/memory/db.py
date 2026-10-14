"""SQLite 数据库层：连接管理、WAL、迁移机制。

设计要点：
- 单连接 + RLock 串行化所有访问（GUI 线程、翻译工作线程、回复线程共用）；
- WAL 模式 + busy_timeout，降低并发写入损坏风险；
- 所有写操作走 transaction() 上下文管理器，保证原子性；
- 迁移按顺序执行并记录版本，可重复执行（幂等）。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("cs2_translator.memory.db")

SCHEMA_VERSION = 2

_MIGRATIONS: list[tuple[str, list[str]]] = [
    (
        "1_initial",
        [
            """
            CREATE TABLE IF NOT EXISTS translation_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_hash TEXT NOT NULL,
                normalized_source TEXT NOT NULL,
                original_source TEXT NOT NULL,
                source_language TEXT NOT NULL DEFAULT '',
                target_language TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                cache_version TEXT NOT NULL DEFAULT '1',
                direction TEXT NOT NULL DEFAULT 'incoming',
                source_type TEXT NOT NULL DEFAULT 'chat',
                confirmed INTEGER NOT NULL DEFAULT 0,
                hit_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_key
            ON translation_memory (source_hash, target_language, direction, cache_version)
            """,
            "CREATE INDEX IF NOT EXISTS idx_memory_updated ON translation_memory (updated_at)",
            """
            CREATE TABLE IF NOT EXISTS translation_terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_term TEXT NOT NULL,
                target_language TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'incoming',
                translated_term TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                confirmed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_terms_key
            ON translation_terms (source_term, target_language, direction)
            """,
            """
            CREATE TABLE IF NOT EXISTS translation_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_pattern TEXT NOT NULL,
                target_language TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'incoming',
                translated_pattern TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                confirmed INTEGER NOT NULL DEFAULT 0,
                usage_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_templates_key
            ON translation_templates (source_pattern, target_language, direction)
            """,
            """
            CREATE TABLE IF NOT EXISTS translation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_text TEXT NOT NULL,
                source_language TEXT NOT NULL DEFAULT '',
                target_language TEXT NOT NULL,
                translated_text TEXT NOT NULL DEFAULT '',
                direction TEXT NOT NULL DEFAULT 'incoming',
                source_type TEXT NOT NULL DEFAULT 'chat',
                hit_kind TEXT NOT NULL DEFAULT '',
                cache_hit INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok',
                latency_ms REAL NOT NULL DEFAULT 0,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_history_created ON translation_history (created_at)",
            """
            CREATE TABLE IF NOT EXISTS memory_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                old_text TEXT NOT NULL,
                new_text TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_revisions_memory ON memory_revisions (memory_id)",
            """
            CREATE TABLE IF NOT EXISTS translation_stats (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                exact_hits INTEGER NOT NULL DEFAULT 0,
                term_hits INTEGER NOT NULL DEFAULT 0,
                template_hits INTEGER NOT NULL DEFAULT 0,
                api_calls INTEGER NOT NULL DEFAULT 0,
                successes INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """,
            "INSERT OR IGNORE INTO translation_stats (id) VALUES (1)",
        ],
    ),
    (
        "2_matches",
        [
            # 对局（match）主表：每局独立 match_id，用于隔离局内消息与历史对局。
            """
            CREATE TABLE IF NOT EXISTS matches (
                match_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                source TEXT NOT NULL DEFAULT 'manual',
                message_count INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT ''
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_matches_started ON matches (started_at)",
            "CREATE INDEX IF NOT EXISTS idx_matches_status ON matches (status)",
            # translation_history 增加 match_id 归属；旧数据为 NULL（不属任何已知对局）。
            "ALTER TABLE translation_history ADD COLUMN match_id TEXT",
            "CREATE INDEX IF NOT EXISTS idx_history_match ON translation_history (match_id)",
            # 局内消息表：只保存当前/历史对局的聊天记录，与长期翻译记忆完全隔离。
            """
            CREATE TABLE IF NOT EXISTS match_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                timestamp TEXT NOT NULL DEFAULT '',
                sender TEXT NOT NULL DEFAULT '',
                channel TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                translated_text TEXT NOT NULL DEFAULT '',
                source_language TEXT NOT NULL DEFAULT '',
                hit_kind TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ok',
                latency_ms REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_match_messages_match ON match_messages (match_id, id)",
        ],
    ),
]


class Database:
    """SQLite 数据库封装（线程安全，单连接）。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # 关闭旗标：关闭后所有查询回传空结果，避免界面定时器在收尾阶段
        # 撞上已关闭的连接而抛 sqlite3.ProgrammingError
        self._closed = False
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=10.0, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self.migrate()

    def _configure(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.execute("PRAGMA foreign_keys=ON")

    # ---------------- 迁移 ----------------
    def migrate(self) -> int:
        """执行未应用的迁移，返回当前版本。"""
        with self._lock, self.transaction():
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT ''
                )"""
            )
            row = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM schema_version"
            ).fetchone()
            current = int(row["v"]) if row else 0
            for index, (name, statements) in enumerate(_MIGRATIONS, start=1):
                if index <= current:
                    continue
                for statement in statements:
                    self._conn.execute(statement)
                self._conn.execute(
                    "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (index, _utc_now()),
                )
                logger.info("已应用数据库迁移 %s (v%d)", name, index)
            return current

    # ---------------- 基础操作 ----------------
    @contextmanager
    def transaction(self):
        """原子事务上下文（隔离级别为 autocommit，手动 BEGIN）。"""
        with self._lock:
            if self._closed:
                raise sqlite3.ProgrammingError("数据库已关闭")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    @contextmanager
    def read(self):
        """只读上下文（仍持锁，避免与写操作交错）。"""
        with self._lock:
            yield self._conn

    def execute(self, sql: str, params: Iterable = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query_all(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        """查询多行。数据库已关闭时回传空清单，而不是抛例外。

        为什么需要这样：关闭窗口时引擎会关掉数据库，但界面上仍有
        定时器（状态刷新）与分页的延迟搜寻计时器可能刚好在此时触发，
        对已关闭的连接查询会抛 sqlite3.ProgrammingError
        （_threads can only be started once_ 那类竞态）。
        资料层直接回传空结果，比让每个呼叫端各自 try/except 更稳。
        """
        with self._lock:
            if self._closed:
                return []
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Iterable = ()):
        """查询单行。数据库已关闭时回传 None（理由见 query_all）。"""
        with self._lock:
            if self._closed:
                return None
            return self._conn.execute(sql, tuple(params)).fetchone()

    def is_closed(self) -> bool:
        """数据库是否已关闭（供界面在关闭流程中提前停止刷新）。"""
        return self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
