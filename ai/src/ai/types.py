"""backend ↔ ai 사이에 오가는 타입. 호출당하는 쪽이 소유합니다."""

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReasonCode(str, Enum):
    NO_URL = "no_url"
    OFFICIAL_MATCH = "official_match"
    LOOKALIKE = "lookalike"
    NOT_IN_WHITELIST = "not_in_whitelist"
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


# ── LLM 출력 (환각 방어를 위해 strict) ──────────────────────────


class RiskSignalCode(str, Enum):
    INSTALL_PROMPT = "install_prompt"
    CREDENTIAL_REQUEST = "credential_request"
    DANGEROUS_PERMISSION = "dangerous_permission"
    REMOTE_CONTROL = "remote_control"
    OVERSIZED_PAYLOAD = "oversized_payload"
    PACKER_DETECTED = "packer_detected"
    BRAND_MISMATCH = "brand_mismatch"


class EvidenceSource(str, Enum):
    MESSAGE = "message"
    OBSERVATION = "observation"


class RiskSignal(StrictModel):
    """LLM이 제안한 위험 신호 후보. decide()의 검증을 통과해야 채택됩니다."""

    code: RiskSignalCode
    evidence_source: EvidenceSource
    evidence_ref: str


class SignalProposal(StrictModel):
    signals: list[RiskSignal] = Field(default_factory=list)


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
