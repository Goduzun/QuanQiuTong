"""配置与存储测试。"""

from __future__ import annotations

import json
import os

import pytest

from cs2_translator.config import AppConfig, ConfigManager, user_data_dir
from cs2_translator.storage import SecretStore, atomic_write_json


@pytest.fixture
def config_manager(tmp_path):
    return ConfigManager(data_dir=tmp_path / "data")


class TestAppConfig:
    def test_to_dict_excludes_api_key(self):
        cfg = AppConfig(api_key="sk-secret")
        data = cfg.to_dict()
        assert "api_key" not in data
        assert "sk-secret" not in json.dumps(data)

    def test_from_dict_ignores_unknown_keys(self):
        cfg = AppConfig.from_dict({"unknown_field": 1, "model": "gpt-4o", "cs2_log_path": "/x"})
        assert cfg.model == "gpt-4o"
        assert cfg.cs2_log_path == "/x"

    def test_from_dict_coerces_bad_types(self):
        cfg = AppConfig.from_dict(
            {"timeout_seconds": "abc", "rate_limit_per_minute": None, "queue_max_size": "50"}
        )
        assert cfg.timeout_seconds == 20.0  # 非法值回退默认
        assert cfg.rate_limit_per_minute == 30
        assert cfg.queue_max_size == 50

    def test_first_run_detection(self):
        assert AppConfig().is_first_run is True
        assert AppConfig(cs2_log_path="/x", api_key="sk-1", first_run_completed=True).is_first_run is False


class TestConfigManager:
    def test_save_and_load_roundtrip(self, config_manager):
        cfg = AppConfig(
            cs2_log_path="/tmp/console.log",
            api_key="sk-test-123",
            model="gpt-4o-mini",
            reply_target_language="ru",
            auto_suggest_reply_language=True,
        )
        config_manager.save(cfg)
        loaded = config_manager.load()
        assert loaded.cs2_log_path == "/tmp/console.log"
        assert loaded.reply_target_language == "ru"
        assert loaded.auto_suggest_reply_language is True

    def test_api_key_not_in_config_file(self, config_manager):
        cfg = AppConfig(cs2_log_path="/x", api_key="sk-ultra-secret")
        config_manager.save(cfg)
        raw = config_manager.config_path.read_text(encoding="utf-8")
        assert "sk-ultra-secret" not in raw

    def test_api_key_recovered_from_secret_store(self, config_manager):
        cfg = AppConfig(cs2_log_path="/x", api_key="sk-recover-me")
        config_manager.save(cfg)
        assert config_manager.load().api_key == "sk-recover-me"

    def test_env_overrides(self, config_manager, monkeypatch):
        monkeypatch.setenv("CS2_LOG_PATH", "/env/path.log")
        monkeypatch.setenv("CS2_MODEL", "env-model")
        loaded = config_manager.load()
        assert loaded.cs2_log_path == "/env/path.log"
        assert loaded.model == "env-model"

    def test_corrupt_config_falls_back(self, config_manager):
        config_manager.data_dir.mkdir(parents=True, exist_ok=True)
        config_manager.config_path.write_text("{ broken", encoding="utf-8")
        cfg = config_manager.load()
        assert isinstance(cfg, AppConfig)

    def test_auto_detect_returns_list(self, config_manager):
        candidates = config_manager.auto_detect_log_paths()
        assert isinstance(candidates, list)

    def test_user_data_dir_is_not_exe_dir(self):
        # 配置目录必须在用户目录，而不是 EXE 所在目录
        assert "QuanQiuTong" in str(user_data_dir()) or "quanqiutong-cs2" in str(user_data_dir())


class TestSecretStore:
    def test_plain_fallback_roundtrip(self, tmp_path):
        store = SecretStore(tmp_path / "secrets.json")
        store.set("api_key", "sk-plain-123")
        assert store.get("api_key") == "sk-plain-123"
        store.delete("api_key")
        assert store.get("api_key") is None

    def test_missing_file_returns_none(self, tmp_path):
        store = SecretStore(tmp_path / "nope.json")
        assert store.get("api_key") is None

    def test_corrupt_file_returns_none(self, tmp_path):
        path = tmp_path / "secrets.json"
        path.write_text("not json", encoding="utf-8")
        assert SecretStore(path).get("api_key") is None


class TestAtomicWrite:
    def test_atomic_write_json(self, tmp_path):
        path = tmp_path / "cfg.json"
        atomic_write_json(path, {"a": 1})
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
        # 不残留临时文件
        assert [p.name for p in tmp_path.iterdir()] == ["cfg.json"]

    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "cfg.json"
        atomic_write_json(path, {"ok": True})
        assert path.is_file()


class TestConfigBomTolerance:
    """設定檔帶 UTF-8 BOM 時不得被靜默丟棄。

    真實事故：PowerShell 的 Set-Content 會給 JSON 加 BOM，
    json.loads 直接拋 JSONDecodeError，整份設定被回退成預設值，
    使用者表現為「模型與 Base URL 突然變回預設、API 不可用」。
    """

    def test_load_config_with_utf8_bom(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        manager = ConfigManager(data_dir=data_dir)

        payload = {
            "model": "deepseek-flash",
            "api_base_url": "https://api.deepseek.com",
            "cs2_log_path": "/x/console.log",
            "reset_on_startup": True,
            "read_history_on_start": False,
        }
        raw = b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8")
        manager.config_path.write_bytes(raw)

        cfg = manager.load()
        assert cfg.model == "deepseek-flash", "BOM 導致模型被回退成預設值"
        assert cfg.api_base_url == "https://api.deepseek.com"
        assert cfg.cs2_log_path == "/x/console.log"
        assert cfg.reset_on_startup is True
        assert cfg.read_history_on_start is False

    def test_saved_config_has_no_bom(self, tmp_path):
        manager = ConfigManager(data_dir=tmp_path / "data")
        cfg = manager.load()
        cfg.model = "custom-model"
        manager.save(cfg)

        assert not manager.config_path.read_bytes().startswith(b"\xef\xbb\xbf")
        assert manager.load().model == "custom-model"

    def test_corrupt_config_falls_back_to_defaults(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        manager = ConfigManager(data_dir=data_dir)
        manager.config_path.write_bytes(b"{ this is not json ")

        cfg = manager.load()  # 不應拋例外
        assert cfg.model == "gpt-4o-mini"
