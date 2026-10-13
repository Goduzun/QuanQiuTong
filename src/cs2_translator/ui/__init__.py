"""PySide6 桌面界面。

模块划分：
- design        统一设计系统（颜色/字体/间距 token + QSS 生成）
- icons         程序化线性图标（无额外依赖）
- widgets       可复用组件（卡片/状态点/徽章/统计项/页面头/空状态）
- context       AppContext：跨页面共享依赖
- main_window   外壳：左侧导航 + 顶部状态栏 + 页面堆栈
- page_live     实时翻译（消息流 + 回复翻译）
- page_history  历史对局
- page_memory   长期翻译记忆
- page_glossary 词汇表
- page_settings 设置
- workers       引擎信号桥接
- memory_dialog 记忆库管理 / 纠错 / 术语 / 模板编辑对话框
- settings_dialog 首次运行配置向导
- reply_panel   回复面板组件
"""

from .workers import EngineController, EngineSignals

__all__ = ["EngineController", "EngineSignals"]
