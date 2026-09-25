"""Deterministic, source-local result assembly without clients or I/O."""

from __future__ import annotations

import html
import re

from ai.message_requests import action_requested, normalized_with_positions, request_context
from ai.page import PageInspection, select_env_doubt
from ai.taxonomy import (
    MessageCandidate, classify_topic, identify_brand, message_candidates,
    select_message_doubt,
)
from ai.types import (
    AnalysisResponse, AnalysisStatus, Brand, CaseSearchResult, EnvDoubt,
    EnvironmentDetails, EnvironmentPart, EvidenceSource, FailureCode,
    IsolatedPage, MessageAnalysis, MessageDetails, MessageDoubt, MessagePart,
    PageAnalysis, RiskSignal, RiskSignalCode, SignalAnalysis, Topic, UrlAnalysis,
    _all_null,
)


_FAILURE_REASONS = {
    FailureCode.COLLECTION_FAILED: "페이지 접속·수집에 실패하여 내용을 확인하지 못했습니다.",
    FailureCode.TIMEOUT: "분석 시간이 초과되어 확인을 완료하지 못했습니다.",
    FailureCode.EMPTY_INPUT: "분석할 자료가 비어 있어 확인하지 못했습니다.",
    FailureCode.MISSING_RESULT: "필요한 자료 또는 분석 결과를 전달받지 못했습니다.",
    FailureCode.INPUT_TOO_LARGE: "자료 크기 제한으로 전체를 확인하지 못했습니다.",
    FailureCode.PARTIAL_CONTENT: "일부 자료만 확보하여 전체를 확인하지 못했습니다.",
    FailureCode.LLM_ERROR: "분석 도중 오류가 발생하여 확인을 완료하지 못했습니다.",
    FailureCode.REFUSED: "분석 요청이 거절되어 확인을 완료하지 못했습니다.",
    FailureCode.INVALID_OUTPUT: "유효한 분석 결과를 확보하지 못했습니다.",
}
_SUBJECT_ACTION = {
    RiskSignalCode.INSTALL_PROMPT: re.compile(
        r"(?:앱|어플|application|app)\s*(?:을|를)?\s*(?P<action>설치|다운로드|내려받|install|download)", re.I),
    RiskSignalCode.CREDENTIAL_REQUEST: re.compile(
        r"(?:비밀번호|비번|인증\s*번호|보안\s*카드(?:\s*(?:전체|모든|전부))?(?:\s*번호)?|password|otp)"
        r"\s*(?:을|를)?\s*(?P<action>입력|전달|제공|제출|보내|enter|send)", re.I),
    RiskSignalCode.REMOTE_CONTROL: re.compile(
        r"(?:원격\s*(?:제어|지원|접속)|remote\s*(?:control|support|access))\s*"
        r"(?:앱|어플|application|app)\s*(?:을|를|에)?\s*(?P<action>설치|연결|접속|install|connect)", re.I),
}


def validate_message_signals(text: str, signals: list[RiskSignal]) -> list[RiskSignal]:
    """Require current-message quotes and explicit code-specific requests."""
    context = request_context(text)
    normalized, positions = normalized_with_positions(context)
    accepted: list[RiskSignal] = []
    seen: set[tuple[RiskSignalCode, str]] = set()
    for signal in signals:
        quote = signal.evidence_ref
        pattern = _SUBJECT_ACTION.get(signal.code)
        key = signal.code, quote
        if (signal.evidence_source is not EvidenceSource.MESSAGE or pattern is None
                or len(quote.strip()) < 4 or quote not in text or key in seen):
            continue
        # Both the subject and action must occur inside the proposed quote; the
        # connected suffix comes from the original, untruncated message.
        grounded = False
        for occurrence in re.finditer(re.escape(quote), text):
            for request in pattern.finditer(normalized):
                start = positions[request.start()].start
                end = positions[request.end("action") - 1].end
                if (occurrence.start() <= start and end <= occurrence.end()
                        and action_requested(context, end)):
                    grounded = True
                    break
            if grounded:
                break
        if grounded:
            accepted.append(signal)
            seen.add(key)
    return accepted


def _plain_quote(quote: str) -> str:
    plain = " ".join(re.sub(r"<[^>]*>", "", html.unescape(quote)).split())
    # Broken tags must not remain raw markup; escaping preserves their text
    # without guessing how to repair or interpret the source.
    return plain.replace("<", "&lt;").replace(">", "&gt;")


def _join_reasons(reasons: list[str]) -> str:
    return " ".join(dict.fromkeys(reason for reason in reasons if reason))


