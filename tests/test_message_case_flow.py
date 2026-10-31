"""링크 없는 사칭 문자 흐름 (세 번째 시나리오, v3 §10 B3·B5).

문자 → R5 → 콜백으로 R6b + Q1 + Q2 → Q2 답 → 새 결과, Q1 답 → K6·A2.
LLM 은 부르지 않는다. 읽기는 conftest 의 어휘표 런타임, 링크 읽기는 stub_read_message 다.
"""

import asyncio
import json

import httpx
import pytest

import main
from ai.case.types import MessageCaseResult

SECRET = "synthetic-skill-secret"
USER = "family-user"
CALLBACK = "https://bot-api.kakao.com/cb"

FAMILY = "엄마 나야\n폰 고장나서 통화가 안돼\n급하게 결제할 게 있는데 50만원만 보내줘"
GIFT = "엄마 나 폰 고장나서 이 번호로 문자해. 문화상품권 사서 핀번호 보내줘"
INSTITUTION = "서울중앙지검 수사관입니다. 고객님 명의 계좌가 범죄에 연루되어 안전계좌로 이체하셔야 합니다"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("KAKAO_SKILL_SECRET", SECRET)
    monkeypatch.setenv("KAKAO_CALLBACK_HOSTS", "bot-api.kakao.com")
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {})
    monkeypatch.setattr(main, "RUNNING_USERS", set())


async def _post(body):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="https://skill.example.test",
    ) as client:
        response = await client.post(
            "/kakao/skill", headers={"X-Skill-Secret": SECRET}, json=body,
        )
    assert response.status_code == 200
    return response.json()


async def _answer(job_id, question_id, option_id, user=USER):
    return await _post({
        "userRequest": {"utterance": "", "user": {"id": user}},
        "action": {"clientExtra": {
            "case_id": job_id, "question_id": question_id, "option_id": option_id,
        }},
    })


async def _analyze_with_callback(monkeypatch, stub_read_message, text):
    """콜백 경로로 분석을 끝내고 (job, 콜백으로 보낸 카드)를 돌려준다."""

    stub_read_message(message=text)
    delivered = []

    def callback(request):
        delivered.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "SUCCESS"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(callback)) as client:
        monkeypatch.setattr(main, "get_http_client", lambda: client)
        job_id = main.create_analysis_job(USER)
        await asyncio.wait_for(
            main.run_analysis_and_callback(text, CALLBACK, USER, job_id), 5
        )
    assert len(delivered) == 1
    return main.ANALYSIS_JOBS[job_id], delivered[0]


def _outputs(card):
    return card["template"]["outputs"]


def _text_card(card, index=0):
    return _outputs(card)[index]["textCard"]


def _list_items(card, index):
    return _outputs(card)[index]["listCard"]["items"]


def _assert_no_placeholders(card):
    assert "{{" not in json.dumps(card, ensure_ascii=False)


# ── 첫 결과 ──────────────────────────────────────────────────


async def test_skill_first_response_is_analyzing_card(monkeypatch):
    started = []

    async def hold(utterance, callback_url, user_id, job_id, **_):
        started.append(utterance)

    monkeypatch.setattr(main, "run_analysis_and_callback", hold)
    response = await _post({"userRequest": {
        "utterance": FAMILY, "user": {"id": USER}, "callbackUrl": CALLBACK,
    }})
    assert response == main.render_card("r5-analyzing")
    assert started == [FAMILY]


