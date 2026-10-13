"""原文标准化与缓存键计算。

标准化原则（保守）：
- 去除不影响语义的首尾空白；
- 统一换行格式（CRLF/CR -> LF）；
- 折叠连续的水平空白为单个空格；
- 清除 CS2 引擎日志可能插入的零宽字符；
- 默认不删除否定词、数字、点位、武器名或方向词；
- 不把语义不同的句子合并到同一个缓存键；
- 不把发送者姓名等无关元数据混入原文（发送者由解析层分离）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# 缓存结构版本：更改此值会使所有既有缓存条目自然失效
# （数据库查询按 cache_version 过滤，旧版本数据保留但不再命中）。
CACHE_SCHEMA_VERSION = "1"

# 零宽字符 / BOM：CS2 控制台日志中会混入 U+200E 等不可见字符
_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff]")

# 连续水平空白（不含换行）
_HSPACE_RE = re.compile(r"[ \t]+")

# 翻译方向
DIRECTION_INCOMING = "incoming"  # 外语 -> 简体中文
DIRECTION_REPLY = "reply"        # 简体中文 -> 目标语言


@dataclass(frozen=True)
class NormalizedText:
    """标准化结果：既保留用于哈希的规范化文本，也保留去除零宽字符后的原文。"""

    original: str   # 去零宽字符、统一换行后的原文（保留首尾空白以外的差异）
    normalized: str  # 进一步 strip + 空白折叠后的文本（缓存键依据）
    hash: str        # normalized 的 SHA-256


def normalize_source_text(text: str) -> NormalizedText:
    """保守标准化。空字符串返回空结果。"""
    if not text:
        return NormalizedText(original="", normalized="", hash="")
    cleaned = _ZERO_WIDTH_RE.sub("", text)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    original = cleaned
    normalized = cleaned.strip()
    normalized = _HSPACE_RE.sub(" ", normalized)
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = normalized.strip()
    return NormalizedText(
        original=original,
        normalized=normalized,
        hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


def normalize_source(text: str) -> str:
    """仅返回标准化文本（便捷函数）。"""
    return normalize_source_text(text).normalized


def make_cache_key(text: str, target_language: str, direction: str) -> str:
    """生成稳定缓存键。

    组成：缓存结构版本 + 翻译方向 + 目标语言 + 标准化原文。
    同一原文翻译成不同目标语言 / 不同方向会得到不同键，不会串用。
    """
    normalized = normalize_source(text)
    payload = "\x00".join(
        [CACHE_SCHEMA_VERSION, direction, normalize_language(target_language), normalized]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_language(language: str) -> str:
    """语言代码小写并去除空白（'EN ' -> 'en'）。"""
    return (language or "").strip().lower()


def texts_equivalent(a: str, b: str) -> bool:
    """两句标准化后是否完全相同（保守相等判断）。"""
    return normalize_source(a) == normalize_source(b)
