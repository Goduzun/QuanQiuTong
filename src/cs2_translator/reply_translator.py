"""双向翻译：中文回复 -> 目标语言（英语/俄语/日语/韩语及其他模型支持的语言）。

与接收翻译共用配置系统（同一份 AppConfig / SecretStore / 术语表），
但独立实例、独立指标与独立状态显示。

第一阶段不实现任何游戏内发送：译文只显示在界面上，供用户手动复制到游戏中。
"""

from __future__ import annotations

import logging

from .language import language_display_name, normalize_language_code, suggest_reply_language
from .translator import _BaseAITranslator, build_reply_system_prompt

logger = logging.getLogger("cs2_translator.reply_translator")

# 界面语言选择器中展示的语言（代码, 显示名）
COMMON_REPLY_TARGETS = [
    ("en", "英语"),
    ("ru", "俄语"),
    ("ja", "日语"),
    ("ko", "韩语"),
    ("de", "德语"),
    ("fr", "法语"),
    ("es", "西班牙语"),
    ("pt", "葡萄牙语"),
    ("it", "意大利语"),
    ("tr", "土耳其语"),
    ("uk", "乌克兰语"),
    ("pl", "波兰语"),
    ("vi", "越南语"),
    ("th", "泰语"),
    ("ar", "阿拉伯语"),
    ("hi", "印地语"),
    ("id", "印尼语"),
    ("nl", "荷兰语"),
    ("sv", "瑞典语"),
]


class ReplyTranslator(_BaseAITranslator):
    """回复翻译：简体中文 -> 目标语言。"""

    kind = "reply"

    def translate(
        self,
        chinese_text: str,
        target_language: str,
        term_hints=None,
        use_internal_cache: bool = True,
    ) -> str:
        text = self._check_text(chinese_text)
        target = normalize_language_code(target_language) or "en"
        cache_key = f"reply|{self.config.model}|{self.config.api_base_url}|{target}|{text}"
        prompt = build_reply_system_prompt(target, term_hints=term_hints)
        return self._chat(prompt, text, cache_key, use_cache=use_internal_cache)

    @staticmethod
    def suggest_target(last_message_text: str | None) -> str | None:
        """根据最近一条已识别的聊天消息建议目标语言（用户可手动修改）。"""
        return suggest_reply_language(last_message_text)

    @staticmethod
    def display_name(target_language: str) -> str:
        return language_display_name(target_language)
