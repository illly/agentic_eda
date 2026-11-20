# -*- coding: utf-8 -*-
"""skills/<폴더>/SKILL.md 를 load_skill 도구로 제공한다.

SKILL.md 맨 위 --- 블록에 name · description을 적고 그 아래에 지침 본문을 쓴다.
모델은 도구 설명에서 skill 목록만 보고, 필요할 때 load_skill로 본문을 불러온다.
본문을 매 요청의 지시문에 싣지 않으려는 것이다. 파일을 고치면 다음 요청부터 반영된다.
"""

import os

from . import config


def parse(text):
    """(머리말 dict, 본문). 머리말은 `키: 값` 줄만 읽는다."""
    meta, body = {}, text
    if text.startswith("---"):
        head, _, body = text[3:].partition("\n---")
        for line in head.splitlines():
            key, found, value = line.partition(":")
            if found:
                meta[key.strip()] = value.strip()
    return meta, body.strip()


def load():
    """{이름: {"description", "body"}}"""
    found = {}
    if not os.path.isdir(config.SKILLS_DIR):
        return found
    for folder in sorted(os.listdir(config.SKILLS_DIR)):
        path = os.path.join(config.SKILLS_DIR, folder, "SKILL.md")
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8-sig") as f:
            meta, body = parse(f.read())
        found[meta.get("name") or folder] = {"description": meta.get("description", ""), "body": body}
    return found


def entries():
    """registry에 넣을 도구 항목. skill이 하나도 없으면 빈 목록."""
    found = load()
    if not found:
        return []

    def call(args):
        skill = load().get(args.get("name"))
        if skill is None:
            return {"오류": "그런 skill이 없습니다. 있는 skill: %s" % ", ".join(found)}
        return skill["body"]

    listing = "\n".join("- %s: %s" % (name, skill["description"]) for name, skill in found.items())
    return [{
        "name": "load_skill",
        "description": "작업 지침(skill)의 본문을 불러온다. 아래 목록에 요청과 관련된 skill이 있으면 "
                       "다른 도구를 쓰기 전에 먼저 불러와 그 지침을 따른다. 대화에서 이미 불러왔으면 다시 부르지 않는다.\n" + listing,
        "parameters": {"type": "object", "required": ["name"],
                       "properties": {"name": {"type": "string", "enum": list(found)}}},
        "source": "skill",
        "call": call,
    }]