async def test_family_message_gets_r6b_with_q1_and_q2(monkeypatch, stub_read_message):
    job, card = await _analyze_with_callback(monkeypatch, stub_read_message, FAMILY)

    assert job["status"] == "completed"
    assert job["analysis_kind"] == "message_case"
    assert job["result"] == card
    MessageCaseResult.model_validate(job["final_payload"])

    outputs = _outputs(card)
    assert len(outputs) == 3  # 결과 카드 + Q1 + Q2 (카카오 상한 안)
    result = _text_card(card)
    assert result["title"] == "🔎 주의가 필요한 문자예요"
    description = result["description"]
    assert description.startswith("돈을 보내지 마세요.\n원래 알던 번호로 직접 전화해 확인하세요.")
    assert "주의할 점\n- 내세운 관계: '엄마 나야'" in description
    assert "- 요구한 것: 송금·입금" in description
    assert "확인한 것\n- 문자 내용" in description
    assert "- 실제로 보낸 사람" in description
    assert "답해 주신 내용 기준" not in description
    assert "스미싱" not in json.dumps(card, ensure_ascii=False)
    assert "\n\n\n" not in description
    assert len(description) <= 400
    _assert_no_placeholders(card)

    assert outputs[1]["listCard"]["header"]["title"] == "🙋 이미 하신 게 있나요?"
    q1 = _list_items(card, 1)
    assert [i["title"] for i in q1] == [
        "정보를 입력했어요", "앱을 설치했어요", "돈이나 상품권을 보냈어요", "아직 안 했어요",
    ]
    assert outputs[2]["listCard"]["header"]["title"] == "📞 원래 알던 번호로 통화해 보셨나요?"
    q2 = _list_items(card, 2)
    assert [i["title"] for i in q2] == [
        "본인이 아니래요", "본인이 맞대요", "전화를 안 받아요", "아직 안 해 봤어요", "잘 모르겠어요",
    ]
    assert q2[0]["extra"] == {
        "case_id": job["job_id"], "question_id": "called_known_number", "option_id": "not_person",
    }
    assert q2[0]["blockId"] == q1[0]["blockId"]


async def test_institution_message_uses_official_number_from_kb(monkeypatch, stub_read_message):
    job, card = await _analyze_with_callback(monkeypatch, stub_read_message, INSTITUTION)

    outputs = _outputs(card)
    assert len(outputs) == 2  # Q2 는 가족·지인 문자에만
    result = _text_card(card)
    assert result["title"] == "🔎 주의가 필요한 문자예요"
    assert "대표번호(" in result["description"]
    assert "- 내세운 기관:" in result["description"]
    assert "- 실제로 보낸 곳" in result["description"]
    _assert_no_placeholders(card)


async def test_chatter_still_gets_input_required(monkeypatch, stub_read_message):
    job, card = await _analyze_with_callback(monkeypatch, stub_read_message, "오늘 저녁 뭐 먹을까")
    assert card == main.render_card("r6-input-required")
    assert job["final_payload"]["conclusion"] == "input_required"


async def test_sync_path_without_job_has_no_question_cards(stub_read_message):
    stub_read_message(message=FAMILY)
    card = await main.run_analysis(FAMILY)
    assert len(_outputs(card)) == 1
    assert _text_card(card)["title"] == "🔎 주의가 필요한 문자예요"


async def test_budget_subtracts_reading_time(monkeypatch, stub_read_message):
    seen = {}

    async def fake(text, *, time_budget_s, **_):
        seen["budget"] = time_budget_s
        raise RuntimeError("stop here")

    monkeypatch.setattr(main, "analyze_message_case", fake)
    stub_read_message(message=FAMILY)
    card = await main.run_message_case(FAMILY, started=main.time.monotonic() - 10)
    assert card == main.render_card("r4-unavailable")
    assert 29 < seen["budget"] <= main.MESSAGE_CASE_BUDGET_S - 10


async def test_unread_links_never_go_to_message_case(monkeypatch, stub_read_message):
    async def unexpected(*args, **kwargs):
        pytest.fail("링크를 읽지 못한 문자를 링크 없는 문자로 분석함")

    monkeypatch.setattr(main, "analyze_message_case", unexpected)
    stub_read_message(message="조회 cjlogistlcs.com", unverified=True)
    response = await main.run_analysis("조회 cjlogistlcs.com")
    assert response == main.kakao_response(main._UNREAD_LINKS_TEXT)


# ── Q2 답 → 새 결과 ─────────────────────────────────────────


