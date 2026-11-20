"""python test_skills.py — skill 파일이 load_skill 도구로 올바르게 읽히는지 확인한다."""
from lab import skills

meta, body = skills.parse("---\nname: a\ndescription: b: c\n---\n\n# 제목\n본문 --- 유지\n")
assert meta == {"name": "a", "description": "b: c"} and body == "# 제목\n본문 --- 유지", (meta, body)
assert skills.parse("머리말 없음") == ({}, "머리말 없음")

entry, = skills.entries()
assert "semiconductor-eda: 반도체" in entry["description"]
text = entry["call"]({"name": "semiconductor-eda"})
assert text.startswith("# 반도체 데이터 EDA 지침") and "description:" not in text
assert "오류" in entry["call"]({"name": "nope"})
print("ok")
