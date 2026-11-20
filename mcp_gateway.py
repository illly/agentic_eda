"""MCP gateway: 등록한 HTTP GET API(CSV)를 MCP 도구로 제공한다.

데이터는 모델에 직접 보내지 않는다. 모델은 파이프라인을 만든다.
  데이터셋 도구   → 소스 노드 (CSV를 인메모리 DB(DuckDB) 테이블로 적재, 스키마만 돌려준다)
  pipeline_node   → SQL 노드 (DB 뷰. 만들 때는 실행하지 않고 출력 컬럼만 돌려준다)
  pipeline_output → 최종 노드. 이때 파이프라인이 실행되고 결과(표·요약·차트)가 모델에 간다
현재 파이프라인은 GET /pipeline 으로 볼 수 있고, 에이전트 화면이 이것을 그린다.

실행: python mcp_gateway.py
  http://127.0.0.1:8001/      등록 화면 (gateway.html) — API 추가·삭제·상태 확인
  http://127.0.0.1:8001/docs  Swagger 화면 — 도구를 하나씩 직접 실행
  http://127.0.0.1:8001/view/<노드 id>  노드의 공유 링크 — 그 노드의 데이터를 표로 본다 (viewer.html)
  http://127.0.0.1:8001/mcp   MCP 접속 주소 (Streamable HTTP)

등록 내용은 apis.json에 저장된다. 매 요청마다 다시 읽으므로 재시작이 필요 없다.
DB와 파이프라인은 메모리에만 있다. 게이트웨이를 끄면 사라진다.
"""
import json
import re
import sys
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

import anyio
import duckdb
import httpx
import plotly.express as px
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from mcp import types
from mcp.server.lowlevel import Server
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.routing import Mount

HERE = Path(__file__).parent
REGISTRY = HERE / "apis.json"
CHARTS = HERE / "charts"
BASE_URL = "http://127.0.0.1:8001"
MAX_CHARS = 10_000              # 도구 결과 글자 수 상한. 모델 컨텍스트를 지킨다
MAX_LINES = 200                 # 원본 미리보기 줄 수 상한
MAX_ROWS = 200                  # 표 결과 행 수 상한
SUMMARY_COLUMNS = 40            # 스키마·요약에서 한 번에 보여줄 컬럼 수
PREVIEW_COLUMNS = 50            # 화면의 노드 미리보기에 보낼 컬럼 수
CHART_MAX_ROWS = 20_000
MAX_DOWNLOAD = 500 * 1024 * 1024
SQL_TIMEOUT = 30                # 초. 넘으면 질의를 중단한다
RESULT = "result"               # 최종 노드 id
RESERVED = ("pipeline_node", "pipeline_remove", "pipeline_output", "pipeline_clear", RESULT)
NODE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")

# ── 인메모리 DB ───────────────────────────────────────────────────────
# 모델이 쓴 SQL이 이 PC의 파일을 읽거나 쓰지 못하게 막는다. 적재용 임시 폴더만 예외로 연다.
DOWNLOADS = Path(tempfile.mkdtemp(prefix="mcp_gateway_"))
DB = duckdb.connect()
DB.execute("SET allowed_directories=['%s']" % DOWNLOADS.as_posix().replace("'", "''"))
DB.execute("SET enable_external_access=false")
DB.execute("SET memory_limit='2GB'")
DB.execute("SET lock_configuration=true")
LOADED = {}                     # 테이블 이름 -> 적재한 URL
PIPELINE = {}                   # 노드 id -> 노드. 넣은 순서대로 그린다
# shortcut: 파이프라인은 게이트웨이에 하나뿐이다(에이전트 대화도 하나), 여러 사용자가 쓰게 되면 세션별로 나눈다


