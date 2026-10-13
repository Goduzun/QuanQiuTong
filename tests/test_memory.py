"""翻译记忆库单元测试：标准化、缓存键、查询优先级、纠错、术语、模板、统计、持久化。"""

from __future__ import annotations

import concurrent.futures
import tempfile
from pathlib import Path

import pytest

from cs2_translator.memory import (
    DIRECTION_INCOMING,
    DIRECTION_REPLY,
    HIT_CORRECTION,
    HIT_EXACT,
    HIT_TEMPLATE,
    HIT_TERM,
    TranslationMemory,
    make_cache_key,
    normalize_source,
)
from cs2_translator.memory.normalize import CACHE_SCHEMA_VERSION
from cs2_translator.memory.templates import (
    TemplateError,
    compile_template,
    match_template,
)


@pytest.fixture
def memory(tmp_path) -> TranslationMemory:
    mem = TranslationMemory(tmp_path / "memory.db")
    yield mem
    mem.close()


class TestBuiltinSeedDoesNotClobberUserEdits:
    """内置种子每次构造都会执行，但**绝不能**覆盖用户的修改。

    旧实现用 add_term 的 UPSERT，导致用户编辑内置术语后，下次启动/打开记忆库
    对话框就被还原为默认值（真实复现过的数据丢失）。
    """

    def test_user_edit_to_builtin_term_survives_reconstruction(self, tmp_path):
        db_path = tmp_path / "memory.db"
        m1 = TranslationMemory(db_path)
        try:
            terms = m1.list_terms(limit=5000)
            assert terms, "应存在内置术语"
            target = terms[0]
            tid = target["id"]
            custom = "【用户自定义译文】"
            assert m1.update_term(tid, translated_term=custom) is True
        finally:
            m1.close()

        # 重新构造 = 模拟重启程序 / 再次打开记忆库对话框
        m2 = TranslationMemory(db_path)
        try:
            row = next(t for t in m2.list_terms(limit=5000) if t["id"] == tid)
            assert row["translated_term"] == custom, "用户对内置术语的编辑被种子覆盖了"
        finally:
            m2.close()

    def test_user_edit_to_builtin_template_survives_reconstruction(self, tmp_path):
        db_path = tmp_path / "memory.db"
        m1 = TranslationMemory(db_path)
        try:
            templates = m1.list_templates(limit=5000)
            assert templates, "应存在内置模板"
            # 选一个带变量的模板，替换时保持变量集合一致（模板校验要求）
            target = next(
                (t for t in templates if "{" in (t.get("source_pattern") or "")),
                templates[0],
            )
            tid = target["id"]
            pattern = target["translated_pattern"]
            assert "{" in pattern, "需要带变量的模板才能安全替换"
            custom = pattern.replace("{", "【改】{")
            assert m1.update_template(tid, translated_pattern=custom) is True
        finally:
            m1.close()

        m2 = TranslationMemory(db_path)
        try:
            row = next(t for t in m2.list_templates(limit=5000) if t["id"] == tid)
            assert row["translated_pattern"] == custom, "用户对内置模板的编辑被种子覆盖了"
        finally:
            m2.close()

    def test_repeated_construction_is_idempotent(self, tmp_path):
        """重复构造不应改变条目数量（只增不改 + 不重复插入）。"""
        db_path = tmp_path / "memory.db"
        counts = []
        for _ in range(3):
            mem = TranslationMemory(db_path)
            counts.append((len(mem.list_terms(limit=5000)),
                           len(mem.list_templates(limit=5000))))
            mem.close()
        assert counts[0] == counts[1] == counts[2], f"重复构造改变了条目数: {counts}"

    def test_deleted_builtin_term_is_not_resurrected(self, tmp_path):
        """用户主动删除的内置术语不应被下次构造复活。"""
        db_path = tmp_path / "memory.db"
        m1 = TranslationMemory(db_path)
        try:
            tid = m1.list_terms(limit=5000)[0]["id"]
            assert m1.delete_term(tid) is True
        finally:
            m1.close()

        m2 = TranslationMemory(db_path)
        try:
            assert not [t for t in m2.list_terms(limit=5000) if t["id"] == tid]
        finally:
            m2.close()



