"""문자 링크를 read_message() 로 받는 흐름 (ADR 0007).

링크를 찾지 못한 이유에 따라 안내가 달라야 한다. LLM 이 실패했거나 링크를 다 받지
못했으면 "링크 없음"(R6)으로 안내하지 않는다.
"""

import asyncio

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


async def test_partly_read_note_never_exceeds_kakao_outputs(stub_read_message, collected):
    links = [f"https://a{i}.top/" for i in range(main.KAKAO_MAX_OUTPUTS)]
    stub_read_message(links, unverified=True)

    response = await main.run_analysis(" ".join(links))

    outputs = response["template"]["outputs"]
    assert len(outputs) == main.KAKAO_MAX_OUTPUTS
    assert main._PARTLY_READ_LINKS_TEXT not in _texts(response)


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
