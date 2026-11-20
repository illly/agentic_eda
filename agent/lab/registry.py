# -*- coding: utf-8 -*-
"""AI에게 넘길 도구 목록. 지금은 MCP 서버(mcp_config.json)가 알려준 도구뿐이다.

항목 = {"name", "description", "parameters", "source", "call"(인자 dict -> 결과)}
내장 도구를 추가하려면 build()에서 같은 형태의 항목을 entries에 더한다.
"""

from . import config, mcp_client


def build():
    """도구 목록과 사용자에게 보여줄 오류 문구를 함께 돌려준다."""
    entries, errors = mcp_client.connect_all(config.MCP_CONFIG_PATH)
    seen, unique = set(), []
    for entry in entries:
        if entry["name"] not in seen:   # 같은 이름이 겹치면 먼저 등록된 쪽을 남긴다
            seen.add(entry["name"])
            unique.append(entry)
    return unique, errors


def declarations(entries):
    """모델에 넘길 함수 선언 {name, description, parameters}로 추린다. API 형식은 llm.py가 맞춘다."""
    out = []
    for e in entries:
        params = e.get("parameters") or {"type": "object", "properties": {}}
        if not params.get("properties"):
            params = {"type": "object", "properties": {}}
        out.append({"name": e["name"],
                    "description": e.get("description", ""),
                    "parameters": params})
    return out


def find(entries, name):
    for e in entries:
        if e["name"] == name:
            return e
    return None
