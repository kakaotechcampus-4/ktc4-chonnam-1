"""문자 링크를 read_message() 로 받는 흐름 (ADR 0007).

링크를 찾지 못한 이유에 따라 안내가 달라야 한다. LLM 이 실패했거나 링크를 다 받지
못했으면 "링크 없음"(R6)으로 안내하지 않는다.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

import main
from ai.types import FailureCode
from scanner.models import CollectResult

SECRET = "synthetic-skill-secret"
USER = "reading-user"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("KAKAO_SKILL_SECRET", SECRET)
    monkeypatch.setenv("KAKAO_CALLBACK_HOSTS", "bot-api.kakao.com")
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {})
    monkeypatch.setattr(main, "RUNNING_USERS", set())


@pytest.fixture
def collected(monkeypatch):
    """링크 수집과 문자 분석을 가짜로 두고, 받은 인자를 남긴다."""

    seen = {"collect": [], "message": []}

    async def collect(link):
        seen["collect"].append(link)
        return CollectResult(link, None, (), None, None, "", None, 1, ("timeout",))

    async def analyze(text, **kwargs):
        seen["message"].append((text, kwargs))
        raise RuntimeError("message analysis not under test")

    monkeypatch.setattr(main, "collect_url_isolated", collect)
    monkeypatch.setattr(main, "analyze_message_part", analyze)
    return seen


def _texts(response: dict) -> list[str]:
    return [
        output["simpleText"]["text"]
        for output in response["template"]["outputs"]
        if "simpleText" in output
    ]


async def test_raw_utterance_and_budget_go_to_read_message(stub_read_message, collected):
    calls = stub_read_message(["https://evil.top/a"], "[CJ대한통운] 확인")

    await main.run_analysis("[CJ대한통운] 확인 evil . top/a")

    assert calls == [("[CJ대한통운] 확인 evil . top/a",
                      {"timeout_s": main.READ_MESSAGE_TIMEOUT_SECONDS})]


async def test_reading_is_reused_and_links_are_collected_by_url(stub_read_message, monkeypatch, collected):
    stub_read_message(["https://evil.top/a", "https://bit.ly/x"], "본문")
    reading = await main.read_message("x")

    async def same_reading(text, **_):
        return reading

    monkeypatch.setattr(main, "read_message", same_reading)
    await main.run_analysis("원문")

    assert collected["collect"] == ["https://evil.top/a", "https://bit.ly/x"]
    # 추출 LLM 을 다시 부르지 않도록 같은 읽기를 넘긴다
    assert collected["message"] == [("본문", {"extracted": reading.extracted})]


@pytest.mark.parametrize("failure", [
    FailureCode.TIMEOUT, FailureCode.LLM_ERROR, FailureCode.REFUSED, FailureCode.INVALID_OUTPUT,
])
async def test_interrupted_reading_is_unavailable_not_no_link(stub_read_message, collected, failure):
    stub_read_message(failure=failure)

    response = await main.run_analysis("https://evil.top/a 확인")

    assert response == main.render_card("r4-unavailable")
    assert collected["collect"] == []


async def test_too_long_message_asks_for_the_link_part(stub_read_message, collected):
    stub_read_message(failure=FailureCode.INPUT_TOO_LARGE)

    response = await main.run_analysis("가" * 9000)

    assert _texts(response) == [main._TOO_LONG_TEXT]


async def test_empty_reading_is_input_required(stub_read_message, collected):
    stub_read_message(failure=FailureCode.EMPTY_INPUT)

    assert await main.run_analysis(" ") == main.render_card("r6-input-required")


async def test_message_without_links_is_input_required(stub_read_message, collected):
    stub_read_message(message="오늘 배송 예정입니다")

    response = await main.run_analysis("오늘 배송 예정입니다")

    assert response == main.render_card("r6-input-required")
    assert collected["collect"] == []


async def test_unread_link_is_not_called_no_link(stub_read_message, collected):
    # LLM 이 철자를 고쳤거나 잘라 와 원문 대조에서 버린 경우
    stub_read_message(message="조회 cjlogistlcs.com", unverified=True)

    response = await main.run_analysis("조회 cjlogistlcs.com")

    assert response != main.render_card("r6-input-required")
    assert _texts(response) == [main._UNREAD_LINKS_TEXT]
    assert collected["collect"] == []


async def test_partly_read_links_say_some_were_not_checked(stub_read_message, collected):
    stub_read_message(["https://a-cj.top/x"], "1차 2차 https://b-cj.top/y", unverified=True)

    response = await main.run_analysis("1차 https://a-cj.top/x 2차 https://b-cj.top/y")

    outputs = response["template"]["outputs"]
    assert outputs[0] == {"simpleText": {"text": main._PARTLY_READ_LINKS_TEXT}}
    assert len(outputs) == 2
    assert collected["collect"] == ["https://a-cj.top/x"]


@pytest.mark.parametrize("with_job", [False, True], ids=["direct", "job"])
@pytest.mark.parametrize("link_count,unverified,failure", [
    (3, True, None),
    (3, False, None),
    (2, True, None),
    (0, True, None),
    (0, False, FailureCode.LLM_ERROR),
], ids=["four_links_three_read", "three_all_read", "three_links_two_read",
        "no_links_read", "extraction_failed"])
async def test_link_notice_and_result_cards_respect_output_limit(
    stub_read_message, collected, monkeypatch, with_job, link_count, unverified, failure,
):
    links = [f"https://a{index}.top/" for index in range(link_count)]
    stub_read_message(links, unverified=unverified, failure=failure)
    rendered = []

    def result_card(payload, job_id=None, *, allow_details=True):
        assert allow_details == (link_count == 1)
        # Distinct outputs prove which analyzed links survive the display limit;
        # real card rendering and callback delivery are covered in the service test.
        card = {"textCard": {"title": payload["url"]["domain"], "description": "분석 결과"}}
        rendered.append((payload, job_id, card))
        return {"version": "2.0", "template": {"outputs": [card]}}

    monkeypatch.setattr(main, "render_result_card", result_card)
    job_id = main.create_analysis_job(USER) if with_job else None
    utterance = " ".join(links + (["https://unread.top/"] if unverified else []))
    response = await main.run_analysis(utterance, job_id=job_id)
    outputs = response["template"]["outputs"]

    assert 1 <= len(outputs) <= main.KAKAO_MAX_OUTPUTS
    assert collected["collect"] == ([] if failure else links)
    if failure:
        assert response == main.render_card("r4-unavailable")
        assert rendered == []
    elif not links:
        assert _texts(response) == [main._UNREAD_LINKS_TEXT]
        assert rendered == []
    else:
        assert len(rendered) == link_count  # analysis is not shortened to fit the UI
        assert all(passed_job_id == job_id for _, passed_job_id, _ in rendered)
        card_count = min(link_count, main.KAKAO_MAX_OUTPUTS - int(unverified))
        assert outputs[int(unverified):] == [card for _, _, card in rendered[:card_count]]
        if unverified:
            texts = _texts(response)
            assert len(texts) == 1
            assert main._PARTLY_READ_LINKS_TEXT in texts[0]
            if card_count < link_count:
                assert "검사 결과" in texts[0] and "생략" in texts[0]
                assert "하나씩 다시 보내" in texts[0]
            else:
                assert texts == [main._PARTLY_READ_LINKS_TEXT]
        else:
            assert _texts(response) == []
        if job_id:
            assert main.ANALYSIS_JOBS[job_id]["final_payload"] == rendered[-1][0]


def _detail_payload(domain, kind):
    return {
        "url": {"domain": domain, "final_url": f"https://{domain}/", "official": "not_registered"},
        "message": {
            "brand": "unknown", "answer": "risk_found" if kind == "r1" else "no_risk_found",
            "details": {"signals": [{"code": "credential_request", "evidence": "비밀번호 입력"}] if kind == "r1" else [],
                        "reason": {"text": "문자 분석 결과", "failures": []}},
        },
        "env": {"answer": "not_run", "details": {"reason": {"failures": []}}},
        "result": False,
    }


@pytest.mark.parametrize("kind", ["r1", "r9"])
@pytest.mark.parametrize("link_count,unverified", [(1, False), (2, False), (3, True)])
@pytest.mark.parametrize("with_job", [False, True], ids=["direct", "job"])
async def test_real_result_cards_disable_only_ambiguous_detail_buttons(
    stub_read_message, collected, monkeypatch, kind, link_count, unverified, with_job,
):
    links = [f"https://link{index}.top/" for index in range(link_count)]
    stub_read_message(links, unverified=unverified)
    payloads = []

    async def finalize(*, url, **_):
        payload = _detail_payload(url.domain, kind)
        payloads.append(payload)
        return SimpleNamespace(model_dump=lambda **_: payload)

    monkeypatch.setattr(main, "finalize_analysis", finalize)
    job_id = main.create_analysis_job(USER) if with_job else None
    response = await main.run_analysis(" ".join(links), job_id=job_id)
    outputs = response["template"]["outputs"]
    assert len(outputs) <= main.KAKAO_MAX_OUTPUTS
    assert collected["collect"] == links
    assert len(payloads) == link_count
    if job_id:
        assert main.ANALYSIS_JOBS[job_id]["final_payload"] == payloads[-1]
        assert main.ANALYSIS_JOBS[job_id]["link_count"] == link_count
    if unverified:
        assert main._PARTLY_READ_LINKS_TEXT in outputs[0]["simpleText"]["text"]
        assert "생략" in outputs[0]["simpleText"]["text"]

    visible = outputs[int(unverified):]
    for output, payload in zip(visible, payloads):
        # Compare the whole real R1/R9 output: only the detail button may change.
        expected = main.render_result_card(payload, job_id=job_id)["template"]["outputs"][0]
        key = "basicCard" if kind == "r1" else "textCard"
        assert any(b["label"] == "자세히 보기" for b in expected[key]["buttons"])
        if link_count > 1:
            expected[key]["buttons"] = [
                b for b in expected[key]["buttons"] if b["label"] != "자세히 보기"
            ]
        else:
            button = next(b for b in output[key]["buttons"] if b["label"] == "자세히 보기")
            if job_id:
                assert button["extra"] == {"job_id": job_id}
                assert main.handle_detail_carousel(job_id) == main.render_detail_carousel(payload)
            else:
                assert "extra" not in button
        assert output == expected


async def test_multilink_detail_request_never_uses_the_hidden_last_payload(
    stub_read_message, collected, monkeypatch,
):
    links = [f"https://link{index}.top/" for index in range(3)]
    stub_read_message(links, unverified=True)

    async def finalize(*, url, **_):
        payload = _detail_payload(url.domain, "r9")
        return SimpleNamespace(model_dump=lambda **_: payload)

    monkeypatch.setattr(main, "finalize_analysis", finalize)
    rendered_details = []

    def detail(payload):
        rendered_details.append(payload["url"]["domain"])
        return main.kakao_response("wrong link detail")

    monkeypatch.setattr(main, "render_detail_carousel", detail)
    job_id = main.create_analysis_job(USER)
    await main.run_analysis(" ".join(links), job_id=job_id)
    assert main.ANALYSIS_JOBS[job_id]["final_payload"]["url"]["domain"] == "link2.top"
    # Replay/manual requests must also be blocked, not just newly rendered buttons.
    response = main.handle_detail_carousel(job_id)
    assert rendered_details == [], "상세 요청이 화면에서 생략된 마지막 링크를 열었음"
    assert "주소를 하나씩 다시 보내" in _texts(response)[0]


async def _skill(utterance, callback_url="https://bot-api.kakao.com/cb"):
    request = {"utterance": utterance, "user": {"id": USER}}
    if callback_url:
        request["callbackUrl"] = callback_url
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="https://skill.example.test",
    ) as client:
        return await client.post(
            "/kakao/skill", headers={"X-Skill-Secret": SECRET}, json={"userRequest": request},
        )


async def test_first_response_waits_for_callback_even_without_links(monkeypatch):
    """링크가 있는지는 LLM 이 읽어야 안다. 첫 응답은 항상 대기 카드다."""

    started = []

    async def hold(utterance, callback_url, user_id, job_id, **_):
        started.append((utterance, user_id))

    monkeypatch.setattr(main, "run_analysis_and_callback", hold)

    response = await _skill("오늘 배송 예정입니다")

    assert response.json() == main.render_card("r5-analyzing")
    assert started == [("오늘 배송 예정입니다", USER)]


@pytest.mark.parametrize("utterance", ["", "   "])
async def test_blank_utterance_answers_at_once_without_job(monkeypatch, utterance):
    async def unexpected(*args, **kwargs):
        pytest.fail("빈 문자로 분석을 시작함")

    monkeypatch.setattr(main, "read_message", unexpected)
    monkeypatch.setattr(main, "run_analysis_and_callback", unexpected)

    response = await _skill(utterance)

    assert response.json() == main.render_card("r6-input-required")
    assert main.ANALYSIS_JOBS == {}
    assert USER not in main.RUNNING_USERS


async def test_callback_delivers_no_link_card(stub_read_message, monkeypatch):
    stub_read_message(message="오늘 배송 예정입니다")
    delivered = []

    def callback(request):
        delivered.append(request)
        return httpx.Response(200, json={"status": "SUCCESS"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(callback)) as client:
        monkeypatch.setattr(main, "get_http_client", lambda: client)
        job_id = main.create_analysis_job(USER)
        await asyncio.wait_for(main.run_analysis_and_callback(
            "오늘 배송 예정입니다", "https://bot-api.kakao.com/cb", USER, job_id,
        ), 2)

    job = main.ANALYSIS_JOBS[job_id]
    assert job["status"] == "completed"
    assert job["result"] == main.render_card("r6-input-required")
    assert len(delivered) == 1