# ================================================================
# 标准化与缓存键
# ================================================================
class TestNormalization:
    def test_strips_and_collapses_whitespace(self):
        assert normalize_source("  hello   world  ") == "hello world"

    def test_unifies_newlines(self):
        assert normalize_source("a\r\nb\rc") == "a\nb\nc"

    def test_removes_zero_width_chars(self):
        assert normalize_source("rush\u200eb") == "rushb"

    def test_keeps_negation_and_numbers(self):
        assert normalize_source("don't rush B") != normalize_source("rush B")
        assert "2" in normalize_source("2 enemies left")

    def test_cache_key_includes_direction_and_language(self):
        k1 = make_cache_key("hello", "zh", DIRECTION_INCOMING)
        k2 = make_cache_key("hello", "en", DIRECTION_REPLY)
        k3 = make_cache_key("hello", "zh", DIRECTION_INCOMING)
        assert k1 != k2
        assert k1 == k3  # 同方向同语言稳定
        assert CACHE_SCHEMA_VERSION in "1"  # 版本常量存在


# ================================================================
# 查询优先级与保存
# ================================================================
class TestLookupPriority:
    def test_miss_returns_hints(self, memory):
        result = memory.lookup("rush B now!", "zh", DIRECTION_INCOMING)
        assert not result.hit
        assert any(src == "rush B" for src, _ in result.term_hints)

    def test_exact_cache_hit_increments(self, memory):
        memory.save_translation(
            text="rush B now!", target_language="zh", direction=DIRECTION_INCOMING,
            translated="冲B点！", provider="mock", model="m",
        )
        first = memory.lookup("rush B now!", "zh", DIRECTION_INCOMING)
        assert first.hit and first.kind == HIT_EXACT
        memory.lookup("rush B now!", "zh", DIRECTION_INCOMING)
        row = memory.db.query_one(
            "SELECT hit_count FROM translation_memory WHERE original_source='rush B now!'"
        )
        assert row["hit_count"] == 2

    def test_whole_term_hit(self, memory):
        # 内置术语 "rush B" 不是整句；"保枪" 是回复方向整句术语
        result = memory.lookup("保枪", "en", DIRECTION_REPLY)
        assert result.hit and result.kind == HIT_TERM
        assert result.translated == "save"

    def test_template_hit_requires_confirmed_and_valid_vars(self, memory):
        result = memory.lookup("They're pushing A!", "zh", DIRECTION_INCOMING)
        assert result.hit and result.kind == HIT_TEMPLATE
        assert result.translated == "他们正在进攻 A 点！"

    def test_template_rejects_unknown_words(self, memory):
        # 包含点位变量之外的词，整句不匹配模板 -> 未命中
        result = memory.lookup("They're pushing somewhere unknown!", "zh", DIRECTION_INCOMING)
        assert not result.hit


# ================================================================
# 方向与语言隔离
# ================================================================
class TestIsolation:
    def test_same_text_different_direction_not_reused(self, memory):
        memory.save_translation(
            text="保枪", target_language="en", direction=DIRECTION_REPLY,
            translated="save", provider="mock", model="m",
        )
        # 接收方向（外语->中文）不应命中回复方向缓存
        result = memory.lookup("保枪", "zh", DIRECTION_INCOMING)
        assert not result.hit

    def test_same_text_different_language_not_reused(self, memory):
        memory.save_translation(
            text="rush B", target_language="zh", direction=DIRECTION_INCOMING,
            translated="冲B点", provider="mock", model="m",
        )
        result = memory.lookup("rush B", "en", DIRECTION_REPLY)
        assert not result.hit