def build_message_part(
    text: str, extracted: MessageAnalysis, cases: CaseSearchResult,
    signals: SignalAnalysis, *, failure: FailureCode | None = None,
) -> MessagePart:
    """Preserve grounded message facts across extraction/search/signal failures."""
    accepted = validate_message_signals(text, signals.signals)
    completed = (failure is None
        and extracted.analysis_status is AnalysisStatus.COMPLETED
        and cases.status is AnalysisStatus.COMPLETED
        and signals.status is AnalysisStatus.COMPLETED)
    # Collect facts independently of the taxonomy's synthetic failure candidate.
    # A real, unclassified request can itself span the entire input.
    candidates = message_candidates(text, extracted.model_copy(
        update={"analysis_status": AnalysisStatus.COMPLETED}))
    for signal in accepted:
        doubt = (MessageDoubt.DATA_INPUT if signal.code is RiskSignalCode.CREDENTIAL_REQUEST
            else MessageDoubt.APP_INSTALL if signal.code is RiskSignalCode.INSTALL_PROMPT
            else MessageDoubt.UNKNOWN)
        candidates.append(MessageCandidate(doubt, signal.evidence_ref, text.index(signal.evidence_ref)))
    doubt = select_message_doubt(candidates) if candidates else (
        MessageDoubt.NONE if completed else None)
    quotes = [candidate.evidence for candidate in sorted(candidates, key=lambda item: item.start)]
    quotes = [quote for quote in quotes if not any(quote != other and quote in other for other in quotes)]
    reasons = [f"문자에서 '{plain}'라고 안내했습니다." for quote in dict.fromkeys(quotes)
        if (plain := _plain_quote(quote))]
    if completed and not candidates:
        reasons.append("제공된 문자에서 명시적인 행동 요구를 확인하지 못했습니다.")
    if extracted.analysis_status is AnalysisStatus.FALLBACK:
        reasons.append("문자 분석을 완료하지 못했습니다.")
    if cases.status is AnalysisStatus.FALLBACK:
        reasons.append("사례 검색 결과를 확보하지 못했습니다.")
    for code in (failure, signals.failure):
        if code is not None:
            reasons.append(_FAILURE_REASONS[code])
    brand, category = identify_brand(text, extracted), classify_topic(text, extracted)
    if extracted.analysis_status is not AnalysisStatus.COMPLETED:
        brand = None if brand is Brand.UNKNOWN else brand
        category = None if category is Topic.UNKNOWN else category
    return MessagePart(brand=brand, category=category,
        answer=(not accepted) if completed else None,
        details=MessageDetails(doubt=doubt, reason=_join_reasons(reasons)))


def build_environment_part(
    page: IsolatedPage | None, inspection: PageInspection, analysis: PageAnalysis,
    *, failure: FailureCode | None = None,
) -> EnvironmentPart:
    """Describe only inspected HTML existence and labeled collector metadata."""
    codes = [code for code in (failure, inspection.failure, analysis.failure) if code is not None]
    if page is None:
        codes.append(FailureCode.MISSING_RESULT)
    completed = not codes and analysis.status is AnalysisStatus.COMPLETED
    doubt = select_env_doubt(inspection.elements) if inspection.elements else (
        EnvDoubt.NONE if completed else None)
    reasons = [f"전달된 HTML에서 {element.doubt.value}을 확인했습니다." for element in inspection.elements]
    if completed and not inspection.elements:
        reasons.append("제공된 HTML에서 분류 대상 요소를 확인하지 못했습니다.")
    brand = None if not completed and analysis.brand is Brand.UNKNOWN else analysis.brand
    category = None if not completed and analysis.category is Topic.UNKNOWN else analysis.category
    metadata: list[str] = []
    if page is not None and not completed:
        if brand is None:
            try:
                brand = Brand(page.brand)
            except ValueError:
                pass
            if brand is Brand.UNKNOWN:
                brand = None
            elif brand is not None:
                metadata.append(brand.value)
        if category is None:
            try:
                category = Topic(page.category)
            except ValueError:
                pass
            if category is Topic.UNKNOWN:
                category = None
            elif category is not None:
                metadata.append(category.value)
    if metadata:
        reasons.append(f"격리 환경 전달 정보: {', '.join(metadata)}.")
    reasons.extend(_FAILURE_REASONS[code] for code in codes)
    elements = {element.element_id for element in inspection.elements}
    accepted = [signal for signal in analysis.signals
        if signal.evidence_source is EvidenceSource.OBSERVATION and signal.evidence_ref in elements]
    if any(signal.code is RiskSignalCode.CREDENTIAL_REQUEST for signal in accepted):
        reasons.append("전달된 HTML 입력 요소에서 민감한 금융 인증정보 요구를 확인했습니다.")
    return EnvironmentPart(brand=brand, category=category,
        answer=(not accepted) if completed else None,
        details=EnvironmentDetails(doubt=doubt, reason=_join_reasons(reasons)))


def missing_message_part() -> MessagePart:
    return MessagePart(details=MessageDetails(
        reason="문자 분석 결과를 전달받지 못했습니다."))


def missing_environment_part() -> EnvironmentPart:
    return EnvironmentPart(details=EnvironmentDetails(
        reason="환경 분석 결과를 전달받지 못했습니다."))


def assemble_analysis(
    url: UrlAnalysis, message: MessagePart | None = None,
    env: EnvironmentPart | None = None,
) -> AnalysisResponse:
    """Preserve supplied analyses and calculate their aggregate result."""
    if message is None or _all_null(message):
        message = missing_message_part()
    if env is None or _all_null(env):
        env = missing_environment_part()
    result = url.official is True and message.answer is True and env.answer is True
    return AnalysisResponse(url=url, message=message, env=env, result=result)
