"""应用入口：GUI 模式 / 命令行模式 / 日志诊断模式。

用法：
    python app.py                          # 启动桌面界面（首次运行会引导配置）
    python app.py --cli                    # 无界面模式，翻译结果输出到终端
    python app.py --diagnose 日志文件      # 本地日志诊断：只分析不上传
    python app.py --reply "中文" --to en   # 翻译一条中文回复并退出
    python app.py --version
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

# 保证以脚本方式运行时可以找到包
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cs2_translator import APP_DISPLAY_NAME, APP_NAME, __version__  # noqa: E402
from cs2_translator.chat_parser import ChatParser  # noqa: E402
from cs2_translator.config import ConfigManager, user_data_dir  # noqa: E402
from cs2_translator.engine import TranslationEngine  # noqa: E402
from cs2_translator.logging_setup import setup_logging  # noqa: E402
from cs2_translator.translator import TranslatorError  # noqa: E402

logger = logging.getLogger("cs2_translator.app")


def _make_console_output_safe() -> None:
    """让控制台输出在 GBK 等非 UTF-8 终端下不崩溃。

    中文 Windows 控制台默认使用 GBK；日志里可能含 BOM、零宽字符或西里尔/日文
    等 GBK 无法编码的字符，直接 print 会抛 UnicodeEncodeError 使诊断模式中断。
    这里把标准输出切换为 UTF-8 并对无法编码的字符使用替换，保证输出完整可读。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # 无控制台（windowed 打包）或流不可配置时忽略
            pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="QuanQiuTong",
        description=f"{APP_DISPLAY_NAME} v{__version__}",
    )
    parser.add_argument("--cli", action="store_true", help="无界面模式，翻译结果输出到终端")
    parser.add_argument("--diagnose", metavar="LOG", help="本地日志诊断模式（只分析，不上传）")
    parser.add_argument("--reply", metavar="TEXT", help="翻译一条中文回复后退出")
    parser.add_argument("--to", metavar="LANG", default="en", help="回复目标语言代码，默认 en")
    parser.add_argument("--config", metavar="PATH", help="指定配置文件路径")
    parser.add_argument("--data-dir", metavar="PATH", help="指定用户数据目录")
    parser.add_argument("--reset-config", action="store_true", help="删除本地配置后退出")
    parser.add_argument("--log-level", default="INFO", help="日志级别（默认 INFO）")
    parser.add_argument("--version", action="store_true", help="显示版本后退出")
    return parser


def _make_config_manager(args) -> ConfigManager:
    data_dir = Path(args.data_dir) if args.data_dir else user_data_dir()
    return ConfigManager(data_dir=data_dir)


# ---------------------------------------------------------------- 诊断模式
def _check_runtime_deps() -> list:
    """檢查打包後容易缺失的執行期依賴。

    為什麼要檢查：PyInstaller 打包時若誤刪 OpenSSL DLL，_ssl 會載入失敗，
    openai 庫隨之無法 import，翻譯功能全廢 —— 而 GUI 上只會顯示一行
    「openai 库不可用」，使用者難以判斷原因。這裡主動檢查並給出可行動的訊息。

    回傳 [(名稱, 是否可用, 說明)]。
    """
    results = []

    # 1) _ssl / OpenSSL —— openai 走 HTTPS 的必要條件
    try:
        import ssl

        results.append(("ssl", True, ssl.OPENSSL_VERSION))
    except Exception as exc:  # noqa: BLE001
        results.append(("ssl", False, f"{type(exc).__name__}: {exc}"))

    # 2) openai —— 翻譯 API 客戶端
    try:
        import openai

        results.append(("openai", True, getattr(openai, "__version__", "?")))
    except Exception as exc:  # noqa: BLE001
        results.append(("openai", False, f"{type(exc).__name__}: {exc}"))

    return results


def _run_diagnose(path: str) -> int:
    target = Path(path)
    if not target.is_file():
        print(f"文件不存在: {target}")
        return 2
    print(f"正在本地分析日志: {target}（仅读取，不上传任何内容）")
    print("\n==== 运行环璄自检 ====")
    for name, ok, detail in _check_runtime_deps():
        mark = "OK" if ok else "缺失"
        print(f"  {name}: {mark}  {detail}")
    try:
        data = target.read_bytes()
    except OSError as exc:
        print(f"读取失败: {exc}")
        return 2
    lines = data.decode("utf-8", errors="replace").splitlines()
    messages, unmatched, stats = ChatParser.diagnose(lines)
    print("\n==== 统计 ====")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    print(f"\n==== 识别到的聊天消息（{len(messages)} 条）====")
    for message in messages[:50]:
        print(
            f"  [{message.channel_label}] {message.sender}: {message.content}"
            + (f"  (时间 {message.timestamp})" if message.timestamp else "")
        )
    if len(messages) > 50:
        print(f"  … 其余 {len(messages) - 50} 条省略")
    print(f"\n==== 未识别行样例（前 10 条，共 {len(unmatched)} 行）====")
    for line in unmatched[:10]:
        print(f"  {line[:160]}")
    if stats["chat_messages"] == 0:
        print(
            "\n提示: 未识别到聊天消息。请确认 CS2 启动参数包含 -condebug，"
            "且游戏内确有聊天发生；若日志格式与预期不同，可在设置中添加自定义正则。"
        )
    # 依賴缺失時給出可行動的修復方向（而不是只說「不可用」）
    broken = [name for name, ok, _ in _check_runtime_deps() if not ok]
    if broken:
        print(
            f"\n⚠ 執行期依賴缺失: {', '.join(broken)}"
            "\n  這通常是打包時誤刪了 OpenSSL DLL 造成的。"
            "\n  修復: 重新打包時不要排除 libssl-1_1.dll / libcrypto-1_1.dll"
            "（Python 3.11 的 _ssl.pyd 直接依賴它們）。"
        )
    return 0


