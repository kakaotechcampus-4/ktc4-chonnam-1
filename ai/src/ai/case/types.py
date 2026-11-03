"""통화·링크 없는 문자 케이스 분석이 함께 쓰는 타입 (v3 §3.1 중 두 경로에 필요한 것만).

링크 문자 전용 칸(categories·claimed_sender 등)은 일부러 넣지 않았다.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field

from ai.types import CaseSearchResult, FailureCode, LinkCandidate, StrictModel


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
    # Q2 답 근거 (v3 §7.1). user_not_person 만 하한(L-4)에 쓴다
    USER_NOT_PERSON = "user_not_person"
    USER_IS_PERSON = "user_is_person"


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
    # 통화 경로는 첫 질문만 쓴다. Q2 는 가족·지인이라고 한 문자에만 붙는다 (v3 §7.6)
    ALREADY_ACTED = "already_acted"
    CALLED_KNOWN_NUMBER = "called_known_number"


class VerifyAction(str, Enum):
    """결과 카드의 확인 안내 (v3 §7.8). 문장은 FE 가 정한다."""

    CALL_KNOWN_NUMBER = "call_known_number"
    CHECK_ACCOUNT_HOLDER = "check_account_holder"


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


# ── 입력: STT 결과 ───────────────────────────────────────────


class Segment(StrictModel):
    # 통화는 화자를 나누지 않아 늘 unknown 이다 (ADR 0008). 누구 말인지는 verify 가 문장 꼴로 가늠한다
    side: Side
    text: str
    start: float | None = None
    end: float | None = None


class TranscriptResult(StrictModel):
    """STT 의 반환 형태. 세그먼트는 문장 하나씩이다(스펙 D1, ADR 0008)."""

    segments: list[Segment]
    failure: FailureCode | None = None
    stt_model: str | None = None
    audio_seconds: float | None = None


class ReadingSource(StrictModel):
    kind: InputKind
    segments: list[Segment]


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
    # self_side: 받는 사람이 되묻거나 옮긴 말, 자기 일을 말한 문장 (forms.is_recipient_side)
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
    # 말로 불러 준 주소(origin=spoken). 철자를 확인할 수 없어 공식 대조 근거가 될 수 없다 (U-3)
    links: list[LinkCandidate] = Field(default_factory=list)
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


class MessageAnswers(StrictModel):
    """링크 없는 문자 결과에 쌓인 답 (v3 §8). BE 는 결과와 함께 저장하기만 한다."""

    # Q1 답은 쌓인다. 고른 순서대로 두고, 같은 답은 한 번만 남긴다
    already_acted: list[str] = Field(default_factory=list)
    # Q2 답은 마지막 것으로 덮어쓴다
    called_known_number: str | None = None


class MessageCaseResult(StrictModel):
    """v3 CaseFinal 중 링크 없는 문자(가족·지인·기관 사칭)에 쓰는 칸."""

    scenario: Scenario
    conclusion: Conclusion
    caution: bool = False
    floors: list[str] = Field(default_factory=list)
    # 읽기에서 받아들인 근거 + 답 근거(origin=user_answer). 답을 다시 처리할 때 답 근거만 갈아 끼운다
    evidence: list[Evidence] = Field(default_factory=list)
    claims: ClaimView = Field(default_factory=ClaimView)
    stop_actions: list[StopAction] = Field(default_factory=list)
    # Q1 답이 오면 apply_message_answer 가 채운다 (v3 §7.7)
    response_actions: list[ResponseAction] = Field(default_factory=list)
    verify_actions: list[VerifyAction] = Field(default_factory=list)
    questions: list[AttachedQuestion] = Field(default_factory=list)
    checked: list[CheckItem] = Field(default_factory=list)
    unchecked: list[Unchecked] = Field(default_factory=list)
    # 내세운 기관의 공식 대표번호. 번호는 대표번호 KB 에서만 온다(문자 속 번호는 쓰지 않는다)
    official_contact: OfficialContact | None = None
    similar_cases: CaseSearchResult | None = None
    rejected: list[RejectedClaim] = Field(default_factory=list)
    reading_status: Literal["ok", "fallback"] = "ok"
    # Q2 답 때문에 결론이나 caution 이 바뀌었을 때만 True (v3 §7.10)
    basis_from_answer: bool = False
    answers: MessageAnswers = Field(default_factory=MessageAnswers)
    explanation: str = ""
    failures: list[FailureCode] = Field(default_factory=list)
