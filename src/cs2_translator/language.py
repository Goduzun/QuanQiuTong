"""语言文字识别：基于 Unicode 区间的轻量检测（无外部依赖，可离线测试）。"""

from __future__ import annotations

import re

# 各文字体系的 Unicode 区间
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_ARABIC_RE = re.compile(r"[\u0600-\u06ff]")
_HEBREW_RE = re.compile(r"[\u0590-\u05ff]")
_THAI_RE = re.compile(r"[\u0e00-\u0e7f]")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097f]")

# 乌克兰语特征字母（用于与俄语区分）
_UKRAINIAN_EXTRA_RE = re.compile(r"[\u0454\u0456\u0457\u0491\u0406\u0407]")

LANGUAGE_NAMES = {
    "zh": "中文",
    "en": "英语",
    "ru": "俄语",
    "uk": "乌克兰语",
    "ja": "日语",
    "ko": "韩语",
    "de": "德语",
    "fr": "法语",
    "es": "西班牙语",
    "pt": "葡萄牙语",
    "it": "意大利语",
    "tr": "土耳其语",
    "pl": "波兰语",
    "vi": "越南语",
    "th": "泰语",
    "ar": "阿拉伯语",
    "he": "希伯来语",
    "hi": "印地语",
    "id": "印尼语",
    "nl": "荷兰语",
    "sv": "瑞典语",
    "other": "其他语言",
}

# 语言代码别名（界面显示名/英文名 -> 代码）
_LANGUAGE_ALIASES = {
    "chinese": "zh", "中文": "zh", "简体中文": "zh", "汉语": "zh",
    "english": "en", "英文": "en", "英语": "en",
    "russian": "ru", "俄文": "ru", "俄语": "ru",
    "ukrainian": "uk", "乌克兰语": "uk",
    "japanese": "ja", "日文": "ja", "日语": "ja",
    "korean": "ko", "韩文": "ko", "韩语": "ko",
    "german": "de", "德语": "de", "德文": "de",
    "french": "fr", "法语": "fr", "法文": "fr",
    "spanish": "es", "西班牙语": "es", "西语": "es",
    "portuguese": "pt", "葡萄牙语": "pt",
    "italian": "it", "意大利语": "it",
    "turkish": "tr", "土耳其语": "tr",
    "polish": "pl", "波兰语": "pl",
    "vietnamese": "vi", "越南语": "vi",
    "thai": "th", "泰语": "th",
    "arabic": "ar", "阿拉伯语": "ar",
    "hebrew": "he", "希伯来语": "he",
    "hindi": "hi", "印地语": "hi",
    "indonesian": "id", "印尼语": "id",
    "dutch": "nl", "荷兰语": "nl",
    "swedish": "sv", "瑞典语": "sv",
}


def normalize_language_code(value: str) -> str:
    """把 '英语'/'english'/'EN' 等归一化为 'en'；未知值原样返回（交由模型处理）。"""
    if not value:
        return ""
    key = str(value).strip().lower()
    if key in LANGUAGE_NAMES:
        return key
    if key in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[key]
    return key


def language_display_name(code: str) -> str:
    code = normalize_language_code(code)
    return LANGUAGE_NAMES.get(code, code or "未知")


def script_counts(text: str) -> dict:
    """统计文本中各文字体系的字符数。"""
    return {
        "cjk": len(_CJK_RE.findall(text)),
        "kana": len(_KANA_RE.findall(text)),
        "hangul": len(_HANGUL_RE.findall(text)),
        "cyrillic": len(_CYRILLIC_RE.findall(text)),
        "latin": len(_LATIN_RE.findall(text)),
        "arabic": len(_ARABIC_RE.findall(text)),
        "hebrew": len(_HEBREW_RE.findall(text)),
        "thai": len(_THAI_RE.findall(text)),
        "devanagari": len(_DEVANAGARI_RE.findall(text)),
    }


def letter_count(text: str) -> int:
    counts = script_counts(text)
    return sum(v for k, v in counts.items() if k != "devanagari") + counts["devanagari"]


def chinese_ratio(text: str) -> float:
    """汉字占“字母类字符”的比例。

    注意：不能仅凭“出现汉字”就判定为中文，否则中英混合消息会被漏翻。
    """
    counts = script_counts(text)
    letters = sum(counts.values())
    if letters == 0:
        return 0.0
    return counts["cjk"] / letters


def detect_primary_language(text: str) -> str:
    """尽力检测主导语言，返回语言代码。

    拉丁字母语言（英/德/法/西等）无法在不调用模型的情况下可靠区分，
    统一返回 'en'，对“翻译成中文”的决策没有影响；对回复目标建议也
    只在确有把握时给出（见 suggest_reply_language）。
    """
    if not text:
        return "other"
    counts = script_counts(text)
    if counts["kana"]:
        return "ja"
    if counts["hangul"]:
        return "ko"
    if counts["cyrillic"]:
        return "uk" if _UKRAINIAN_EXTRA_RE.search(text) else "ru"
    if counts["arabic"]:
        return "ar"
    if counts["hebrew"]:
        return "he"
    if counts["thai"]:
        return "th"
    if counts["devanagari"]:
        return "hi"
    if counts["cjk"]:
        # 有汉字且没有假名：按主导比例判断
        return "zh" if chinese_ratio(text) >= 0.3 else "other"
    if counts["latin"]:
        return "en"
    return "other"


def is_chinese_dominant(text: str, threshold: float = 0.3) -> bool:
    counts = script_counts(text)
    letters = sum(counts.values())
    if letters == 0:
        return False
    if counts["kana"] or counts["hangul"] or counts["cyrillic"]:
        return False
    return counts["cjk"] / letters >= threshold


def needs_translation(text: str, enabled_languages) -> bool:
    """判断一条消息是否需要翻译。

    规则：
    - 空消息 / 纯标点与数字 -> 不翻译；
    - 中文占主导 -> 不翻译（保留原文）；
    - 中英混合但外语占主导 -> 翻译（非中文部分由模型处理）；
    - 检测到的语言在用户启用列表中 -> 翻译；'other' 作为兜底。
    """
    if not text or not text.strip():
        return False
    stripped = text.strip()
    counts = script_counts(stripped)
    letters = sum(counts.values())
    if letters == 0:
        return False  # 纯数字/标点/表情
    if is_chinese_dominant(stripped):
        return False
    lang = detect_primary_language(stripped)
    if lang == "zh":
        return False
    enabled = {str(x).lower() for x in (enabled_languages or [])}
    if lang in enabled:
        return True
    if lang == "other" and "other" in enabled:
        return True
    return False


def suggest_reply_language(last_message_text: str | None) -> str | None:
    """根据最近一条已识别聊天消息建议回复目标语言。

    只在确有把握时给出建议（俄语/日语/韩语等特征明确的文字）；
    中文、英语或无法判断时返回 None，由调用方使用用户默认值。
    """
    if not last_message_text:
        return None
    lang = detect_primary_language(last_message_text)
    if lang in ("ru", "uk", "ja", "ko", "ar", "he", "th", "hi"):
        return lang
    return None