class Api(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$",  # LLM function 이름 규칙
                      description="도구 이름이자 DB 테이블 이름. 영문·숫자·_·- 1~64자", examples=["secom"])
    description: str = Field("", description="모델이 도구를 고를 때 읽는 설명",
                             examples=["SECOM 센서 데이터 (CSV)"])
    url: str = Field(pattern=r"^https?://", description="GET으로 CSV를 돌려주는 주소",
                     examples=["http://127.0.0.1:8000/secom"])

    @field_validator("name")
    @classmethod
    def not_reserved(cls, value):
        if value in RESERVED:
            raise ValueError("게이트웨이가 쓰는 이름은 쓸 수 없습니다: %s" % ", ".join(RESERVED))
        return value


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


# ── 원본 미리보기 (등록 화면의 상태 확인 · Swagger) ───────────────────

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


# ── SQL 실행과 결과 표시 ──────────────────────────────────────────────

def ident(name):
    return '"%s"' % str(name).replace('"', '""')


@contextmanager
def cursor():
    """호출마다 커서를 새로 쓴다(스레드 간 안전). SQL_TIMEOUT초를 넘으면 질의를 중단한다."""
    cur = DB.cursor()
    timer = threading.Timer(SQL_TIMEOUT, cur.interrupt)
    timer.start()
    try:
        yield cur
    finally:
        timer.cancel()
        cur.close()


def run_sql(sql, max_rows):
    """(컬럼 이름, 행, 더 있는지). 결과는 max_rows행까지만 가져온다."""
    with cursor() as cur:
        cur.execute(sql)
        if cur.description is None:
            return [], [], False
        rows = cur.fetchmany(max_rows + 1)
        return [d[0] for d in cur.description], rows[:max_rows], len(rows) > max_rows


def cell(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return "%.6g" % value
    return str(value)


def render(columns, rows, more, hint):
    """탭 구분 표. 숫자 CSV는 토큰을 많이 먹으므로 유효숫자를 줄이고 상한에서 자른다."""
    text = "\n".join(["\t".join(columns)] + ["\t".join(cell(v) for v in row) for row in rows])
    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS] + "\n[글자 수 상한에서 잘림. %s]" % hint
    if more:
        text += "\n[앞 %d행만 표시. %s]" % (len(rows), hint)
    return text or "(결과 없음)"


# ── 파이프라인 노드 ───────────────────────────────────────────────────

def refresh():
    """노드가 바뀔 때마다 모든 노드의 출력 컬럼을 다시 확인한다. 앞 노드를 바꿔 뒤 노드가 깨지면 여기서 드러난다."""
    PIPELINE.pop(RESULT, None)      # 파이프라인이 바뀌면 최종 노드는 다시 붙여야 한다
    for node in list(PIPELINE.values()):
        try:
            _, rows, _ = run_sql("DESCRIBE %s" % ident(node["id"]), 100_000)
            # [이름, 타입] 쌍. 화면이 노드 사이의 스키마 변화를 컬럼 이름으로 비교한다
            node["columns"], node["error"] = [[r[0], r[1]] for r in rows], ""
        except duckdb.Error as exc:
            node["columns"], node["error"] = [], str(exc)


def node_text(node):
    """모델에게 돌려줄 노드 설명. 스키마만 있고 데이터는 없다."""
    columns = node["columns"]
    text = "노드 %s 준비됨: %s%d열.\n컬럼(이름 타입): %s" % (
        ident(node["id"]), "%d행 × " % node["rows"] if "rows" in node else "", len(columns),
        ", ".join("%s %s" % tuple(c) for c in columns[:SUMMARY_COLUMNS]))
    if len(columns) > SUMMARY_COLUMNS:
        text += " … 외 %d개" % (len(columns) - SUMMARY_COLUMNS)
    broken = [n["id"] for n in PIPELINE.values() if n["error"]]
    if broken:
        text += "\n주의: 지금 깨져 있는 노드가 있다(고쳐야 실행된다): %s" % ", ".join(broken)
    return (text + "\n아직 실행되지 않았다. 데이터는 pipeline_output으로 최종 노드를 붙여야 받는다."
            "\n사용자가 이 노드의 데이터를 표로 보는 공유 링크: %s/view/%s" % (BASE_URL, node["id"]))


