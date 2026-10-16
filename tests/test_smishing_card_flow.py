"""대표 스미싱 결과에서 상세 근거까지 이동할 수 있는 카드 계약."""

import pytest

from services.kakao_card_renderer import load_card_template
from services.result_card_renderer import render_detail_carousel, render_result_card


def analysis(has_signal):
    return {
        "url": {"official": "brand_mismatch", "domain": "parcel-test.example"},
        "message": {
            "brand": "CJ대한통운", "category": "택배",
            "answer": "risk_found" if has_signal else "no_risk_found",
            "details": {
                "signals": [{"code": "install_prompt", "evidence": "배송 확인 앱을 설치해 주세요"}]
                if has_signal else [],
                "doubts": [{"value": "앱 설치", "evidence": "배송 확인 앱을 설치해 주세요"}],
                "reason": {"text": "문자는 CJ대한통운을 내세우며 앱 설치를 요구합니다.", "failures": []},
            },
        },
        "env": {"answer": "not_run", "details": {"signals": [], "reason": {"failures": ["missing_result"]}}},
    }


@pytest.mark.parametrize("has_signal", [True, False])
def test_nonofficial_parcel_result_opens_its_saved_details(has_signal):
    result = render_result_card(analysis(has_signal), job_id="parcel-job")
    output = result["template"]["outputs"][0]
    card = output.get("basicCard") or output["textCard"]
    detail = next(button for button in card["buttons"] if button["label"] == "자세히 보기")
    assert detail["action"] == "block"
    assert detail["blockId"] == "6aad3abfe745d3358bd90c8d"
    assert detail["extra"] == {"job_id": "parcel-job"}


def test_details_use_text_carousel_for_multiline_evidence():
    carousel = render_detail_carousel(analysis(True))["template"]["outputs"][0]["carousel"]
    # basicCard 캐러셀은 두 줄 뒤의 의심 이유·안내가 숨겨진다.
    assert carousel["type"] == "textCard"
    items = carousel["items"]
    assert "배송 확인 앱을 설치해 주세요" in items[0]["description"]
    assert "악성 앱일 수 있어요" in items[0]["description"]
    assert "페이지 내용은 확인하지 않았어요" in items[1]["description"]
    assert all("thumbnail" not in item and len(item["description"]) <= 128 for item in items)


def test_guidance_template_matches_text_details():
    carousel = load_card_template("c4-guidance")["template"]["outputs"][0]["carousel"]
    assert carousel["type"] == "textCard"
    assert "thumbnail" not in carousel["items"][0]


def test_long_remote_control_quote_keeps_the_reason_within_text_card_limit():
    payload = analysis(True)
    payload["message"]["details"]["signals"] = [{
        "code": "remote_control", "evidence": "원격 지원 앱을 설치하고 연결해 주세요. " + "가" * 100,
    }]

    items = render_detail_carousel(payload)["template"]["outputs"][0]["carousel"]["items"]

    assert len(items[0]["description"]) <= 128
    assert "다른 사람이 내 휴대폰을 조작할 수 있어요" in items[0]["description"]
