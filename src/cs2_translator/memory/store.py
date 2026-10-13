"""翻译记忆库：查询优先级、保存、人工纠错、术语、模板、统计。

查询优先级（从高到低）：
1. 已确认的人工修正（translation_memory 中 confirmed=1 的精确匹配行）；
2. 完全匹配的翻译缓存（confirmed=0 的 AI 译文行）；
3. 已确认的整句术语/短语（translation_terms）；
4. 已确认的句式模板（translation_templates，变量严格校验）；
5. 均未命中 -> 由调用方请求在线 AI API。

冲突处理规则：
- 已确认的人工修正不会被未确认的 AI 结果覆盖；
- 未确认的 AI 译文不会因为重复出现而自动变为“已确认”；
- 人工纠错立即更新记忆行并记录修订历史，下次查询即用新译文。
"""

from __future__ import annotations

import csv
import json
import logging
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..glossary import ZH_TO_TARGET_TERMS
from .db import Database, _utc_now
from .normalize import (
    CACHE_SCHEMA_VERSION,
    DIRECTION_INCOMING,
    DIRECTION_REPLY,
    NormalizedText,
    normalize_language,
    normalize_source_text,
)
from .templates import (
    CompiledTemplate,
    TemplateError,
    compile_template,
    match_template,
)

logger = logging.getLogger("cs2_translator.memory.store")

HIT_CORRECTION = "correction"  # 已确认的人工修正
HIT_EXACT = "exact"            # 精确缓存（未确认 AI 译文）
HIT_TERM = "term"              # 整句术语/短语
HIT_TEMPLATE = "template"      # 确认模板复用

# 内置种子术语（接收方向：外语 -> 简体中文）
_BUILTIN_INCOMING_TERMS = [
    ("rush", "快攻/冲点", "战术"),
    ("rush B", "冲B点", "战术"),
    ("save", "保枪", "经济"),
    ("eco", "经济局(手枪局)", "经济"),
    ("force buy", "强起", "经济"),
    ("rotate", "转点", "战术"),
    ("push", "前压", "战术"),
    ("hold", "守点", "战术"),
    ("fall back", "撤退", "战术"),
    ("drop", "丢枪/发枪", "装备"),
    ("trade", "补枪", "战术"),
    ("clutch", "残局(1vN)", "战术"),
    ("plant", "下包", "战术"),
    ("defuse", "拆包", "战术"),
    ("retake", "回防", "战术"),
    ("lurk", "单摸/游击", "战术"),
    ("entry", "突破手", "角色"),
    ("support", "辅助位", "角色"),
    ("igl", "指挥", "角色"),
    ("camper", "蹲点/老六", "战术"),
    ("boost", "架人", "战术"),
    ("stack", "多人集中", "战术"),
    ("mid", "中路", "点位"),
    ("long", "长道", "点位"),
    ("short", "短道", "点位"),
    ("site", "包点", "点位"),
    ("banana", "香蕉道", "点位"),
    ("apps", "公寓区", "点位"),
    ("heaven", "天堂位", "点位"),
    ("AWP", "AWP狙击枪", "武器"),
    ("scout", "鸟狙(SSG08)", "武器"),
    ("AK", "AK-47", "武器"),
    ("M4", "M4A4/M4A1", "武器"),
    ("deagle", "沙鹰(沙漠之鹰)", "武器"),
    ("smoke", "烟雾弹", "道具"),
    ("flash", "闪光弹", "道具"),
    ("molotov", "燃烧弹", "道具"),
    ("HE", "手雷", "道具"),
    ("nade", "手雷", "道具"),
    ("gg", "打得漂亮", "常用语"),
    ("gl", "祝好运", "常用语"),
    ("hf", "祝开局顺利", "常用语"),
    ("nt", "好局", "常用语"),
    ("ez", "轻松/太简单", "常用语"),
    ("noob", "新手", "常用语"),
]