async def test_q2_not_person_turns_into_warning_and_updates_check_result(monkeypatch, stub_read_message):
    job, _ = await _analyze_with_callback(monkeypatch, stub_read_message, FAMILY)

    card = await _answer(job["job_id"], "called_known_number", "not_person")

    result = _text_card(card)
    assert result["title"] == "⚠️ 사칭 문자로 의심돼요"
    assert "답해 주신 내용 기준으로 안내했어요." in result["description"]
    assert "- 원래 번호로 통화한 결과 (본인이 아니라고 답해 주셨어요)" in result["description"]
    # Q2 는 떨어지고 Q1 만 남는다
    assert len(_outputs(card)) == 2
    assert _outputs(card)[1]["listCard"]["header"]["title"] == "🙋 이미 하신 게 있나요?"
    assert job["final_payload"]["conclusion"] == "suspected"
    # "결과 확인" 도 새 결과를 보여 준다
    assert job["result"] == card
    checked = main.handle_check_result(USER)
    assert _text_card(checked, 1)["title"] == "⚠️ 사칭 문자로 의심돼요"


async def test_q2_is_person_turns_caution_off(monkeypatch, stub_read_message):
    job, _ = await _analyze_with_callback(monkeypatch, stub_read_message, FAMILY)

    card = await _answer(job["job_id"], "called_known_number", "is_person")

    result = _text_card(card)
    assert result["title"] == "🤔 판단하기 어려워요"
    assert "보내기 전에 받는 계좌가 본인 명의인지 한 번 더 확인하세요." in result["description"]
    assert "본인이 맞다고 답해 주셨어요" in result["description"]
    assert len(_outputs(card)) == 1  # 질문이 모두 떨어진다
    _assert_no_placeholders(card)


async def test_old_q2_button_after_not_person_does_not_downgrade(monkeypatch, stub_read_message):
    job, _ = await _analyze_with_callback(monkeypatch, stub_read_message, FAMILY)
    await _answer(job["job_id"], "called_known_number", "not_person")

    card = await _answer(job["job_id"], "called_known_number", "is_person")

    assert _text_card(card)["title"] == "⚠️ 사칭 문자로 의심돼요"
    assert job["final_payload"]["conclusion"] == "suspected"


async def test_q2_not_yet_keeps_asking(monkeypatch, stub_read_message):
    job, _ = await _analyze_with_callback(monkeypatch, stub_read_message, FAMILY)
    card = await _answer(job["job_id"], "called_known_number", "not_yet")
    assert _text_card(card)["title"] == "🔎 주의가 필요한 문자예요"
    assert len(_outputs(card)) == 3


# ── Q1 답 → K6·A2 ───────────────────────────────────────────


async def test_q1_sent_money_gets_k6_with_112(monkeypatch, stub_read_message):
    job, _ = await _analyze_with_callback(monkeypatch, stub_read_message, FAMILY)

    card = await _answer(job["job_id"], "already_acted", "sent_money")

    k6 = _text_card(card)
    assert k6["title"] == "🚨 지금 먼저 하세요"
    assert k6["description"].startswith(
        "1. 송금한 은행 고객센터에 지급정지를 요청하세요.\n2. 112에 신고하세요.\n\n"
    )
    assert [b["label"] for b in k6["buttons"]] == ["112 신고"]
    # Q1 답은 결론을 바꾸지 않는다
    assert job["final_payload"]["conclusion"] == "inconclusive"


async def test_q1_gift_card_sent_money_contacts_issuer(monkeypatch, stub_read_message):
    job, _ = await _analyze_with_callback(monkeypatch, stub_read_message, GIFT)
    card = await _answer(job["job_id"], "already_acted", "sent_money")
    assert _text_card(card)["description"].startswith(
        "1. 상품권 발행사 고객센터에 사용 정지를 요청하세요.\n2. 112에 신고하세요."
    )


async def test_q1_entered_info_gets_kisa_button(monkeypatch, stub_read_message):
    job, _ = await _analyze_with_callback(monkeypatch, stub_read_message, FAMILY)
    card = await _answer(job["job_id"], "already_acted", "entered_info")
    k6 = _text_card(card)
    assert [b["label"] for b in k6["buttons"]] == ["KISA 상담하기"]
    assert "pd.fss.or.kr" in k6["description"]


