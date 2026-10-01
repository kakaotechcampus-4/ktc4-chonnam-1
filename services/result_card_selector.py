"""`AnalysisResponse`(dict, `model_dump(mode="json")` 결과)를 보고
어떤 결과 카드를 보여줄지 고른다.

FE·AI가 "정상이라고 말할 조건"(docs/designs/result-meaning-cases.md
6절 항목 9)을 아직 확정하지 않았으므로, 여기 규칙은 **임시 규칙**이다.
조건을 Renderer와 섞지 않고 이 함수 하나로 모아둔 이유도 그래서다 —
합의가 끝나면 이 함수만 고치면 된다.

근거 문서:
- be_teammate_card_integration_tasks.md 4절
- docs/designs/result-meaning-cases.md 4절 "안내 상태 정의", 6절 항목 9
- docs/scheme.md (DomainMatch, AnswerState 값)
"""

RESULT_CARD_LOOKALIKE = "r1-lookalike"
RESULT_CARD_OFFICIAL = "r2-official"
RESULT_CARD_INCONCLUSIVE = "r3-inconclusive"
RESULT_CARD_NOT_OFFICIAL = "r8-not-official"
RESULT_CARD_UNCERTAIN = "r9-uncertain"

_NO_RISK = "no_risk_found"


def _has_signals(part: dict | None) -> bool:
    details = (part or {}).get("details") or {}
    return bool(details.get("signals"))


def select_result_card(analysis_result: dict) -> str:
    """analysis_result(dict)를 보고 결과 카드 이름을 고른다.

    우선순위 (result-meaning-cases.md 4절 "검증된 위험 근거는 상태보다
    먼저 본다" / PR #39 멘토 피드백 "일부 단계가 안 끝났어도 확실한
    위험 근거가 있으면 위험하다고 안내해야 한다"):

    1. 문자·페이지 어느 쪽이든 검증된 signals가 있으면 → 무조건 위험 카드.
       `answer`가 `partial`이어도 signals가 있으면 위험 근거가 있다는
       뜻이다(docs/scheme.md). `answer`만 보고 버리지 않는다.
    2. official == "official"이고 문자·페이지 모두 "no_risk_found"면
       → 정상 카드 (= AnalysisResponse.result와 같은 조건).
    3. official == "official"인데 2번 조건을 못 채우면(대부분 페이지
       수집기 미연결로 env가 not_run인 현재 상황) → "판단하기 어려워요".
       result-meaning-cases.md 6절 항목 9가 미해결 상태로 남긴 질문의
       잠정 답이다 ("후보는 S4 결론 줄").
    4. official == "brand_mismatch" → 위험 카드 (사칭 패턴 자체가 근거).
    5. official == "not_registered"인데 브랜드 자체를 특정 못 했으면
       → "확인할 정보가 부족해요" (org_name을 채울 수 없는 경우).
    6. official == "not_registered"면 → "공식 주소 목록에 없어요".
    7. 그 외(unresolved 등) → "판단하기 어려워요".
    """

    url = analysis_result.get("url") or {}
    official = url.get("official")

    message = analysis_result.get("message") or {}
    env = analysis_result.get("env") or {}

    if _has_signals(message) or _has_signals(env):
        return RESULT_CARD_LOOKALIKE

    if official == "official":
        if message.get("answer") == _NO_RISK and env.get("answer") == _NO_RISK:
            return RESULT_CARD_OFFICIAL
        return RESULT_CARD_UNCERTAIN

    if official == "brand_mismatch":
        return RESULT_CARD_LOOKALIKE

    if official == "not_registered":
        if not message.get("brand"):
            return RESULT_CARD_INCONCLUSIVE
        return RESULT_CARD_NOT_OFFICIAL

    return RESULT_CARD_UNCERTAIN
