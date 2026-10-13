"""应用上下文：把配置、对局管理、引擎控制器打包传给各页面。

原先 MainWindow 一个人持有全部状态与逻辑（600+ 行、28 个方法），
重构后由 ``AppContext`` 承载共享依赖，各页面只负责自己的展示与交互，
避免页面之间互相引用，也便于单独测试。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("cs2_translator.ui.context")


@dataclass
class AppContext:
    """跨页面共享的运行时依赖。

    Attributes:
        config: 当前配置（保存设置后会替换为新对象）。
        config_manager: 配置读写。
        controller: 引擎控制器（持有 TranslationEngine）。
        matches: 对局管理器（三层数据隔离）。
        data_dir: 用户数据目录。
    """

    config: object
    config_manager: object
    controller: object
    matches: object
    data_dir: object

    # ---- 便捷访问 ----
    @property
    def engine(self):
        return getattr(self.controller, "engine", None)

    @property
    def memory(self):
        engine = self.engine
        return getattr(engine, "memory", None)

    def memory_stats(self) -> dict:
        try:
            return self.controller.memory_stats()
        except Exception:
            logger.exception("读取记忆库统计失败")
            return {}

    def replace_config(self, config) -> None:
        """设置页保存后更新上下文中的配置对象。"""
        self.config = config


def format_clock(iso_or_text: str) -> str:
    """把各种时间表示格式化为 HH:MM:SS，失败则原样返回。"""
    if not iso_or_text:
        return "--:--:--"
    text = str(iso_or_text)
    # ISO-8601（数据库 started_at / created_at）
    if "T" in text:
        try:
            from datetime import datetime

            return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%H:%M:%S")
        except Exception:
            pass
    # 已经是 HH:MM:SS 或 MM/DD HH:MM:SS
    if len(text) >= 8 and text[-8] == ":":
        return text[-8:]
    return text


def format_datetime(iso_or_text: str) -> str:
    """格式化为 YYYY-MM-DD HH:MM:SS（本地时区）。"""
    if not iso_or_text:
        return "-"
    text = str(iso_or_text)
    if "T" in text:
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return text
