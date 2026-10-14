"""用户配置：默认值、加载/保存、环境变量覆盖、CS2 日志路径自动检测。"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

from . import APP_NAME, __version__
from .storage import SecretStore, atomic_write_json

logger = logging.getLogger("cs2_translator.config")

DEFAULT_API_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"

# 可作为“回复目标语言”的常见选择（模型支持的其他语言也可手动输入代码）
REPLY_TARGET_LANGUAGES = [
    "en", "ru", "ja", "ko", "de", "fr", "es", "pt", "it", "tr", "uk", "pl",
    "vi", "th", "ar", "hi", "id", "nl", "sv",
]

# 默认识别并翻译的来源语言。
# 包含 language.detect_primary_language 可能返回的全部代码，外加 "other" 兜底；
# 用户可在设置中删除某一项以关闭对应语言的翻译。
DEFAULT_ENABLED_SOURCE_LANGUAGES = [
    "en", "ru", "uk", "ja", "ko", "ar", "he", "th", "hi",
    "de", "fr", "es", "pt", "it", "tr", "pl", "vi", "id", "nl", "sv",
    "other",
]

CONFIG_VERSION = 2


def user_data_dir() -> Path:
    """用户数据目录（不放 EXE 内部，保证只读安装位置也能运行）。"""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "cs2-chat-translator"


@dataclass
class AppConfig:
    # ---- 日志来源 ----
    cs2_log_path: str = ""

    # ---- 翻译 API ----
    api_base_url: str = DEFAULT_API_BASE_URL
    api_key: str = ""  # 仅运行时保存在内存；落盘时移交 SecretStore
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 20.0
    max_retries: int = 2
    rate_limit_per_minute: int = 30

    # ---- 接收翻译 ----
    # 默认 False：只处理启动之后新写入的日志，避免每次启动重复翻译历史消息
    read_history_on_start: bool = False
    enabled_source_languages: list = field(default_factory=lambda: list(DEFAULT_ENABLED_SOURCE_LANGUAGES))
    translate_enabled: bool = True

    # ---- 回复翻译（双向） ----
    reply_target_language: str = "en"
    auto_suggest_reply_language: bool = True

    # ---- 界面 ----
    auto_scroll: bool = True
    # 实时翻译页的消息排列：False = 最新在底部（自动滚动到底）
    #                      True  = 最新在顶部（永远第一眼看到最新）
    newest_first: bool = False
    minimize_to_tray: bool = True

    # ---- 流水线 ----
    max_message_length: int = 500
    queue_max_size: int = 200
    translation_workers: int = 2
    cache_ttl_seconds: int = 600
    skip_expired_seconds: int = 0
    extra_chat_patterns: list = field(default_factory=list)
    enable_generic_name_pattern: bool = False
    log_poll_interval: float = 0.25

    # ---- 隐私 ----
    save_history: bool = False

    # ---- 翻译记忆库（本地 SQLite） ----
    memory_enabled: bool = True          # 启用本地翻译记忆（精确缓存 + 术语 + 模板）
    save_memory_history: bool = True     # 记录 translation_history（关闭不影响缓存功能）
    memory_price_per_1k_tokens: float = 0.0  # 可选：每千 token 价格，用于估算节省费用（0=不估算）

    # ---- 对局（match）与分层数据保留 ----
    # A 层：当前局显示上限（界面最多渲染多少条，超出淘汰最旧）
    match_message_limit: int = 300
    # B 层：历史对局保留局数（超出按最旧优先清理；只删对局，不动长期记忆）
    history_match_limit: int = 10
    auto_cleanup_history: bool = True    # 是否在换局时自动按上限清理历史对局
    auto_match_detection: bool = False   # 自动识别新一局（信号尚未经真实多局验证，默认关闭）
    # C 层：长期翻译记忆永久保留，只有用户主动清理才删除
    # 每次启动清空「实时翻译」与「历史对局」，只保留长期缓存与词汇表
    reset_on_startup: bool = True

    # ---- 界面主题 ----
    theme: str = "dark"                  # dark / light
    sidebar_collapsed: bool = False

    # ---- 元数据 ----
    first_run_completed: bool = False

    # ---------- 序列化 ----------
    def to_dict(self) -> dict:
        data = {}
        for f in fields(self):
            if f.name == "api_key":
                continue  # 密钥绝不写入普通配置文件
            data[f.name] = getattr(self, f.name)
        data["config_version"] = CONFIG_VERSION
        data["app_version"] = __version__
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        data = data or {}
        known = {f.name: f for f in fields(cls)}
        kwargs = {}
        for key, value in data.items():
            if key not in known or key == "api_key":
                continue
            kwargs[key] = value
        cfg = cls(**kwargs)
        cfg._coerce()
        return cfg

    def _coerce(self) -> None:
        def _int(name, lo, hi, default):
            try:
                v = int(getattr(self, name))
            except (TypeError, ValueError):
                v = default
            setattr(self, name, max(lo, min(hi, v)))

        def _float(name, lo, hi, default):
            try:
                v = float(getattr(self, name))
            except (TypeError, ValueError):
                v = default
            if v != v or v in (float("inf"), float("-inf")):
                v = default
            setattr(self, name, max(lo, min(hi, v)))

        def _bool(name):
            setattr(self, name, bool(getattr(self, name)))

        def _list(name):
            v = getattr(self, name)
            if not isinstance(v, list):
                setattr(self, name, [])
            else:
                setattr(self, name, [str(x) for x in v])

        _float("timeout_seconds", 1.0, 300.0, 20.0)
        _float("memory_price_per_1k_tokens", 0.0, 10000.0, 0.0)
        _int("max_retries", 0, 5, 2)
        _int("rate_limit_per_minute", 1, 600, 30)
        _int("max_message_length", 20, 4000, 500)
        _int("queue_max_size", 10, 5000, 200)
        _int("translation_workers", 1, 8, 2)
        _int("cache_ttl_seconds", 0, 86400, 600)
        _int("skip_expired_seconds", 0, 3600, 0)
        _int("match_message_limit", 50, 5000, 300)
        _int("history_match_limit", 1, 200, 10)
        _float("log_poll_interval", 0.05, 10.0, 0.25)
        for name in (
            "read_history_on_start", "translate_enabled", "auto_suggest_reply_language",
            "auto_scroll", "newest_first", "minimize_to_tray", "save_history", "first_run_completed",
            "enable_generic_name_pattern", "memory_enabled", "save_memory_history",
            "auto_cleanup_history", "auto_match_detection", "sidebar_collapsed",
            "reset_on_startup",
        ):
            _bool(name)
        for name in ("enabled_source_languages", "extra_chat_patterns"):
            _list(name)
        for name in (
            "cs2_log_path", "api_base_url", "api_key", "model", "reply_target_language", "theme",
        ):
            if not isinstance(getattr(self, name), str):
                setattr(self, name, str(getattr(self, name) or ""))
        if not self.model.strip():
            self.model = DEFAULT_MODEL
        if self.theme.strip().lower() not in ("dark", "light"):
            self.theme = "dark"

    # ---------- 便捷判断 ----------
    @property
    def is_first_run(self) -> bool:
        return not self.first_run_completed or not self.cs2_log_path or not self.api_key


def _load_dotenv_files() -> None:
    """加载可选 .env（当前目录与 EXE 目录），不覆盖已存在的环境变量。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    candidates = [Path.cwd() / ".env"]
    argv0 = sys.argv[0] if sys.argv and sys.argv[0] else ""
    if argv0:
        try:
            candidates.append(Path(argv0).resolve().parent / ".env")
        except OSError:
            pass
    for candidate in candidates:
        try:
            if candidate.is_file():
                load_dotenv(candidate, override=False)
        except Exception:
            continue