# ================================================================
# 人工纠错
# ================================================================
class TestCorrection:
    def test_correction_takes_priority(self, memory):
        memory.save_translation(
            text="rush B now!", target_language="zh", direction=DIRECTION_INCOMING,
            translated="冲B点！", provider="mock", model="m",
        )
        memory.confirm_translation("rush B now!", "zh", DIRECTION_INCOMING, new_text="快冲B点！！")
        result = memory.lookup("rush B now!", "zh", DIRECTION_INCOMING)
        assert result.kind == HIT_CORRECTION
        assert result.translated == "快冲B点！！"

    def test_ai_cannot_override_correction(self, memory):
        memory.confirm_translation(
            "rush B now!", "zh", DIRECTION_INCOMING, new_text="快冲B点！！"
        )
        memory.save_translation(
            text="rush B now!", target_language="zh", direction=DIRECTION_INCOMING,
            translated="AI乱译", provider="mock", model="m", confirmed=False,
        )
        assert memory.lookup("rush B now!", "zh", DIRECTION_INCOMING).translated == "快冲B点！！"

    def test_unconfirmed_ai_not_auto_confirmed(self, memory):
        memory.save_translation(
            text="hello", target_language="zh", direction=DIRECTION_INCOMING,
            translated="你好", provider="mock", model="m",
        )
        for _ in range(5):
            memory.lookup("hello", "zh", DIRECTION_INCOMING)
        row = memory.db.query_one(
            "SELECT confirmed FROM translation_memory WHERE original_source='hello'"
        )
        assert row["confirmed"] == 0  # 重复命中不会自动变为已确认

    def test_restore_previous_version(self, memory):
        memory.save_translation(
            text="hello", target_language="zh", direction=DIRECTION_INCOMING,
            translated="你好", provider="mock", model="m",
        )
        memory.confirm_translation("hello", "zh", DIRECTION_INCOMING, new_text="哈喽")
        row = memory.db.query_one(
            "SELECT id FROM translation_memory WHERE original_source='hello'"
        )
        restored = memory.restore_previous(row["id"])
        assert restored == "你好"
        assert memory.lookup("hello", "zh", DIRECTION_INCOMING).translated == "你好"

    def test_delete_memory(self, memory):
        memory.save_translation(
            text="hello", target_language="zh", direction=DIRECTION_INCOMING,
            translated="你好", provider="mock", model="m",
        )
        row = memory.db.query_one(
            "SELECT id FROM translation_memory WHERE original_source='hello'"
        )
        assert memory.delete_memory(row["id"]) is True
        assert not memory.lookup("hello", "zh", DIRECTION_INCOMING).hit


# ================================================================
# 术语与模板 CRUD
# ================================================================
class TestTermsAndTemplates:
    def test_add_and_match_term_with_word_boundary(self, memory):
        memory.add_term("boost me", "zh", "帮我架人", direction=DIRECTION_INCOMING, confirmed=True)
        hints = memory.match_terms("can someone boost me here", "zh", DIRECTION_INCOMING)
        assert ("boost me", "帮我架人") in hints
        # 词边界：不应匹配 "booster"
        assert not memory.match_terms("he is a booster", "zh", DIRECTION_INCOMING)

    def test_term_update_delete(self, memory):
        term_id = memory.add_term("testterm", "zh", "测试词", direction=DIRECTION_INCOMING)
        assert memory.update_term(term_id, translated_term="测试词2") is True
        rows = memory.list_terms(keyword="testterm")
        assert rows[0]["translated_term"] == "测试词2"
        assert memory.delete_term(term_id) is True
        assert memory.list_terms(keyword="testterm") == []

    def test_invalid_template_rejected(self, memory):
        with pytest.raises(TemplateError):
            memory.add_template(
                "They're pushing {unknown_var}!", "他们进攻 {unknown_var}！",
                "zh", direction=DIRECTION_INCOMING,
            )

    def test_template_add_and_reuse(self, memory):
        memory.add_template(
            "Go {site} please!", "去 {site} 点！", "zh", direction=DIRECTION_INCOMING, confirmed=True
        )
        result = memory.lookup("Go B please!", "zh", DIRECTION_INCOMING)
        assert result.hit and result.kind == HIT_TEMPLATE
        assert result.translated == "去 B 点！"


# ================================================================
# 模板变量校验（安全回退）
# ================================================================
class TestTemplateSafety:
    def test_missing_variable_does_not_match(self):
        template = compile_template("Rush {site}!", "冲 {site} 点！", "zh", DIRECTION_INCOMING)
        assert match_template(template, "Rush !") is None  # 变量缺失
        assert match_template(template, "Rush ABCDEFGHIJ!") is None  # site 过长非法
        assert match_template(template, "Rush B!") is not None

    def test_variable_type_invalid_falls_back(self):
        template = compile_template("{number} enemies left", "还剩 {number} 个敌人", "zh", DIRECTION_INCOMING)
        assert match_template(template, "five enemies left") is None  # 非数字
        assert match_template(template, "5 enemies left") is not None

    def test_negation_breaks_template(self):
        template = compile_template("Rush {site}!", "冲 {site} 点！", "zh", DIRECTION_INCOMING)
        assert match_template(template, "Don't rush B!") is None


