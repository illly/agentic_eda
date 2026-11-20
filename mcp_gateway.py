"""MCP gateway: 등록한 HTTP GET API를 MCP 도구로 제공한다.

실행: python mcp_gateway.py
  http://127.0.0.1:8001/      등록 화면 (gateway.html) — API 추가·삭제·호출 테스트
  http://127.0.0.1:8001/docs  Swagger 화면 — 같은 기능을 API로 직접 호출
  http://127.0.0.1:8001/mcp   MCP 접속 주소 (Streamable HTTP)

등록 내용은 apis.json에 저장된다. 매 요청마다 다시 읽으므로 재시작이 필요 없다.
"""
import json
import sys
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, PlainTextResponse
from mcp import types
from mcp.server.lowlevel import Server
from pydantic import BaseModel, Field, ValidationError
from starlette.routing import Mount

REGISTRY = Path(__file__).parent / "apis.json"
MAX_CHARS = 10_000  # 한 번에 돌려줄 글자 수 상한. 53MB CSV가 통째로 모델 컨텍스트에 들어가지 않게 한다
MAX_LINES = 200

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "offset": {"type": "integer", "default": 0,
                   "description": "건너뛸 줄 수. 0이면 첫 줄(CSV 헤더)부터"},
        "limit": {"type": "integer", "default": 20,
                  "description": "가져올 줄 수 (최대 %d). 글자 수 상한에 걸리면 더 적게 온다" % MAX_LINES},
    },
}


class Api(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$",  # LLM function 이름 규칙
                      description="도구 이름. 영문·숫자·_·- 1~64자", examples=["secom"])
    description: str = Field("", description="모델이 도구를 고를 때 읽는 설명",
                             examples=["SECOM 센서 데이터 (CSV)"])
    url: str = Field(pattern=r"^https?://", description="GET으로 호출할 주소",
                     examples=["http://127.0.0.1:8000/secom"])


def read_registry():
    return json.loads(REGISTRY.read_text(encoding="utf-8-sig"))


def write_registry(items):
    REGISTRY.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def without(items, name):
    """이름이 같은 항목만 뺀다. 손으로 적은 다른 항목(형식이 틀린 것 포함)은 건드리지 않는다."""
    return [a for a in items if not (isinstance(a, dict) and a.get("name") == name)]


def load_apis():
    apis = {}
    for item in read_registry():
        try:
            api = Api.model_validate(item)
        except ValidationError:
            print("apis.json: 잘못된 항목을 건너뜀: %r" % (item,), file=sys.stderr)
            continue
        apis[api.name] = api
    return apis


async def fetch(url, offset, limit):
    """응답을 줄 단위로 읽어 [offset, offset+limit) 구간만 돌려준다. 필요한 만큼만 받고 연결을 끊는다."""
    lines, size, more, n = [], 0, False, -1
    async with httpx.AsyncClient(timeout=30) as client, client.stream("GET", url) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            n += 1
            if n < offset:
                continue
            if len(lines) == limit or (lines and size + len(line) > MAX_CHARS):
                more = True
                break
            if len(line) > MAX_CHARS:
                line = line[:MAX_CHARS] + " …(줄이 길어 잘림)"
            lines.append(line)
            size += len(line) + 1
    if not lines:
        return "[데이터 없음: offset=%d 이 끝을 넘었습니다]" % offset
    text = "\n".join(lines)
    if more:
        text += "\n[%d~%d번째 줄. 더 있음: 다음은 offset=%d]" % (offset, offset + len(lines) - 1, offset + len(lines))
    return text


# ── MCP ───────────────────────────────────────────────────────────────

async def list_tools(_ctx, _params):
    return types.ListToolsResult(tools=[
        types.Tool(name=api.name, description=api.description, input_schema=INPUT_SCHEMA)
        for api in load_apis().values()])


