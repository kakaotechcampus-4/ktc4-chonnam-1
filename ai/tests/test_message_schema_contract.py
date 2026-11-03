"""contracts/schemas/message-case-result.schema.json 이 MessageCaseResult 와 어긋나지 않게 지킨다.

모델을 바꿨다면 아래로 스키마를 다시 만든다.
    python -X utf8 ai/tests/test_message_schema_contract.py
"""

from __future__ import annotations

import json
from pathlib import Path

from ai.case.types import MessageCaseResult

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "schemas"
    / "message-case-result.schema.json"
)
SCHEMA_ID = "https://github.com/kakaotechcampus-4/ktc4-chonnam-1/blob/develop/contracts/schemas/message-case-result.schema.json"
TITLE = "링크 없는 문자 분석 결과 (MessageCaseResult)"
DESCRIPTION = (
    "ai.case.analyze_message_case 와 apply_message_answer 가 돌려주는 링크 없는 문자(가족·지인·기관 사칭) 분석 결과. 백엔드가 저장해 두었다가 "
    "카카오 콜백 카드와 '결과 확인' 조회에 쓴다. 판정은 결정적 로직이 하며 "
    "LLM 설명(explanation)은 부가 정보다."
)


def build_schema() -> dict:
    schema = MessageCaseResult.model_json_schema(mode="serialization")
    head = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": TITLE,
        "description": DESCRIPTION,
    }
    return {**head, **{k: v for k, v in schema.items() if k not in head}}


def test_committed_schema_matches_model():
    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert committed == build_schema(), (
        "MessageCaseResult 가 바뀌었습니다. 이 파일을 직접 실행해 스키마를 다시 만드세요."
    )


if __name__ == "__main__":
    SCHEMA_PATH.write_text(
        json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {SCHEMA_PATH}")
