"""QuanQiuTong（全球通）—— CS2 实时聊天中文翻译助手。

模块化结构：
- config         用户配置与配置目录管理
- storage        密钥安全存储（Windows DPAPI）与原子写入
- log_watcher    只读增量日志监听
- chat_parser    CS2 console.log 聊天消息识别
- language       语言文字识别
- translator     在线 AI 翻译（外语 -> 简体中文）
- reply_translator 在线 AI 回复翻译（简体中文 -> 目标语言）
- phrases        常用战术短语收藏
- queue_manager  有界翻译队列与工作线程
- engine         各模块组装与异步编排
- ui             PySide6 桌面界面
"""

from __future__ import annotations

import sys
from pathlib import Path

__version__ = "1.2.5"
# APP_NAME 用于数据目录（%APPDATA%\CS2ChatTranslator）与内部标识。
# 刻意与对外显示名解耦：改名不会导致既有配置、翻译记忆与术语库搬家。
# ⚠ 绝对不要因为「改产品名」就改这个值 —— 那会让使用者的
#   config.json / secrets.json（API Key）/ memory.db（翻译记忆、术语库）
#   全部变成孤儿数据：程序会去一个全新的空目录，看起来像「资料全没了」。
APP_NAME = "CS2ChatTranslator"
APP_DISPLAY_NAME = "全球通"


def asset_path(name: str) -> Path | None:
    """解析随包分发的资源文件路径。

    依次尝试 PyInstaller 解包目录（sys._MEIPASS）与开发目录 assets/，
    找到即返回绝对路径，找不到返回 None。
    """
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / name)
    candidates.append(Path(__file__).resolve().parent.parent.parent / "assets" / name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None