# 内置种子模板（接收方向）
_BUILTIN_INCOMING_TEMPLATES = [
    ("They're pushing {site}!", "他们正在进攻 {site} 点！"),
    ("Rush {site}!", "冲 {site} 点！"),
    ("Rotate to {site}!", "转点去 {site} 点！"),
    ("Plant the bomb!", "下包！"),
    ("Defuse the bomb!", "拆包！"),
    ("{number} enemies left", "还剩 {number} 个敌人"),
    ("I need a drop!", "我需要发把枪！"),
    ("Save your {weapon}!", "保枪，留住你的 {weapon}！"),
]

# 内置种子模板（回复方向：中文 -> 目标语言）
_BUILTIN_REPLY_TEMPLATES = {
    "en": [
        ("冲{site}点！", "Rush {site}!"),
        ("转点去{site}！", "Rotate to {site}!"),
        ("快攻{site}点！", "Push {site}!"),
        ("守{site}点！", "Hold {site}!"),
        ("我还有{number}点血", "I have {number} HP"),
    ],
    "ru": [
        ("冲{site}点！", "Раш {site}!"),
        ("转点去{site}！", "Ротируем на {site}!"),
    ],
}


@dataclass
class LookupResult:
    """记忆查询结果。"""

    hit: bool
    kind: str = ""  # correction / exact / term / template
    translated: str | None = None
    memory_id: int | None = None
    term_hints: list = field(default_factory=list)  # [(source_term, translated_term)]
    normalized: NormalizedText | None = None

    @classmethod
    def miss(cls, normalized: NormalizedText | None = None, term_hints=None) -> "LookupResult":
        return cls(hit=False, term_hints=term_hints or [])


