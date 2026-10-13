"""引擎：组装日志监听、解析、队列、翻译与回复翻译，提供异步 API 与状态回调。

线程模型：
- LogWatcher 线程：只读增量读取日志 -> 解析 -> 提交有界队列；
- N 个翻译工作线程：消费队列 -> 调用翻译 API -> 保序回调；
- 回复线程池（单线程）：处理交互式回复翻译，与接收翻译相互独立；
- 状态监控线程：周期性汇总状态。
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .chat_parser import ChatMessage, ChatParser
from .config import AppConfig, ConfigManager
from .language import detect_primary_language, needs_translation
from .log_watcher import LogWatcher
from .memory import (
    DIRECTION_INCOMING,
    DIRECTION_REPLY,
    LookupResult,
    MatchManager,
    SingleFlight,
    TranslationMemory,
    detect_match_signal,
    extract_map_name,
    make_cache_key,
    normalize_source_text,
)
from .queue_manager import TranslationQueueManager
from .reply_translator import ReplyTranslator
from .storage import HistoryStore
from .translator import SAMPLE_TRANSLATION_TEXT, OpenAITranslator, TranslatorError

logger = logging.getLogger("cs2_translator.engine")


@dataclass
class TranslateOutcome:
    """一次翻译的最终结果（含缓存命中信息）。"""

    translated: str | None = None
    hit_kind: str = ""       # ""=在线 API；correction/exact/term/template=记忆命中
    cache_hit: bool = False
    error: TranslatorError | None = None
    latency_ms: float = 0.0
    tokens_used: int = 0


class TranslationEngine:
    """CS2 聊天翻译引擎（无 GUI 依赖，可在命令行与测试中直接使用）。"""

    def __init__(self, config: AppConfig, data_dir: Path | None = None):
        self.config = config
        self.data_dir = Path(data_dir) if data_dir else ConfigManager().data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.parser = ChatParser(
            extra_patterns=config.extra_chat_patterns,
            enable_generic=config.enable_generic_name_pattern,
        )
        self.translator = OpenAITranslator(config, api_key=config.api_key)
        self.reply_translator = ReplyTranslator(config, api_key=config.api_key)
        self.history = HistoryStore(self.data_dir / "history.jsonl")

        # 本地翻译记忆库（SQLite）+ 并发单飞
        self.memory = TranslationMemory(
            self.data_dir / "memory.db",
            enabled=config.memory_enabled,
            history_enabled=config.save_memory_history,
        )
        self._singleflight = SingleFlight()

        # 回调（由 UI 或 CLI 设置）
        self.on_chat = None            # (ChatMessage, is_test=False)
        self.on_translation = None     # (ChatMessage, translated, latency_ms, error, skipped, meta)
        self.on_status = None          # (dict)
        self.on_log_event = None       # (dict)
        self.on_reply = None           # (dict)
        self.on_match_change = None    # (dict) 对局切换通知，供界面清空显示

        self._queue = TranslationQueueManager(
            handler=self._handle_queued,
            max_size=config.queue_max_size,
            workers=config.translation_workers,
            skip_expired_seconds=config.skip_expired_seconds,
            on_event=self._on_queue_event,
        )
        self._watcher = LogWatcher(
            path=config.cs2_log_path,
            poll_interval=config.log_poll_interval,
            read_from_start=config.read_history_on_start,
            on_line=self._on_log_line,
            on_event=self._on_watcher_event,
        )
        self._reply_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reply")
        self._reply_futures = []
        self._reply_pending = 0

        self._status_stop = threading.Event()
        self._status_thread = None
        self._running = False
        self._last_incoming: ChatMessage | None = None
        self._counters = {"translated": 0, "failed": 0, "dropped": 0, "skipped": 0}
        self._last_error = None
        self._api_state = "unknown"  # unknown / ok / error

        # ---- 对局隔离 ----
        # 复用记忆库同一个 Database 连接，保证「局内消息」与「长期翻译记忆」在
        # 同一事务域内、共用同一把锁，避免并发写入冲突。
        self.matches = MatchManager(
            self.memory.db,
            message_limit=getattr(config, "match_message_limit", 300),
            history_limit=getattr(config, "history_match_limit", 10),
            on_change=self._on_match_change,
        )
        # id(message) -> {"match_id", "generation", "row_id"}，用于把迟到的
        # 翻译结果丢弃、或落回它真正归属的那一局。
        self._message_context: dict[int, dict] = {}
        self._context_lock = threading.Lock()
        self.auto_match_detection = bool(getattr(config, "auto_match_detection", False))
        # 上一次启动重置的结果（供界面提示，未执行则为 None）
        self.last_reset_result: dict | None = None
        # 上一次触发换局的地图名，用于忽略同一地图的重复载入
        self._last_match_map: str | None = None

    # ==================================================================
    # 生命周期
    # ==================================================================
    def start(self) -> None:
        if self._running:
            return
        # 启动重置：清空「实时翻译」与「历史对局」，只保留长期缓存与词汇表。
        # 必须在 watcher 启动前完成，避免清空与写入交错。
        if getattr(self.config, "reset_on_startup", True) and self.matches is not None:
            try:
                result = self.matches.reset_for_new_session()
                self.last_reset_result = result
                logger.info(
                    "已按设置重置对局数据：%s 局 / %s 条消息（长期记忆保留）",
                    result.get("matches_removed"), result.get("messages_removed"),
                )
            except Exception:
                logger.exception("启动重置对局数据失败")
        self._running = True
        self._queue.start()
        self._watcher.start()
        self._status_stop.clear()
        self._status_thread = threading.Thread(
            target=self._status_loop, name="engine-status", daemon=True
        )
        self._status_thread.start()
        logger.info("引擎已启动 (v%s)，监听: %s", __version__, self.config.cs2_log_path)

    def stop(self, timeout: float = 10.0) -> None:
        if not self._running:
            return
        self._running = False
        self._status_stop.set()
        self._watcher.stop(timeout=timeout)
        self._queue.stop(timeout=timeout)
        for future in self._reply_futures:
            future.cancel()
        self._reply_futures.clear()
        try:
            self._reply_pool.shutdown(wait=True, cancel_futures=True)
        except TypeError:  # Python < 3.9 兼容
            self._reply_pool.shutdown(wait=True)
        if self._status_thread:
            self._status_thread.join(timeout=2.0)
        try:
            self.memory.close()
        except Exception:
            logger.exception("关闭翻译记忆库失败")
        logger.info("引擎已停止")

    # ==================================================================
    # 接收翻译流水线
    # ==================================================================
    def _on_log_line(self, line: str) -> None:
        # 对局边界识别：默认关闭（真实日志中观察到的 Host activate 信号尚未在
        # 真实多局对战下验证，因此不默认启用，也不伪造识别成功）。
        if self.auto_match_detection and self.matches is not None:
            signal = detect_match_signal(line)
            if signal is not None:
                kind, reason = signal
                if kind == "start":
                    map_name = extract_map_name(line)
                    # 去噪：练习图会反复重载同一张图（真实日志中 5e_aimhub 连续重载 3 次），
                    # 那不是新的一局，不应每次重载都切换对局。
                    if map_name and map_name == self._last_match_map:
                        logger.debug("忽略同一地图的重复载入: %s", map_name)
                    else:
                        self._last_match_map = map_name or self._last_match_map
                        self.start_new_match(source="auto", note=f"自动识别: {reason}")
                elif kind == "end":
                    self.matches.end_current_match()

        message = self.parser.parse(line)
        if message is None:
            return
        self._last_incoming = message
        if self.on_chat:
            self._safe_call(self.on_chat, message)

        match_id = self.matches.current_match_id if self.matches else None
        generation = self.matches.generation if self.matches else None

        # 先把消息写入它所属的那一局，拿到 row_id 供回填译文
        row_id = 0
        if match_id and self.matches is not None:
            try:
                row_id = self.matches.add_message(
                    timestamp=message.timestamp or "",
                    sender=message.sender or "",
                    channel=message.channel_label or message.channel or "",
                    content=message.content or "",
                    status="pending",
                )
            except Exception:
                logger.exception("写入局内消息失败")
        if generation is not None:
            with self._context_lock:
                self._message_context[id(message)] = {
                    "match_id": match_id,
                    "generation": generation,
                    "row_id": row_id,
                }
            self._prune_message_context()

        if not self.config.translate_enabled:
            if row_id and self.matches is not None:
                self.matches.update_message(row_id, translated_text=message.content, status="skipped")
            return
        if not needs_translation(message.content, self.config.enabled_source_languages):
            self._counters["skipped"] += 1
            # 保留原文：同时把原文当作译文写回该局，状态标记为 skipped
            if row_id and self.matches is not None:
                self.matches.update_message(
                    row_id, translated_text=message.content, status="skipped"
                )
            if self.on_translation:
                self._safe_call(
                    self.on_translation,
                    message,
                    message.content,
                    0.0,
                    None,
                    True,
                    {"hit_kind": "", "cache_hit": False, "match_id": match_id, "stale": False},
                )
            self._forget_context(message)
            return
        if not self._queue.submit(message):
            # 队列已满被丢弃：必须回调，否则界面永远停在“翻译中…”
            self._counters["dropped"] += 1
            if row_id and self.matches is not None:
                self.matches.update_message(
                    row_id, translated_text="", hit_kind="dropped", status="dropped"
                )
            if self.on_translation:
                self._safe_call(
                    self.on_translation,
                    message,
                    "",
                    0.0,
                    TranslatorError("翻译队列已满，该条已丢弃", kind="queue_full"),
                    False,
                    {"hit_kind": "dropped", "cache_hit": False, "match_id": match_id, "stale": False},
                )
            self._forget_context(message)

    def _forget_context(self, message: ChatMessage) -> None:
        with self._context_lock:
            self._message_context.pop(id(message), None)

    def _on_match_change(self, event: str) -> None:
        """MatchManager 的对局变更回调 -> 转发给界面。

        自动识别换局时界面必须同步清空消息流，否则会一直显示上一局内容
        （实测：引擎已切到新对局，界面仍停留在旧对局的聊天记录）。
        """
        if not self.on_match_change:
            return
        info = self.matches.current if self.matches else None
        self._safe_call(
            self.on_match_change,
            {
                "event": event,
                "match_id": info.match_id if info else None,
                "source": info.source if info else "",
                "note": info.note if info else "",
            },
        )

    def _prune_message_context(self) -> None:
        """有界化消息上下文，避免长时间运行内存无限增长。

        ``_message_context`` 只在成功回调（emit）里清理。队列因过期被跳过、
        或处理函数抛异常的消息永远不会走到 emit，条目会一直残留
        （id() 复用还可能让新消息读到旧对局上下文，导致误判为「迟到结果」）。
        因此按上限裁剪最旧的条目。
        """
        limit = 2000
        with self._context_lock:
            overflow = len(self._message_context) - limit
            if overflow <= 0:
                return
            # dict 保持插入顺序，丢弃最早的条目
            for key in list(self._message_context.keys())[:overflow]:
                self._message_context.pop(key, None)

    # ==================================================================
    # 对局控制（供界面调用）
    # ==================================================================
    def start_new_match(self, source: str = "manual", note: str = "") -> str | None:
        """结束当前局、开启新的一局，并清理局内临时状态。

        返回新对局 id。已发出的 API 请求允许安全结束，但结果会因代际不符被丢弃。
        """
        if self.matches is None:
            return None
        # 取消尚未开始的局内翻译任务，并清空去重/保序临时状态
        try:
            self._queue.cancel_pending()
        except Exception:
            logger.exception("取消待翻译任务失败")
        with self._context_lock:
            self._message_context.clear()
        info = self.matches.start_new_match(source=source, note=note)
        logger.info("已切换到新对局 %s (source=%s)", info.match_id, source)
        return info.match_id

    def _handle_queued(self, message: ChatMessage, seq: int) -> None:
        # 捕获本条消息所属的对局与代际：换局后若代际已变，结果必须丢弃，
        # 避免上一局的迟到翻译结果污染新一局的消息流。
        context = self._message_context.get(id(message), {})
        match_id = context.get("match_id")
        generation = context.get("generation")

        source = detect_primary_language(message.content)
        message.source_language = source
        outcome = self._translate_with_memory(
            text=message.content,
            direction=DIRECTION_INCOMING,
            target_language="zh",
            source_language=source,
            source_type="chat",
            api_translator=self.translator,
            api_call=lambda hints: self.translator.translate(
                message.content,
                source_language=source,
                term_hints=hints,
                use_internal_cache=not self.config.memory_enabled,
            ),
        )
        translated = outcome.translated
        error = outcome.error
        latency_ms = outcome.latency_ms

        if error is None:
            self._counters["translated"] += 1
            self._api_state = "ok"
        else:
            self._counters["failed"] += 1
            self._last_error = str(error)
            if error.kind in ("auth", "bad_request"):
                self._api_state = "error"

        def emit():
            # ---- 代际校验：上一局的迟到结果绝不进入新一局 ----
            stale = (
                generation is not None
                and self._is_stale_generation(generation)
            )

            # 结果落到它真正归属的那一局（而不是"当前局"）
            if match_id and self.matches is not None and not stale:
                try:
                    self.matches.update_message(
                        context.get("row_id", 0),
                        translated_text=translated or "",
                        hit_kind=outcome.hit_kind or ("error" if error else ""),
                        status="ok" if error is None else "failed",
                        latency_ms=latency_ms or 0.0,
                        source_language=source,
                    )
                except Exception:
                    logger.exception("写入对局消息译文失败")

            if stale:
                # 丢弃但不丢事件：让界面知道这条结果属于上一局，可安全忽略
                if self.on_translation:
                    self._safe_call(
                        self.on_translation,
                        message,
                        translated,
                        latency_ms,
                        error,
                        False,
                        {
                            "hit_kind": outcome.hit_kind,
                            "cache_hit": outcome.cache_hit,
                            "match_id": match_id,
                            "stale": True,
                        },
                    )
                return

            if self.on_translation:
                self._safe_call(
                    self.on_translation,
                    message,
                    translated,
                    latency_ms,
                    error,
                    False,
                    {
                        "hit_kind": outcome.hit_kind,
                        "cache_hit": outcome.cache_hit,
                        "match_id": match_id,
                        "stale": False,
                    },
                )
            if error is None and self.config.save_history:
                self.history.append(
                    {
                        "ts": time.time(),
                        "sender": message.sender,
                        "channel": message.channel,
                        "original": message.content,
                        "translated": translated,
                    }
                )
            # 清理消息上下文，避免长时间运行时字典无限增长
            self._message_context.pop(id(message), None)

        self._queue.submit_result(seq, emit)

    def _is_stale_generation(self, generation: int) -> bool:
        """判断某个代际是否已被换局淘汰。"""
        if self.matches is None:
            return False
        return generation != self.matches.generation

    # ==================================================================
    # 回复翻译（双向）
    # ==================================================================
    def translate_reply(self, text: str, target_language: str) -> None:
        """异步翻译一条中文回复；结果通过 on_reply 回调返回。"""
        if not self._running:
            raise RuntimeError("引擎未运行")
        self._reply_pending += 1
        try:
            future = self._reply_pool.submit(self._reply_worker, text, target_language)
        except RuntimeError:
            self._reply_pending -= 1
            raise
        self._reply_futures.append(future)
        self._reply_futures = [f for f in self._reply_futures if not f.done()]

    def cancel_pending_replies(self) -> int:
        cancelled = sum(1 for f in self._reply_futures if f.cancel())
        self._reply_futures.clear()
        return cancelled

    def _reply_worker(self, text: str, target_language: str) -> None:
        started = time.monotonic()
        result = {
            "ok": False,
            "original": text,
            "translated": None,
            "target": target_language,
            "error": None,
            "error_kind": None,
            "latency_ms": 0.0,
            "hit_kind": "",
            "cache_hit": False,
        }
        try:
            outcome = self._translate_with_memory(
                text=text,
                direction=DIRECTION_REPLY,
                target_language=target_language,
                source_language="zh",
                source_type="reply",
                api_translator=self.reply_translator,
                api_call=lambda hints: self.reply_translator.translate(
                    text,
                    target_language,
                    term_hints=hints,
                    use_internal_cache=not self.config.memory_enabled,
                ),
            )
            if outcome.error is not None:
                raise outcome.error
            result["translated"] = outcome.translated
            result["ok"] = True
            result["hit_kind"] = outcome.hit_kind
            result["cache_hit"] = outcome.cache_hit
            self._api_state = "ok"
        except TranslatorError as exc:
            result["error"] = str(exc)
            result["error_kind"] = exc.kind
            if exc.kind in ("auth", "bad_request"):
                self._api_state = "error"
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"未预期的错误: {exc}"
            result["error_kind"] = "internal"
        finally:
            result["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
            self._reply_pending = max(0, self._reply_pending - 1)
            if self.on_reply:
                self._safe_call(self.on_reply, result)

    def suggest_reply_language(self) -> str:
        """建议回复目标语言：优先按最近一条聊天消息，其次用户默认值。"""
        if self.config.auto_suggest_reply_language and self._last_incoming is not None:
            suggested = self.reply_translator.suggest_target(self._last_incoming.content)
            if suggested:
                return suggested
        return self.config.reply_target_language

    # ==================================================================
    # 翻译记忆库链路（接收 + 回复共用）
    # ==================================================================
    def _translate_with_memory(
        self,
        *,
        text: str,
        direction: str,
        target_language: str,
        source_language: str,
        source_type: str,
        api_translator,
        api_call,
    ) -> TranslateOutcome:
        """统一的翻译入口：记忆查询 -> 在线 API -> 保存。

        查询优先级：已确认人工修正 > 精确缓存 > 整句术语 > 确认模板 > 在线 API。
        并发相同句子通过单飞共享一次 API 请求；API 失败不写入缓存。
        """
        started = time.monotonic()

        lookup = (
            self.memory.lookup(text, target_language, direction)
            if self.config.memory_enabled
            else LookupResult.miss()
        )

        if lookup.hit:
            latency_ms = (time.monotonic() - started) * 1000
            self.memory.record_history(
                source_text=text,
                target_language=target_language,
                translated_text=lookup.translated or "",
                direction=direction,
                source_type=source_type,
                hit_kind=lookup.kind,
                cache_hit=True,
                latency_ms=latency_ms,
                source_language=source_language,
                provider="memory",
                model=self.config.model,
            )
            return TranslateOutcome(
                translated=lookup.translated,
                hit_kind=lookup.kind,
                cache_hit=True,
                latency_ms=latency_ms,
            )

        cache_key = make_cache_key(text, target_language, direction)
        result, error = self._singleflight.run(cache_key, lambda: api_call(lookup.term_hints))
        latency_ms = (time.monotonic() - started) * 1000

        if error is not None:
            translator_error = (
                error
                if isinstance(error, TranslatorError)
                else TranslatorError(f"未预期的翻译错误: {error}", kind="internal")
            )
            self.memory.bump_api(success=False)
            self.memory.record_history(
                source_text=text,
                target_language=target_language,
                translated_text="",
                direction=direction,
                source_type=source_type,
                hit_kind="",
                cache_hit=False,
                status="failed",
                latency_ms=latency_ms,
                source_language=source_language,
                provider=self.config.api_base_url,
                model=self.config.model,
            )
            # 失败绝不写入缓存，后续可正常重试
            return TranslateOutcome(
                error=translator_error, latency_ms=latency_ms
            )

        tokens_used = getattr(api_translator, "last_usage_tokens", 0) or 0
        self.memory.bump_api(success=True, tokens_used=tokens_used)
        self.memory.save_translation(
            text=text,
            target_language=target_language,
            direction=direction,
            translated=result,
            source_language=source_language,
            provider=self.config.api_base_url,
            model=self.config.model,
            source_type=source_type,
        )
        self.memory.record_history(
            source_text=text,
            target_language=target_language,
            translated_text=result,
            direction=direction,
            source_type=source_type,
            hit_kind="",
            cache_hit=False,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
            source_language=source_language,
            provider=self.config.api_base_url,
            model=self.config.model,
        )
        return TranslateOutcome(
            translated=result,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
        )

    # ==================================================================
    # 记忆库管理（供界面调用）
    # ==================================================================
    def memory_stats(self) -> dict:
        return self.memory.get_stats(self.config.memory_price_per_1k_tokens)

    def save_correction(
        self, text: str, target_language: str, direction: str, new_text: str | None = None
    ) -> int | None:
        """保存人工修正（优先级最高，立即可用）。"""
        return self.memory.confirm_translation(text, target_language, direction, new_text)

    def confirm_translation(self, text: str, target_language: str, direction: str) -> int | None:
        return self.memory.confirm_translation(text, target_language, direction)

    def delete_memory(self, memory_id: int) -> bool:
        return self.memory.delete_memory(memory_id)

    def restore_previous(self, memory_id: int) -> str | None:
        return self.memory.restore_previous(memory_id)

    def find_memory_id(self, text: str, target_language: str, direction: str) -> int | None:
        normalized = normalize_source_text(text)
        row = self.memory._find_memory_row(
            normalized.hash, target_language.lower(), direction
        )
        return int(row["id"]) if row else None

    # ==================================================================
    # 测试辅助
    # ==================================================================
    def test_log_read(self, max_lines: int = 500, path: str | None = None) -> dict:
        """测试日志读取：只读打开，统计可识别的聊天消息（供设置界面调用）。

        ``path`` 允许测试界面上尚未保存的路径；为空则用当前配置。
        """
        path = path or self.config.cs2_log_path
        result = {"path": path, "exists": False, "readable": False, "lines": 0,
                  "chat_messages": 0, "samples": [], "error": None}
        try:
            with open(path, "rb") as fh:
                result["exists"] = True
                result["readable"] = True
                data = fh.read()
        except FileNotFoundError:
            result["error"] = "日志文件不存在（CS2 未使用 -condebug 启动或路径不正确）"
            return result
        except OSError as exc:
            result["exists"] = True
            result["error"] = f"无法读取日志文件: {exc}"
            return result

        lines = data.decode("utf-8", errors="replace").splitlines()[-max_lines:]
        result["lines"] = len(lines)
        count = 0
        for line in lines:
            message = self.parser.parse(line)
            if message is not None:
                count += 1
                if len(result["samples"]) < 5:
                    result["samples"].append(message.to_dict())
        result["chat_messages"] = count
        if count == 0:
            result["error"] = (
                "日志可读，但未识别到聊天消息。请确认 CS2 启动参数包含 -condebug，"
                "且游戏内确有聊天发生；也可以使用“日志诊断”查看原始内容。"
            )
        return result

    def test_api(self) -> tuple:
        """测试翻译 API：翻译一条固定短句。返回 (成功?, 消息)。"""
        try:
            translated = self.translator.translate(SAMPLE_TRANSLATION_TEXT)
            self._api_state = "ok"
            return True, f"连接正常。测试译文: {translated}"
        except TranslatorError as exc:
            self._api_state = "error"
            self._last_error = str(exc)
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001
            self._api_state = "error"
            return False, f"未预期的错误: {exc}"

    # ==================================================================
    # 状态
    # ==================================================================
    def status(self) -> dict:
        watcher_stats = dict(self._watcher.stats)
        queue_stats = self._queue.stats()
        incoming_metrics = self.translator.metrics()
        reply_metrics = self.reply_translator.metrics()
        incoming_metrics["queue_length"] = queue_stats["queue_length"]
        reply_metrics["queue_length"] = self._reply_pending
        return {
            "running": self._running,
            "log_path": self.config.cs2_log_path,
            "log_exists": Path(self.config.cs2_log_path).is_file()
            if self.config.cs2_log_path
            else False,
            "api_state": self._api_state,
            "last_error": self._last_error,
            "translate_enabled": self.config.translate_enabled,
            "paused": self._queue.is_paused,
            "counters": dict(self._counters),
            "watcher": watcher_stats,
            "queue": queue_stats,
            "incoming_metrics": incoming_metrics,
            "reply_metrics": reply_metrics,
            "memory": self.memory_stats(),
            "model": self.config.model,
            "api_base_url": self.config.api_base_url,
        }

    def _status_loop(self) -> None:
        while not self._status_stop.wait(0.5):
            if self.on_status:
                self._safe_call(self.on_status, self.status())

    # ==================================================================
    # 内部工具
    # ==================================================================
    def _on_watcher_event(self, event: dict) -> None:
        if self.on_log_event:
            self._safe_call(self.on_log_event, event)

    def _on_queue_event(self, event: dict) -> None:
        if event.get("type") == "dropped":
            self._counters["dropped"] += 1

    @staticmethod
    def _safe_call(callback, *args) -> None:
        try:
            callback(*args)
        except Exception:
            logger.exception("回调执行失败: %s", getattr(callback, "__name__", callback))
