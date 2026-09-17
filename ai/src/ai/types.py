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


class AnalysisStatus(str, Enum):
    COMPLETED = "completed"
    FALLBACK = "fallback"


class CategoryCode(str, Enum):
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
