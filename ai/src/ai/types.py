"""backend ↔ ai 사이에 오가는 타입. 호출당하는 쪽이 소유합니다."""

from dataclasses import dataclass
from enum import Enum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)


class ReasonCode(str, Enum):
    """판정을 올린 **대표 이유** 하나. 근거 전부는 `Verdict.accepted_signals`."""

    NO_URL = "no_url"
    OFFICIAL_MATCH = "official_match"
    LOOKALIKE = "lookalike"
    # 대조를 끝냈고 목록에 없었으며 다른 근거도 없을 때만 씁니다. 대조 자체가
    # 실패한 UNRESOLVED 에 이 코드를 붙이면 하지 않은 확인을 주장하게 됩니다.
    NOT_IN_WHITELIST = "not_in_whitelist"
    OFFICIAL_BUT_RISKY = "official_but_risky"
    RISK_SIGNAL = "risk_signal"
    EXPIRED_LINK = "expired_link"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Verdict:
    """백엔드가 판정을 끝낸 결과. ai는 이걸 문장으로 바꾸기만 합니다."""

    reason_code: ReasonCode
    url: str | None = None
    official_domain: str | None = None
    carrier_name: str | None = None
    final_state: "FinalState | None" = None
    accepted_signals: "tuple[RiskSignal, ...]" = ()


class AnalysisStatus(str, Enum):
    COMPLETED = "completed"
    FALLBACK = "fallback"


class Brand(str, Enum):
    CJ_LOGISTICS = "CJ대한통운"
    CJ_PARCEL = "CJ택배"
    CJ_EXPRESS = "CJ익스프레스"
    CJ_SHOPPING = "CJ오쇼핑"
    HANJIN = "한진택배"
    LOGEN = "로젠택배"
    EPOST = "우체국택배"
    DHL = "DHL"
    HYUNDAI = "현대택배"
    LOTTE_PARCEL = "롯데택배"
    CU = "CU"
    DAESHIN = "대신택배"
    KGB = "KGB택배"
    KYUNGDONG = "경동택배"
    HAPDONG = "합동택배"
    COUPANG = "쿠팡"
    AUCTION = "옥션"
    LOTTE_MALL = "롯데몰"
    KAKAO_GIFT = "카카오톡 선물하기"
    SEVEN_ELEVEN = "7-11"
    RAKUTEN_EXPRESS = "라쿠텐 익스프레스"
    KISA = "KISA"
    PROSECUTION = "검찰청"
    UNKNOWN = "unknown"


class Topic(str, Enum):
    PARCEL = "택배"
    SHOPPING = "쇼핑"
    FINANCE = "금융"
    PUBLIC = "공공기관"
    HEALTH = "의료·건강"
    SECURITY = "보안"
    GIFT = "선물·이벤트"
    UNKNOWN = "unknown"


class MessageDoubt(str, Enum):
    APP_INSTALL = "앱 설치"
    ADDRESS_EDIT = "주소 입력·수정"
    ADDRESS_CHECK = "주소 확인"
    IDENTITY_CHECK = "본인 확인"
    DATA_INPUT = "정보 입력"
    PHOTO_VIEW = "사진 확인"
    PARCEL_LOOKUP = "배송 조회"
    DETAIL_VIEW = "상세 내용 확인"
    CANCEL_REFUND = "주문 취소·환불"
    PICKUP = "수령·일정 확인"
    WITHDRAW = "금전 인출"
    ANSWER_PHONE = "전화 응대"
    OPEN_LINK = "링크 접속"
    NONE = "없음"
    UNKNOWN = "unknown"


class EnvDoubt(str, Enum):
    APP_LINK = "앱 다운로드 링크"
    LOGIN_FORM = "로그인·인증 입력폼"
    PAYMENT = "결제 요청 요소"
    PERSONAL_FORM = "개인정보 입력폼"
    ADDRESS_FORM = "주소 입력폼"
    PARCEL_WIDGET = "배송 조회 요소"
    DOCUMENT_VIEW = "사진·문서 열람 요소"
    NONE = "없음"
    UNKNOWN = "unknown"


class FailureCode(str, Enum):
    EMPTY_INPUT = "empty_input"
    MISSING_RESULT = "missing_result"
    COLLECTION_FAILED = "collection_failed"
    TIMEOUT = "timeout"
    LLM_ERROR = "llm_error"
    REFUSED = "refused"
    INVALID_OUTPUT = "invalid_output"
    INPUT_TOO_LARGE = "input_too_large"
    PARTIAL_CONTENT = "partial_content"


