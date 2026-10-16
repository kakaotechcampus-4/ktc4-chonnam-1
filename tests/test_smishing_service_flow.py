"""실제 내부 처리와 가짜 외부 HTTP로 스미싱 서비스 전체 흐름을 검증한다."""

import json

import httpx
import pytest
from openai import AsyncOpenAI

import ai.pipeline.analysis as message_pipeline
import ai.llm.page as page_llm
import main
from scanner.isolation_client import collect_url_isolated


TEXT = "[CJ대한통운] 주소 불일치로 배송이 보류되었습니다. 배송 확인 앱을 설치해 주세요."
LINK = "https://parcel-test.example.com/"
REQUEST = "배송 확인 앱을 설치해 주세요"
FORM = ('<html><body><form><label for="pw">계좌 비밀번호</label>'
        '<input id="pw" type="password" name="pw"></form></body></html>')
HEADERS = {"X-Skill-Secret": "synthetic-skill-secret"}


@pytest.mark.parametrize("callback_fails", [False, True])
@pytest.mark.parametrize("collection", ["clean", "partial", "failed", "unavailable"])
async def test_input_callback_details_and_check_result_share_one_analysis(monkeypatch, callback_fails, collection):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("KAKAO_SKILL_SECRET", HEADERS["X-Skill-Secret"])
    monkeypatch.setenv("KAKAO_CALLBACK_HOSTS", "bot-api.kakao.com")
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.delenv("URLSCAN_API_KEY", raising=False)
    monkeypatch.setenv("ISOLATION_API_URL", "https://collector.example")
    monkeypatch.setenv("ISOLATION_API_TOKEN", "synthetic-collector-token")
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {})
    monkeypatch.setattr(main, "RUNNING_USERS", set())
    calls = {"llm": 0, "collect": 0, "callback": 0}
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
            elif schema == "SignalProposal":
                parsed = {"signals": [{"code": "install_prompt", "evidence_source": "message",
                                        "evidence_ref": REQUEST}]}
            else:
                assert schema == "PageProposal"
                page_input = json.loads(json.loads(request.content)["messages"][1]["content"])
                assert page_input["page_text"] == ("계좌 비밀번호" if collection == "partial" else "배송 조회 안내")
                parsed = {"signals": ([{"code": "credential_request", "evidence_source": "observation",
                                        "evidence_ref": "element-0003"}] if collection == "partial" else [])}
            return httpx.Response(200, json={
                "id": "synthetic-completion", "object": "chat.completion", "created": 0,
                "model": "test", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)}}],
            })
        if request.url.host == "collector.example":
            calls["collect"] += 1
            assert request.url.path == "/collect"
            assert request.headers["Authorization"] == "Bearer synthetic-collector-token"
            assert json.loads(request.content) == {"url": LINK}
            if collection == "unavailable":
                return httpx.Response(503)
            return httpx.Response(200, json={
                "input_url": LINK, "final_url": LINK, "redirect_chain": [],
                "status_code": None if collection == "failed" else 200,
                "content_type": "text/html", "title": None, "elapsed_ms": 10,
                "html": "" if collection == "failed" else (FORM if collection == "partial" else "<p>배송 조회 안내</p>"),
                "failures": {"clean": [], "partial": ["html_truncated"],
                             "failed": ["connection_failed"]}[collection],
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
    monkeypatch.setattr(page_llm, "create_client", message_pipeline.create_client)

    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(transport=transport) as upstream_client:
            monkeypatch.setattr(main, "get_http_client", lambda: upstream_client)
            async def collect(link):
                return await collect_url_isolated(link, client=upstream_client)
            monkeypatch.setattr(main, "collect_url_isolated", collect)
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
                assert payload["env"]["answer"] == {
                    "clean": "no_risk_found", "partial": "partial",
                    "failed": "failed", "unavailable": "failed",
                }[collection]
                assert payload["env"]["details"]["reason"]["failures"] == {
                    "clean": [], "partial": ["partial_content"],
                    "failed": ["collection_failed"], "unavailable": ["collection_failed"],
                }[collection]
                assert payload["url"]["final_url"] == (LINK if collection in {"clean", "partial"} else None)
                assert payload["env"]["details"]["signals"] == (
                    [{"code": "credential_request", "evidence": "계좌 비밀번호 입력 필드"}]
                    if collection == "partial" else [])
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
                assert len(items) >= 3
                assert any("1588-1255" in item["description"] for item in items)

                checked = await client.post("/kakao/skill", json={
                    "intent": {"name": "결과 확인"},
                    "userRequest": {"utterance": "결과 확인", "user": {"id": "parcel-user"}},
                })
                outputs = checked.json()["template"]["outputs"]
                assert "에 확인한 결과예요" in outputs[0]["simpleText"]["text"]
                assert outputs[1:] == job["result"]["template"]["outputs"]
                assert "parcel-user" not in main.RUNNING_USERS
                assert calls == {"llm": 2 if collection in {"failed", "unavailable"} else 3,
                                 "collect": 1, "callback": 1}