def add_source(name):
    _, count, _ = run_sql("SELECT count(*) FROM %s" % ident(name), 1)
    # columns·error는 refresh()가 채운다. 그 사이에 화면이 읽어 가도 깨지지 않게 빈 값으로 넣어 둔다
    PIPELINE[name] = {"id": name, "kind": "source", "title": "", "sql": "", "inputs": [], "rows": count[0][0],
                      "columns": [], "error": ""}
    refresh()
    return node_text(PIPELINE[name])


async def download(url, path):
    size = 0
    async with httpx.AsyncClient(timeout=60) as client, client.stream("GET", url) as response:
        response.raise_for_status()
        with open(path, "wb") as f:
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise ValueError("응답이 %dMB를 넘어 적재하지 않습니다." % (MAX_DOWNLOAD >> 20))
                f.write(chunk)


def create_table(name, path):
    # sample_size=-1: 파일 전체를 보고 타입을 정한다. 숫자 컬럼에 깨진 문자열이 섞여 있으면 VARCHAR가 된다
    run_sql("CREATE OR REPLACE TABLE %s AS SELECT * FROM read_csv('%s', sample_size=-1)"
            % (ident(name), path.as_posix().replace("'", "''")), 1)


async def load_dataset(api, refresh_data=False):
    """CSV를 받아 테이블로 만들고 소스 노드로 놓는다. 이미 적재했으면 다시 받지 않는다."""
    if refresh_data or LOADED.get(api.name) != api.url:
        path = DOWNLOADS / (uuid.uuid4().hex + ".csv")
        try:
            await download(api.url, path)
            await anyio.to_thread.run_sync(create_table, api.name, path)
        finally:
            path.unlink(missing_ok=True)
        LOADED[api.name] = api.url
    return await anyio.to_thread.run_sync(add_source, api.name)