async def test_q1_installed_app_only_has_no_buttons(monkeypatch, stub_read_message):
    job, _ = await _analyze_with_callback(monkeypatch, stub_read_message, FAMILY)
    card = await _answer(job["job_id"], "already_acted", "installed_app")
    k6 = _text_card(card)
    assert k6["buttons"] == []
    assert "1. 휴대폰 전원을 끄거나 비행기 모드로 바꾸세요." in k6["description"]


async def test_q1_not_yet_gets_a2_and_later_answers_accumulate(monkeypatch, stub_read_message):
    job, _ = await _analyze_with_callback(monkeypatch, stub_read_message, FAMILY)

    a2 = await _answer(job["job_id"], "already_acted", "not_yet")
    assert _text_card(a2)["description"].startswith("잘하셨어요. 지금처럼 돈을 보내지 마세요.")

    await _answer(job["job_id"], "already_acted", "sent_money")
    again = await _answer(job["job_id"], "already_acted", "not_yet")
    # 대응이 있는 답을 이미 골랐으면 A2 가 아니라 K6 가 다시 나간다
    assert _text_card(again)["title"] == "🚨 지금 먼저 하세요"
    assert job["final_payload"]["answers"]["already_acted"] == ["not_yet", "sent_money"]


# ── 케이스를 찾지 못한 답 ────────────────────────────────────


async def test_q1_for_missing_case_gets_notice_and_default_response():
    card = await _answer("no-such-job", "already_acted", "sent_money")
    assert _outputs(card)[0] == {"simpleText": {"text": main._CASE_NOT_FOUND_TEXT}}
    k6 = _text_card(card, 1)
    assert k6["title"] == "🚨 지금 먼저 하세요"
    assert "지급정지" in k6["description"]
    assert [b["label"] for b in k6["buttons"]] == ["112 신고"]


async def test_q1_not_yet_for_missing_case_gets_notice_only():
    card = await _answer("no-such-job", "already_acted", "not_yet")
    assert card == main.kakao_response(main._CASE_NOT_FOUND_TEXT)


async def test_q2_for_missing_case_asks_to_send_again():
    card = await _answer("no-such-job", "called_known_number", "not_person")
    assert card == main.kakao_response(main._CASE_NOT_FOUND_TEXT)


async def test_answer_from_other_user_does_not_touch_case(monkeypatch, stub_read_message):
    job, _ = await _analyze_with_callback(monkeypatch, stub_read_message, FAMILY)
    before = dict(job["final_payload"])

    card = await _answer(job["job_id"], "called_known_number", "not_person", user="someone-else")

    assert card == main.kakao_response(main._CASE_NOT_FOUND_TEXT)
    assert job["final_payload"] == before


async def test_answer_while_running_does_not_start_new_analysis(monkeypatch, stub_read_message):
    job, _ = await _analyze_with_callback(monkeypatch, stub_read_message, FAMILY)
    main.RUNNING_USERS.add(USER)

    card = await _answer(job["job_id"], "already_acted", "sent_money")

    assert _text_card(card)["title"] == "🚨 지금 먼저 하세요"


# ── 결과 확인 ───────────────────────────────────────────────


async def test_check_result_keeps_within_three_outputs(monkeypatch, stub_read_message):
    job, card = await _analyze_with_callback(monkeypatch, stub_read_message, FAMILY)
    assert len(_outputs(card)) == 3

    checked = main.handle_check_result(USER)

    assert len(_outputs(checked)) <= main.KAKAO_MAX_OUTPUTS
    assert checked == card


async def test_check_result_adds_time_when_room(monkeypatch, stub_read_message):
    job, _ = await _analyze_with_callback(monkeypatch, stub_read_message, INSTITUTION)
    checked = main.handle_check_result(USER)
    assert len(_outputs(checked)) == 3
    assert "확인한 결과예요" in _outputs(checked)[0]["simpleText"]["text"]
