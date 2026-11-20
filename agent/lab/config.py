# -*- coding: utf-8 -*-
"""앱 전역 설정과 경로.

.env에서 OPENAI_API_KEY를 읽는다. 키 값은 화면·로그·오류 메시지 어디에도 쓰지 않는다.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "state")
SYSTEM_PROMPT_PATH = os.path.join(STATE_DIR, "system_prompt.txt")
MCP_CONFIG_PATH = os.path.join(ROOT, "mcp_config.json")
SKILLS_DIR = os.path.join(ROOT, "skills")
GATEWAY = "http://127.0.0.1:8001"        # 파이프라인 그림을 가져오는 곳 (MCP 주소는 mcp_config.json)

GEN_MODEL = "gpt-5.6-luna"
REASONING_EFFORT = "none"
TEMPERATURE = 0.0
MAX_TOOL_STEPS = 20                      # 한 요청에서 도구 호출을 반복할 최대 횟수. 파이프라인은 노드마다 한 번씩 부른다

HOST = "127.0.0.1"
PORT = 8002                              # API 서버(8000) · MCP 게이트웨이(8001) 다음


def load_env():
    """앱 폴더의 .env를 읽어 os.environ에 넣는다 (이미 있는 값은 유지)."""
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def api_key():
    """OPENAI_API_KEY를 반환한다. 없으면 예외. 값은 절대 밖으로 내보내지 않는다."""
    load_env()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("NO_API_KEY")
    return key
