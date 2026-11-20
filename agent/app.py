# -*- coding: utf-8 -*-
"""EDA 에이전트 로컬 서버 (껍데기). example/agent-sim의 도구 탭만 남긴 구조다.

실행: python app.py  ->  http://127.0.0.1:8002/
도구는 mcp_config.json에 적힌 MCP 서버(기본: MCP 게이트웨이 :8001)에서 받아 온다.
"""

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lab import config, llm, mcp_client, registry, runner      # noqa: E402

STATIC = os.path.join(config.ROOT, "static")
LOCK = threading.Lock()               # 요청을 한 번에 하나씩 처리한다
MAX_BODY = 1024 * 1024

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8"}

CHAT = {"items": []}
# shortcut: 프로세스 메모리에 한 대화만 둔다. 재시작하면 사라지고, 길어지면 [대화 초기화]로 비운다.


def tools():
    """게이트웨이에 등록한 API가 바로 보이도록 매번 다시 연결한다."""
    mcp_client.reset()
    return registry.build()


def state():
    entries, errors = tools()
    return {"tools": [{"name": e["name"], "description": e.get("description", ""),
                       "source": e.get("source", "")} for e in entries],
            "errors": errors}


def handle_chat(payload):
    message = (payload.get("message") or "").strip()
    if not message:
        return {"ok": False, "message": "무엇을 도와드릴지 입력해 주세요."}
    entries, _ = tools()
    if not entries:
        return {"ok": False, "message": "쓸 수 있는 도구가 없습니다. MCP 게이트웨이가 켜져 있고 API가 등록되어 있는지 확인해 주세요."}
    try:
        out = runner.run(message, entries, history=CHAT["items"])
    except llm.QuotaExceeded:
        return {"ok": False, "message": "잠시 후 다시 시도해 주세요 (약 1분)."}
    except RuntimeError as exc:
        if str(exc) == "NO_API_KEY":
            return {"ok": False, "message": ".env 파일에 키가 없습니다. OPENAI_API_KEY를 확인해 주세요."}
        return {"ok": False, "message": "처리 중 문제가 생겼습니다."}
    except llm.LLMError:
        return {"ok": False, "message": "응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요."}
    CHAT["items"] = out.pop("items")
    out["ok"] = True
    return out


def handle_reset(_payload):
    CHAT["items"] = []
    clear = registry.find(tools()[0], "pipeline_clear")
    if clear:
        clear["call"]({})             # 대화를 비우면 화면의 파이프라인도 비운다
    return {"ok": True}


def gateway_get(path):
    """게이트웨이의 GET을 대신 불러 (상태 코드, JSON)을 돌려준다. 화면이 채팅 중에도 계속 물어 진행 상황을 그린다."""
    try:
        with urllib.request.urlopen(config.GATEWAY + path, timeout=40) as response:
            return 200, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail")
        except (ValueError, AttributeError):
            detail = None
        return exc.code, {"error": str(detail or "게이트웨이가 요청을 처리하지 못했습니다.")}
    except (OSError, ValueError):
        return 502, {"error": "MCP 게이트웨이에 연결하지 못했습니다."}


ROUTES = {"/api/chat": handle_chat, "/api/chat/reset": handle_reset}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, content_type):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

    def _static(self, name):
        path = os.path.join(STATIC, os.path.basename(name))
        if not os.path.isfile(path):
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        with open(path, "rb") as f:
            self._send(200, f.read(), MIME.get(os.path.splitext(path)[1].lower(), "application/octet-stream"))

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._static("index.html")
        elif path.startswith("/static/"):
            self._static(path[len("/static/"):])
        elif path == "/api/state":
            with LOCK:
                self._json(state())
        elif path == "/api/pipeline":
            # LOCK을 잡지 않는다. /api/chat이 락을 쥐고 도는 동안 읽어야 진행 상황이 보인다.
            code, payload = gateway_get("/pipeline")
            self._json(payload, code)
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        handler = ROUTES.get(self.path.split("?")[0])
        if handler is None:
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._json({"ok": False, "message": "보낸 내용이 너무 큽니다."}, 413)
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, UnicodeDecodeError):
            self._json({"ok": False, "message": "요청을 읽지 못했습니다."}, 400)
            return
        with LOCK:
            self._json(handler(payload))


if __name__ == "__main__":
    print("EDA 에이전트: http://%s:%d/" % (config.HOST, config.PORT))
    ThreadingHTTPServer((config.HOST, config.PORT), Handler).serve_forever()