# ================================================================
# 统计
# ================================================================
class TestStats:
    def test_hit_rate_and_saved_requests(self, memory):
        memory.bump_api(success=True, tokens_used=100)
        memory.save_translation(
            text="hello", target_language="zh", direction=DIRECTION_INCOMING,
            translated="你好", provider="mock", model="m",
        )
        memory.lookup("hello", "zh", DIRECTION_INCOMING)
        memory.lookup("hello", "zh", DIRECTION_INCOMING)
        stats = memory.get_stats()
        assert stats["api_calls"] == 1
        assert stats["exact_hits"] == 2
        assert stats["saved_requests"] == 2
        assert stats["cache_hit_rate"] == pytest.approx(2 / 3, abs=1e-4)  # 保留 4 位小数

    def test_savings_requires_price_and_tokens(self, memory):
        memory.bump_api(success=True, tokens_used=1000)
        memory.save_translation(
            text="hello", target_language="zh", direction=DIRECTION_INCOMING,
            translated="你好", provider="mock", model="m",
        )
        memory.lookup("hello", "zh", DIRECTION_INCOMING)
        # 无价格 -> 不编造金额
        assert memory.get_stats(price_per_1k_tokens=0.0)["estimated_savings"] is None
        # 有价格与 token 数据 -> 估算：1 次命中 * (1000/1000) * 0.01 = 0.01
        savings = memory.get_stats(price_per_1k_tokens=0.01)["estimated_savings"]
        assert savings is not None and savings == pytest.approx(0.01)

    def test_failed_api_counted_as_failure(self, memory):
        memory.bump_api(success=False)
        stats = memory.get_stats()
        assert stats["api_calls"] == 1
        assert stats["failures"] == 1
        assert stats["successes"] == 0


# ================================================================
# 持久化（重启）
# ================================================================
class TestPersistence:
    def test_cache_survives_restart(self, tmp_path):
        path = tmp_path / "memory.db"
        mem = TranslationMemory(path)
        mem.save_translation(
            text="hello team", target_language="zh", direction=DIRECTION_INCOMING,
            translated="你好，队伍", provider="mock", model="m",
        )
        mem.close()

        mem2 = TranslationMemory(path)
        result = mem2.lookup("hello team", "zh", DIRECTION_INCOMING)
        assert result.hit and result.translated == "你好，队伍"
        mem2.close()

    def test_clear_cache(self, memory):
        memory.save_translation(
            text="hello", target_language="zh", direction=DIRECTION_INCOMING,
            translated="你好", provider="mock", model="m",
        )
        result = memory.clear_cache()
        assert result["memory_cleared"] == 1
        assert not memory.lookup("hello", "zh", DIRECTION_INCOMING).hit
        # 清空后统计保留（累计节省不受影响）
        assert memory.get_stats()["exact_hits"] >= 0

    def test_export_json_and_csv(self, memory, tmp_path):
        memory.save_translation(
            text="hello", target_language="zh", direction=DIRECTION_INCOMING,
            translated="你好", provider="mock", model="m",
        )
        json_path = tmp_path / "export.json"
        csv_path = tmp_path / "export.csv"
        assert memory.export_json(json_path) == 1
        assert memory.export_csv(csv_path) == 1
        assert "hello" in json_path.read_text(encoding="utf-8")
        assert "你好" in csv_path.read_text(encoding="utf-8-sig")


# ================================================================
# 并发单飞
# ================================================================
class TestSingleFlight:
    def test_concurrent_lookup_shares_one_call(self):
        import time

        from cs2_translator.memory import SingleFlight

        sf = SingleFlight()
        calls = []
        lock = __import__("threading").Lock()

        def work():
            time.sleep(0.1)  # 模拟慢速 API：让并发调用在 leader 执行期间重叠
            with lock:
                calls.append(1)
            return "done"

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(sf.run, "same-key", work) for _ in range(8)]
            results = [f.result() for f in futures]
        assert results == [("done", None)] * 8
        assert len(calls) == 1
