"""python test_gateway.py — 게이트웨이 자체 점검. 실제 apis.json은 건드리지 않는다."""
import asyncio
import json
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from starlette.testclient import TestClient

import mcp_gateway

PAGES = {
    "/ok": "\n".join(["header"] + ["row%d" % i for i in range(300)] + ["x" * 30_000]),
    # v는 숫자, broken은 숫자 사이에 깨진 값이 섞인 컬럼
    "/csv": "g,v,broken\n" + "\n".join("%s,%d,%s" % ("ab"[i % 2], i, "Okt.96" if i == 250 else i) for i in range(300)),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path, "").encode()
        self.send_response(200 if self.path in PAGES else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % httpd.server_port
url = base + "/ok"

# ── 원본 미리보기: 줄 단위 페이징과 글자 수 상한 ──────────────────────
get = lambda offset, limit: asyncio.run(mcp_gateway.fetch(url, offset, limit))
first = get(0, 3).split("\n")
assert first[:3] == ["header", "row0", "row1"] and "offset=3" in first[3], first
assert get(299, 2).split("\n")[:2] == ["row298", "row299"]
big = get(0, 100_000)                       # 줄 수가 아니라 글자 수 상한에 걸린다
assert len(big) < mcp_gateway.MAX_CHARS + 100 and "더 있음" in big
assert "줄이 길어 잘림" in get(301, 1)       # 한 줄이 상한보다 길 때
assert "데이터 없음" in get(999, 5)
try:
    asyncio.run(mcp_gateway.fetch(base + "/nope", 0, 1))
    raise AssertionError("404가 오류가 되어야 한다")
except mcp_gateway.httpx.HTTPStatusError:
    pass
print("ok (preview)")

mcp_gateway.REGISTRY = Path(tempfile.mkdtemp()) / "apis.json"
mcp_gateway.REGISTRY.write_text('[{"name": "hand written, invalid"}]', encoding="utf-8")
with TestClient(mcp_gateway.app, base_url="http://127.0.0.1:8001") as client:
    # ── 등록: 추가 -> 목록 -> 덮어쓰기 -> 삭제 ───────────────────────
    api = {"name": "t1", "description": "d", "url": url}
    assert client.post("/apis", json=api).status_code == 200
    assert client.post("/apis", json=dict(api, description="d2")).status_code == 200
    assert client.get("/apis").json() == [dict(api, description="d2")]
    assert client.post("/apis", json=dict(api, name="bad name")).status_code == 422
    assert client.post("/apis", json=dict(api, name="pipeline_node")).status_code == 422   # 내장 도구 이름
    assert client.post("/apis", json=dict(api, url="file:///etc/passwd")).status_code == 422
    # 다른 웹 페이지가 브라우저로 몰래 등록할 수 있는 형식(text/plain, 폼)은 거부해야 한다
    sneaky = dict(api, name="t2")
    assert client.post("/apis", content=json.dumps(sneaky), headers={"Content-Type": "text/plain"}).status_code == 422
    assert client.post("/apis", data=sneaky).status_code == 422
    assert client.get("/apis/t1/test", params={"limit": 2}).text.splitlines()[:2] == ["header", "row0"]
    assert client.get("/apis/nope/test").status_code == 404
    paths = lambda: client.get("/openapi.json").json()["paths"]
    assert paths()["/apis/t1/test"]["get"]["summary"] == "t1"      # 등록된 API가 Swagger에 각각 뜬다
    assert {"/tools/t1", "/tools/pipeline_node", "/tools/pipeline_output", "/pipeline"} <= set(paths())
    assert client.delete("/apis/t1").status_code == 200
    assert client.delete("/apis/t1").status_code == 404
    assert "/apis/t1/test" not in paths() and "/tools/t1" not in paths() and "/apis" in paths()
    assert "mcp-gateway" in client.get("/").text
    assert client.get("/docs").status_code == 200
    print("ok (registry)")

    # ── 파이프라인: 소스 노드 -> SQL 노드 -> 변경 -> 최종 노드(실행) ──
    tool = lambda name, **args: client.post("/tools/" + name, json=args)
    nodes = lambda: {n["id"]: n for n in client.get("/pipeline").json()}
    assert client.post("/apis", json={"name": "my-data", "description": "", "url": base + "/csv"}).status_code == 200
    assert tool("pipeline_node", id="early", sql='SELECT * FROM "my-data"').status_code == 400   # 적재 전에는 없다
    loaded = tool("my-data").text
    assert "300행 × 3열" in loaded and "v BIGINT" in loaded and "broken VARCHAR" in loaded, loaded
    assert "\t" not in loaded and "Okt" not in loaded            # 스키마만 온다. 데이터는 오지 않는다
    PAGES["/csv"] = "g,v\na,1\n"                                # 원본이 바뀌어도
    assert "300행" in tool("my-data").text                      # 다시 받지 않고
    assert "1행 × 2열" in tool("my-data", refresh=True).text    # refresh로만 새로 받는다
    PAGES["/csv"] = "g,v\n" + "\n".join("%s,%d" % ("ab"[i % 2], i) for i in range(300))
    tool("my-data", refresh=True)

    made = tool("pipeline_node", id="by_g", title="그룹별 집계",
                sql='SELECT g, count(*) AS n, avg(v) AS mean FROM "my-data" GROUP BY g ORDER BY g').text
    assert "g VARCHAR, n BIGINT, mean DOUBLE" in made and "149" not in made, made      # 컬럼만, 값은 없다
    assert tool("pipeline_node", id="top", sql="SELECT g, mean FROM by_g WHERE n > 0").status_code == 200
    assert nodes()["by_g"]["inputs"] == ["my-data"] and nodes()["top"]["inputs"] == ["by_g"]   # 참조가 연결선
    assert list(nodes()) == ["my-data", "by_g", "top"] and nodes()["my-data"]["rows"] == 300
    assert nodes()["top"]["columns"] == [["g", "VARCHAR"], ["mean", "DOUBLE"]]      # 화면이 노드마다 스키마 변화를 그린다

    for bad in (dict(id="x", sql="SELECT 1; DROP TABLE \"my-data\""), dict(id="x", sql="DROP TABLE \"my-data\""),
                dict(id="bad id", sql="SELECT 1"), dict(id="result", sql="SELECT 1"), dict(id="my-data", sql="SELECT 1"),
                dict(id="by_g", sql="SELECT * FROM top"),                              # 순환
                dict(id="x", sql="SELECT * FROM read_text('%s')" % mcp_gateway.REGISTRY.as_posix())):   # 파일 접근
        assert tool("pipeline_node", **bad).status_code == 400, bad
    assert list(nodes()) == ["my-data", "by_g", "top"]           # 실패한 호출은 파이프라인을 바꾸지 않는다

    done = tool("pipeline_output", node="top", output="table")
    assert done.text.splitlines() == ["g\tmean", "a\t149", "b\t150"], done.text       # 이때 실행되어 데이터가 온다
    assert nodes()["result"]["status"] == "done" and nodes()["result"]["inputs"] == ["top"]

    # 공유 링크: 노드 데이터를 사람이 표로 본다
    assert "/view/top" in tool("pipeline_node", id="top", sql="SELECT g, mean FROM by_g WHERE n > 0").text
    assert "PIPELINE NODE" in client.get("/view/top").text
    preview = client.get("/pipeline/top/rows", params={"limit": 1}).json()
    assert preview == {"columns": ["g", "mean"], "total_columns": 2, "more_rows": True, "rows": [["a", "149"]]}, preview
    assert client.get("/pipeline/result/rows").status_code == 404 and client.get("/pipeline/nope/rows").status_code == 404

    changed = tool("pipeline_node", id="by_g", sql='SELECT g, count(*) AS n FROM "my-data" GROUP BY g').text
    assert "깨져 있는 노드" in changed and "top" in changed       # 앞 노드를 바꿔 뒤 노드가 깨진 것을 알려 준다
    assert "result" not in nodes() and nodes()["top"]["error"]   # 파이프라인이 바뀌면 최종 노드는 떨어진다
    assert tool("pipeline_output", node="top", output="table").status_code == 400
    assert nodes()["result"]["status"] == "error"
    assert tool("pipeline_remove", id="by_g").status_code == 400                       # 쓰는 노드가 있다
    assert tool("pipeline_remove", id="top").status_code == 200 and list(nodes()) == ["my-data", "by_g"]

    summary = tool("pipeline_output", node="my-data", output="summary", columns=["v"]).text.splitlines()
    assert summary[0].startswith("column_name\tcolumn_type") and summary[1].startswith("v\tBIGINT\t0\t299"), summary
    limited = tool("pipeline_output", node="my-data", output="table", limit=5).text
    assert len(limited.splitlines()) == 7 and "앞 5행만" in limited, limited
    tool("pipeline_node", id="huge", sql="SELECT repeat('x', 30000) AS t")
    assert tool("pipeline_output", node="huge", output="table").text.count("x") <= mcp_gateway.MAX_CHARS

    drawn = tool("pipeline_output", node="by_g", output="bar", x="g", y="n", title="t")
    name = re.search(r"/charts/([0-9a-f]+\.html)", drawn.text).group(1)
    assert "plotly" in client.get("/charts/" + name).text.lower()
    (mcp_gateway.CHARTS / name).unlink()
    assert tool("pipeline_output", node="by_g", output="bar", x="nope").status_code == 400
    assert tool("pipeline_output", node="by_g", output="pie", x="g").status_code == 400
    assert tool("pipeline_output", node="nope", output="table").status_code == 400

    # ── MCP: 같은 도구가 /mcp로도 나온다 ─────────────────────────────
    rpc = lambda method, params: client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={"Accept": "application/json, text/event-stream"}).json()["result"]
    assert [t["name"] for t in rpc("tools/list", {})["tools"]] == [
        "my-data", "pipeline_node", "pipeline_remove", "pipeline_output", "pipeline_clear"]
    result = rpc("tools/call", {"name": "pipeline_output", "arguments": {"node": "by_g", "output": "table"}})
    assert result["content"][0]["text"].splitlines()[0] == "g\tn" and not result.get("isError"), result
    assert rpc("tools/call", {"name": "pipeline_node", "arguments": {"id": "x", "sql": "SELECT nope"}})["isError"]
    assert tool("pipeline_clear").status_code == 200 and nodes() == {}
    assert tool("pipeline_node", id="again", sql="SELECT 1 AS one").status_code == 200
    assert tool("pipeline_node", id="reuse", sql='SELECT count(*) AS n FROM "my-data"').status_code == 200
    assert list(nodes()) == ["again", "my-data", "reuse"]        # 적재돼 있던 데이터셋은 소스 노드로 다시 놓인다
    tool("pipeline_clear")
    print("ok (pipeline, mcp)")

assert {"name": "hand written, invalid"} in mcp_gateway.read_registry()   # 남의 항목은 보존
