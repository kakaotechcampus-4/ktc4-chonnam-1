"""통화 케이스 분석이 함께 쓰는 타입 (v3 §3.1 중 통화에 필요한 것만).

문자 전용 칸(categories·claimed_sender 등)은 일부러 넣지 않았다.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field

from ai.types import CaseSearchResult, FailureCode, StrictModel


# ── enum ────────────────────────────────────────────────────


class InputKind(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"


class Scenario(str, Enum):
    LINK_SMISHING = "link_smishing"
    VOICE_PHISHING = "voice_phishing"
    IMPERSONATION_NO_LINK = "impersonation_no_link"
    UNKNOWN = "unknown"


class Conclusion(str, Enum):
    SUSPECTED = "suspected"
    OFFICIAL_DOMAIN = "official_domain"
    INCONCLUSIVE = "inconclusive"
    NOT_ANALYZABLE = "not_analyzable"
    INPUT_REQUIRED = "input_required"


class Origin(str, Enum):
    MESSAGE = "message"
    IMAGE_TEXT = "image_text"
    TRANSCRIPT = "transcript"
    TOOL = "tool"
    USER_ANSWER = "user_answer"


class Side(str, Enum):
    OTHER = "other"
    SELF = "self"
    UNKNOWN = "unknown"


class IdentityKind(str, Enum):
    INSTITUTION = "institution"
    COMPANY = "company"
    ACQUAINTANCE = "acquaintance"
    OTHER = "other"


class DemandKind(str, Enum):
    MONEY_TRANSFER = "money_transfer"
    CASH_WITHDRAWAL = "cash_withdrawal"
    GIFT_CARD = "gift_card"
    AUTH_CODE = "auth_code"
    PERSONAL_INFO = "personal_info"
    APP_INSTALL = "app_install"
    REMOTE_CONTROL = "remote_control"
    CALL_NUMBER = "call_number"
    MOVE_MESSENGER = "move_messenger"
    VISIT_LINK = "visit_link"
    OTHER = "other"


class EvidenceCode(str, Enum):
    UNOFFICIAL_NUMBER = "unofficial_number"
    INSTITUTION_CLAIM = "institution_claim"
    ACQUAINTANCE_CLAIM = "acquaintance_claim"
    CONTACT_BLOCKER = "contact_blocker"
    # 요구 코드 (DemandKind 와 짝은 v3 §5.2 표)
    MONEY_REQUEST = "money_request"
    CASH_DELIVERY_REQUEST = "cash_delivery_request"
    GIFT_CARD_REQUEST = "gift_card_request"
    AUTH_CODE_REQUEST = "auth_code_request"
    INFO_REQUEST = "info_request"
    APP_INSTALL_REQUEST = "app_install_request"
    REMOTE_APP_MENTION = "remote_app_mention"
    CALL_REQUEST = "call_request"
    MESSENGER_MOVE = "messenger_move"


class StopAction(str, Enum):
    DONT_CLICK_LINK = "dont_click_link"
    DONT_ENTER_INFO = "dont_enter_info"
    DONT_INSTALL_APP = "dont_install_app"
    DONT_SEND_MONEY = "dont_send_money"
    DONT_SEND_GIFT_CARD = "dont_send_gift_card"
    DONT_SHARE_AUTH_CODE = "dont_share_auth_code"
    DONT_CALL_NUMBER_IN_MESSAGE = "dont_call_number_in_message"
    HANG_UP = "hang_up"
    PASTE_TEXT = "paste_text"


class ResponseAction(str, Enum):
    # call_1332 는 쓰지 않는다 (FE D17: 지급정지는 112 로 연결되고 정보 유출은 노출 등록이 할 일)
    CONTACT_GIFT_CARD_ISSUER = "contact_gift_card_issuer"
    REPORT_112 = "report_112"
    REQUEST_PAYMENT_STOP = "request_payment_stop"
    CONTACT_CARD_ISSUER = "contact_card_issuer"
    CHANGE_PASSWORD = "change_password"
    REGISTER_INFO_EXPOSURE = "register_info_exposure"
    # 앱을 설치했을 때 금감원 대처 안내 순서 (FE D17)
    ISOLATE_PHONE = "isolate_phone"
    USE_OTHER_DEVICE = "use_other_device"
    REMOVE_APP_AND_CHECK_DEVICE = "remove_app_and_check_device"
    DELETE_DOWNLOADED_FILE = "delete_downloaded_file"


class QuestionId(str, Enum):
    # 통화 경로는 첫 질문만 쓴다
    ALREADY_ACTED = "already_acted"


class CheckItem(str, Enum):
    MESSAGE_TEXT = "message_text"
    SCREENSHOT_TEXT = "screenshot_text"
    TRANSCRIPT = "transcript"
    LINK_ADDRESS = "link_address"
    OFFICIAL_DOMAIN = "official_domain"
    DOMAIN_AGE = "domain_age"
    PAGE_STATIC = "page_static"
    PAGE_RENDERED = "page_rendered"
    PHONE_NUMBER = "phone_number"
    SIMILAR_CASES = "similar_cases"
    USER_ANSWER = "user_answer"


class UncheckedItem(str, Enum):
    ACTUAL_SENDER = "actual_sender"
    PHONE_NUMBER = "phone_number"
    SIMILAR_CASES = "similar_cases"
    TRANSCRIPT = "transcript"
    # 받아 적은 글이 실제 통화와 같은지는 확인할 수 없다 (Task 7)
    TRANSCRIPT_ACCURACY = "transcript_accuracy"
    LINK_SPELLING = "link_spelling"
    LINK_ADDRESS = "link_address"
    OFFICIAL_DOMAIN = "official_domain"
    DOMAIN_AGE = "domain_age"
    PAGE_STATIC = "page_static"
    PAGE_RENDERED = "page_rendered"


class UncheckedReason(str, Enum):
    SKIPPED_ENOUGH_EVIDENCE = "skipped_enough_evidence"
    NOT_RUN = "not_run"
    NOT_ALLOWED = "not_allowed"
    NO_LINK = "no_link"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"
    TIMEOUT = "timeout"
    KB_EMPTY = "kb_empty"
    READ_FROM_IMAGE = "read_from_image"
    READ_FROM_VOICE = "read_from_voice"
    CANNOT_VERIFY = "cannot_verify"


class Unchecked(StrictModel):
    item: UncheckedItem
    reason: UncheckedReason


# ── 입력: STT·화자 분리 결과 ─────────────────────────────────


class Segment(StrictModel):
    side: Side
    # "화자1", "화자2", "기타" 같은 문자열 (스펙 D1)
    speaker: str | None = None
    text: str
    start: float | None = None
    end: float | None = None


class TranscriptResult(StrictModel):
    """STT·화자 분리의 반환 형태. side 는 unknown 으로 두고 읽기 뒤에 채운다(v3 §4)."""

    segments: list[Segment]
    speaker_split_failed: bool
    failure: FailureCode | None = None
    stt_model: str | None = None
    diarize_model: str | None = None
    audio_seconds: float | None = None


class ReadingSource(StrictModel):
    kind: InputKind
    segments: list[Segment]
    other_speaker: str | None = None
    speaker_split_failed: bool = False


# ── 주장·요구 읽기 (LLM 출력) ────────────────────────────────


class IdentityClaim(StrictModel):
    kind: IdentityKind
    name: str
    quote: str


class PretextClaim(StrictModel):
    quote: str


class DemandClaim(StrictModel):
    kind: DemandKind
    quote: str


class BlockerClaim(StrictModel):
    quote: str


class ClaimReading(StrictModel):
    # 개수 제한은 프롬프트가 아니라 검증으로 건다 (v3 §5.1)
    identity: list[IdentityClaim] = Field(default_factory=list, max_length=3)
    pretexts: list[PretextClaim] = Field(default_factory=list, max_length=3)
    demands: list[DemandClaim] = Field(default_factory=list, max_length=6)
    blockers: list[BlockerClaim] = Field(default_factory=list, max_length=3)
    other_speaker: str | None = None
    speaker_confidence: Literal["high", "medium", "low"] = "low"
    spoken_hosts: list[str] = Field(default_factory=list, max_length=3)


class ClaimReadingResult(StrictModel):
    status: Literal["ok", "fallback"]
    reading: ClaimReading | None = None
    failure: FailureCode | None = None


# ── 검증·결과 ────────────────────────────────────────────────


class Evidence(StrictModel):
    code: EvidenceCode
    origin: Origin
    quote: str | None = None
    record_id: str | None = None
    derived_by: Literal["llm", "lexicon", "code", "answer"]
    counts_for_floor: bool


class RejectedClaim(StrictModel):
    kind: Literal["identity", "pretext", "demand", "blocker", "spoken_host"]
    quote: str
    reason: Literal[
        "not_in_source",
        "too_short",
        "self_side",
        "notice_sentence",
        "negated",
        "not_url",
    ]


class AttachedQuestion(StrictModel):
    question_id: QuestionId
    # 순서 고정. 라벨은 FE가 정한다
    option_ids: list[str]


class ClaimViewIdentity(StrictModel):
    kind: IdentityKind
    name: str
    quote: str
    # V-5 목록에 걸렸다는 뜻이지 실제 기관이라는 뜻이 아니다
    listed_institution: bool


class ClaimViewDemand(StrictModel):
    kind: DemandKind
    quote: str


class ClaimView(StrictModel):
    """받아들인 주장만 원문 순서로 담는다."""

    identity: list[ClaimViewIdentity] = Field(default_factory=list)
    pretexts: list[str] = Field(default_factory=list)
    demands: list[ClaimViewDemand] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class NumberCheck(StrictModel):
    """상대가 말한 번호를 내세운 기관의 공식 대표번호와 맞춰 본 결과 (v3 §6.3)."""

    number: str
    org: str
    # org_not_listed: 대표번호 KB 에 없는 기관. 공식이 아니라는 뜻이 아니다
    result: Literal["official", "not_official", "org_not_listed"]
    source_url: str | None = None


class OfficialContact(StrictModel):
    """상대가 내세운 기관의 공식 대표번호. V1 확인 안내(`verify_line`)에 쓴다.

    번호는 대표번호 KB 에서만 온다. 녹음 속 번호는 사기범 번호일 수 있어 쓰지 않는다.
    """

    org: str
    number: str
    source_url: str


class VoiceCaseResult(StrictModel):
    """v3 CaseFinal 중 통화에 쓰는 칸."""

    scenario: Scenario
    conclusion: Conclusion
    caution: bool = False
    floors: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    claims: ClaimView = Field(default_factory=ClaimView)
    stop_actions: list[StopAction] = Field(default_factory=list)
    # 분석 직후에는 비어 있다. Q1 답이 오면 apply_call_answers 가 채운다 (v3 §8)
    response_actions: list[ResponseAction] = Field(default_factory=list)
    questions: list[AttachedQuestion] = Field(default_factory=list)
    checked: list[CheckItem] = Field(default_factory=list)
    unchecked: list[Unchecked] = Field(default_factory=list)
    number_checks: list[NumberCheck] = Field(default_factory=list)
    official_contact: OfficialContact | None = None
    similar_cases: CaseSearchResult | None = None
    rejected: list[RejectedClaim] = Field(default_factory=list)
    reading_status: Literal["ok", "fallback"] = "ok"
    explanation: str = ""
    failures: list[FailureCode] = Field(default_factory=list)
