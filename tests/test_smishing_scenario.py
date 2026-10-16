"""실제 내부 처리와 가짜 외부 HTTP로 스미싱 한 건의 사용자 흐름을 검증한다."""

import json

import httpx
import pytest
from openai import AsyncOpenAI

import ai.pipeline.analysis as message_pipeline
import main
from backend.src.server import urlscan_service


TEXT = "[CJ대한통운] 주소 불일치로 배송이 보류되었습니다. 배송 확인 앱을 설치해 주세요."
LINK = "https://parcel-test.example.com/"
REQUEST = "배송 확인 앱을 설치해 주세요"
HEADERS = {"X-Skill-Secret": "synthetic-skill-secret"}


@pytest.mark.parametrize("callback_fails", [False, True])
async def test_input_callback_details_and_check_result_share_one_analysis(monkeypatch, callback_fails):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("KAKAO_SKILL_SECRET", HEADERS["X-Skill-Secret"])
    monkeypatch.setenv("KAKAO_CALLBACK_HOSTS", "bot-api.kakao.com")
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr(urlscan_service, "URLSCAN_API_KEY", "synthetic-urlscan-key")
    monkeypatch.setattr(main, "ENABLE_URL_COLLECTOR_SHADOW", False)
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {})
    monkeypatch.setattr(main, "RUNNING_USERS", set())
    calls = {"llm": 0, "scan": 0, "callback": 0}
    delivered = []

    def upstream(request):
        if request.url.host == "llm.example":
            calls["llm"] += 1
            schema = json.loads(request.content)["response_format"]["json_schema"]["name"]
            if schema == "ExtractedMessage":
                parsed = {
                    "categories": [{"code": "address_correction", "custom_label": None,
                                    "evidence": "주소 불일치"}],
                    "claimed_sender": {"value": "CJ대한통운", "evidence": "CJ대한통운"},
                    "claimed_purpose": {"value": "배송 보류", "evidence": "배송이 보류되었습니다"},
                    "requested_actions": [{"value": "앱 설치", "evidence": REQUEST}],
                    "persuasion_signals": [],
                }
            else:
                assert schema == "SignalProposal"
                parsed = {"signals": [{"code": "install_prompt", "evidence_source": "message",
                                        "evidence_ref": REQUEST}]}
            return httpx.Response(200, json={
                "id": "synthetic-completion", "object": "chat.completion", "created": 0,
                "model": "test", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)}}],
            })
        if request.url.host == "urlscan.io":
            if request.method == "POST":
                calls["scan"] += 1
                assert json.loads(request.content)["url"] == LINK
                return httpx.Response(200, json={"uuid": "synthetic-scan"})
            assert request.url.path == "/api/v1/result/synthetic-scan/"
            return httpx.Response(200, json={
                "task": {"url": LINK},
                "page": {"url": LINK, "domain": "parcel-test.example.com", "title": "합성 테스트"},
                "verdicts": {"urlscan": {"score": 0, "malicious": False, "categories": [], "brands": []}},
            })
        assert request.url.host == "bot-api.kakao.com"
        calls["callback"] += 1
        delivered.append(json.loads(request.content))
        if callback_fails:
            raise httpx.ConnectError("synthetic callback failure", request=request)
        return httpx.Response(200, json={"status": "SUCCESS"})

    transport = httpx.MockTransport(upstream)
    monkeypatch.setattr(message_pipeline, "create_client", lambda timeout: AsyncOpenAI(
        api_key="synthetic-llm-key", base_url="https://llm.example/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=transport),
    ))

    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=transport) as upstream_client:
            monkeypatch.setattr(main, "get_http_client", lambda: upstream_client)
            monkeypatch.setattr(urlscan_service, "get_http_client", lambda: upstream_client)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test", headers=HEADERS,
            ) as client:
                response = await client.post("/kakao/skill", json={
                    "intent": {"name": "문자 확인"},
                    "userRequest": {"utterance": f"{TEXT} {LINK}", "user": {"id": "parcel-user"},
                                    "callbackUrl": "https://bot-api.kakao.com/callback/synthetic"},
                })
                assert response.status_code == 200
                assert response.json()["useCallback"] is True
                job = main.find_latest_job_by_user("parcel-user")
                assert job["status"] == "completed"
                assert job["callback_status"] == ("failed" if callback_fails else "success")
                assert delivered == [job["result"]]
                payload = job["final_payload"]
                assert payload["message"]["brand"] == "CJ대한통운"
                assert payload["message"]["details"]["signals"] == [
                    {"code": "install_prompt", "evidence": REQUEST},
                ]
                assert payload["env"]["answer"] == "not_run"
                card = job["result"]["template"]["outputs"][0]["basicCard"]
                assert "앱을 설치하지 마세요" in card["description"]
                button = next(b for b in card["buttons"] if b["label"] == "자세히 보기")

                detail = await client.post("/kakao/skill", json={
                    "userRequest": {"utterance": "자세히 보기", "user": {"id": "parcel-user"}},
                    "action": {"clientExtra": button["extra"]},
                })
                assert detail.status_code == 200
                items = detail.json()["template"]["outputs"][0]["carousel"]["items"]
                assert REQUEST in items[0]["description"]
                assert "페이지 내용은 확인하지 않았어요" in items[1]["description"]
                assert any("1588-1255" in item["description"] for item in items)

                checked = await client.post("/kakao/skill", json={
                    "intent": {"name": "결과 확인"},
                    "userRequest": {"utterance": "결과 확인", "user": {"id": "parcel-user"}},
                })
                outputs = checked.json()["template"]["outputs"]
                assert "에 확인한 결과예요" in outputs[0]["simpleText"]["text"]
                assert outputs[1:] == job["result"]["template"]["outputs"]
                assert "parcel-user" not in main.RUNNING_USERS
                assert calls == {"llm": 2, "scan": 1, "callback": 1}
