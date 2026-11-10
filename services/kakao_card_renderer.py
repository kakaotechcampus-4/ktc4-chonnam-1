"""`frontend/kakao-templates/cards/`의 카드 JSON을 직접 읽어 렌더링한다.

be_teammate_card_integration_tasks.md 2절 기준. BE 내부에 카드 사본을
새로 만들지 않고 FE 소유 디렉터리를 그대로 읽는다 — 문구가 바뀌어도
BE 배포 없이 반영되게 하려는 경계다 (backend/README.md의 소유 경계와
같은 원칙).
"""

import json
import re
from pathlib import Path
from typing import Any

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

CARDS_DIR = (
    Path(__file__).resolve().parent.parent
    / "frontend"
    / "kakao-templates"
    / "cards"
)


class CardNotFoundError(ValueError):
    pass


def _substitute(node: Any, values: dict[str, Any]) -> Any:
    """`{{ key }}` placeholder를 JSON 구조를 유지한 채로 치환한다.

    - dict/list는 재귀적으로 내려가며, 리프 문자열만 치환한다.
    - 문자열 전체가 정확히 `{{ key }}` 하나뿐이면 원래 타입(숫자·불린 등)을
      보존하기 위해 해당 값으로 완전히 대체한다.
    - 문자열 안에 여러 placeholder나 다른 텍스트와 섞여 있으면 부분 치환한다.
    - `values`에 없는 placeholder(예: FE가 나중에 채울 `{{ block_xxx }}`)는
      건드리지 않고 그대로 남긴다 — 구조를 깨뜨리지 않기 위해서다.
    """

    if isinstance(node, dict):
        return {key: _substitute(value, values) for key, value in node.items()}

    if isinstance(node, list):
        return [_substitute(item, values) for item in node]

    if isinstance(node, str):
        return _substitute_string(node, values)

    return node


def _substitute_string(text: str, values: dict[str, Any]) -> Any:
    # 문자열 전체가 placeholder 하나뿐이면 값의 원래 타입(목록·숫자 등)을 보존한다.
    whole = _PLACEHOLDER_RE.fullmatch(text)
    if whole:
        return values.get(whole.group(1), text)

    # 한 번에 치환한다. 키마다 차례로 바꾸면 먼저 들어간 값 안의 `{{ key }}` 가 다시 치환된다.
    return _PLACEHOLDER_RE.sub(
        lambda match: str(values[match.group(1)]) if match.group(1) in values else match.group(0),
        text,
    )


def load_card_template(card_name: str) -> dict:
    """`<card_name>.json` 원본을 그대로 읽는다 (치환 없음, 수정 없음)."""

    path = CARDS_DIR / f"{card_name}.json"

    if not path.exists():
        raise CardNotFoundError(f"카드를 찾을 수 없습니다: {card_name}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def render_card(card_name: str, values: dict[str, Any] | None = None) -> dict:
    """카드 JSON을 읽어 `{{ placeholder }}`를 채운 Kakao 응답 dict를 반환한다.

    원본 JSON 파일은 수정하지 않는다 (매번 새로 읽는다).
    """

    template = load_card_template(card_name)
    return substitute_values(template, values or {})


def substitute_values(node: Any, values: dict[str, Any]) -> Any:
    """카드 JSON의 일부(예: 캐러셀의 item 하나)에도 같은 치환 규칙을
    적용하고 싶을 때 쓰는 공개 함수. `render_card()`도 내부적으로 이걸 쓴다.
    """

    return _substitute(node, values)


def kakao_response(text: str) -> dict:
    """글만 담은 카카오 SkillResponse(simpleText). 카드 템플릿이 없는 짧은 안내에 쓴다."""

    return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": text}}]}}