class TranslationMemory:
    """本地翻译记忆库（SQLite 持久化）。"""

    def __init__(
        self,
        db_path: Path | str,
        enabled: bool = True,
        history_enabled: bool = True,
    ):
        self.db = Database(db_path)
        self.enabled = enabled
        self.history_enabled = history_enabled
        self._template_lock = threading.Lock()
        self._template_cache: dict[tuple[str, str], list[CompiledTemplate]] = {}
        self._seed_builtins()

    # ================================================================
    # 查询
    # ================================================================
    def lookup(
        self,
        text: str,
        target_language: str,
        direction: str,
    ) -> LookupResult:
        """按优先级查询记忆库。enabled=False 时直接返回 miss。"""
        if not self.enabled:
            return LookupResult.miss()
        normalized = normalize_source_text(text)
        if not normalized.normalized:
            return LookupResult.miss(normalized)

        target = normalize_language(target_language)

        # ---- 1&2. 精确缓存（已确认修正优先于未确认 AI 译文） ----
        row = self._find_memory_row(normalized.hash, target, direction)
        if row is not None:
            self._bump_hit(row["id"])
            kind = HIT_CORRECTION if row["confirmed"] else HIT_EXACT
            self._bump_stats(exact_hits=1)
            return LookupResult(
                hit=True,
                kind=kind,
                translated=row["translated_text"],
                memory_id=row["id"],
                normalized=normalized,
            )

        # ---- 3. 整句术语/短语 ----
        term_row = self._find_whole_term(normalized.normalized, target, direction)
        if term_row is not None:
            self._bump_stats(term_hits=1)
            return LookupResult(
                hit=True,
                kind=HIT_TERM,
                translated=term_row["translated_term"],
                normalized=normalized,
            )

        # ---- 4. 已确认模板 ----
        for template in self._confirmed_templates(target, direction):
            match = match_template(template, normalized.normalized)
            if match is not None:
                self._bump_template_usage(template)
                self._bump_stats(template_hits=1)
                return LookupResult(
                    hit=True,
                    kind=HIT_TEMPLATE,
                    translated=match.translated,
                    normalized=normalized,
                )

        # ---- 5. 未命中：返回术语提示供 prompt 使用 ----
        return LookupResult.miss(
            normalized=normalized,
            term_hints=self.match_terms(normalized.normalized, target, direction),
        )

    def _find_memory_row(self, source_hash: str, target: str, direction: str):
        return self.db.query_one(
            "SELECT * FROM translation_memory "
            " WHERE source_hash=? AND target_language=? AND direction=? AND cache_version=?",
            (source_hash, target, direction, CACHE_SCHEMA_VERSION),
        )

    def _find_whole_term(self, normalized_text: str, target: str, direction: str):
        return self.db.query_one(
            "SELECT * FROM translation_terms "
            " WHERE target_language=? AND direction=? AND confirmed=1 "
            "   AND LOWER(TRIM(source_term))=?",
            (target, direction, normalized_text.lower()),
        )

    # ================================================================
    # 保存（翻译成功后调用）
    # ================================================================
    def save_translation(
        self,
        *,
        text: str,
        target_language: str,
        direction: str,
        translated: str,
        source_language: str = "",
        provider: str = "",
        model: str = "",
        confirmed: bool = False,
        source_type: str = "chat",
    ) -> int | None:
        """保存一条 AI 译文（UPSERT）。失败/空译文不会进入这里。"""
        if not self.enabled:
            return None
        if not translated or not translated.strip():
            return None
        normalized = normalize_source_text(text)
        if not normalized.normalized:
            return None
        target = normalize_language(target_language)
        now = _utc_now()

        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM translation_memory "
                " WHERE source_hash=? AND target_language=? AND direction=? AND cache_version=?",
                (normalized.hash, target, direction, CACHE_SCHEMA_VERSION),
            ).fetchone()
            if row is not None:
                if row["confirmed"] and not confirmed:
                    # 人工确认的译文不被未确认 AI 结果覆盖
                    return row["id"]
                if row["translated_text"] != translated:
                    conn.execute(
                        "INSERT INTO memory_revisions (memory_id, old_text, new_text, reason, created_at)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (
                            row["id"],
                            row["translated_text"],
                            translated,
                            "user_edit" if confirmed else "ai_update",
                            now,
                        ),
                    )
                conn.execute(
                    "UPDATE translation_memory SET translated_text=?, original_source=?,"
                    " source_language=?, provider=?, model=?, confirmed=?, source_type=?,"
                    " updated_at=? WHERE id=?",
                    (
                        translated,
                        normalized.original,
                        source_language,
                        provider,
                        model,
                        1 if confirmed else 0,
                        source_type,
                        now,
                        row["id"],
                    ),
                )
                return row["id"]

            cursor = conn.execute(
                "INSERT INTO translation_memory (source_hash, normalized_source, original_source,"
                " source_language, target_language, translated_text, provider, model,"
                " cache_version, direction, source_type, confirmed, hit_count, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    normalized.hash,
                    normalized.normalized,
                    normalized.original,
                    source_language,
                    target,
                    translated,
                    provider,
                    model,
                    CACHE_SCHEMA_VERSION,
                    direction,
                    source_type,
                    1 if confirmed else 0,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    # ================================================================
    # 人工纠错
    # ================================================================
    def confirm_translation(
        self,
        text: str,
        target_language: str,
        direction: str,
        new_text: str | None = None,
    ) -> int | None:
        """标记译文为已确认；可同时提供修正后的译文。"""
        normalized = normalize_source_text(text)
        if not normalized.normalized:
            return None
        target = normalize_language(target_language)
        now = _utc_now()

        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM translation_memory "
                " WHERE source_hash=? AND target_language=? AND direction=? AND cache_version=?",
                (normalized.hash, target, direction, CACHE_SCHEMA_VERSION),
            ).fetchone()
            if row is None:
                if not new_text or not new_text.strip():
                    return None
                cursor = conn.execute(
                    "INSERT INTO translation_memory (source_hash, normalized_source, original_source,"
                    " source_language, target_language, translated_text, provider, model,"
                    " cache_version, direction, source_type, confirmed, hit_count, created_at, updated_at)"
                    " VALUES (?, ?, ?, '', ?, ?, 'user', '', ?, ?, 'chat', 1, 0, ?, ?)",
                    (
                        normalized.hash,
                        normalized.normalized,
                        normalized.original,
                        target,
                        new_text.strip(),
                        CACHE_SCHEMA_VERSION,
                        direction,
                        now,
                        now,
                    ),
                )
                return int(cursor.lastrowid)

            if new_text and new_text.strip() and new_text.strip() != row["translated_text"]:
                conn.execute(
                    "INSERT INTO memory_revisions (memory_id, old_text, new_text, reason, created_at)"
                    " VALUES (?, ?, ?, 'user_edit', ?)",
                    (row["id"], row["translated_text"], new_text.strip(), now),
                )
                conn.execute(
                    "UPDATE translation_memory SET translated_text=?, provider='user',"
                    " confirmed=1, updated_at=? WHERE id=?",
                    (new_text.strip(), now, row["id"]),
                )
            else:
                conn.execute(
                    "UPDATE translation_memory SET confirmed=1, updated_at=? WHERE id=?",
                    (now, row["id"]),
                )
            return row["id"]

    def restore_previous(self, memory_id: int) -> str | None:
        """恢复到该记忆的上一个修订版本，返回恢复后的译文。"""
        row = self.db.query_one(
            "SELECT * FROM translation_memory WHERE id=?", (memory_id,)
        )
        if row is None:
            return None
        revision = self.db.query_one(
            "SELECT * FROM memory_revisions WHERE memory_id=? ORDER BY id DESC LIMIT 1",
            (memory_id,),
        )
        if revision is None:
            return None
        now = _utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO memory_revisions (memory_id, old_text, new_text, reason, created_at)"
                " VALUES (?, ?, ?, 'restore', ?)",
                (memory_id, row["translated_text"], revision["old_text"], now),
            )
            conn.execute(
                "UPDATE translation_memory SET translated_text=?, updated_at=? WHERE id=?",
                (revision["old_text"], now, memory_id),
            )
        return revision["old_text"]

    def delete_memory(self, memory_id: int) -> bool:
        """删除一条记忆（下次同句将重新请求 API）。"""
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM translation_memory WHERE id=?", (memory_id,)
            )
            return cursor.rowcount > 0

    def delete_memory_by_key(
        self, text: str, target_language: str, direction: str
    ) -> bool:
        normalized = normalize_source_text(text)
        row = self._find_memory_row(
            normalized.hash, normalize_language(target_language), direction
        )
        if row is None:
            return False
        return self.delete_memory(row["id"])

    # ================================================================
    # 术语
    # ================================================================
    def match_terms(
        self,
        text: str,
        target_language: str,
        direction: str,
        limit: int = 12,
    ) -> list:
        """返回文本中出现的已确认术语 [(source_term, translated_term)]。

        结合词边界与上下文：拉丁术语用词边界匹配（不拆词），
        CJK 术语用子串匹配；只作为翻译提示，不做输出替换。
        """
        target = normalize_language(target_language)
        rows = self.db.query_all(
            "SELECT source_term, translated_term FROM translation_terms"
            " WHERE target_language=? AND direction=? AND confirmed=1"
            " ORDER BY LENGTH(source_term) DESC",
            (target, direction),
        )
        matched = []
        for row in rows:
            if _term_in_text(row["source_term"], text):
                matched.append((row["source_term"], row["translated_term"]))
                if len(matched) >= limit:
                    break
        return matched

    def term_stats(self) -> dict:
        """用 SQL 聚合回傳術語統計（供詞彙表頁顯示）。

        比「撈出全部術語再在 Python 逐筆數」快得多，且不受 limit 影響。
        """
        row = self.db.query_one(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN direction=? THEN 1 ELSE 0 END) AS incoming,"
            " SUM(CASE WHEN direction=? THEN 1 ELSE 0 END) AS reply,"
            " SUM(CASE WHEN confirmed=1 THEN 1 ELSE 0 END) AS confirmed"
            " FROM translation_terms",
            (DIRECTION_INCOMING, DIRECTION_REPLY),
        )
        if row is None:
            return {"total": 0, "incoming": 0, "reply": 0, "confirmed": 0}
        return {
            "total": int(row["total"] or 0),
            "incoming": int(row["incoming"] or 0),
            "reply": int(row["reply"] or 0),
            "confirmed": int(row["confirmed"] or 0),
        }

    def add_term(
        self,
        source_term: str,
        target_language: str,
        translated_term: str,
        direction: str = DIRECTION_INCOMING,
        category: str = "",
        confirmed: bool = True,
    ) -> int:
        source_term = (source_term or "").strip()
        translated_term = (translated_term or "").strip()
        if not source_term or not translated_term:
            raise ValueError("术语原文与译文不能为空")
        target = normalize_language(target_language)
        now = _utc_now()
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM translation_terms WHERE source_term=? AND target_language=? AND direction=?",
                (source_term, target, direction),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    "UPDATE translation_terms SET translated_term=?, category=?, confirmed=?,"
                    " updated_at=? WHERE id=?",
                    (translated_term, category, 1 if confirmed else 0, now, existing["id"]),
                )
                return int(existing["id"])
            cursor = conn.execute(
                "INSERT INTO translation_terms (source_term, target_language, direction,"
                " translated_term, category, confirmed, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_term,
                    target,
                    direction,
                    translated_term,
                    category,
                    1 if confirmed else 0,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def update_term(
        self,
        term_id: int,
        translated_term: str | None = None,
        category: str | None = None,
        confirmed: bool | None = None,
    ) -> bool:
        assignments = []
        params: list = []
        if translated_term is not None:
            if not translated_term.strip():
                raise ValueError("译文不能为空")
            assignments.append("translated_term=?")
            params.append(translated_term.strip())
        if category is not None:
            assignments.append("category=?")
            params.append(category)
        if confirmed is not None:
            assignments.append("confirmed=?")
            params.append(1 if confirmed else 0)
        if not assignments:
            return False
        assignments.append("updated_at=?")
        params.append(_utc_now())
        params.append(term_id)
        with self.db.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE translation_terms SET {', '.join(assignments)} WHERE id=?",
                tuple(params),
            )
            return cursor.rowcount > 0

    def delete_term(self, term_id: int) -> bool:
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM translation_terms WHERE id=?", (term_id,))
            return cursor.rowcount > 0

    def list_terms(
        self,
        target_language: str | None = None,
        direction: str | None = None,
        keyword: str | None = None,
        limit: int = 500,
    ) -> list:
        sql = "SELECT * FROM translation_terms"
        conditions, params = [], []
        if target_language:
            conditions.append("target_language=?")
            params.append(normalize_language(target_language))
        if direction:
            conditions.append("direction=?")
            params.append(direction)
        if keyword:
            conditions.append("(source_term LIKE ? OR translated_term LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY LENGTH(source_term) DESC, id LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self.db.query_all(sql, tuple(params))]

    # ================================================================
    # 模板
    # ================================================================
    def add_template(
        self,
        source_pattern: str,
        translated_pattern: str,
        target_language: str,
        direction: str = DIRECTION_INCOMING,
        confidence: float = 1.0,
        confirmed: bool = True,
    ) -> int:
        compiled = compile_template(
            source_pattern, translated_pattern, target_language, direction
        )  # 变量类型非法时抛 TemplateError
        target = normalize_language(target_language)
        now = _utc_now()
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM translation_templates"
                " WHERE source_pattern=? AND target_language=? AND direction=?",
                (source_pattern, target, direction),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    "UPDATE translation_templates SET translated_pattern=?, confidence=?,"
                    " confirmed=?, updated_at=? WHERE id=?",
                    (
                        translated_pattern,
                        confidence,
                        1 if confirmed else 0,
                        now,
                        existing["id"],
                    ),
                )
                self._invalidate_template_cache()
                return int(existing["id"])
            cursor = conn.execute(
                "INSERT INTO translation_templates (source_pattern, target_language, direction,"
                " translated_pattern, confidence, confirmed, usage_count, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    source_pattern,
                    target,
                    direction,
                    translated_pattern,
                    confidence,
                    1 if confirmed else 0,
                    now,
                    now,
                ),
            )
            self._invalidate_template_cache()
            return int(cursor.lastrowid)

    def update_template(
        self,
        template_id: int,
        translated_pattern: str | None = None,
        confidence: float | None = None,
        confirmed: bool | None = None,
    ) -> bool:
        row = self.db.query_one(
            "SELECT * FROM translation_templates WHERE id=?", (template_id,)
        )
        if row is None:
            return False
        new_translated = row["translated_pattern"]
        if translated_pattern is not None:
            new_translated = translated_pattern
            compile_template(
                row["source_pattern"], new_translated, row["target_language"], row["direction"]
            )  # 校验
        assignments = ["translated_pattern=?", "updated_at=?"]
        params: list = [new_translated, _utc_now()]
        if confidence is not None:
            assignments.append("confidence=?")
            params.append(confidence)
        if confirmed is not None:
            assignments.append("confirmed=?")
            params.append(1 if confirmed else 0)
        params.append(template_id)
        with self.db.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE translation_templates SET {', '.join(assignments)} WHERE id=?",
                tuple(params),
            )
            self._invalidate_template_cache()
            return cursor.rowcount > 0

    def delete_template(self, template_id: int) -> bool:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM translation_templates WHERE id=?", (template_id,)
            )
            self._invalidate_template_cache()
            return cursor.rowcount > 0

    def list_templates(
        self,
        target_language: str | None = None,
        direction: str | None = None,
        limit: int = 200,
    ) -> list:
        sql = "SELECT * FROM translation_templates"
        conditions, params = [], []
        if target_language:
            conditions.append("target_language=?")
            params.append(normalize_language(target_language))
        if direction:
            conditions.append("direction=?")
            params.append(direction)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self.db.query_all(sql, tuple(params))]

    def _confirmed_templates(
        self, target: str, direction: str
    ) -> list[CompiledTemplate]:
        cache_key = (target, direction)
        with self._template_lock:
            cached = self._template_cache.get(cache_key)
            if cached is not None:
                return cached
        compiled = []
        for row in self.list_templates(target_language=target, direction=direction):
            if not row["confirmed"]:
                continue
            try:
                compiled.append(
                    compile_template(
                        row["source_pattern"],
                        row["translated_pattern"],
                        row["target_language"],
                        row["direction"],
                    )
                )
            except TemplateError as exc:
                logger.warning("跳过非法模板 #%s: %s", row["id"], exc)
        with self._template_lock:
            self._template_cache[cache_key] = compiled
        return compiled

    def _invalidate_template_cache(self) -> None:
        with self._template_lock:
            self._template_cache.clear()

    # ================================================================
    # 历史与统计
    # ================================================================
    def record_history(
        self,
        *,
        source_text: str,
        target_language: str,
        translated_text: str,
        direction: str = DIRECTION_INCOMING,
        source_type: str = "chat",
        hit_kind: str = "",
        cache_hit: bool = False,
        status: str = "ok",
        latency_ms: float = 0.0,
        tokens_used: int = 0,
        source_language: str = "",
        provider: str = "",
        model: str = "",
    ) -> None:
        """记录一条翻译历史（用户可在设置中关闭；关闭不影响缓存功能）。"""
        if not self.history_enabled:
            return
        try:
            self.db.execute(
                "INSERT INTO translation_history (source_text, source_language, target_language,"
                " translated_text, direction, source_type, hit_kind, cache_hit, status,"
                " latency_ms, tokens_used, provider, model, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_text,
                    source_language,
                    normalize_language(target_language),
                    translated_text,
                    direction,
                    source_type,
                    hit_kind,
                    1 if cache_hit else 0,
                    status,
                    round(float(latency_ms), 1),
                    int(tokens_used or 0),
                    provider,
                    model,
                    _utc_now(),
                ),
            )
        except Exception:
            logger.exception("写入翻译历史失败")

    def list_history(self, limit: int = 200, offset: int = 0) -> list:
        rows = self.db.query_all(
            "SELECT * FROM translation_history ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [dict(row) for row in rows]

    def _bump_stats(self, **counts) -> None:
        assignments = ", ".join(f"{key}={key}+?" for key in counts)
        params = list(counts.values())
        params.append(_utc_now())
        with self.db.transaction() as conn:
            conn.execute(
                f"UPDATE translation_stats SET {assignments}, updated_at=? WHERE id=1",
                tuple(params),
            )

    def bump_api(self, *, success: bool, tokens_used: int = 0) -> None:
        counts = {"api_calls": 1, "successes": 1 if success else 0, "failures": 0 if success else 1}
        if tokens_used:
            counts["tokens_used"] = int(tokens_used)
        self._bump_stats(**counts)

    def _bump_hit(self, memory_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE translation_memory SET hit_count=hit_count+1, updated_at=?"
                " WHERE id=?",
                (_utc_now(), memory_id),
            )

    def _bump_template_usage(self, template: CompiledTemplate) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE translation_templates SET usage_count=usage_count+1"
                " WHERE source_pattern=? AND target_language=? AND direction=?",
                (
                    template.source_pattern,
                    template.target_language,
                    template.direction,
                ),
            )

    def get_stats(self, price_per_1k_tokens: float = 0.0) -> dict:
        """汇总统计。命中率口径：命中次数 /（命中次数 + API 调用次数）。

        预计节省费用仅在用户配置了每千 token 价格且存在 token 用量数据时
        计算；否则为 None（界面只显示节省的请求数，不编造金额）。
        """
        row = self.db.query_one("SELECT * FROM translation_stats WHERE id=1")
        stats = {
            "exact_hits": 0,
            "term_hits": 0,
            "template_hits": 0,
            "api_calls": 0,
            "successes": 0,
            "failures": 0,
            "tokens_used": 0,
        }
        if row is not None:
            for key in stats:
                stats[key] = row[key] or 0
        hits = stats["exact_hits"] + stats["term_hits"] + stats["template_hits"]
        total = hits + stats["api_calls"]
        stats["cache_hits"] = hits
        stats["total_lookups"] = total
        stats["cache_hit_rate"] = round(hits / total, 4) if total else 0.0
        stats["saved_requests"] = hits
        stats["estimated_savings"] = None
        if price_per_1k_tokens and price_per_1k_tokens > 0 and stats["api_calls"] > 0:
            avg_tokens = stats["tokens_used"] / stats["api_calls"]
            if avg_tokens > 0:
                stats["estimated_savings"] = round(
                    hits * avg_tokens / 1000.0 * price_per_1k_tokens, 4
                )
        return stats

    # ================================================================
    # 缓存管理
    # ================================================================
    def list_memory(
        self,
        keyword: str | None = None,
        direction: str | None = None,
        confirmed_only: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list:
        sql = "SELECT * FROM translation_memory"
        conditions, params = [], []
        if keyword:
            conditions.append("(original_source LIKE ? OR translated_text LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        if direction:
            conditions.append("direction=?")
            params.append(direction)
        if confirmed_only:
            conditions.append("confirmed=1")
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [dict(row) for row in self.db.query_all(sql, tuple(params))]

    def count_memory(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS c FROM translation_memory")
        return int(row["c"]) if row else 0

    def export_json(self, path: Path | str) -> int:
        entries = self.list_memory(limit=100000)
        payload = {
            "app": "quanqiutong-cs2",
            "cache_version": CACHE_SCHEMA_VERSION,
            "exported_at": _utc_now(),
            "entries": entries,
        }
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return len(entries)

    def export_csv(self, path: Path | str) -> int:
        entries = self.list_memory(limit=100000)
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "原文", "标准化原文", "源语言", "目标语言", "方向",
                    "译文", "已确认", "命中次数", "更新时间",
                ]
            )
            for entry in entries:
                writer.writerow(
                    [
                        entry["original_source"],
                        entry["normalized_source"],
                        entry["source_language"],
                        entry["target_language"],
                        entry["direction"],
                        entry["translated_text"],
                        "是" if entry["confirmed"] else "否",
                        entry["hit_count"],
                        entry["updated_at"],
                    ]
                )
        return len(entries)

    def clear_cache(self) -> dict:
        """清空翻译缓存与历史（术语、模板、累计统计保留）。需界面二次确认。"""
        with self.db.transaction() as conn:
            memory = conn.execute("DELETE FROM translation_memory").rowcount
            history = conn.execute("DELETE FROM translation_history").rowcount
        return {"memory_cleared": memory, "history_cleared": history}

    def backup(self, dest_path: Path | str) -> Path:
        """备份数据库文件（先做 WAL 检查点，保证副本完整）。"""
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._template_lock:
            try:
                self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                logger.exception("WAL 检查点失败，仍继续备份")
            shutil.copyfile(self.db.path, dest)
        return dest

    # ================================================================
    # 内置种子
    # ================================================================
    def _seed_builtins(self) -> None:
        """写入内置术语与模板。

        幂等且**只增不改**：已存在的条目一律跳过，绝不覆盖。
        这样用户在界面上对内置术语/模板做的编辑不会在下次启动时被还原
        （旧实现用 UPSERT，会覆盖用户修改 —— 已实测复现的数据丢失）。
        """
        try:
            for term, translation, category in _BUILTIN_INCOMING_TERMS:
                self._seed_term_if_absent(
                    term,
                    "zh",
                    translation,
                    direction=DIRECTION_INCOMING,
                    category=category,
                )
            for target, table in ZH_TO_TARGET_TERMS.items():
                for source_term, translated_term in table.items():
                    self._seed_term_if_absent(
                        source_term,
                        target,
                        translated_term,
                        direction=DIRECTION_REPLY,
                        category="战术",
                    )
            for source_pattern, translated_pattern in _BUILTIN_INCOMING_TEMPLATES:
                self._seed_template_if_absent(
                    source_pattern,
                    translated_pattern,
                    "zh",
                    direction=DIRECTION_INCOMING,
                )
            for target, patterns in _BUILTIN_REPLY_TEMPLATES.items():
                for source_pattern, translated_pattern in patterns:
                    self._seed_template_if_absent(
                        source_pattern,
                        translated_pattern,
                        target,
                        direction=DIRECTION_REPLY,
                    )
        except Exception:
            logger.exception("写入内置术语/模板失败")

    def _seed_term_if_absent(
        self,
        source_term: str,
        target_language: str,
        translated_term: str,
        *,
        direction: str,
        category: str = "",
    ) -> int | None:
        """仅在术语不存在时写入（内置种子专用）。

        与 ``add_term`` 的区别：**绝不更新已存在的行**。
        内置种子每次构造都会执行；若沿用 ``add_term`` 的 UPSERT，用户在界面上
        对内置术语做的编辑会在下次启动时被默认值覆盖（已实测复现的数据丢失）。
        """
        target = normalize_language(target_language)
        try:
            existing = self.db.query_one(
                "SELECT id FROM translation_terms"
                " WHERE source_term=? AND target_language=? AND direction=?",
                (source_term, target, direction),
            )
        except Exception:
            logger.exception("检查内置术语是否存在失败: %r", source_term)
            return None
        if existing is not None:
            return None
        try:
            return self.add_term(
                source_term,
                target_language,
                translated_term,
                direction=direction,
                category=category,
                confirmed=True,
            )
        except Exception:
            logger.exception("写入内置术语失败: %r", source_term)
            return None

    def _seed_template_if_absent(
        self,
        source_pattern: str,
        translated_pattern: str,
        target_language: str,
        *,
        direction: str,
    ) -> int | None:
        """仅在句式模板不存在时写入（内置种子专用，同样不覆盖用户修改）。"""
        target = normalize_language(target_language)
        try:
            existing = self.db.query_one(
                "SELECT id FROM translation_templates"
                " WHERE source_pattern=? AND target_language=? AND direction=?",
                (source_pattern, target, direction),
            )
        except Exception:
            logger.exception("检查内置模板是否存在失败: %r", source_pattern)
            return None
        if existing is not None:
            return None
        try:
            return self.add_template(
                source_pattern,
                translated_pattern,
                target_language,
                direction=direction,
                confirmed=True,
            )
        except Exception as exc:
            logger.warning("内置模板无效 %r: %s", source_pattern, exc)
            return None

    def close(self) -> None:
        self.db.close()


# ---------------------------------------------------------------- 工具
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def _term_in_text(term: str, text: str) -> bool:
    """术语命中判断：CJK 用子串，拉丁用词边界（不拆词）。

    大小写策略：术语含大写字母（如 HE、AWP、CT、M4）时按大小写敏感匹配，
    避免把 "HE"（手雷）误匹配到 "he"（他）；全小写术语不区分大小写。
    """
    if not term or not text:
        return False
    if _CJK_RE.search(term) or _CJK_RE.search(text):
        return term in text
    has_upper = any(ch.isupper() for ch in term)
    pattern = r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"
    flags = 0 if has_upper else re.IGNORECASE
    return re.search(pattern, text, flags) is not None