def _parse_libraryfolders(vdf_path: Path) -> list:
    libs = []
    try:
        text = vdf_path.read_text("utf-8", errors="replace")
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            libs.append(Path(match.group(1).replace("\\\\", "\\")))
    except OSError:
        pass
    return libs


def _windows_steam_libraries() -> list:
    libs = []

    def add_root(root) -> None:
        if not root:
            return
        root = Path(root)
        steamapps = root / "steamapps"
        if steamapps.is_dir():
            libs.append(root)
        vdf = steamapps / "libraryfolders.vdf"
        if vdf.is_file():
            libs.extend(_parse_libraryfolders(vdf))

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
            add_root(Path(steam_path))
    except Exception:
        pass
    for env_name in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
        base = os.environ.get(env_name)
        if base:
            add_root(Path(base) / "Steam")
    for extra in ("D:\\Steam", "E:\\Steam", "F:\\Steam", "D:\\SteamLibrary", "E:\\SteamLibrary"):
        add_root(Path(extra))
    return libs


def _linux_steam_libraries() -> list:
    home = Path.home()
    candidates = [
        home / ".steam" / "steam",
        home / ".local" / "share" / "Steam",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
    ]
    libs = []
    for candidate in candidates:
        steamapps = candidate / "steamapps"
        if steamapps.is_dir():
            libs.append(candidate)
        vdf = steamapps / "libraryfolders.vdf"
        if vdf.is_file():
            libs.extend(_parse_libraryfolders(vdf))
    return libs


