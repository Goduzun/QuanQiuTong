"""命令行入口测试：--version / --diagnose / --reply / --cli / --reset-config。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

import app as app_module
from cs2_translator.config import AppConfig, ConfigManager

FIXTURE_LOG = PROJECT_ROOT / "tests" / "fixtures" / "sample_console.log"


def test_version(capsys):
    assert app_module.main(["--version"]) == 0
    from cs2_translator import APP_DISPLAY_NAME

    assert APP_DISPLAY_NAME in capsys.readouterr().out


def test_diagnose_fixture_log(capsys):
    assert app_module.main(["--diagnose", str(FIXTURE_LOG)]) == 0
    out = capsys.readouterr().out
    assert "识别到的聊天消息" in out
    assert "rush B now!" in out  # 英文聊天被识别
    assert "ахпахпап" in out  # 俄文聊天被识别
    assert "大家好" in out  # 中文消息被识别（是否翻译由语言模块决定）


def test_diagnose_missing_file(capsys):
    assert app_module.main(["--diagnose", "/nonexistent/console.log"]) == 2
    assert "不存在" in capsys.readouterr().out


def test_diagnose_reports_no_chat(capsys, tmp_path):
    log = tmp_path / "noise.log"
    log.write_text("WARNING: only noise\nExecuting command: quit\n", encoding="utf-8")
    assert app_module.main(["--diagnose", str(log)]) == 0
    out = capsys.readouterr().out
    assert "chat_messages: 0" in out


def test_reply_mode(monkeypatch, capsys, tmp_path):
    from cs2_translator.reply_translator import ReplyTranslator

    monkeypatch.setattr(ReplyTranslator, "translate", lambda self, text, lang: "rush B, follow me")
    code = app_module.main(["--reply", "冲B点，跟我走", "--to", "en", "--data-dir", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "冲B点，跟我走" in out
    assert "rush B, follow me" in out


def test_reply_mode_error(monkeypatch, capsys, tmp_path):
    from cs2_translator.reply_translator import ReplyTranslator
    from cs2_translator.translator import AuthError

    def broken(self, text, lang):
        raise AuthError("API Key 无效")

    monkeypatch.setattr(ReplyTranslator, "translate", broken)
    code = app_module.main(["--reply", "你好", "--data-dir", str(tmp_path)])
    assert code == 3
    assert "API Key" in capsys.readouterr().out


def test_cli_without_config_returns_error(capsys, tmp_path):
    code = app_module.main(["--cli", "--data-dir", str(tmp_path / "empty")])
    assert code == 2
    out = capsys.readouterr().out
    assert "日志" in out


def test_reset_config(capsys, tmp_path):
    manager = ConfigManager(data_dir=tmp_path)
    manager.save(AppConfig(cs2_log_path="/x", api_key="sk-1"))
    assert manager.config_path.is_file()
    code = app_module.main(["--reset-config", "--data-dir", str(tmp_path)])
    assert code == 0
    assert not manager.config_path.exists()
