"""CS2 console.log 聊天消息识别。

日志形态说明（依据 CS2 自身在 `-condebug` 启动参数下写出的 console.log
的文本结构整理而成；示例行是自造的合成数据，不含任何真实玩家信息）：

    01/02 03:04:05  [ALL] Alpha: attack B now
    01/02 03:04:06  [ALL] Bravo: bonjour a tous
    01/02 03:04:07  [ALL] Charlie [DEAD]: rotate to A
    01/02 03:04:08  [CT] Delta [DEAD]: hold the site

特征：
- 每行可选 "MM/DD HH:MM:SS" 时间戳前缀；
- 频道标签：[ALL] 全体、[CT]/[T]/[TEAM] 队伍、[Spectator]/[SPEC] 旁观；
- 死亡玩家消息：[DEAD] 标签在玩家名之后、冒号之前，或 *DEAD* 在名字前；
- 部分区域设置下 CS2 会在玩家名后插入不可见 LTR 标记（U+200E），需要清除。

本解析器刻意保守：只有命中已知聊天形态的行才会被识别为聊天，
其余引擎/服务器/网络日志一律忽略，避免把系统输出误翻译。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("cs2_translator.chat_parser")

# 不可见/双向格式化字符
_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff]")

# 可选时间戳前缀（美式 MM/DD 或 ISO）
_TIMESTAMP = (
    r"(?:(?P<ts>\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}:\d{2}"
    r"|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+)?"
)

# 死亡标记：名字前 *DEAD* 或 [DEAD]
_DEAD_PREFIX = r"(?:(?P<dead1>\*DEAD\*|\[DEAD\])\s*)?"

# 频道标签
_CHANNELS = r"ALL|CT|T|TEAM|Spectator|SPEC"

_CHANNEL_MAP = {
    "ALL": "ALL",
    "CT": "TEAM",
    "T": "TEAM",
    "TEAM": "TEAM",
    "SPECTATOR": "SPECTATOR",
    "SPEC": "SPECTATOR",
}

_CHANNEL_LABELS = {
    "ALL": "全体",
    "TEAM": "队伍",
    "SPECTATOR": "旁观",
    "DEAD": "死亡",
    "UNKNOWN": "未知",
}

MAX_SENDER_LEN = 64
MAX_LINE_LEN = 2000

# 分隔符：真实 CS2 日志中既有半角冒号 ":"，也有全角冒号 "："（U+FF1A）。
# 例如中文/日文输入法环境或含非 ASCII 昵称时，游戏会写出全角冒号：
#     10/13 10:16:51  [ALL] 玩家名： hi bro
# 因此两种冒号都必须接受，否则玩家自己的消息会完全无法识别。
_SEP = r"[:\uff1a]"

# 形态一（最常见）：[频道] 玩家名（可选 [DEAD]）: 消息
_BRACKET_RE = re.compile(
    r"^" + _TIMESTAMP + r"\s*" + _DEAD_PREFIX
    + r"\[(?P<channel>" + _CHANNELS + r")\]\s*"
    + r"(?P<sender>.+?)\s*(?:\[(?P<dead2>DEAD)\])?\s*" + _SEP + r"\s*(?P<content>.+?)\s*$",
    re.IGNORECASE,
)

# 形态二：无方括号的裸标签，如 "ALL Ivan: gg" / "TEAM s1mple: hi"
_BARE_RE = re.compile(
    r"^" + _TIMESTAMP + r"\s*" + _DEAD_PREFIX
    + r"(?P<channel>ALL|TEAM)\s+(?P<sender>.+?)\s*(?:\[(?P<dead2>DEAD)\])?\s*"
    + _SEP + r"\s*(?P<content>.+?)\s*$",
    re.IGNORECASE,
)

# 形态三：仅死亡标签，如 "[DEAD] Name: 消息"
_DEAD_ONLY_RE = re.compile(
    r"^" + _TIMESTAMP + r"\s*\[(?P<dead3>DEAD)\]\s*(?P<sender>.+?)\s*"
    + _SEP + r"\s*(?P<content>.+?)\s*$",
    re.IGNORECASE,
)

# 形态四（默认关闭，需用户在设置中启用）：无任何频道标签的 "玩家名: 消息"
_GENERIC_RE = re.compile(
    r"^" + _TIMESTAMP
    + r"(?P<sender>[^:\uff1a\[\]<>]{1,32})\s*" + _SEP + r"\s*(?P<content>\S.*?)\s*$"
)

# 引擎/服务器/网络等系统日志特征（用于通用形态的二次校验）
_SYSTEM_LINE_RE = re.compile(
    r"(?i)^\s*(?:"
    r"warning|error|\[error\]|executing|unknown command|convars|cvars|"
    r"server_|client_|sv_|cl_|net_|snd_|r_|mat_|m_|budget|fps|frame|tick|"
    r"conn|signon|spawn|loading|map |changelevel|disconnect|reconnect|dropped|"
    r"connecting|connection|network|steam|vac|uid|banid|kick|client |server |"
    r"console|demo|playdemo|record|precache|model|material|shader|texture|"
    r"vertex|render|sound|voice|video|input|keyboard|mouse|sensitivity|volume|"
    r"resolution|fullscreen|display|physics|script|entity|hitbox|damage|score|"
    r"team |round |bomb |defuse|hostage|weapon|ammo|reload|recoil|spread|scope|"
    r"flash|smoke|molotov|grenade|jump|duck|crouch|walk|buy|money|inventory|"
    r"loadout|skin|sticker|gloves|agent|music|kit|graffiti|patch|case|key |"
    r"crate|operation|pass|mission|quest|star|coin|pin|trophy|medal|badge|"
    r"level|rank|xp|premier|wingman|danger|zone|competitive|casual|deathmatch|"
    r"warmup|knife|surf|retake|quota|thread|async|static|prop|world|skybox|"
    r"lightmap|lighting|ambient|occlusion|reflect|refraction|post|bloom|tonemap|"
    r"gamma|contrast|saturation|sharpen|film|grain|vignette|chromatic|aberration|"
    r"motion|blur|depth|field|view|focus|aperture|exposure|shutter|iso|sensor|"
    r"cmd|command|alias|bind|unbind|echo|say|say_team|quit|exit|status|connect|"
    r"heartbeat|ping|rate|updaterate|cmdrate|interp|lagcompensation|predict|"
    r"cheats|pure|lan|region|steamid|friends|avatar|nickname|cpu|gpu|memory|"
    r"crash|assert|exception|segfault|leak|malloc|heap|stack|reloading|"
    r"-----|====|====|downloading|initializing|initialised|initialized|"
    r"starting|stopping|shutting|rebooting|connecting to|waiting for"
    r")"
)

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class ChatMessage:
    """一条已识别的聊天消息。"""

    sender: str
    content: str
    channel: str = "UNKNOWN"
    is_dead: bool = False
    timestamp: str | None = None
    raw: str = ""
    source_language: str | None = None  # 识别后由引擎填充

    @property
    def channel_label(self) -> str:
        label = _CHANNEL_LABELS.get(self.channel, "未知")
        if self.is_dead and self.channel != "DEAD":
            return f"{label}·死亡"
        return label

    def to_dict(self) -> dict:
        return {
            "sender": self.sender,
            "content": self.content,
            "channel": self.channel,
            "channel_label": self.channel_label,
            "is_dead": self.is_dead,
            "timestamp": self.timestamp,
            "source_language": self.source_language,
        }


def _clean(text: str) -> str:
    return _ZERO_WIDTH_RE.sub("", text).strip()


def _contains_control_chars(text: str) -> bool:
    return bool(_CONTROL_CHARS_RE.search(text))


class ChatParser:
    """聊天行解析器。可通过 extra_patterns 扩展新日志格式。"""

    def __init__(self, extra_patterns=None, enable_generic: bool = False):
        self._patterns = [_BRACKET_RE, _BARE_RE, _DEAD_ONLY_RE]
        self.enable_generic = enable_generic
        for pattern in extra_patterns or []:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                logger.warning("忽略无效的自定义聊天正则 %r: %s", pattern, exc)
                continue
            self._patterns.append(compiled)

    # ------------------------------------------------------------------
    def parse(self, line: str):
        """把一行日志解析为 ChatMessage；不是聊天则返回 None。"""
        if not line:
            return None
        line = line.rstrip("\r\n")
        if not line.strip() or len(line) > MAX_LINE_LEN:
            return None
        if _contains_control_chars(line):
            return None

        for pattern in self._patterns:
            match = pattern.match(line)
            if not match:
                continue
            groups = match.groupdict()
            sender = _clean(groups.get("sender") or "")
            content = _clean(groups.get("content") or "")
            if not sender or not content:
                continue
            if len(sender) > MAX_SENDER_LEN:
                continue
            if _contains_control_chars(sender) or _contains_control_chars(content):
                continue
            channel_raw = (groups.get("channel") or "").strip().upper()
            channel = _CHANNEL_MAP.get(channel_raw, "UNKNOWN")
            is_dead = bool(groups.get("dead1") or groups.get("dead2") or groups.get("dead3"))
            if channel_raw == "DEAD":
                is_dead = True
                channel = "UNKNOWN"
            timestamp = (groups.get("ts") or "").strip() or None
            return ChatMessage(
                sender=sender,
                content=content,
                channel=channel,
                is_dead=is_dead,
                timestamp=timestamp,
                raw=line,
            )

        if self.enable_generic:
            return self._parse_generic(line)
        return None

    def _parse_generic(self, line: str):
        if _SYSTEM_LINE_RE.match(line):
            return None
        match = _GENERIC_RE.match(line)
        if not match:
            return None
        sender = _clean(match.group("sender"))
        content = _clean(match.group("content"))
        if not sender or not content:
            return None
        if not re.search(r"[A-Za-z\u00c0-\u024f\u0400-\u04ff\u3040-\u30ff\uac00-\ud7af]", sender):
            return None  # 发送者至少要含有文字字符
        if _SYSTEM_LINE_RE.match(sender + ":"):
            return None
        return ChatMessage(
            sender=sender,
            content=content,
            channel="UNKNOWN",
            timestamp=(match.groupdict().get("ts") or "").strip() or None,
            raw=line,
        )

    # ------------------------------------------------------------------
    def looks_like_system(self, line: str) -> bool:
        return bool(_SYSTEM_LINE_RE.match(line or ""))

    @staticmethod
    def diagnose(lines):
        """本地日志诊断：返回 (识别到的聊天, 未识别行, 统计)。仅本地分析，不上传。"""
        messages, unmatched = [], []
        total = 0
        for line in lines:
            total += 1
            msg = ChatParser().parse(line)
            if msg is not None:
                messages.append(msg)
            elif line.strip():
                unmatched.append(line)
        stats = {
            "total_lines": total,
            "chat_messages": len(messages),
            "unmatched_lines": len(unmatched),
        }
        return messages, unmatched, stats
