"""聊天解析器测试：覆盖需求 5.3 与测试清单 1-8、12。"""

from __future__ import annotations

import pytest

from cs2_translator.chat_parser import ChatParser, ChatMessage


@pytest.fixture
def parser():
    return ChatParser()


# 来自公开项目实测的真实 CS2 console.log 格式样本
REAL_SAMPLES = [
    "08/28 18:00:28  [ALL] LITE | котость: ахпахпап",
    "08/28 18:02:15  [ALL] Bot David: bonjour",
    "08/28 18:05:53  [ALL] cadena de [DEAD]: убей его уже",
    "08/28 18:06:19  [CT] pikKo [DEAD]: не тянр",
]

# 真实 console.log 中带有「全角冒号 U+FF1A」的聊天行。
# 中文/日文输入法环境或含非 ASCII 昵称时，CS2 会写出全角冒号；若不接受，
# 玩家自己的消息会完全无法识别（这是真实日志里验证到的问题）。
REAL_FULLWIDTH_SAMPLES = [
    ("10/13 10:16:51  [ALL] 慢拔out急拔in\u200e： hi bro", "hi bro", "ALL"),
    ("10/13 10:17:34  [ALL] 慢拔out急拔in\u200e： cheater", "cheater", "ALL"),
    ("10/13 10:19:11  [ALL] 慢拔out急拔in\u200e： hi", "hi", "ALL"),
    ("10/13 10:22:59  [ALL] 慢拔out急拔in\u200e： Чё?", "Чё?", "ALL"),
]


class TestFullwidthColon:
    """全角冒号（U+FF1A）必须被接受，否则玩家自己的消息无法翻译。"""

    @pytest.mark.parametrize("line,expected_content,expected_channel", REAL_FULLWIDTH_SAMPLES)
    def test_real_fullwidth_lines_are_parsed(self, parser, line, expected_content, expected_channel):
        msg = parser.parse(line)
        assert msg is not None, f"未识别全角冒号聊天行: {line!r}"
        assert msg.content == expected_content
        assert msg.channel == expected_channel
        assert msg.timestamp == line[:14]

    def test_zero_width_char_stripped_from_sender(self, parser):
        msg = parser.parse("10/13 10:16:51  [ALL] 慢拔out急拔in\u200e： hi bro")
        assert msg is not None
        # U+200E（从左至右标记）属于零宽字符，必须清除
        assert "\u200e" not in msg.sender

    def test_halfwidth_colon_still_works(self, parser):
        msg = parser.parse("[ALL] Player: hello")
        assert msg is not None and msg.content == "hello"

    def test_fullwidth_in_dead_only_form(self, parser):
        msg = parser.parse("[DEAD] 某玩家： gg")
        assert msg is not None
        assert msg.content == "gg"
        assert msg.is_dead is True


class TestRealFormat:
    def test_english_chat(self, parser):
        msg = parser.parse("[ALL] Player One: rush B now!")
        assert msg is not None
        assert msg.sender == "Player One"
        assert msg.content == "rush B now!"
        assert msg.channel == "ALL"

    def test_russian_chat(self, parser):
        msg = parser.parse("08/28 18:00:28  [ALL] LITE | котость: ахпахпап")
        assert msg is not None
        assert msg.sender == "LITE | котость"
        assert msg.content == "ахпахпап"
        assert msg.channel == "ALL"
        assert msg.timestamp == "08/28 18:00:28"

    def test_chinese_message_preserved(self, parser):
        msg = parser.parse("[ALL] 小明: 大家好呀")
        assert msg is not None
        assert msg.content == "大家好呀"

    def test_mixed_chinese_english(self, parser):
        msg = parser.parse("[ALL] Mixer: rush B 冲！")
        assert msg is not None
        assert msg.content == "rush B 冲！"

    def test_player_name_with_spaces(self, parser):
        msg = parser.parse("[ALL] Bot David: bonjour")
        assert msg is not None
        assert msg.sender == "Bot David"

    def test_player_name_with_pipe_and_spaces(self, parser):
        msg = parser.parse("[ALL] cadena de [DEAD]: убей его уже")
        assert msg.sender == "cadena de"
        assert msg.is_dead is True
        assert msg.channel == "ALL"
        assert msg.channel_label == "全体·死亡"

    def test_content_contains_colons(self, parser):
        msg = parser.parse("[ALL] Linker: check this: http://example.com ok")
        assert msg is not None
        assert msg.sender == "Linker"
        assert msg.content == "check this: http://example.com ok"

    def test_team_chat_ct_tag(self, parser):
        msg = parser.parse("[CT] s1mple: rotate mid")
        assert msg is not None
        assert msg.channel == "TEAM"
        assert msg.channel_label == "队伍"

    def test_team_chat_t_tag(self, parser):
        msg = parser.parse("[T] TerrorGuy: go go go")
        assert msg is not None
        assert msg.channel == "TEAM"

    def test_bare_all_tag(self, parser):
        msg = parser.parse("ALL Ivan: gg wp")
        assert msg is not None
        assert msg.channel == "ALL"
        assert msg.sender == "Ivan"
        assert msg.content == "gg wp"

    def test_spectator_channel(self, parser):
        msg = parser.parse("[Spectator] Watcher: nice shot")
        assert msg is not None
        assert msg.channel == "SPECTATOR"

    def test_dead_prefix_tag(self, parser):
        msg = parser.parse("*DEAD* [ALL] Ghost: boo")
        assert msg is not None
        assert msg.is_dead is True
        assert msg.sender == "Ghost"

    def test_dead_only_tag(self, parser):
        msg = parser.parse("[DEAD] Ghost: still here")
        assert msg is not None
        assert msg.is_dead is True
        assert msg.sender == "Ghost"

    def test_zero_width_chars_stripped(self, parser):
        msg = parser.parse("[ALL] Na\u200eme: he\u200bllo")
        assert msg is not None
        assert msg.sender == "Name"
        assert msg.content == "hello"

    def test_timestamp_iso_format(self, parser):
        msg = parser.parse("2025-08-28 18:00:28 [ALL] Nick: hi")
        assert msg is not None
        assert msg.timestamp == "2025-08-28 18:00:28"

    def test_all_real_samples_parse(self, parser):
        for line in REAL_SAMPLES:
            assert parser.parse(line) is not None, line


