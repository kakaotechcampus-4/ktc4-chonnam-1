"""카카오 스킬 웹훅.

지금은 에코. 1주차 목표는 카톡에서 응답이 뜨는 것까지입니다.
"""

import json
from pathlib import Path

from fastapi import APIRouter, Request

router = APIRouter()

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


def simple_text(text: str) -> dict:
    return {
        "version": "2.0",
        "template": {"outputs": [{"simpleText": {"text": text}}]},
    }


@router.post("/skill")
async def skill(request: Request) -> dict:
    body = await request.json()

    # 첫 요청 payload를 fixture로 저장해두면 이후 카톡 없이 개발할 수 있습니다.
    sample = FIXTURES / "kakao_request.json"
    if not sample.exists():
        FIXTURES.mkdir(parents=True, exist_ok=True)
        sample.write_text(json.dumps(body, ensure_ascii=False, indent=2))

    utterance = body.get("userRequest", {}).get("utterance", "")
    return simple_text(f"받았습니다: {utterance}")
