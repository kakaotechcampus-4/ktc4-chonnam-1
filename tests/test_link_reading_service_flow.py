"""문자 링크 읽기(read_message, ADR 0007)의 서비스 전체 흐름.

카카오 요청 → 실제 read_message·링크 검증 → 격리 수집 → 최종 판정 → 콜백까지 실제 코드로
돌리고, LLM·수집기·카카오 콜백만 가짜 HTTP 로 둔다. LLM 이 링크를 빠뜨리거나 고치거나
실패할 때 사용자가 받는 카드를 확인한다.
"""

import json

import httpx
import pytest
from openai import AsyncOpenAI

import ai.llm.page as page_llm
import ai.pipeline.analysis as message_pipeline
import main
from scanner.isolation_client import collect_url_isolated


HEADERS = {"X-Skill-Secret": "synthetic-skill-secret"}
USER = "link-reading-user"
SENDER = "[CJ대한통운] 주소 불일치로 배송이 보류되었습니다. "
LLM_TIMEOUT = "timeout"
LLM_ERROR = "error"


def _extraction(urls):
    return {
        "categories": [], "claimed_sender": {"value": "CJ대한통운", "evidence": "CJ대한통운"},
        "claimed_purpose": {"value": None, "evidence": None},
        "requested_actions": [], "persuasion_signals": [], "urls": urls,
    }


async def _run(monkeypatch, utterance, llm_urls, *, html="<p>배송 조회 안내</p>"):
    """요청 하나를 끝까지 돌리고 (job, 호출 수, 수집한 주소)를 돌려준다."""

    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("KAKAO_SKILL_SECRET", HEADERS["X-Skill-Secret"])
    monkeypatch.setenv("KAKAO_CALLBACK_HOSTS", "bot-api.kakao.com")
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setenv("ISOLATION_API_URL", "https://collector.example")
    monkeypatch.setenv("ISOLATION_API_TOKEN", "synthetic-collector-token")
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {})
    monkeypatch.setattr(main, "RUNNING_USERS", set())
    calls = {"extract": 0, "llm": 0, "callback": 0}
    collected = []

    def upstream(request):
        if request.url.host == "llm.example":
            calls["llm"] += 1
            schema = json.loads(request.content)["response_format"]["json_schema"]["name"]
            if schema == "ExtractedMessage":
                calls["extract"] += 1
                if llm_urls == LLM_TIMEOUT:
                    raise httpx.ReadTimeout("synthetic timeout", request=request)
                if llm_urls == LLM_ERROR:
                    return httpx.Response(500, json={"error": {"message": "synthetic"}})
                parsed = _extraction(llm_urls)
            else:
                parsed = {"signals": []}
            return httpx.Response(200, json={
                "id": "synthetic", "object": "chat.completion", "created": 0, "model": "test",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)}}],
            })
        if request.url.host == "collector.example":
            url = json.loads(request.content)["url"]
            collected.append(url)
            return httpx.Response(200, json={
                "input_url": url, "final_url": url, "redirect_chain": [], "status_code": 200,
                "content_type": "text/html", "title": None, "elapsed_ms": 10,
                "html": html, "failures": [],
            })
        assert request.url.host == "bot-api.kakao.com"
        calls["callback"] += 1
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
                    "userRequest": {"utterance": utterance, "user": {"id": USER},
                                    "callbackUrl": "https://bot-api.kakao.com/callback/synthetic"},
                })
    # 링크가 있든 없든 첫 응답은 대기 카드다
    assert response.json() == main.render_card("r5-analyzing")
    job = main.find_latest_job_by_user(USER)
    assert job["status"] == "completed"
    assert calls["callback"] == 1
    assert USER not in main.RUNNING_USERS
    return job, calls, collected


def _texts(result):
    return [output["simpleText"]["text"]
            for output in result["template"]["outputs"] if "simpleText" in output]