# ---------------------------------------------------------------- 回复模式
def _run_reply(config, text: str, target: str) -> int:
    from cs2_translator.reply_translator import ReplyTranslator

    translator = ReplyTranslator(config, api_key=config.api_key)
    try:
        translated = translator.translate(text, target)
    except TranslatorError as exc:
        print(f"翻译失败: {exc}")
        return 3
    print(f"中文原文: {text}")
    print(f"译文({target}): {translated}")
    return 0


# ---------------------------------------------------------------- CLI 模式
def _run_cli(config, config_manager) -> int:
    setup_logging(config_manager.data_dir / "logs", console=False)
    if not config.cs2_log_path:
        print("错误: 尚未配置 CS2 日志路径。请先运行一次图形界面完成配置，或设置 CS2_LOG_PATH 环境变量。")
        return 2
    if not config.api_key:
        print("错误: 尚未配置 API Key。请先运行一次图形界面完成配置，或设置 OPENAI_API_KEY 环境变量。")
        return 2

    engine = TranslationEngine(config, data_dir=config_manager.data_dir)

    def on_chat(message):
        print(f"[聊天][{message.channel_label}] {message.sender}: {message.content}", flush=True)

    def on_translation(message, translated, latency_ms, error, skipped, meta=None):
        hit_kind = (meta or {}).get("hit_kind", "")
        if error is not None:
            print(f"  [翻译失败] {error}", flush=True)
        elif skipped:
            print(f"  [保留原文] {translated}", flush=True)
        elif hit_kind:
            from cs2_translator.ui.memory_dialog import hit_kind_label
            print(f"  [译文][{hit_kind_label(hit_kind)}命中] {translated}  ({latency_ms:.0f} ms)", flush=True)
        else:
            print(f"  [译文] {translated}  ({latency_ms:.0f} ms)", flush=True)

    def on_status(status):
        if status.get("api_state") == "error" and status.get("last_error"):
            print(f"[状态] API 错误: {status['last_error']}", flush=True)

    engine.on_chat = on_chat
    engine.on_translation = on_translation
    engine.on_status = on_status
    engine.start()
    print(f"{APP_DISPLAY_NAME} v{__version__}（命令行模式）")
    print(f"监听日志: {config.cs2_log_path}")
    print("按 Ctrl+C 退出。\n")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n正在退出…")
    finally:
        engine.stop()
    return 0


# ---------------------------------------------------------------- GUI 模式
def _run_gui(config, config_manager) -> int:
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError as exc:
        print(f"错误: 无法加载图形界面（缺少 PySide6）: {exc}")
        print("请使用 --cli 命令行模式，或执行: pip install PySide6")
        return 4

    from cs2_translator.single_instance import SingleInstanceGuard, instance_key
    from cs2_translator.ui.design import build_stylesheet, set_active_theme
    from cs2_translator.ui.main_window import MainWindow
    from cs2_translator.ui.settings_dialog import SettingsDialog

    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setStyle("Fusion")
    palette = set_active_theme(getattr(config, "theme", "dark"))
    app.setStyleSheet(build_stylesheet(palette))

    # 单实例：必须在任何窗口/引擎创建**之前**判断。
    # 现场曾同时跑着 2 份实例（onefile 的 bootloader + 主进程成对，共 4 个进程），
    # 各自每 250ms 轮询 console.log、各自每秒刷新 UI、各自一个托盘图标。
    guard = SingleInstanceGuard(instance_key(APP_NAME))
    if not guard.try_acquire():
        # 已有实例：try_acquire 里已经发过唤醒指令，本进程直接退出
        print(f"{APP_DISPLAY_NAME} 已在运行，已请求显示已有窗口。")
        return 0

    if config.is_first_run:
        QMessageBox.information(
            None,
            "首次运行",
            "欢迎使用 全球通（CS2 实时聊天中文翻译助手）。\n\n"
            "接下来请配置 CS2 日志路径与翻译 API。\n"
            "提示: 需要先在 Steam 中为 CS2 添加启动参数 -condebug。",
        )
        dialog = SettingsDialog(config_manager, config, first_run=True)
        if not dialog.exec():
            guard.release()
            return 0
        config = config_manager.load()

    window = MainWindow(config, config_manager, instance_guard=guard)
    window.show()

    def handle_sigint(*_args):
        window._on_quit()

    signal.signal(signal.SIGINT, handle_sigint)
    # 退出时释放单实例锁，避免下次启动因为残留 endpoint 而误判
    app.aboutToQuit.connect(guard.release)
    try:
        return app.exec()
    finally:
        guard.release()


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    _make_console_output_safe()
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"{APP_DISPLAY_NAME} v{__version__}")
        return 0

    config_manager = _make_config_manager(args)

    if args.reset_config:
        try:
            config_manager.config_path.unlink()
            print(f"已删除配置: {config_manager.config_path}")
        except FileNotFoundError:
            print("没有找到本地配置。")
        return 0

    if args.diagnose:
        return _run_diagnose(args.diagnose)

    config = config_manager.load()

    if args.reply:
        return _run_reply(config, args.reply, args.to)

    setup_logging(config_manager.data_dir / "logs", level=getattr(logging, args.log_level.upper(), logging.INFO))

    if args.cli:
        return _run_cli(config, config_manager)
    return _run_gui(config, config_manager)


if __name__ == "__main__":
    sys.exit(main())