class TestSystemNoise:
    ENGINE_LINES = [
        "Executing command: connect 127.0.0.1",
        "WARNING: Sound system not initialized",
        "ERROR: Failed to load model",
        " L 03/12/2025 - 12:00:00: Server tried to send out of band",
        'Client "Bot" connected, address "127.0.0.1"',
        "Server is hibernating",
        "ConVarRef cl_updaterate doesn't point to an existing ConVar",
        "Network: connection lost",
        "Dropped Bot from server",
        "----- Loading Map de_dust2 -----",
        "compression: SDR bytes 1234",
        "CVARS: sv_cheats 0",
        "",
        "   ",
        "\t",
    ]

    @pytest.mark.parametrize("line", ENGINE_LINES)
    def test_engine_lines_not_matched(self, parser, line):
        assert parser.parse(line) is None, line

    def test_control_characters_rejected(self, parser):
        assert parser.parse("[ALL] Evil\x07Name: msg") is None

    def test_empty_sender_rejected(self, parser):
        assert parser.parse("[ALL] : message") is None

    def test_empty_content_rejected(self, parser):
        assert parser.parse("[ALL] Name:") is None

    def test_overlong_line_rejected(self, parser):
        long_line = "[ALL] Name: " + "x" * 3000
        assert parser.parse(long_line) is None


class TestExtensibility:
    def test_extra_custom_pattern(self):
        parser = ChatParser(extra_patterns=[r"^CHAT (?P<sender>[^:]+): (?P<content>.+)$"])
        msg = parser.parse("CHAT 服务器: 欢迎来到服务器")
        assert msg is not None
        assert msg.sender == "服务器"
        assert msg.content == "欢迎来到服务器"

    def test_invalid_extra_pattern_ignored(self):
        parser = ChatParser(extra_patterns=["[unclosed"])
        # 无效正则被忽略，默认模式仍可用
        assert parser.parse("[ALL] Nick: hi") is not None

    def test_generic_pattern_disabled_by_default(self, parser):
        # 无频道标签的行默认不识别（保守策略）
        assert parser.parse("RandomPlayer: hello") is None

    def test_generic_pattern_when_enabled(self):
        parser = ChatParser(enable_generic=True)
        msg = parser.parse("RandomPlayer: hello there")
        assert msg is not None
        assert msg.sender == "RandomPlayer"
        assert msg.content == "hello there"

    def test_generic_pattern_still_rejects_system_lines(self):
        parser = ChatParser(enable_generic=True)
        assert parser.parse("Executing command: connect") is None
        assert parser.parse("WARNING: something") is None

    def test_generic_pattern_rejects_sender_without_letters(self):
        parser = ChatParser(enable_generic=True)
        assert parser.parse("12345: hello") is None


class TestDiagnoseMode:
    def test_diagnose_local_only(self, parser):
        lines = [
            "[ALL] A: hi",
            "WARNING: noise",
            "[TEAM] B: go",
            "",
            "executing something",
        ]
        messages, unmatched, stats = ChatParser.diagnose(lines)
        assert stats["total_lines"] == 5
        assert stats["chat_messages"] == 2
        assert stats["unmatched_lines"] == 2
        assert [m.sender for m in messages] == ["A", "B"]
        assert unmatched == ["WARNING: noise", "executing something"]
