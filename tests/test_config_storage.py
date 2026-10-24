"""配置与存储测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cs2_translator.config import AppConfig, ConfigManager, user_data_dir
from cs2_translator.storage import SecretStore, atomic_write_json

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def config_manager(tmp_path):
    return ConfigManager(data_dir=tmp_path / "data")


class TestPackagingSpec:
    """打包 spec 的回歸保護。

    真實事故：瘦身時把 libssl-1_1.dll / libcrypto-1_1.dll 列入排除清單，
    誤以為「Python 3.11 的 ssl 使用自帶 OpenSSL 3.x」。實際上這個 Python
    的 _ssl.pyd 直接匯入 libcrypto-1_1/libssl-1_1（OpenSSL 1.1.1u），
    排除後 _ssl 載入失敗 → openai 無法 import → 翻譯功能全廢，
    GUI 只顯示一行「openai 库不可用: DLL load failed while importing _ssl」。
    因此這裡鎖住：關鍵 OpenSSL DLL 絕不可出現在排除清單裡。
    """

    # 這些 DLL 是執行期必須，任何 spec 都不得排除
    MUST_NOT_DROP = ("libssl-1_1", "libcrypto-1_1")

    @staticmethod
    def _spec_text() -> str:
        spec = PROJECT_ROOT / "QuanQiuTong-onefile.spec"
        assert spec.is_file(), f"找不到 {spec}"
        return spec.read_text(encoding="utf-8")

    def test_spec_does_not_exclude_openssl_dlls(self):
        text = self._spec_text()
        for must in self.MUST_NOT_DROP:
            # 檢查是否被當成排除項寫進 BINARY_DROP（用引號包住的字串）
            banned = f'"{must}"'
            assert banned not in text, (
                f"spec 的排除清單含 {must}！Python 的 _ssl.pyd 依賴它，"
                f"排除後 openai 無法使用。"
            )

    def test_spec_explains_the_pitfall(self):
        """排除清單旁必須留有警告，避免後人重犯。"""
        text = self._spec_text()
        assert "绝不可排除" in text or "不可移除" in text, \
            "spec 應保留「不可排除 OpenSSL」的警告註解"

    def test_spec_keeps_required_qt_modules(self):
        """必用的 Qt 模組不得被排除。"""
        text = self._spec_text()
        for must in ("QtCore", "QtGui", "QtWidgets"):
            assert f'"PySide6.{must}"' not in text, f"{must} 是必需模組，不該被排除"

    def test_spec_keeps_qtnetwork_for_single_instance(self):
        """QtNetwork 不得被排除 —— 單實例鎖用 QLocalServer/QLocalSocket。

        真實事故：瘦身清單把 PySide6.QtNetwork 與 Qt6Network 一起移除，
        本機以原始碼跑測試完全正常（模組從 site-packages 載入），
        但**打包版一啟動就 ImportError**，單實例保護直接失效。
        這種「只在打包後才炸」的缺陷必須靠 spec 靜態檢查擋住。
        """
        text = self._spec_text()
        assert '"PySide6.QtNetwork"' not in text, \
            "spec 排除了 PySide6.QtNetwork，單實例鎖會失效並導致打包版啟動失敗"
        assert '"Qt6Network"' not in text, \
            "spec 排除了 Qt6Network DLL，QLocalServer 無法運作"

    def test_single_instance_module_imports(self):
        """單實例模組必須可 import（含 QtNetwork）。"""
        from cs2_translator.single_instance import SingleInstanceGuard, instance_key

        assert callable(SingleInstanceGuard)
        assert callable(instance_key)

    def test_diagnose_self_check_covers_deps(self):
        """app.py 的 --diagnose 必須自檢 ssl 與 openai。"""
        app = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
        assert "_check_runtime_deps" in app, "app.py 應提供執行期依賴自檢"
        assert "import ssl" in app and "import openai" in app, \
            "自檢應涵蓋 ssl 與 openai"


class TestDatabaseClosedState:
    """資料庫關閉後的行為（真實競態 bug 的回歸測試）。

    真實事故：關閉視窗時引擎會關掉資料庫，但介面上仍有定時器
    （狀態列每秒刷新、分頁的搜尋防抖）可能在關閉後才觸發，
    對已關閉的連線查詢會拋 `sqlite3.ProgrammingError:
    Cannot operate on a closed database`，在控制台留下紅色堆疊。

    修法：Database 加 _closed 旗標。關閉後 query_one 回 None、
    query_all 回 []（讀取路徑安全）；寫入用的 transaction 仍明確報錯，
    因為靜默吞掉寫失敗會掩蓋真正的問題。
    """

    @staticmethod
    def _db(tmp_path):
        from cs2_translator.memory.db import Database

        return Database(tmp_path / "t.db")

    def test_query_before_close_works(self, tmp_path):
        db = self._db(tmp_path)
        try:
            assert db.is_closed() is False
            assert db.query_one("SELECT 1 AS n")["n"] == 1
        finally:
            db.close()

    def test_query_one_returns_none_after_close(self, tmp_path):
        db = self._db(tmp_path)
        db.close()
        assert db.is_closed() is True
        assert db.query_one("SELECT 1 AS n") is None

    def test_query_all_returns_empty_after_close(self, tmp_path):
        db = self._db(tmp_path)
        db.close()
        assert db.query_all("SELECT 1 AS n") == []

    def test_double_close_is_safe(self, tmp_path):
        db = self._db(tmp_path)
        db.close()
        db.close()  # 不應拋出例外
        assert db.is_closed() is True

    def test_transaction_still_raises_after_close(self, tmp_path):
        """寫入路徑必須明確報錯，不能靜默吞掉。"""
        import sqlite3

        db = self._db(tmp_path)
        db.close()
        with pytest.raises(sqlite3.ProgrammingError):
            with db.transaction():
                pass

    def test_pages_expose_stop_timers(self, qapp, tmp_path):
        """有延遲定時器的頁面必須提供 stop_timers()，供關閉流程呼叫。"""
        from cs2_translator.config import ConfigManager
        from cs2_translator.ui.main_window import MainWindow

        cfg = AppConfig(cs2_log_path=str(tmp_path / "console.log"), api_key="sk",
                        model="gpt-4o-mini", log_poll_interval=0.05)
        win = MainWindow(cfg, ConfigManager(data_dir=tmp_path / "data"))
        win.show()
        try:
            for key in ("memory", "glossary"):
                page = win.pages[key]
                assert hasattr(page, "stop_timers"), f"{key} 頁缺少 stop_timers()"
                # 觸發定時器後再停掉，確認不會拋例外
                page.search_edit.setText("x")
                page.stop_timers()
        finally:
            win._really_quit = True
            win.close()


class TestDataDirStability:
    """資料目錄名稱必須穩定，且與對外顯示名解耦。

    真實事故：改產品名時把 APP_NAME 一起改成 "QuanQiuTong"，結果程式跑去
    %APPDATA%\\QuanQiuTong 這個全新的空目錄，使用者的 config.json、
    secrets.json（API Key）與 memory.db（翻譯記憶、術語庫）全部變成孤兒資料，
    對使用者而言就像「資料全沒了」。
    因此 APP_NAME 必須維持 CS2ChatTranslator，只有 APP_DISPLAY_NAME 可改。
    """

    def test_app_name_is_stable(self):
        import cs2_translator

        assert cs2_translator.APP_NAME == "CS2ChatTranslator", (
            "APP_NAME 是資料目錄名稱，不可因改產品名而變動，"
            "否則既有使用者的資料會變成孤兒"
        )

    def test_display_name_is_decoupled(self):
        import cs2_translator

        assert cs2_translator.APP_DISPLAY_NAME == "全球通"

    def test_user_data_dir_uses_app_name(self, monkeypatch, tmp_path):
        import cs2_translator

        monkeypatch.setenv("APPDATA", str(tmp_path))
        d = user_data_dir()
        assert d.name == cs2_translator.APP_NAME
        assert d.parent == tmp_path
        # 不可以是顯示名
        assert d.name != cs2_translator.APP_DISPLAY_NAME


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
        """配置目录必须在用户目录下，而不是 EXE 所在目录。

        注意：这里刻意**不**断言目录名等于产品名（全球通 / QuanQiuTong）。
        目录名是 APP_NAME，属于内部标识，改名时必须保持不变，
        否则既有使用者的 config.json / secrets.json / memory.db 会变成孤儿数据。
        """
        import cs2_translator

        d = user_data_dir()
        # 必须在用户目录（APPDATA / Library / .config）之下
        home = Path.home()
        assert home in d.parents or str(d).startswith(str(home)), \
            f"資料目錄 {d} 不在使用者目錄下"
        # 不能是 exe 所在目录（打包后 exe 在 dist/ 或用户放的任意位置）
        assert d.name == cs2_translator.APP_NAME


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