def tables(sql):
    """SQL이 참조하는 테이블 이름들. 실행하지 않고 구문만 분석한다. 이것이 노드 사이의 연결선이 된다."""
    with cursor() as cur:
        tree = json.loads(cur.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
    found = set()

    def walk(part):
        if isinstance(part, dict):
            if part.get("type") == "BASE_TABLE":
                found.add(part["table_name"])
            for value in part.values():
                walk(value)
        elif isinstance(part, list):
            for value in part:
                walk(value)

    walk(tree)
    return found


def depends_on(name, target):
    return name == target or any(depends_on(i, target) for i in PIPELINE.get(name, {}).get("inputs", []))


def set_node(node_id, sql, title=""):
    if not NODE_ID.match(node_id) or node_id == RESULT:
        raise ValueError("노드 id는 영문·숫자·_ 로 된 1~40자여야 하고 '%s'는 쓸 수 없습니다." % RESULT)
    if node_id in LOADED or node_id in load_apis():
        raise ValueError("데이터셋 이름과 같은 id는 쓸 수 없습니다: %s" % node_id)
    with cursor() as cur:
        statements = cur.extract_statements(sql)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError("sql은 SELECT 문 하나여야 합니다.")
    names = tables(sql)
    if any(depends_on(name, node_id) for name in names):
        raise ValueError("노드가 자기 자신을 (다른 노드를 거쳐서라도) 참조할 수 없습니다.")
    for name in names:
        if name in LOADED and name not in PIPELINE:
            add_source(name)        # 적재만 되어 있고 그림에 없는 데이터셋은 소스 노드로 놓는다
    run_sql("CREATE OR REPLACE VIEW %s AS %s" % (ident(node_id), sql.strip().rstrip(";")), 1)
    PIPELINE[node_id] = {"id": node_id, "kind": "sql", "title": title, "sql": sql,
                         "inputs": [n for n in PIPELINE if n in names], "columns": [], "error": ""}
    refresh()
    return node_text(PIPELINE[node_id])


def remove_node(node_id):
    PIPELINE.pop(RESULT, None)
    node = PIPELINE.get(node_id)
    if node is None:
        raise ValueError("그런 노드가 없습니다. 있는 노드: %s" % (", ".join(PIPELINE) or "(없음)"))
    users = [n["id"] for n in PIPELINE.values() if node_id in n["inputs"]]
    if users:
        raise ValueError("이 노드를 쓰는 노드가 있습니다. 먼저 바꾸거나 지우세요: %s" % ", ".join(users))
    if node["kind"] == "sql":
        run_sql("DROP VIEW IF EXISTS %s" % ident(node_id), 1)
    del PIPELINE[node_id]
    return "노드 %s 을(를) 지웠습니다. 남은 노드: %s" % (node_id, ", ".join(PIPELINE) or "(없음)")


def clear():
    for node in list(PIPELINE.values()):
        if node["kind"] == "sql":
            run_sql("DROP VIEW IF EXISTS %s" % ident(node["id"]), 1)
    PIPELINE.clear()                # 적재한 테이블은 남긴다. 다시 쓸 때 내려받지 않는다
    return "파이프라인을 비웠습니다."


# ── 최종 노드: 실행해서 결과를 만든다 ─────────────────────────────────

def short(value):
    """SUMMARIZE는 통계값을 긴 문자열로 준다(3030.930000000000291). 유효숫자 6자리로 줄인다."""
    if isinstance(value, str):
        try:
            return "%.6g" % float(value)
        except ValueError:
            pass
    return value


def summarize(table, columns=None):
    selected = ", ".join(ident(c) for c in columns) if columns else "*"
    names, rows, more = run_sql("SUMMARIZE SELECT %s FROM %s" % (selected, ident(table)), SUMMARY_COLUMNS)
    rows = [tuple(row[:2]) + tuple(short(v) for v in row[2:]) for row in rows]
    return render(names, rows, more,
                  "columns로 컬럼을 지정하거나, SELECT … FROM (SUMMARIZE %s) WHERE … 노드를 만들어 골라서 본다" % ident(table))


def query(sql, limit=None):
    names, rows, more = run_sql(sql, min(MAX_ROWS, max(1, int(limit or 50))))
    return render(names, rows, more, "집계·필터 노드를 추가해 결과를 줄인다")


CHARTS_BY_KIND = {"bar": px.bar, "line": px.line, "scatter": px.scatter,
                  "histogram": px.histogram, "box": px.box, "heatmap": px.density_heatmap}
OUTPUTS = ["table", "summary"] + list(CHARTS_BY_KIND)


def chart(sql, kind, x, y=None, color=None, title=None):
    if not x:
        raise ValueError("차트에는 x가 필요합니다.")
    with cursor() as cur:
        df = cur.execute("SELECT * FROM (%s) LIMIT %d" % (sql.strip().rstrip(";"), CHART_MAX_ROWS + 1)).df()
    if len(df) > CHART_MAX_ROWS:
        raise ValueError("차트 데이터가 %d행을 넘습니다. 집계하거나 USING SAMPLE로 줄이는 노드를 추가하세요." % CHART_MAX_ROWS)
    if df.empty:
        raise ValueError("노드의 결과가 비어 있습니다.")
    missing = [c for c in (x, y, color) if c and c not in df.columns]
    if missing:
        raise ValueError("노드에 없는 컬럼: %s. 있는 컬럼: %s" % (", ".join(missing), ", ".join(df.columns)))
    options = {"x": x, "y": y, "title": title, "z" if kind == "heatmap" else "color": color}
    if kind == "heatmap":
        options["histfunc"] = "avg"
    figure = CHARTS_BY_KIND[kind](df, **{k: v for k, v in options.items() if v})
    name = uuid.uuid4().hex[:12] + ".html"
    # shortcut: 차트 파일은 지우지 않고 쌓인다, 많아지면 charts/ 폴더를 비운다
    figure.write_html(CHARTS / name, include_plotlyjs="cdn")
    return ("차트를 만들었습니다 (%s, %d행): %s/charts/%s\n답변에 이 주소를 그대로 적어 사용자에게 알려 준다."
            % (kind, len(df), BASE_URL, name))


def finish(node_id, output, limit=None, columns=None, x=None, y=None, color=None, title=None):
    """최종 노드를 붙이고 파이프라인을 실행한다. 데이터가 모델에게 가는 유일한 길이다."""
    PIPELINE.pop(RESULT, None)
    if node_id not in PIPELINE:
        raise ValueError("그런 노드가 없습니다. 있는 노드: %s" % (", ".join(PIPELINE) or "(없음)"))
    if output not in OUTPUTS:
        raise ValueError("output은 %s 중 하나여야 합니다." % ", ".join(OUTPUTS))
    result = PIPELINE[RESULT] = {"id": RESULT, "kind": "output", "title": output, "sql": "", "inputs": [node_id],
                                 "columns": [], "error": "", "status": "running"}
    try:
        if output == "table":
            text = query("SELECT * FROM %s" % ident(node_id), limit)
        elif output == "summary":
            text = summarize(node_id, columns)
        else:
            text = chart("SELECT * FROM %s" % ident(node_id), output, x, y, color, title)
    except Exception as exc:
        result["status"], result["error"] = "error", str(exc)
        raise
    result["status"] = "done"
    return text


# ── 도구 목록과 실행 ──────────────────────────────────────────────────

DATASET_SCHEMA = {"type": "object", "properties": {
    "refresh": {"type": "boolean", "default": False,
                "description": "true면 원본 API에서 다시 받아 테이블을 새로 만든다"}}}
DATASET_NOTE = (" — 호출하면 이 데이터셋을 파이프라인의 소스 노드로 놓고 스키마만 돌려준다(데이터는 돌려주지 않는다)."
                " 노드 id와 테이블 이름은 도구 이름과 같다.")

PIPELINE_TOOLS = [
    types.Tool(
        name="pipeline_node",
        description="파이프라인에 SQL 노드를 추가한다. 이미 있는 id면 그 노드를 바꾼다. 실행하지 않고 출력 컬럼만 돌려준다. "
                    "sql은 DuckDB SELECT 문 하나이고, 다른 노드와 데이터셋을 id로 테이블처럼 참조한다(참조가 곧 연결선이다). "
                    "사용자가 화면에서 단계마다 스키마가 바뀌는 것을 보므로, WITH로 여러 단계를 한 노드에 넣지 말고 "
                    "단계마다 노드를 나눈다(정제, 형태 변환, 집계, 순위 등). 이름에 특수문자가 있으면 큰따옴표로 감싼다. "
                    "숫자여야 하는데 VARCHAR인 컬럼은 TRY_CAST(col AS DOUBLE)로 읽는다.",
        input_schema={"type": "object", "required": ["id", "sql"], "properties": {
            "id": {"type": "string", "description": "노드 id. 영문·숫자·_ (예: clean, by_wafer)"},
            "sql": {"type": "string", "description": "DuckDB SELECT 문 하나"},
            "title": {"type": "string", "description": "화면에 보일 짧은 설명 (예: 결측 많은 센서 제외)"}}}),
    types.Tool(
        name="pipeline_remove",
        description="노드 하나를 지운다. 다른 노드가 참조하는 노드는 지울 수 없다.",
        input_schema={"type": "object", "required": ["id"], "properties": {
            "id": {"type": "string", "description": "지울 노드 id"}}}),
    types.Tool(
        name="pipeline_output",
        description="최종 노드를 붙이고 파이프라인을 실행해 결과를 돌려준다. 데이터를 받는 유일한 방법이다. "
                    "table은 행을 탭 구분 표로(최대 %d행, %d자), summary는 컬럼별 요약 통계(타입·최소·최대·고유값 수·평균·"
                    "표준편차·사분위수·행 수·결측률 %%, 한 번에 %d개 컬럼)를, 나머지는 차트를 만들어 주소를 돌려준다. "
                    "차트 그림은 모델이 볼 수 없다. 차트 데이터는 %d행 이하여야 한다. 실행이 실패하면 노드를 고쳐 다시 호출한다."
                    % (MAX_ROWS, MAX_CHARS, SUMMARY_COLUMNS, CHART_MAX_ROWS),
        input_schema={"type": "object", "required": ["node", "output"], "properties": {
            "node": {"type": "string", "description": "결과를 낼 노드 id"},
            "output": {"type": "string", "enum": OUTPUTS,
                       "description": "histogram은 x만 쓴다. heatmap은 x·y 칸마다 color 컬럼의 평균을 칠한다"},
            "limit": {"type": "integer", "default": 50, "description": "table: 돌려줄 최대 행 수 (최대 %d)" % MAX_ROWS},
            "columns": {"type": "array", "items": {"type": "string"}, "description": "summary: 요약할 컬럼들. 생략하면 앞에서부터"},
            "x": {"type": "string", "description": "차트: x축 컬럼"},
            "y": {"type": "string", "description": "차트: y축 컬럼"},
            "color": {"type": "string", "description": "차트: 색으로 구분할 컬럼 (heatmap에서는 값 컬럼)"},
            "title": {"type": "string", "description": "차트: 제목"}}}),
    types.Tool(
        name="pipeline_clear",
        description="파이프라인의 모든 노드를 지운다. 앞선 분석과 이어지지 않는 새 분석을 시작할 때 쓴다.",
        input_schema={"type": "object", "properties": {}}),
]


def tool_list():
    datasets = [types.Tool(name=api.name, description=api.description + DATASET_NOTE, input_schema=DATASET_SCHEMA)
                for api in load_apis().values()]
    return datasets + PIPELINE_TOOLS


TOOL_ERRORS = (httpx.HTTPError, duckdb.Error, ValueError, TypeError, KeyError)


async def call(name, args):
    """도구 실행. MCP와 Swagger(/tools/{name})가 같은 코드를 탄다."""
    run = anyio.to_thread.run_sync      # DuckDB·Plotly는 블로킹이라 스레드에서 돌린다
    if name == "pipeline_node":
        return await run(set_node, args["id"], args["sql"], args.get("title") or "")
    if name == "pipeline_remove":
        return await run(remove_node, args["id"])
    if name == "pipeline_output":
        return await run(finish, args["node"], args["output"], args.get("limit"), args.get("columns"),
                         args.get("x"), args.get("y"), args.get("color"), args.get("title"))
    if name == "pipeline_clear":
        return await run(clear)
    api = load_apis().get(name)
    if api is None:
        raise ValueError("등록되지 않은 도구: %s" % name)
    return await load_dataset(api, bool(args.get("refresh")))


# ── MCP ───────────────────────────────────────────────────────────────

async def list_tools(_ctx, _params):
    return types.ListToolsResult(tools=tool_list())


async def call_tool(_ctx, params):
    try:
        text, failed = await call(params.name, params.arguments or {}), False
    except TOOL_ERRORS as exc:
        text, failed = "%s: %s" % (type(exc).__name__, exc), True
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=failed)


