"""`docs/designs/chatbot-copy.md` 4절의 고정 문구 상수.

카드 문구를 코드에 직접 짓지 않는다 — BE가 임의로 문장을 만들면
CLAUDE.md 절대 원칙 2(판정 결과를 사람이 읽을 문장으로 바꾸는 것은
LLM/문구 소유자의 역할)를 BE가 침범하게 된다. 여기 값은 FE 문서의
표를 그대로 옮긴 것이고, 문구가 바뀌면 이 상수만 고치면 된다.

AI `RiskSignalCode` 중 지금 문자에서 검증을 통과할 수 있는 코드는
세 가지뿐이다 (`ai/src/ai/pipeline/results.py` `_SUBJECT_ACTION`).
나머지 코드와 `env.details.signals`의 문구는 페이지 수집기를 연결할
때 정한다 (chatbot-copy.md 4절).
"""

# code -> (signal_title, reason)
SIGNAL_COPY: dict[str, tuple[str, str]] = {
    "install_prompt": (
        "문자에서 앱 설치를 요구해요",
        "출처를 모르는 앱은 휴대폰 정보를 빼가는 악성 앱일 수 있어요.",
    ),
    "credential_request": (
        "문자에서 비밀번호·인증번호를 요구해요",
        "비밀번호나 인증번호를 넘기면 계정이나 돈을 빼앗길 수 있어요.",
    ),
    "remote_control": (
        "문자에서 원격 제어 앱 연결을 요구해요",
        "원격 제어 앱을 연결하면 다른 사람이 내 휴대폰을 조작할 수 있어요.",
    ),
}

# 아직 고정 문구가 없는 코드(페이지 수집기 연결 전)를 만났을 때 쓰는 대체값.
# 카드 자체를 비우지 않기 위한 최소한의 중립 문구다.
_FALLBACK_SIGNAL_TITLE = "위험할 수 있는 내용을 확인했어요"
_FALLBACK_SIGNAL_REASON = "자세한 설명은 아직 준비되지 않았어요."


def get_signal_copy(code: str) -> tuple[str, str]:
    """signal code에 대응하는 (signal_title, reason)을 돌려준다."""

    return SIGNAL_COPY.get(code, (_FALLBACK_SIGNAL_TITLE, _FALLBACK_SIGNAL_REASON))


# env.answer -> C2 "unverified" 문장.
# result-meaning-cases.md 4절 "페이지 확인 범위 문장" 표.
# risk_found는 그 표에 명시된 문장이 없어, 같은 절의 취지(확인했다는
# 사실 자체를 말한다)를 따른 잠정 문구다 — FE 확정 전까지 임시값.
UNVERIFIED_COPY: dict[str, str] = {
    "no_risk_found": "페이지에서도 다른 점을 찾지 못했어요.",
    "risk_found": "페이지에서도 확인한 내용이 있어요.",
    "failed": "페이지를 열어보려 했지만 열지 못했어요.",
    "partial": "페이지는 일부만 확인했어요.",
    "not_run": "페이지 내용은 확인하지 않았어요.",
}


def get_unverified_copy(env_answer: str | None) -> str:
    """env.answer 값에 대응하는 C2 "확인하지 못한 부분" 문장을 돌려준다."""

    if env_answer is None:
        return UNVERIFIED_COPY["not_run"]
    return UNVERIFIED_COPY.get(env_answer, UNVERIFIED_COPY["not_run"])


# message.details.reason.failures[0] -> 문자 분석이 끝나지 못한 이유 설명.
# 이슈 #59: AI가 문자 분석 실패 원인을 보존하도록 수정했다(refactor/edit-card-ai,
# 9296458: timeout/refused/invalid_output/llm_error). timeout 문구는 이슈
# 본문에 적힌 예시 그대로다. 내부 코드(FailureCode 값)를 그대로 보여주지
# 않고 여기서 사람이 읽을 설명으로만 바꾼다(CLAUDE.md 절대 원칙 2).
MESSAGE_FAILURE_COPY: dict[str, str] = {
    "timeout": "분석 시간이 초과되어 끝까지 확인하지 못했어요",
    "refused": "분석 요청이 거절되어 확인하지 못했어요",
    "invalid_output": "분석 결과를 올바르게 받지 못했어요",
    "llm_error": "분석 중 오류가 발생해 확인하지 못했어요",
}

# 원인 정보가 없거나(이슈 #59: "원인 정보가 부족한 기존 응답") 모르는
# 코드일 때 쓰는 기본 안내. 이슈 본문의 예시 문구 그대로다.
_FALLBACK_MESSAGE_FAILURE = "문자 분석을 완료하지 못했어요"


def get_message_failure_copy(failures: list[str] | None) -> str:
    """실패 원인 중 우선순위가 가장 높은 원인의 안내 문구를 반환한다."""
    if not failures:
        return _FALLBACK_MESSAGE_FAILURE

    priority = ("timeout", "refused", "invalid_output", "llm_error")

    for code in priority:
        if code in failures:
            return MESSAGE_FAILURE_COPY[code]

    return _FALLBACK_MESSAGE_FAILURE
