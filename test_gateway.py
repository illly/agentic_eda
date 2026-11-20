"""python test_gateway.py — fetch()의 줄 단위 페이징과 글자 수 상한을 확인한다."""
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mcp_gateway

BODY = "\n".join(["header"] + ["row%d" % i for i in range(300)] + ["x" * 30_000]).encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/ok" else 404)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *args):
        pass


httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/ok" % httpd.server_port
get = lambda offset, limit: asyncio.run(mcp_gateway.fetch(url, offset, limit))

first = get(0, 3).split("\n")
assert first[:3] == ["header", "row0", "row1"] and "offset=3" in first[3], first
assert get(299, 2).split("\n")[:2] == ["row298", "row299"]
big = get(0, 100_000)                       # 줄 수가 아니라 글자 수 상한에 걸린다
assert len(big) < mcp_gateway.MAX_CHARS + 100 and "더 있음" in big
assert "줄이 길어 잘림" in get(301, 1)       # 한 줄이 상한보다 길 때
assert "데이터 없음" in get(999, 5)
try:
    asyncio.run(mcp_gateway.fetch(url.replace("/ok", "/nope"), 0, 1))
    raise AssertionError("404가 오류가 되어야 한다")
except mcp_gateway.httpx.HTTPStatusError:
    pass
print("ok")

# 등록 API: 추가 -> 목록 -> 덮어쓰기 -> 호출 테스트 -> 삭제. 실제 apis.json은 건드리지 않는다.
import json
import tempfile
from pathlib import Path

from starlette.testclient import TestClient

mcp_gateway.REGISTRY = Path(tempfile.mkdtemp()) / "apis.json"
mcp_gateway.REGISTRY.write_text('[{"name": "hand written, invalid"}]', encoding="utf-8")
with TestClient(mcp_gateway.app, base_url="http://127.0.0.1:8001") as client:
    api = {"name": "t1", "description": "d", "url": url}
    assert client.post("/apis", json=api).status_code == 200
    assert client.post("/apis", json=dict(api, description="d2")).status_code == 200
    assert client.get("/apis").json() == [dict(api, description="d2")]
    assert client.post("/apis", json=dict(api, name="bad name")).status_code == 422
    assert client.post("/apis", json=dict(api, url="file:///etc/passwd")).status_code == 422
    # 다른 웹 페이지가 브라우저로 몰래 등록할 수 있는 형식(text/plain, 폼)은 거부해야 한다
    sneaky = dict(api, name="t2")
    assert client.post("/apis", content=json.dumps(sneaky), headers={"Content-Type": "text/plain"}).status_code == 422
    assert client.post("/apis", data=sneaky).status_code == 422
    assert client.get("/apis/t1/test", params={"limit": 2}).text.splitlines()[:2] == ["header", "row0"]
    assert client.get("/apis/nope/test").status_code == 404
    paths = lambda: client.get("/openapi.json").json()["paths"]
    assert paths()["/apis/t1/test"]["get"]["summary"] == "t1"      # 등록된 API가 Swagger에 각각 뜬다
    assert client.delete("/apis/t1").status_code == 200
    assert client.delete("/apis/t1").status_code == 404
    assert "/apis/t1/test" not in paths() and "/apis" in paths()   # 삭제하면 Swagger에서도 사라진다
    assert "MCP 게이트웨이" in client.get("/").text
    assert client.get("/docs").status_code == 200
    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                        headers={"Accept": "application/json, text/event-stream"})
    assert tools.status_code == 200 and tools.json()["result"]["tools"] == [], tools.text   # /mcp가 FastAPI에 가려지지 않는다
assert mcp_gateway.read_registry() == [{"name": "hand written, invalid"}]   # 남의 항목은 보존
print("ok (registry)")
