"""pytest 共享配置与测试辅助设施。"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

# 无显示环境下的 Qt 离屏渲染（必须在导入 PySide6 之前设置）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import pytest  # noqa: E402

from cs2_translator.config import AppConfig  # noqa: E402


# ---------------------------------------------------------------- 假 OpenAI 客户端
def make_completion_response(content: str):
    """构造与 openai SDK 返回对象兼容的简易响应。"""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                message=SimpleNamespace(role="assistant", content=content),
                finish_reason="stop",
            )
        ]
    )


class FakeOpenAIClient:
    """可编程的假 OpenAI 客户端，用于单元测试（不发起真实网络请求）。"""

    def __init__(self, responses=None, errors=None):
        self._responses = list(responses or [])
        self._errors = list(errors or [])
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._errors:
            error = self._errors.pop(0)
            if callable(error) and not isinstance(error, BaseException):
                error()
            if isinstance(error, BaseException):
                raise error
        if self._responses:
            response = self._responses.pop(0)
            if callable(response):
                response = response(kwargs)
            if isinstance(response, BaseException):
                raise response
            return make_completion_response(response)
        return make_completion_response("默认译文")


def make_api_error(status: int, message: str, headers: dict | None = None):
    """构造真实的 openai 异常对象（openai 2.x）。"""
    import httpx
    import openai

    request = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
    response = httpx.Response(status, request=request, headers=headers or {})
    if status == 429:
        return openai.RateLimitError(message, response=response, body=None)
    if status == 401:
        return openai.AuthenticationError(message, response=response, body=None)
    if status == 403:
        return openai.PermissionDeniedError(message, response=response, body=None)
    return openai.APIStatusError(message, response=response, body=None)


def make_timeout_error():
    import httpx
    import openai

    return openai.APITimeoutError(request=httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions"))


def make_connection_error():
    import httpx
    import openai

    return openai.APIConnectionError(
        message="connection refused", request=httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
    )


# ---------------------------------------------------------------- Mock HTTP 服务器
def chat_completion_payload(content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class MockOpenAIServer:
    """本地 Mock OpenAI 兼容服务器，用于端到端 HTTP 测试。

    - default_responder: 接收请求体，返回响应 JSON（默认回显标记）；
    - script: 按顺序返回的响应列表 [{'status': int, 'json': dict, 'headers': dict}]；
    - requests: 记录所有收到的请求。
    """

    def __init__(self, default_responder=None):
        self.default_responder = default_responder or (
            lambda body: chat_completion_payload(f"[译] {body['messages'][-1]['content']}")
        )
        self.script = []
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静音
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = {}
                outer.requests.append(
                    {
                        "path": self.path,
                        "body": body,
                        "auth": self.headers.get("Authorization"),
                    }
                )
                if outer.script:
                    spec = outer.script.pop(0)
                else:
                    spec = {"status": 200, "json": outer.default_responder(body)}
                status = spec.get("status", 200)
                payload = json.dumps(spec.get("json", {})).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for key, value in (spec.get("headers") or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def mock_server():
    server = MockOpenAIServer()
    server.start()
    yield server
    server.stop()


# ---------------------------------------------------------------- 配置 fixture
@pytest.fixture
def base_config(tmp_path) -> AppConfig:
    return AppConfig(
        cs2_log_path=str(tmp_path / "console.log"),
        api_base_url="https://api.openai.com/v1",
        api_key="sk-test-key",
        model="gpt-4o-mini",
        timeout_seconds=10.0,
        max_retries=2,
        rate_limit_per_minute=600,
        read_history_on_start=False,
        cache_ttl_seconds=600,
        queue_max_size=100,
        translation_workers=2,
        log_poll_interval=0.05,
        max_message_length=500,
    )


@pytest.fixture
def fake_client():
    def factory(responses=None, errors=None):
        return FakeOpenAIClient(responses=responses, errors=errors)

    return factory


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
