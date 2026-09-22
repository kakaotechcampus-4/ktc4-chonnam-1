import json
from pathlib import Path


TEMPLATE_DIR = (
    Path(__file__).parent
    / "kakao_templates"
)


def load_kakao_template(
    template_name: str
) -> dict:
    """
    FE가 정의한 Kakao 카드 템플릿 JSON을 읽는다.
    """

    template_path = (
        TEMPLATE_DIR
        / f"{template_name}.json"
    )

    if not template_path.exists():
        raise ValueError(
            f"Kakao template not found: "
            f"{template_name}"
        )

    with template_path.open(
        "r",
        encoding="utf-8"
    ) as file:
        return json.load(file)


def render_r1_lookalike() -> dict:
    """
    R1 - lookalike 카드 렌더링.

    현재는 FE-BE 연동 확인 단계이므로
    템플릿을 그대로 반환한다.

    추후 실제 분석 결과를 받아
    description 등의 동적 값을 채운다.
    """

    return load_kakao_template(
        "r1-lookalike"
    )