# ── 등록 API와 Swagger (/docs) ────────────────────────────────────────

web = FastAPI(title="MCP 게이트웨이",
              description="등록한 HTTP GET API(CSV)를 MCP 도구로 제공합니다. MCP 접속 주소는 `/mcp` 입니다.")
CHARTS.mkdir(exist_ok=True)
web.mount("/charts", StaticFiles(directory=CHARTS))


@web.get("/", include_in_schema=False)
async def home():
    return FileResponse(HERE / "gateway.html")


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


@web.get("/pipeline", summary="현재 파이프라인의 노드들 (에이전트 화면이 이것을 그린다)", tags=["파이프라인"])
async def pipeline_state() -> list[dict]:
    return list(PIPELINE.values())


@web.get("/view/{node_id}", include_in_schema=False)
async def view(node_id: str):
    """노드의 공유 링크. 테이블 뷰어(viewer.html)가 주소에서 노드 id를 읽어 아래 /rows를 부른다."""
    return FileResponse(HERE / "viewer.html")


@web.get("/pipeline/{node_id}/rows", tags=["파이프라인"],
         summary="노드의 데이터 (공유 링크의 테이블 뷰어가 쓴다. 모델에게는 가지 않는다)")
async def pipeline_rows(node_id: str, limit: int = Query(100, ge=1, le=MAX_ROWS)) -> dict:
    node = PIPELINE.get(node_id)
    if node is None or node["kind"] == "output":
        raise HTTPException(404, "이 노드는 지금 파이프라인에 없습니다.")
    try:
        names, rows, more = await anyio.to_thread.run_sync(run_sql, "SELECT * FROM %s" % ident(node_id), limit)
    except duckdb.Error as exc:
        raise HTTPException(400, str(exc))
    return {"columns": names[:PREVIEW_COLUMNS], "total_columns": len(names), "more_rows": more,
            "rows": [[cell(v) for v in row[:PREVIEW_COLUMNS]] for row in rows]}


