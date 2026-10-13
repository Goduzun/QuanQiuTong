"""本地翻译记忆库（SQLite 持久化）。

模块划分：
- normalize    保守标准化与稳定缓存键
- db           SQLite 连接、WAL 与迁移
- templates    带变量校验的句式模板复用
- singleflight 并发同句单飞，避免重复 API 请求
- store         TranslationMemory：查询优先级 / 保存 / 纠错 / 术语 / 统计
- match        对局（match）生命周期与三层数据隔离
"""

from .match import (
    DEFAULT_HISTORY_MATCH_LIMIT,
    DEFAULT_MATCH_MESSAGE_LIMIT,
    MATCH_ACTIVE,
    MATCH_ENDED,
    SOURCE_AUTO,
    SOURCE_MANUAL,
    MatchInfo,
    MatchManager,
    MatchMessage,
    detect_match_signal,
    extract_map_name,
)
from .normalize import (
    CACHE_SCHEMA_VERSION,
    DIRECTION_INCOMING,
    DIRECTION_REPLY,
    make_cache_key,
    normalize_source,
    normalize_source_text,
    texts_equivalent,
)
from .singleflight import SingleFlight
from .store import (
    HIT_CORRECTION,
    HIT_EXACT,
    HIT_TEMPLATE,
    HIT_TERM,
    LookupResult,
    TranslationMemory,
)

__all__ = [
    "CACHE_SCHEMA_VERSION",
    "DEFAULT_HISTORY_MATCH_LIMIT",
    "DEFAULT_MATCH_MESSAGE_LIMIT",
    "DIRECTION_INCOMING",
    "DIRECTION_REPLY",
    "HIT_CORRECTION",
    "HIT_EXACT",
    "HIT_TEMPLATE",
    "HIT_TERM",
    "LookupResult",
    "MATCH_ACTIVE",
    "MATCH_ENDED",
    "MatchInfo",
    "MatchManager",
    "MatchMessage",
    "SOURCE_AUTO",
    "SOURCE_MANUAL",
    "SingleFlight",
    "TranslationMemory",
    "detect_match_signal",
    "extract_map_name",
    "make_cache_key",
    "normalize_source",
    "normalize_source_text",
    "texts_equivalent",
]