async def test_message_without_links_gets_no_link_card(monkeypatch):
    job, calls, collected = await _run(monkeypatch, SENDER + "오늘 중 배송 예정입니다.", [])

    assert job["result"] == main.render_card("r6-input-required")
    assert collected == []
    assert calls["extract"] == 1


@pytest.mark.parametrize("text, urls", [
    # LLM 이 철자를 고쳤다 → 원문에 없는 구간
    (SENDER + "cjlogistlcs.com/track 에서 확인", ["cjlogistics.com/track"]),
    # 긴 주소의 앞만 떼어 왔다 → 공식 주소처럼 보이는 조각
    (SENDER + "cjlogistics.com.evil.top/track 에서 확인", ["cjlogistics.com"]),
    # 링크를 통째로 빠뜨렸다
    (SENDER + "https://evil.top/track 에서 확인", []),
], ids=["corrected", "cut_short", "missed"])
async def test_unread_link_is_never_no_link_or_official(monkeypatch, text, urls):
    job, calls, collected = await _run(monkeypatch, text, urls)

    assert _texts(job["result"]) == [main._UNREAD_LINKS_TEXT]
    assert job["result"] != main.render_card("r6-input-required")
    # 고치거나 잘라 낸 주소로 수집·공식 대조를 하지 않는다
    assert collected == []
    assert job["final_payload"] is None


async def test_partly_read_message_checks_read_link_and_says_so(monkeypatch):
    text = SENDER + "1차 https://a-cj.top/x 2차 https://b-cj.top/y"

    job, calls, collected = await _run(monkeypatch, text, ["https://a-cj.top/x"])

    assert collected == ["https://a-cj.top/x"]
    outputs = job["result"]["template"]["outputs"]
    assert outputs[0] == {"simpleText": {"text": main._PARTLY_READ_LINKS_TEXT}}
    assert len(outputs) == 2
    assert job["final_payload"]["url"]["domain"] == "a-cj.top"


@pytest.mark.parametrize("text, span, url", [
    (SENDER + "evil[.]top/track 에서 확인", "evil[.]top/track", "https://evil.top/track"),
    (SENDER + "evil.top/track로 접속해 주세요", "evil.top/track로", "https://evil.top/track"),
], ids=["defanged", "particle"])
async def test_rewritten_link_is_collected_in_normal_form(monkeypatch, text, span, url):
    job, calls, collected = await _run(monkeypatch, text, [span])

    assert collected == [url]
    assert job["final_payload"]["url"]["official"] != "official"
    assert main._UNREAD_LINKS_TEXT not in _texts(job["result"])


async def test_userinfo_trick_is_judged_by_real_host(monkeypatch):
    link = "https://www.cjlogistics.com@evil.top/a"

    job, calls, collected = await _run(monkeypatch, SENDER + link, [link])

    payload = job["final_payload"]
    assert payload["url"]["domain"] == "evil.top"
    assert payload["url"]["official"] != "official"


async def test_official_link_still_confirms(monkeypatch):
    link = "https://www.cjlogistics.com/ko/tool/parcel/tracking"

    job, calls, collected = await _run(monkeypatch, SENDER + link, [link])

    assert collected == [link]
    assert job["final_payload"]["url"]["official"] == "official"


@pytest.mark.parametrize("failure", [LLM_TIMEOUT, LLM_ERROR])
async def test_failed_reading_is_unavailable_not_no_link(monkeypatch, failure):
    job, calls, collected = await _run(monkeypatch, SENDER + "https://evil.top/a", failure)

    assert job["result"] == main.render_card("r4-unavailable")
    assert collected == []
    assert calls["extract"] == 1


async def test_too_long_message_skips_llm(monkeypatch):
    job, calls, collected = await _run(monkeypatch, "가" * 9000 + " https://evil.top/a", [])

    assert _texts(job["result"]) == [main._TOO_LONG_TEXT]
    assert calls["llm"] == 0
    assert collected == []