async def call_tool(_ctx, params):
    args = params.arguments or {}
    try:
        api = load_apis().get(params.name)
        if api is None:
            raise ValueError("등록되지 않은 API: %s" % params.name)
        text = await fetch(api.url,
                           max(0, int(args.get("offset") or 0)),
                           min(MAX_LINES, max(1, int(args.get("limit") or 20))))
        failed = False
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        text, failed = "%s: %s" % (type(exc).__name__, exc), True
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=failed)


# ── 등록 API (Swagger: /docs) ─────────────────────────────────────────

web = FastAPI(title="MCP 게이트웨이",
              description="등록한 HTTP GET API를 MCP 도구로 제공합니다. MCP 접속 주소는 `/mcp` 입니다.")


@web.get("/", include_in_schema=False)
async def home():
    return FileResponse(Path(__file__).parent / "gateway.html")


@web.get("/apis", summary="등록된 API 목록", tags=["등록 관리"])
async def apis_list() -> list[Api]:
    return list(load_apis().values())


@web.post("/apis", summary="API 등록 (같은 이름이면 덮어쓴다)", tags=["등록 관리"])
async def apis_add(api: Api) -> Api:
    write_registry(without(read_registry(), api.name) + [api.model_dump()])
    return api


@web.delete("/apis/{name}", summary="API 삭제", tags=["등록 관리"])
async def apis_delete(name: str) -> dict:
    items = read_registry()
    kept = without(items, name)
    if len(kept) == len(items):
        raise HTTPException(404, "그런 이름의 API가 없습니다.")
    write_registry(kept)
    return {"deleted": name}


# Swagger에는 이 경로 대신 등록된 API마다 하나씩(/apis/secom/test …) 띄운다 — 아래 openapi()
@web.get("/apis/{name}/test", include_in_schema=False, response_class=PlainTextResponse)
async def apis_test(name: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=MAX_LINES)) -> str:
    api = load_apis().get(name)
    if api is None:
        raise HTTPException(404, "그런 이름의 API가 없습니다.")
    try:
        return await fetch(api.url, offset, limit)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "%s: %s" % (type(exc).__name__, exc))


def openapi():
    """Swagger 문서를 요청 때마다 새로 만든다. 등록된 API가 각각 한 항목으로 뜬다 (등록 후 /docs 새로고침)."""
    schema = get_openapi(title=web.title, version=web.version, description=web.description, routes=web.routes)
    for api in load_apis().values():
        schema["paths"]["/apis/%s/test" % api.name] = {"get": {
            "tags": ["등록된 API (MCP 도구)"],
            "summary": api.name,
            "description": "%s\n\n원본 API: %s\n\nMCP 도구 `%s` 가 모델에게 돌려줄 내용을 그대로 보여준다."
                           % (api.description, api.url, api.name),
            "operationId": "test_" + api.name,
            "parameters": [
                {"name": "offset", "in": "query", "description": INPUT_SCHEMA["properties"]["offset"]["description"],
                 "schema": {"type": "integer", "minimum": 0, "default": 0}},
                {"name": "limit", "in": "query", "description": INPUT_SCHEMA["properties"]["limit"]["description"],
                 "schema": {"type": "integer", "minimum": 1, "maximum": MAX_LINES, "default": 20}},
            ],
            "responses": {"200": {"description": "응답의 해당 줄 구간",
                                  "content": {"text/plain": {"schema": {"type": "string"}}}},
                          "502": {"description": "원본 API 호출 실패"}},
        }}
    return schema


web.openapi = openapi


server = Server("api-gateway", on_list_tools=list_tools, on_call_tool=call_tool)
# /mcp는 MCP 앱이 직접 받고, 나머지 경로는 FastAPI(web)로 넘긴다
app = server.streamable_http_app(json_response=True, stateless_http=True,
                                 custom_starlette_routes=[Mount("/", app=web)])

if __name__ == "__main__":
    # shortcut: 쿼리 파라미터·POST·인증 헤더 없음(고정 URL GET만). 그런 API를 등록해야 할 때 Api 모델에 필드를 추가한다
    load_apis()  # 시작할 때 apis.json 형식 오류를 바로 드러낸다
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")
