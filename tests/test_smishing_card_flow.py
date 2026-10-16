"""기존 R1·R9의 버튼으로 저장된 상세를 조회하는 연결만 검증한다."""

import httpx
import pytest

import main
from services.result_card_renderer import render_result_card


@pytest.mark.parametrize("has_signal", [True, False])
async def test_existing_result_button_opens_the_saved_analysis(monkeypatch, has_signal):
    payload = {
        "url": {"official": "brand_mismatch" if has_signal else "unresolved"},
        "message": {
            "brand": "CJ대한통운", "answer": "risk_found" if has_signal else "no_risk_found",
            "details": {
                "signals": [{"code": "install_prompt", "evidence": "앱을 설치해 주세요"}]
                if has_signal else [],
                "reason": {"text": "택배 배송을 내세운 문자입니다.", "failures": []},
            },
        },
        "env": {"answer": "not_run", "details": {"reason": {"failures": ["missing_result"]}}},
    }
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {
        "parcel-job": {"user_id": "parcel-user", "status": "completed", "final_payload": payload},
    })
    result = render_result_card(payload, job_id="parcel-job")
    output = result["template"]["outputs"][0]
    card = output.get("basicCard") or output["textCard"]
    button = next(b for b in card["buttons"] if b["label"] == "자세히 보기")
    assert button["action"] == "block"
    assert button["blockId"] == "6aad3abfe745d3358bd90c8d"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test",
    ) as client:
        response = await client.post("/kakao/skill", json={
            "userRequest": {"utterance": "자세히 보기", "user": {"id": "parcel-user"}},
            "action": {"clientExtra": button["extra"]},
        })

    assert response.status_code == 200
    items = response.json()["template"]["outputs"][0]["carousel"]["items"]
    descriptions = "\n".join(item["description"] for item in items)
    assert "페이지 내용은 확인하지 않았어요" in descriptions
    if has_signal:
        assert "앱을 설치해 주세요" in descriptions
    else:
        assert len(items) == 2  # 확인 범위 + 행동 안내. 다른 분석 결과가 섞이지 않는다.
