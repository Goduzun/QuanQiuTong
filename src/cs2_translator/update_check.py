# -*- coding: utf-8 -*-
"""版本更新检查（启动时后台执行，不阻塞界面）。

设计取舍：
- 只向 GitHub 的公开 Releases API 查询**版本号**，不上传任何本地信息；
- 在后台线程执行，失败静默（网络不通不该影响使用）；
- 可透过 config.check_updates 关闭（重视隐私的使用者可以关掉）。

安全闸门：这是本项目**唯一**会主动联网的非翻译请求。它不发送任何使用者数据，
只读公开的版本号。若使用者关闭该选项，程式完全不连 GitHub。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

REPO = "Goduzun/QuanQiuTong"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
TIMEOUT = 8


def parse_version(text: str) -> tuple:
    """把 'v1.2.1' / '1.2.1' 解析成可比较的 tuple。"""
    nums = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in nums[:4]) if nums else (0,)


def is_newer(latest: str, current: str) -> bool:
    """latest 是否比 current 新。"""
    return parse_version(latest) > parse_version(current)


class UpdateCheckWorker(QThread):
    """后台查询最新版本。"""

    # (有新版本?, 最新版本字串, 下载页 URL)
    finished_check = Signal(bool, str, str)

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self.current_version = current_version

    def run(self) -> None:  # noqa: D102
        try:
            req = urllib.request.Request(
                API_URL,
                headers={"User-Agent": "QuanQiuTong-UpdateCheck",
                         "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            tag = str(data.get("tag_name") or "")
            page = str(data.get("html_url") or RELEASES_PAGE)
            if tag and is_newer(tag, self.current_version):
                self.finished_check.emit(True, tag.lstrip("v"), page)
            else:
                self.finished_check.emit(False, self.current_version, page)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            # 网络问题静默处理：检查更新失败不该打扰使用者
            logger.debug("检查更新失败（网络）")
        except Exception:
            logger.exception("检查更新失败")