class CategoryCode(str, Enum):
    """메시지의 **주제** 분류입니다. 위험 신호가 아닙니다.

    정상 택배 알림도 delivery로 분류됩니다. decide()의 판정 입력으로
    쓰지 마세요.
    """

    DELIVERY = "delivery"
    ADDRESS_CORRECTION = "address_correction"
    PAYMENT = "payment"
    PENALTY = "penalty"
    CARD_OR_ACCOUNT = "card_or_account"
    PUBLIC_REFUND = "public_refund"
    PUBLIC_SUPPORT = "public_support"
    ACQUAINTANCE_IMPERSONATION = "acquaintance_impersonation"
    INVITATION = "invitation"
    OBITUARY = "obituary"
    PRIZE_OR_EVENT = "prize_or_event"
    HEALTH_CHECK = "health_check"
    TELECOM_REFUND = "telecom_refund"
    ACCOUNT_SECURITY = "account_security"
    OTHER = "other"
    UNKNOWN = "unknown"


class PersuasionCode(str, Enum):
    URGENCY = "urgency"
    FEAR = "fear"
    REWARD = "reward"
    AUTHORITY = "authority"
    RELATIONSHIP = "relationship"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceField(StrictModel):
    value: str | None = None
    evidence: str | None = None

    @model_validator(mode="after")
    def require_pair(self) -> "EvidenceField":
        if (self.value is None) != (self.evidence is None):
            raise ValueError("value and evidence must both be set or both be null")
        return self


class CategoryEvidence(StrictModel):
    code: CategoryCode
    custom_label: str | None = None
    evidence: str

    @model_validator(mode="after")
    def validate_custom_label(self) -> "CategoryEvidence":
        if self.code is CategoryCode.OTHER and not self.custom_label:
            raise ValueError("other requires custom_label")
        if self.code is not CategoryCode.OTHER and self.custom_label is not None:
            raise ValueError("custom_label is only valid for other")
        return self


class PersuasionEvidence(StrictModel):
    code: PersuasionCode
    evidence: str


class ExtractedMessage(StrictModel):
    categories: list[CategoryEvidence] = Field(default_factory=list)
    claimed_sender: EvidenceField = Field(default_factory=EvidenceField)
    claimed_purpose: EvidenceField = Field(default_factory=EvidenceField)
    requested_actions: list[EvidenceField] = Field(default_factory=list)
    persuasion_signals: list[PersuasionEvidence] = Field(default_factory=list)


class MessageAnalysis(ExtractedMessage):
    analysis_status: AnalysisStatus


# ── 외부 입력 (백엔드가 채워서 넘긴다) ──────────────────────────
# 백엔드가 키를 추가·제거해도 ai를 고치지 않도록 모르는 키는 무시합니다.


class InboundModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CheckState(str, Enum):
    """검사 결과 3분법. 빈 목록으로 '없음'을 추론하면 안 됩니다."""

    FOUND = "found"
    CHECKED_ABSENT = "checked_absent"
    UNKNOWN = "unknown"


class PageState(str, Enum):
    RENDERED = "rendered"
    EXPIRED = "expired"
    CLOAKED_SUSPECT = "cloaked_suspect"
    UNREACHABLE = "unreachable"


class ObservationStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    NOT_RUN = "not_run"


class CheckResult(InboundModel):
    state: CheckState
    items: list[str] = Field(default_factory=list)


class UncheckedItem(InboundModel):
    check: str
    reason: str


class Observations(InboundModel):
    """urlscan과 격리 서버 결과를 백엔드가 합친 공통 구조."""

    status: ObservationStatus
    source: str
    page_state: PageState
    input_url: str | None = None
    final_url: str | None = None
    payload_type: str | None = None
    checks: dict[str, CheckResult] = Field(default_factory=dict)
    static_risk_signals: list[str] = Field(default_factory=list)
    unchecked: list[UncheckedItem] = Field(default_factory=list)


class DomainMatch(str, Enum):
    OFFICIAL = "official"
    BRAND_MISMATCH = "brand_mismatch"
    NOT_REGISTERED = "not_registered"
    NO_URL = "no_url"
    UNRESOLVED = "unresolved"


class DomainCheck(InboundModel):
    """백엔드의 화이트리스트 대조 결과. ai는 목록 파일을 읽지 않습니다."""

    match: DomainMatch
    checked_domain: str | None = None
    official_domain: str | None = None
    carrier_name: str | None = None