class ConfigManager:
    """配置读写 + 密钥托管 + 日志路径自动检测。"""

    def __init__(self, data_dir: Path | None = None, config_name: str = "config.json"):
        self.data_dir = Path(data_dir) if data_dir else user_data_dir()
        self.config_path = self.data_dir / config_name
        self.secret_store = SecretStore(self.data_dir / "secrets.json")

    # ---------- 读写 ----------
    def load(self) -> AppConfig:
        _load_dotenv_files()
        cfg = AppConfig()
        if self.config_path.is_file():
            try:
                cfg = AppConfig.from_dict(self._read_config_data())
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning("配置文件读取失败，使用默认配置: %s", exc)
        cfg = self._apply_env_overrides(cfg)
        key = (
            os.environ.get("CS2_TRANSLATOR_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or self.secret_store.get("api_key")
            or ""
        )
        cfg.api_key = key
        return cfg

    def _read_config_data(self) -> dict:
        """读取配置文件，容忍 BOM 等常见编码问题。

        Windows 上很多编辑器（含 PowerShell 的 Set-Content）会在写入 JSON 时
        加上 UTF-8 BOM。``json.loads`` 遇到 BOM 会直接抛 JSONDecodeError，
        导致**整份配置被静默丢弃并回退默认值**（用户的模型、Base URL 全部丢失，
        表现为 API 突然不可用）。这里显式处理 BOM。
        """
        raw = self.config_path.read_bytes()
        # 去掉 UTF-8 BOM / UTF-16 BOM
        for bom in (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"):
            if raw.startswith(bom):
                raw = raw[len(bom):]
                break
        text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
        return json.loads(text)

    def save(self, cfg: AppConfig) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if cfg.api_key:
            self.secret_store.set("api_key", cfg.api_key)
        atomic_write_json(self.config_path, cfg.to_dict())
        logger.info("配置已保存: %s", self.config_path)

    @staticmethod
    def _apply_env_overrides(cfg: AppConfig) -> AppConfig:
        log_path = os.environ.get("CS2_LOG_PATH")
        if log_path:
            cfg.cs2_log_path = log_path
        base = os.environ.get("CS2_API_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        if base:
            cfg.api_base_url = base
        model = os.environ.get("CS2_MODEL") or os.environ.get("OPENAI_MODEL")
        if model:
            cfg.model = model
        return cfg

    # ---------- 日志路径自动检测 ----------
    def auto_detect_log_paths(self) -> list:
        """返回候选 console.log 路径列表（已存在的排在前面）。"""
        if sys.platform.startswith("win"):
            libs = _windows_steam_libraries()
        else:
            libs = _linux_steam_libraries()
        found, guesses = [], []
        seen = set()
        for lib in libs:
            candidate = (
                Path(lib)
                / "steamapps"
                / "common"
                / "Counter-Strike Global Offensive"
                / "game"
                / "csgo"
                / "console.log"
            )
            key = str(candidate).lower()
            if key in seen:
                continue
            seen.add(key)
            (found if candidate.is_file() else guesses).append(str(candidate))
        # 去重（保留顺序）
        return found + [g for g in guesses if g not in found]
