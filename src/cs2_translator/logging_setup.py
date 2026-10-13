"""日志配置：控制台 + 滚动文件，并对 API 密钥等敏感信息脱敏。"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

REDACT_TOKEN = "***REDACTED***"

# 常见的密钥形态：sk-...、Bearer ...、api_key=...、authorization: ...
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9_\-\.]{6,}"),
    re.compile(r"(?i)\b(api[_-]?key|apikey|authorization|token|secret)\b(\s*[=:]\s*)(\S+)"),
]


class SecretRedactingFilter(logging.Filter):
    """在日志写入前替换敏感信息。"""

    def __init__(self, secrets=()):
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def add_secret(self, secret: str) -> None:
        if secret and secret not in self._secrets:
            self._secrets.append(secret)

    @staticmethod
    def _redact_text(text: str) -> str:
        def _sub(match: re.Match) -> str:
            groups = match.groups()
            if len(groups) >= 3 and groups[1]:
                return f"{groups[0]}{groups[1]}{REDACT_TOKEN}"
            return REDACT_TOKEN

        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(_sub, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            record.msg = self._redact_text(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: (self._redact_text(v) if isinstance(v, str) else v)
                        for k, v in record.args.items()
                    }
                else:
                    # 仅脱敏字符串参数；保留 int/float 等类型，
                    # 否则会破坏其他 handler 的 %d/%f 惰性格式化
                    record.args = tuple(
                        self._redact_text(a) if isinstance(a, str) else a
                        for a in record.args
                    )
        except Exception:  # 脱敏失败不能影响日志本身
            pass
        return True


def setup_logging(
    log_dir: Path,
    level: int = logging.INFO,
    console: bool = False,
    secrets=(),
) -> logging.Logger:
    """初始化 cs2_translator 日志器，返回根日志器。"""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("cs2_translator")
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    redactor = SecretRedactingFilter(secrets)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)
    logger.addHandler(file_handler)

    if console:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(redactor)
        logger.addHandler(stream_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"cs2_translator.{name}")