class UrlAnalysis(InboundModel):
    final_url: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    official: StrictBool

    @field_validator("final_url", "domain")
    @classmethod
    def reject_blank_value(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


class IsolatedPage(InboundModel):
    brand: str
    category: str
    info: str


# ── LLM 출력 (환각 방어를 위해 strict) ──────────────────────────


class RiskSignalCode(str, Enum):
    INSTALL_PROMPT = "install_prompt"
    CREDENTIAL_REQUEST = "credential_request"
    DANGEROUS_PERMISSION = "dangerous_permission"
    REMOTE_CONTROL = "remote_control"
    OVERSIZED_PAYLOAD = "oversized_payload"
    PACKER_DETECTED = "packer_detected"
    BRAND_MISMATCH = "brand_mismatch"
    # decide() 만 발행합니다. 프롬프트에도 싣지 않고, LLM 이 제안하면
    # verdict.SYNTHETIC_ONLY_CODES 가 버립니다.
    EXPIRED_LINK = "expired_link"


class EvidenceSource(str, Enum):
    MESSAGE = "message"
    OBSERVATION = "observation"


class RiskSignal(StrictModel):
    """확인된 위험 근거.

    대부분은 LLM 이 제안하고 decide() 의 검증을 통과한 것입니다. 일부는
    decide() 가 결정적 관측에서 직접 발행합니다 (EXPIRED_LINK). 어느 쪽이든
    `accepted_signals` 에 실린 것은 실재가 확인된 근거이므로 설명이 인용해도
    됩니다.
    """

    code: RiskSignalCode
    evidence_source: EvidenceSource
    evidence_ref: str


class SignalProposal(StrictModel):
    signals: list[RiskSignal] = Field(default_factory=list)


class MessageDetails(StrictModel):
    doubt: MessageDoubt | None = None
    reason: str | None = None


class EnvironmentDetails(StrictModel):
    doubt: EnvDoubt | None = None
    reason: str | None = None


def _all_null(part: BaseModel) -> bool:
    data = part.model_dump()
    return all(data[key] is None for key in ("brand", "category", "answer")) and all(
        value is None for value in data["details"].values()
    )


def _require_complete_or_failed(part: BaseModel) -> None:
    data = part.model_dump()
    leaf_values = *(data[key] for key in ("brand", "category", "answer")), *data[
        "details"
    ].values()
    if data["answer"] is not None:
        if any(value is None for value in leaf_values):
            raise ValueError("completed parts require all fields")
    elif any(value is not None for value in leaf_values):
        reason = data["details"]["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("incomplete parts require a nonblank failure reason")


class MessagePart(StrictModel):
    brand: Brand | None = None
    category: Topic | None = None
    answer: StrictBool | None = None
    details: MessageDetails = Field(default_factory=MessageDetails)

    @model_validator(mode="after")
    def require_complete_or_skipped(self) -> "MessagePart":
        _require_complete_or_failed(self)
        return self


class EnvironmentPart(StrictModel):
    brand: Brand | None = None
    category: Topic | None = None
    answer: StrictBool | None = None
    details: EnvironmentDetails = Field(default_factory=EnvironmentDetails)

    @model_validator(mode="after")
    def require_complete_or_skipped(self) -> "EnvironmentPart":
        _require_complete_or_failed(self)
        return self


class AnalysisResponse(StrictModel):
    url: UrlAnalysis
    message: MessagePart
    env: EnvironmentPart
    result: StrictBool

    @model_validator(mode="after")
    def validate_result_and_parts(self) -> "AnalysisResponse":
        if _all_null(self.message) or _all_null(self.env):
            raise ValueError("responses require normalized analysis parts")
        expected = (self.url.official is True
            and self.message.answer is True
            and self.env.answer is True)
        if self.result is not expected:
            raise ValueError("result must match the aggregate analysis")
        return self


class SignalAnalysis(StrictModel):
    status: AnalysisStatus
    signals: list[RiskSignal] = Field(default_factory=list)
    failure: FailureCode | None = None

    @model_validator(mode="after")
    def validate_failure(self) -> "SignalAnalysis":
        if (self.status is AnalysisStatus.COMPLETED) != (self.failure is None):
            raise ValueError("completed analyses have no failure; fallback analyses require one")
        return self


class PageProposal(StrictModel):
    brand: EvidenceField = Field(default_factory=EvidenceField)
    category: EvidenceField = Field(default_factory=EvidenceField)
    signals: list[RiskSignal] = Field(default_factory=list)


class PageAnalysis(StrictModel):
    status: AnalysisStatus
    brand: Brand = Brand.UNKNOWN
    category: Topic = Topic.UNKNOWN
    signals: list[RiskSignal] = Field(default_factory=list)
    failure: FailureCode | None = None

    @model_validator(mode="after")
    def validate_failure(self) -> "PageAnalysis":
        if (self.status is AnalysisStatus.COMPLETED) != (self.failure is None):
            raise ValueError("completed analyses have no failure; fallback analyses require one")
        return self


# ── 사례 검색 ────────────────────────────────────────────────


class CaseMatch(StrictModel):
    case_id: str
    similarity: float
    matched_variant: str
    categories: list[CategoryCode] = Field(default_factory=list)


class CaseSearchResult(StrictModel):
    status: AnalysisStatus
    matches: list[CaseMatch] = Field(default_factory=list)


# ── 최종 상태 ────────────────────────────────────────────────


class FinalState(str, Enum):
    SMISHING_SUSPECTED = "smishing_suspected"
    OFFICIAL_DOMAIN = "official_domain"
    INCONCLUSIVE = "inconclusive"
    NOT_ANALYZABLE = "not_analyzable"
    INPUT_REQUIRED = "input_required"