# 아래 두 경로는 Swagger에 그대로 띄우지 않고, 등록된 API·도구마다 하나씩 펼쳐서 띄운다 — openapi()
@web.get("/apis/{name}/test", include_in_schema=False, response_class=PlainTextResponse)
async def apis_test(name: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=MAX_LINES)) -> str:
    api = load_apis().get(name)
    if api is None:
        raise HTTPException(404, "그런 이름의 API가 없습니다.")
    try:
        return await fetch(api.url, offset, limit)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "%s: %s" % (type(exc).__name__, exc))


@web.post("/tools/{name}", include_in_schema=False, response_class=PlainTextResponse)
async def tools_call(name: str, args: dict = Body(default={})) -> str:
    try:
        return await call(name, args)
    except TOOL_ERRORS as exc:
        raise HTTPException(400, "%s: %s" % (type(exc).__name__, exc))


def openapi():
    """Swagger 문서를 요청 때마다 새로 만든다 (등록 후 /docs 새로고침)."""
    schema = get_openapi(title=web.title, version=web.version, description=web.description, routes=web.routes)
    text = {"content": {"text/plain": {"schema": {"type": "string"}}}}
    for tool in tool_list():
        schema["paths"]["/tools/%s" % tool.name] = {"post": {
            "tags": ["도구 실행 (MCP 도구와 동일)"],
            "summary": tool.name,
            "description": tool.description,
            "operationId": "tool_" + tool.name,
            "requestBody": {"content": {"application/json": {"schema": tool.input_schema}}},
            "responses": {"200": dict(text, description="모델이 받는 결과 그대로"),
                          "400": {"description": "도구 오류 (모델에게는 오류 문장으로 전달된다)"}},
        }}
    for api in load_apis().values():
        schema["paths"]["/apis/%s/test" % api.name] = {"get": {
            "tags": ["원본 API 미리보기"],
            "summary": api.name,
            "description": "%s\n\n원본 API: %s\n\nDB를 거치지 않고 원본 응답의 일부 줄만 보여준다." % (api.description, api.url),
            "operationId": "preview_" + api.name,
            "parameters": [
                {"name": "offset", "in": "query", "description": "건너뛸 줄 수. 0이면 첫 줄(CSV 헤더)부터",
                 "schema": {"type": "integer", "minimum": 0, "default": 0}},
                {"name": "limit", "in": "query", "description": "가져올 줄 수. 글자 수 상한에 걸리면 더 적게 온다",
                 "schema": {"type": "integer", "minimum": 1, "maximum": MAX_LINES, "default": 20}},
            ],
            "responses": {"200": dict(text, description="응답의 해당 줄 구간"),
                          "502": {"description": "원본 API 호출 실패"}},
        }}
    return schema


web.openapi = openapi

server = Server("api-gateway", on_list_tools=list_tools, on_call_tool=call_tool)
# /mcp는 MCP 앱이 직접 받고, 나머지 경로는 FastAPI(web)로 넘긴다
app = server.streamable_http_app(json_response=True, stateless_http=True,
                                 custom_starlette_routes=[Mount("/", app=web)])

if __name__ == "__main__":
    # shortcut: CSV를 돌려주는 고정 URL GET만 지원(쿼리 파라미터·POST·인증·JSON 응답 없음), 필요해지면 Api 모델에 필드를 추가한다
    load_apis()  # 시작할 때 apis.json 형식 오류를 바로 드러낸다
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")
