import asyncio
import hmac
import os
import time

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone, timedelta
from typing import NamedTuple

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from pydantic import ValidationError

from backend.src.server.http_client import get_http_client
from backend.src.server.lifespan import app_lifespan
from backend.src.server.log import get_logger
from backend.src.server.budgets import ANALYSIS_DEADLINE_SECONDS, CASE_ANALYZE_BUDGET_S
from backend.src.server.callback import send_callback
from backend.src.server.callback_guard import callback_host, is_allowed_callback_url
from backend.src.server.jobs import (
    ANALYSIS_JOBS,
    RUNNING_USERS,
    create_analysis_job,
    find_latest_job_by_user,
    finish_job,
    now_iso,
)
from backend.src.server.media_guard import (
    is_allowed_media_url,
)
from backend.src.server.investigation import (
    record_trace,
    see_claims,
    see_collection,
    see_official,
    see_signals,
)
from backend.src.server.voice_flow import log_voice_stage, run_voice_analysis
from services.official_url_analysis import build_ai_url_analysis
from services.kakao_card_renderer import kakao_response, render_card
from services.result_card_renderer import (
    render_result_card,
    merge_kakao_responses,
    render_detail_carousel,
    render_voice_detail_carousel,
    render_voice_answer_card,
    render_message_case_card,
    render_message_answer_card,
    render_message_default_answer_card,
    link_caution_applies,
    render_link_caution_card,
)

# AI 연결
from ai.pipeline import analyze_message_part, finalize_analysis, read_message
from ai.types import AnalysisStatus, FailureCode, MessagePart, MessageReading, UrlAnalysis

# 분석 장부와 에이전트 루프. 에이전트는 돌릴 분석을 고르기만 하고 카드는 코드가 만든다
from ai.agent import (
    Board, Tool, case_search_tool, investigation, official_number_tool,
)

# 통화(음성) 분석 연결. ai.case 는 카카오 payload를 모른다 (CLAUDE.md 의존 방향).
from ai.case import apply_call_answers
from ai.case.types import VoiceCaseResult

# 링크 없는 문자(가족·지인·기관 사칭) 분석 연결. 답 처리는 LLM 없이 결과만 다시 정한다.
from ai.case import analyze_message_case, apply_message_answer
from ai.case.actions import default_response_actions
from ai.case.types import MessageCaseResult

from scanner.isolation_client import collect_url_isolated
from scanner.isolation_page import to_isolated_page
from scanner.models import CollectResult
from urllib.parse import urlparse


app = FastAPI(lifespan=app_lifespan)
_log = get_logger("server")


# ============================================================
# Skill Request Auth (이슈 #66 BE 할 일 2)
# ============================================================

def verify_skill_secret(request: Request) -> None:
    """카카오 스킬 서버로 위장한 요청을 막는다.

    local/test 환경에서는 매번 비밀값을 셋업하기 번거로우므로 검사를
    생략한다 — staging/production은 카카오 관리자센터가 실제로 호출하는
    환경이라 생략하지 않는다.

    KAKAO_SKILL_SECRET이 비어 있으면(운영 배포에 값을 안 넣은 실수까지
    포함) 모든 요청을 거부한다 — 빈 값끼리 비교해 우연히 통과하는 것을
    막기 위함이다. KAKAO_SKILL_SECRET_PREVIOUS는 키 교체 기간에만 쓰며,
    비어 있으면 비교 대상에서 제외한다.

    거부할 때마다 원인 코드만 로그에 남긴다(헤더 원문·secret·토큰은
    절대 남기지 않는다) — 오픈빌더 실제 호출이 401로 막히는 원인이
    서버 설정 누락인지, 헤더 자체가 안 오는지, 값이 다른지 운영
    로그만으로 구분하기 위함이다.
    """

    app_env = os.getenv("APP_ENV", "production").lower()
    if app_env in {"local", "test"}:
        return

    secret = os.getenv("KAKAO_SKILL_SECRET", "")
    if not secret:
        _log.warning("[SKILL AUTH REJECTED] reason=secret_not_configured")
        raise HTTPException(status_code=401, detail="Skill secret not configured")

    provided = request.headers.get("X-Skill-Secret", "")

    if hmac.compare_digest(provided, secret):
        return

    previous_secret = os.getenv("KAKAO_SKILL_SECRET_PREVIOUS", "")
    if previous_secret and hmac.compare_digest(provided, previous_secret):
        return

    if not provided:
        _log.warning("[SKILL AUTH REJECTED] reason=header_missing")
    else:
        _log.warning("[SKILL AUTH REJECTED] reason=secret_mismatch")

    raise HTTPException(status_code=401, detail="Invalid skill secret")


# 콜백 URL 유효 시간을 고려한 안전 마진
CALLBACK_DEADLINE_SECONDS = ANALYSIS_DEADLINE_SECONDS

# 링크는 요구 분석 LLM 이 찾고(ADR 0007) 수집은 그 뒤에 시작한다. 추출 기본 상한 30초를
# 다 쓰면 45초 안에 수집(17초)·페이지 분석·카드 여유가 남지 않는다 (docs/latency-budget.md)
READ_MESSAGE_TIMEOUT_SECONDS = 15.0

# 카카오 SkillResponse 한 번에 담을 수 있는 outputs 수
KAKAO_MAX_OUTPUTS = 3


# ============================================================
# Analysis Job API
# ============================================================

@app.get("/api/analyses/{job_id}", dependencies=[Depends(verify_skill_secret)])
async def get_analysis_job(job_id: str):
    """
    인메모리에 저장된 분석 작업 상태와 결과를 조회한다.

    현재는 개발/테스트용 API이며,
    서버 재시작 시 저장된 작업은 소실된다.
    """

    job = ANALYSIS_JOBS.get(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Analysis job not found"
        )

    return {
        "success": True,
        "job": job
    }


# ============================================================
# Time Format
# ============================================================

def format_completed_at_kst(
    completed_at: str | None
) -> str | None:
    """UTC completed_at을 한국 시간(KST, UTC+9) HH:MM으로 변환한다."""

    if not completed_at:
        return None

    try:
        completed_datetime = datetime.fromisoformat(
            completed_at.replace("Z", "+00:00")
        )

        if completed_datetime.tzinfo is None:
            completed_datetime = completed_datetime.replace(
                tzinfo=timezone.utc
            )

        kst = timezone(
            timedelta(hours=9)
        )

        kst_datetime = completed_datetime.astimezone(
            kst
        )

        return kst_datetime.strftime("%H:%M")

    except (ValueError, TypeError):
        return None


# ============================================================
# Handle Check Result
# ============================================================

def handle_check_result(
    user_id: str | None
) -> dict:
    job = find_latest_job_by_user(user_id)

    if job is None:
        return render_card("k4-no-result")

    status = job.get("status")

    if status == "running":
        return render_card("k1-still-running")

    if status == "completed":
        result = job.get("result")

        if not result:
            return render_card("k4-no-result")

        completed_time = format_completed_at_kst(
            job.get("completed_at")
        )

        if not completed_time:
            return result

        # 결과 카드 + Q1 + Q2 처럼 이미 3개면 시각 말풍선을 붙이지 않는다(카카오 outputs 상한)
        if len(result.get("template", {}).get("outputs", [])) >= KAKAO_MAX_OUTPUTS:
            return result

        time_message = kakao_response(
            f"{completed_time}에 확인한 결과예요."
        )

        return merge_kakao_responses([
            time_message,
            result,
        ])

    if status in ("failed", "timeout"):
        return render_card("r4-unavailable")

    return render_card("k4-no-result")


# ============================================================
# Handle Detail Carousel (이슈 #69)
# ============================================================

def handle_detail_carousel(job_id: str) -> dict:
    """"자세히 보기" 버튼 클릭을 처리한다.

    분석을 다시 하지 않는다 — 저장해 둔 `final_payload`로
    `render_detail_carousel()`을 다시 불러 캐러셀만 새로 만든다. Job이
    없거나(서버 재시작 등으로 소실) 아직 `final_payload`가 채워지기 전
    (분석 진행 중·실패)이면 재판정 없이 안내만 돌려준다.
    """

    job = ANALYSIS_JOBS.get(job_id)

    if job is None:
        return kakao_response(_CASE_NOT_FOUND_TEXT)

    if job.get("analysis_kind") != "voice" and job.get("link_count", 0) > 1:
        return kakao_response(
            "여러 주소를 함께 확인한 경우에는 상세 결과를 제공하지 않아요. "
            "확인하고 싶은 주소를 하나씩 다시 보내 주세요."
        )

    final_payload = job.get("final_payload")

    if not final_payload:
        return kakao_response(_CASE_NOT_FOUND_TEXT)

    # 음성(VoiceCaseResult)과 문자·URL(AnalysisResponse)은 모양이 완전히
    # 달라서 같은 렌더러로 돌리면 안 된다 — 음성 결과를 문자 캐러셀
    # 렌더러에 넘기면 빈 칸투성이 캐러셀이 나간다.
    if job.get("analysis_kind") == "voice":
        return render_voice_detail_carousel(final_payload)

    return render_detail_carousel(final_payload)


# ============================================================
# Kakao Skill
# ============================================================

_VOICE_ATTACHMENT_EXTENSIONS = (".m4a", ".mp3", ".wav", ".webm")


def _utterance_voice_attachment(utterance: str) -> dict | None:
    """`params.media` 없이 녹음 파일(M4A 등)을 파일로 첨부했을 때를 인식한다.

    실기기 확인 결과(`docs/archive/designs/voice-phishing-flow.md` 0절): 카톡 **파일 첨부**로 보낸
    녹음은 `params.media`가 오지 않고, `utterance`에 카카오 CDN의 서명된
    주소만 그대로 온다. 음성 **메시지**(마이크 버튼)의 `media.type=="audio"`
    경로와는 별개 경로라 여기서 추가로 인식한다. 허용 호스트(`is_allowed_media_url`)와
    경로 끝 오디오 확장자로만 판별하고, 문자나 일반 링크를 음성으로 오인하지 않는다.
    """

    candidate = (utterance or "").strip()
    if not candidate or any(ch.isspace() for ch in candidate):
        return None
    if not is_allowed_media_url(candidate):
        return None
    try:
        path = urlparse(candidate).path.lower()
    except ValueError:
        return None
    if not path.endswith(_VOICE_ATTACHMENT_EXTENSIONS):
        return None
    return {"type": "audio", "url": candidate}


@app.post("/kakao/skill", dependencies=[Depends(verify_skill_secret)])
async def kakao_skill(
    request: Request,
    background_tasks: BackgroundTasks
):
    body = await request.json()

    user_request = body.get("userRequest", {})

    utterance = user_request.get("utterance", "")
    user_id = user_request.get("user", {}).get("id")
    callback_url = user_request.get("callbackUrl")

    # 카카오톡 음성 메시지는 두 모양으로 온다 (docs/archive/designs/voice-phishing-flow.md 0절):
    # (1) 마이크 버튼 메시지 — params.media.type == "audio"
    # (2) 파일로 첨부한 녹음(m4a 등) — params.media 없이 utterance에 카카오
    #     CDN 서명 주소만 옴. 호스트+확장자로 판별한다.
    media: dict = (user_request.get("params") or {}).get("media") or {}
    if media.get("type") != "audio":
        attachment_media = _utterance_voice_attachment(utterance)
        if attachment_media is not None:
            media = attachment_media
    is_voice_message = media.get("type") == "audio"

    # 문자 원문·서명 URL·사용자 ID 대신 요청 종류와 길이만 진단에 남긴다.
    _log.info(
        f"[KAKAO] "
        f"input_type={'voice' if is_voice_message else 'text'} "
        f"utterance_length={len(utterance) if isinstance(utterance, str) else 0} "
        f"user_present={bool(user_id)}"
    )

    _log.info(
        f"[CALLBACK EXISTS] "
        f"{bool(callback_url)}"
    )

    # 경로에는 1회용 콜백 토큰이 있어 호스트만 남긴다. 허용 목록을 좁힐 때 쓴다.
    _log.info(f"[CALLBACK HOST] {callback_host(callback_url)}")

    # 내부 주소로 결과를 POST 하게 만드는 요청을 막는다 (멘토 리뷰, isolation-security §5.5).
    if callback_url is not None and not is_allowed_callback_url(callback_url):
        _log.warning("[CALLBACK REJECTED] 허용되지 않은 callbackUrl")
        return render_card("r4-unavailable")

    intent_name = (
        body.get("intent", {}).get("name", "")
    )

    # "자세히 보기" 버튼 클릭 (이슈 #69). 결과 카드에 꽂아 둔 job_id가
    # 버튼의 extra로 저장돼 있다가, 클릭 시 카카오가 action.clientExtra로
    # 그대로 돌려준다(`services/result_card_renderer.py`의 `_inject_job_id`).
    client_extra = body.get("action", {}).get("clientExtra") or {}
    detail_job_id = client_extra.get("job_id")

    if detail_job_id:
        return handle_detail_carousel(detail_job_id)

    # Q1 "이미 하신 게 있나요?"·Q2 "원래 알던 번호로 통화해 보셨나요?" 리스트 카드 항목 클릭.
    # LLM·네트워크 없이 바로 답한다(설계 문서 D11, 콜백 불필요). 녹음·문자 결과 모두
    # 같은 답변 블록을 쓰므로 저장된 Job 의 분석 종류로 나눈다.
    qa_case_id = client_extra.get("case_id")
    qa_question_id = client_extra.get("question_id")
    qa_option_id = client_extra.get("option_id")

    if qa_case_id and qa_question_id and qa_option_id:
        return handle_case_answer(
            str(qa_case_id), str(qa_question_id), str(qa_option_id), user_id,
            kind=client_extra.get("kind"),
        )

    # 사용자가 결과 확인 요청하면 최근 분석 결과를 리턴
    if intent_name == "결과 확인":
        return handle_check_result(user_id)

    # 카카오톡 음성 메시지. utterance에도 음성 파일의 서명된 CDN URL이
    # 그대로 실려 올 수 있으므로, 반드시 문자 분석(read_message()) **전에**
    # 분기한다 — utterance를 링크로 보고 기존 문자·URL 분석으로
    # 흘려보내면 안 된다(다운로드 주소 출처는 오직 media.url이어야 함).
    if is_voice_message:
        return await handle_voice_message(
            media, user_id, callback_url, background_tasks
        )

    # 빈 문자는 LLM 을 부를 것도 없이 바로 입력 요청 카드
    if not isinstance(utterance, str) or not utterance.strip():
        return render_card("r6-input-required")

    # 링크는 요구 분석 LLM 이 문자를 읽으며 찾는다(ADR 0007). 수 초가 걸려 첫 응답(5초)
    # 전에는 링크가 있는지 모른다. 그래서 링크가 없는 문자도 콜백 뒤에 결과를 보낸다.

    # 같은 사용자의 이전 분석이 아직 진행 중인 경우
    if user_id and user_id in RUNNING_USERS:
        return render_card("k5-duplicate")

    # callbackUrl이 없는 경우 개발/테스트용 동기 처리
    if not callback_url:
        _log.info("[CALLBACK] callbackUrl 없음 - 동기 처리")

        return await run_analysis(utterance)

    return _start_background_analysis(background_tasks, utterance, callback_url, user_id)


def _start_background_analysis(
    background_tasks: BackgroundTasks,
    utterance: str,
    callback_url: str,
    user_id: str | None,
    *,
    voice_media_url: str | None = None,
) -> dict:
    """Job 을 만들고 분석을 백그라운드로 넘긴 뒤 대기 카드를 돌려준다. 문자와 통화가 같이 쓴다.

    콜백 주소가 만료됐거나 전송이 실패해도 `run_analysis_and_callback()` 이 결과를 Job 에
    남기므로 여기서 따로 처리하지 않는다.
    """

    if user_id:
        RUNNING_USERS.add(user_id)

    job_id = create_analysis_job(user_id)
    extra = {}
    if voice_media_url is not None:
        ANALYSIS_JOBS[job_id]["analysis_kind"] = "voice"
        extra["voice_media_url"] = voice_media_url

    background_tasks.add_task(
        run_analysis_and_callback, utterance, callback_url, user_id, job_id, **extra,
    )
    return render_card("r5-analyzing")


# ============================================================
# Main Analysis
# ============================================================

# read_message() 가 링크를 다 받지 못했을 때의 안내. 판정이 아니라 안내에만 쓴다 (ADR 0007)
_UNREAD_LINKS_TEXT = (
    "문자에서 주소처럼 보이는 부분을 찾았지만\n"
    "정확히 읽지 못해 검사하지 못했어요.\n\n"
    "확인하고 싶은 주소를 그대로 복사해\n"
    "다시 보내 주세요.\n\n"
    "※ 확인 전까지 링크는 누르지 마세요."
)
_PARTLY_READ_LINKS_TEXT = (
    "문자에 있는 주소 중 일부는 정확히 읽지 못해 검사하지 못했어요. "
    "아래 결과는 읽은 주소만 확인한 거예요.\n"
    "※ 검사하지 못한 주소는 누르지 마세요."
)
_OMITTED_LINK_RESULTS_TEXT = (
    "일부 검사 결과는 한 번에 표시할 수 없어 생략했어요. "
    "확인하고 싶은 주소를 하나씩 다시 보내 주세요."
)
_TOO_LONG_TEXT = (
    "문자가 너무 길어서 확인하지 못했어요.\n\n"
    "확인하고 싶은 부분만 복사해 다시 보내 주세요."
)


def _response_without_links(reading: MessageReading) -> dict | None:
    """링크를 조사할 수 없을 때의 응답. 링크가 있거나 링크 없는 문자로 확인됐으면 None.

    LLM 이 실패하면 링크가 있는지 모르므로 "링크 없음"(R6·R6b)으로 안내하지 않는다.
    받지 못한 링크(`unverified_links`)가 있을 때도 마찬가지다. 링크가 없다고 확인된 문자는
    호출한 쪽이 링크 없는 사칭 문자 분석(`run_message_case`)으로 넘긴다.
    """

    extracted = reading.extracted
    if extracted.analysis_status is not AnalysisStatus.COMPLETED:
        failure = extracted.failure
        _log.warning(f"[READ MESSAGE FAILED] {failure.value if failure else 'unknown'}")
        if failure is FailureCode.EMPTY_INPUT:
            return render_card("r6-input-required")
        if failure is FailureCode.INPUT_TOO_LARGE:
            return kakao_response(_TOO_LONG_TEXT)
        return render_card("r4-unavailable")
    if reading.links:
        return None
    if reading.unverified_links:
        return kakao_response(_UNREAD_LINKS_TEXT)
    return None


async def _collect_link(link: str) -> CollectResult:
    """격리 환경에서 링크를 수집한다. 수집 API 가 죽어도 문자 근거는 쓸 수 있게 실패를 결과로 돌려준다."""

    try:
        return await collect_url_isolated(link)
    except Exception as error:  # noqa: BLE001 — 수집 실패는 판단 보류로 이어지는 정상 갈래다
        _log.warning(f"[COLLECTION FAILED] {type(error).__name__}")
        return CollectResult.failed(link, "collection_failed")


class _Need(NamedTuple):
    """카드가 읽는 분석. 카드 코드가 board.get 으로 꺼내므로 에이전트가 빼먹어도 돈다."""

    collect: Tool[CollectResult]
    signals: Tool[MessagePart | None]
    official: Tool[UrlAnalysis]
    claims: Tool[MessageCaseResult]


def _analysis_tools(
    utterance: str, reading: MessageReading, started: float,
) -> tuple[_Need, tuple[Tool, ...]]:
    """이 요청의 분석 도구. 새 시나리오는 여기에 Tool 하나를 더한다.

    함수 이름을 부를 때 찾으므로 테스트가 main.* 를 바꿔 끼울 수 있다.
    """
    # ponytail: 격리 수집기의 동시 처리량을 재지 않아 한 번에 하나만 수집한다. 재고 나면 링크별 병렬로 푼다
    one_collection = asyncio.Lock()

    async def collect(_board: Board, n: int) -> CollectResult:
        # 주소는 여기서만 정해진다. 부르는 쪽은 번호만 낸다
        async with one_collection:
            return await _collect_link(reading.links[n - 1].url)

    async def signals(_board: Board, _: None) -> MessagePart | None:
        try:
            # 같은 읽기를 넘겨 추출 LLM 을 다시 부르지 않는다
            return await analyze_message_part(reading.message, extracted=reading.extracted)
        except Exception as error:  # noqa: BLE001 — LLM 이 실패해도 응답은 나가야 한다 (원칙 3)
            _log.warning(f"[AI MESSAGE ERROR] {type(error).__name__}")
            return None

    async def official(board: Board, n: int) -> UrlAnalysis:
        collected, part = await asyncio.gather(board.get(need.collect, n), board.get(need.signals))
        brand = part.brand if part is not None else None
        return build_ai_url_analysis(collected, brand, reading.links[n - 1].url)

    async def claims(_board: Board, _: None) -> MessageCaseResult:
        if reading.links:
            return await _claims(reading.message, started, has_link=True)
        return await _claims(utterance, started)

    # 설명은 에이전트가 읽는다. 언제 필요한 분석인지까지 적는다
    need = _Need(
        Tool("collect_link",
             "링크를 격리 환경에서 열어 이동 경로와 페이지를 수집한다. 공식 대조가 함께 시작하므로 결과를 먼저 보고 싶을 때만 부른다",
             True, collect, see_collection),
        Tool("analyze_message_signals",
             "문자 본문의 위험 신호와 내세운 기관 이름을 읽는다. 공식 대조가 함께 시작하므로 결과를 먼저 보고 싶을 때만 부른다",
             False, signals, see_signals),
        Tool("check_official_domain",
             "링크 주소를 공식 도메인 목록과 대조한다. 그 링크의 수집과 본문 분석을 함께 시작한다. 링크가 있으면 링크마다 필요하다",
             True, official, see_official),
        Tool("analyze_claims",
             "문자가 내세운 사칭 주장과 요구를 읽는다. 링크가 없는 문자에 필요하다",
             False, claims, see_claims),
    )
    # 에이전트에게는 카드가 반드시 읽는 분석과, LLM·임베딩을 쓰지 않는 참고 분석만 내준다.
    # 카드가 안 읽을 수도 있는 분석을 시작시키면 카드용 임베딩 작업자를 빼앗아 카드 근거가 달라질 수 있다
    required = (need.collect, need.signals, need.official) if reading.links else (need.claims,)
    return need, (*required, case_search_tool(reading.message), official_number_tool(reading.message))


async def run_analysis(
    utterance: str,
    job_id: str | None = None,
) -> dict:
    """Read links from the message, then collect each once while message analysis runs.

    링크가 없다고 확인된 문자는 링크 없는 사칭 문자 분석으로 보낸다(세 번째 시나리오).
    """
    started = time.monotonic()
    reading = await read_message(utterance, timeout_s=READ_MESSAGE_TIMEOUT_SECONDS)
    _log.info(
        f"[LINK COUNT] {len(reading.links)} "
        f"unverified={reading.unverified_links}"
    )
    response = _response_without_links(reading)
    if response is not None:
        return response
    need, tools = _analysis_tools(utterance, reading, started)
    # 여기서는 카드 종류만 고른다. 어떤 분석이 도는지는 카드가 board 에서 꺼내는 것으로 정해진다
    async with investigation(
        reading, tools, on_close=lambda trace: record_trace(job_id, trace),
    ) as board:
        if reading.links:
            response = await _link_cards(board, need, reading, job_id)
        else:
            response = await run_message_case(
                utterance, job_id=job_id, started=started, claims=lambda: board.get(need.claims),
            )
    return response


async def _link_cards(
    board: Board, need: _Need, reading: MessageReading, job_id: str | None,
) -> dict:
    """링크마다 결과 카드를 만든다. 근거는 직접 만들지 않고 board 에서 꺼낸다."""
    link_count = len(reading.links)
    if job_id is not None and (job := ANALYSIS_JOBS.get(job_id)):
        # 링크별 상세 저장은 지원하지 않는다. 숨겨진 결과로의 직접 조회도 막는다.
        job["link_count"] = link_count

    cards: list[dict] = []
    # 미검사 안내를 먼저 확보한다. 분석은 모두 수행하고 마지막에 표시 개수만 제한한다.
    if reading.unverified_links:
        cards.append(kakao_response(_PARTLY_READ_LINKS_TEXT))
    for n in range(1, link_count + 1):
        try:
            cards.append(await _link_card(board, need, n, job_id))
        except Exception as error:  # noqa: BLE001 — 링크 하나가 실패해도 나머지 링크 결과는 보낸다
            _log.warning(f"[ANALYSIS ERROR] {type(error).__name__}")
            cards.append(render_card("r4-unavailable"))
    response = merge_kakao_responses(cards)
    outputs = response["template"]["outputs"]
    if reading.unverified_links and len(outputs) > KAKAO_MAX_OUTPUTS:
        # 첫 output은 위에서 만든 안내다. 결과 카드의 output 수에 관계없이
        # 이 안내를 남기고, 이미 분석한 결과 중 일부가 표시되지 않음을 명시한다.
        outputs[0]["simpleText"]["text"] += "\n\n" + _OMITTED_LINK_RESULTS_TEXT
        response["template"]["outputs"] = outputs[:KAKAO_MAX_OUTPUTS]
    return response


async def _link_card(board: Board, need: _Need, n: int, job_id: str | None) -> dict:
    """링크 하나의 결과 카드. 판단 보류면 문자 게이트를 읽는다. 게이트는 board 에서 한 번만 돈다(#89)."""

    # 공식 대조가 이 링크의 수집과 문자 분석을 함께 시작한다
    url_analysis = await board.get(need.official, n)
    collected = await board.get(need.collect, n)
    message_result = await board.get(need.signals)
    page, failure = to_isolated_page(collected)
    final_result = await finalize_analysis(
        url=url_analysis, message=message_result, page=page, failure=failure,
    )
    final_payload = final_result.model_dump(mode="json")
    caution_card = None
    if link_caution_applies(final_payload):
        caution_card = render_link_caution_card(final_payload, await _link_caution_payload(board, need))
    card = caution_card or render_result_card(
        final_payload, job_id=job_id, allow_details=(board.link_count == 1),
    )
    if job_id is not None and (job := ANALYSIS_JOBS.get(job_id)):
        job["final_payload"] = final_payload
    return card


async def _link_caution_payload(board: Board, need: _Need) -> dict | None:
    """링크 결과가 판단 보류일 때 본문을 문자 게이트로 읽는다(#89). 실패하면 None 이라 R9 를 쓴다.

    결과는 판단 보류 카드의 주의 갈래를 고르는 데만 쓴다. 링크 판정과 Job 저장 값은 바꾸지 않는다.
    """

    try:
        result = await board.get(need.claims)
    except Exception as error:  # noqa: BLE001 — 주의 갈래를 고르는 부가 정보다. 실패하면 R9 로 간다
        _log.warning(f"[LINK CAUTION ERROR] {type(error).__name__}")
        return None
    payload = result.model_dump(mode="json")
    _log.info(f"[LINK CAUTION] conclusion={payload.get('conclusion')} caution={payload.get('caution')}")
    return payload


# ============================================================
# Message Case (링크 없는 사칭 문자, 세 번째 시나리오)
#
# read_message()가 링크가 없다고 확인한 문자만 온다. 판정은 AI 게이트가 하고
# BE는 결과(MessageCaseResult)를 Job에 저장해 Q1·Q2 답과 "결과 확인"에 다시 쓴다.
# ============================================================

# analyze_message_case()에 줄 예산. 녹음과 같이 전체 분석 45초에서 카드·저장 여유
# 5초를 빼고, 이미 쓴 read_message() 시간을 뺀다(docs/latency-budget.md).
MESSAGE_CASE_BUDGET_S = CASE_ANALYZE_BUDGET_S

_CASE_NOT_FOUND_TEXT = "분석 결과를 찾을 수 없어요. 문자나 통화 녹음을 다시 보내 주세요."


async def _claims(text: str, started: float | None, **options) -> MessageCaseResult:
    """문자 게이트를 남은 예산으로 부른다. 예산은 처음 불린 때의 `40 - 경과 초`다."""

    elapsed = time.monotonic() - started if started is not None else 0.0
    budget = max(MESSAGE_CASE_BUDGET_S - elapsed, 0.0)
    return await analyze_message_case(text, time_budget_s=budget, **options)


async def run_message_case(
    utterance: str,
    job_id: str | None = None,
    started: float | None = None,
    *,
    claims: Callable[[], Awaitable[MessageCaseResult]] | None = None,
) -> dict:
    """링크 없는 문자를 분석해 R6b(또는 R6·R4) 카드와 붙일 질문을 만든다.

    analyze_message_case()는 계약상 CancelledError만 올리지만 외부 함수라 방어적으로 감싼다.
    run_analysis 는 board 에 올린 같은 분석을 `claims` 로 넘긴다.
    """

    try:
        result = await (claims() if claims else _claims(utterance, started))
    except Exception as error:  # noqa: BLE001 — 분석이 실패해도 분석 불가 카드는 나간다 (원칙 3)
        _log.warning(f"[MESSAGE CASE ERROR] {type(error).__name__}")
        return render_card("r4-unavailable")

    payload = result.model_dump(mode="json")
    _log.info(
        f"[MESSAGE CASE] conclusion={payload.get('conclusion')} "
        f"caution={payload.get('caution')} "
        f"questions={[q.get('question_id') for q in payload.get('questions') or []]}"
    )

    if job_id is not None and (job := ANALYSIS_JOBS.get(job_id)):
        job["final_payload"] = payload
        job["analysis_kind"] = "message_case"

    return render_message_case_card(payload, job_id)


# 문자 결과 질문과 선택지. 목록 밖의 답은 결과를 바꾸지 않고 안내만 한다
_MESSAGE_QUESTION_OPTIONS = {
    "already_acted": {"entered_info", "installed_app", "sent_money", "not_yet", "unknown"},
    "called_known_number": {"not_person", "is_person", "no_answer", "not_yet", "unknown"},
}


def handle_case_answer(
    case_id: str,
    question_id: str,
    option_id: str,
    user_id: str | None,
    *,
    kind: str | None = None,
) -> dict:
    """Q1·Q2 리스트 카드 항목 클릭을 분석 종류에 맞는 처리로 보낸다.

    Job 을 찾지 못하면 항목에 넣어 둔 kind 로 고른다. 문자 결과 항목에만 kind="message" 가 있고,
    없으면 지금처럼 녹음 답 처리(안내 문장)로 보낸다.
    """

    job = ANALYSIS_JOBS.get(case_id)
    is_message = (
        job.get("analysis_kind") == "message_case" if job is not None else kind == "message"
    )

    if not is_message:
        if question_id == "already_acted":
            return handle_voice_answer(case_id, option_id, user_id)
        return kakao_response(_CASE_NOT_FOUND_TEXT)

    if option_id not in _MESSAGE_QUESTION_OPTIONS.get(question_id, ()):
        _log.warning("[MESSAGE ANSWER IGNORED] reason=unsupported_option")
        return kakao_response(_CASE_NOT_FOUND_TEXT)

    return handle_message_answer(case_id, question_id, option_id, user_id)


def handle_message_answer(
    job_id: str,
    question_id: str,
    option_id: str,
    user_id: str | None,
) -> dict:
    """링크 없는 문자 결과의 Q1·Q2 답을 처리한다(v3 §8, B3).

    - Q1(already_acted): 답이 쌓이고 대응(K6)이나 A2 를 보낸다. 결론은 그대로다.
    - Q2(called_known_number): 게이트를 다시 돌려 새 결과 카드(R6b + 남은 질문)를 보낸다.
      "결과 확인"도 새 결과를 보여 주도록 Job 의 result 를 바꾼다.
    - Job 이 없거나 다른 사용자의 Job 이면 Q1 은 기본 대응(default_response_actions)으로 답한다.
    """

    job = ANALYSIS_JOBS.get(job_id)
    final_payload = job.get("final_payload") if job else None
    owner = job.get("user_id") if job else None
    same_owner = not owner or owner == user_id

    result: MessageCaseResult | None = None
    if (
        job is not None
        and job.get("analysis_kind") == "message_case"
        and final_payload
        and same_owner
    ):
        try:
            result = MessageCaseResult.model_validate(final_payload)
        except ValidationError as error:
            _log.warning(f"[MESSAGE ANSWER ERROR] {type(error).__name__}")

    if result is None:
        notice = kakao_response(_CASE_NOT_FOUND_TEXT)
        if question_id == "already_acted":
            actions = [action.value for action in default_response_actions(option_id)]
            if actions:
                return merge_kakao_responses([
                    notice, render_message_default_answer_card(actions, option_id),
                ])
        return notice

    updated = apply_message_answer(result, question_id, option_id)
    payload = updated.model_dump(mode="json")
    job["final_payload"] = payload

    _log.info(
        f"[MESSAGE ANSWER] question={question_id} option={option_id} "
        f"conclusion={payload.get('conclusion')}"
    )

    if question_id == "called_known_number":
        card = render_message_case_card(payload, job_id)
        if job.get("status") == "completed":
            # "결과 확인" 이 새 결과와 답한 시각을 보여 주게 한다
            job["result"] = card
            job["completed_at"] = now_iso()
        return card

    return render_message_answer_card(payload)


# ============================================================
# Voice Message (카카오톡 내부 음성 메시지)
#
# URL·문자 분석과 완전히 분리된 경로다. 음성은 read_message()를 거치지
# 않고(kakao_skill()에서 미리 분기), 다운로드 주소는 반드시
# media.url에서만 가져온다(utterance에 실려 오는 서명된 CDN URL은
# 출처로 쓰지 않는다).
# ============================================================

# callbackUrl 없는 동기 경로(개발용) 예산. 실제 STT(최대 30초)+
# 다운로드(최대 15초)+분석을 콜백 상한 없이 넉넉하게 담는다 — 운영 경로(콜백)는
# CALLBACK_DEADLINE_SECONDS를 따로 쓴다.
VOICE_SYNC_TIMEOUT_SECONDS = 90.0


async def handle_voice_message(
    media: dict,
    user_id: str | None,
    callback_url: str | None,
    background_tasks: BackgroundTasks,
) -> dict:
    """카카오 음성 메시지 수신을 처리한다.

    기존 URL 분석과 같은 job/콜백 뼈대(`ANALYSIS_JOBS`,
    `run_analysis_and_callback`)를 그대로 재사용한다 — "결과 확인" 폴백,
    콜백 보안 검사, 중복 요청 차단이 전부 그대로 적용된다.
    """

    media_url = media.get("url")
    if not isinstance(media_url, str) or not media_url:
        _log.info("[VOICE MESSAGE] media.url 없음")
        return render_card("r4-unavailable")

    if user_id and user_id in RUNNING_USERS:
        return render_card("k5-duplicate")

    # callbackUrl이 없는 경우 개발/테스트용 동기 처리 (문자 전용 경로와 동일한 패턴)
    if not callback_url:
        _log.info("[CALLBACK] callbackUrl 없음 - 음성 동기 처리")
        if user_id:
            RUNNING_USERS.add(user_id)
        try:
            return await asyncio.wait_for(
                run_voice_analysis(media_url),
                timeout=VOICE_SYNC_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return render_card("r4-unavailable")
        except Exception as error:  # noqa: BLE001 — Job 이 없는 경로라 여기서 안내 카드로 끝낸다
            _log.warning(f"[VOICE ANALYSIS ERROR] sync path failed: {type(error).__name__}")
            return render_card("r4-unavailable")
        finally:
            if user_id:
                RUNNING_USERS.discard(user_id)

    return _start_background_analysis(
        background_tasks, "", callback_url, user_id, voice_media_url=media_url,
    )


def handle_voice_answer(job_id: str, option_id: str, user_id: str | None) -> dict:
    """Q1 "이미 하신 게 있나요?" 리스트 카드 항목 클릭을 처리한다.

    `apply_call_answers()`는 LLM·네트워크를 쓰지 않는 순수 함수라 콜백 없이
    바로 답한다(설계 문서 D11). 답은 쌓인다 — 같은 case(job)에 여러 번
    답해도 지금까지 고른 답 전부로 다시 안내를 고른다(D16). 같은 답은 한 번만
    남긴다. 결론·멈출 행동은 `apply_call_answers()`가 바꾸지 않는다(`response_actions`만).
    다른 사용자의 Job 이면 문자 답 처리와 같이 건드리지 않고 안내만 한다.
    """

    job = ANALYSIS_JOBS.get(job_id)
    final_payload = job.get("final_payload") if job else None
    owner = job.get("user_id") if job else None

    if (
        job is None
        or job.get("analysis_kind") != "voice"
        or not final_payload
        or (owner and owner != user_id)
    ):
        return kakao_response(_CASE_NOT_FOUND_TEXT)

    try:
        result = VoiceCaseResult.model_validate(final_payload)
    except ValidationError as error:
        _log.warning(f"[VOICE ANSWER ERROR] {type(error).__name__}")
        return kakao_response(_CASE_NOT_FOUND_TEXT)

    answers: list[str] = job.setdefault("qa_answers", [])
    if option_id not in answers:
        answers.append(option_id)

    updated = apply_call_answers(result, answers)
    job["final_payload"] = updated.model_dump(mode="json")

    return render_voice_answer_card(job["final_payload"], answers)


# ============================================================
# 분석 실행과 콜백
# ============================================================

_ANALYSIS_TIMEOUT_TEXT = "분석이 예상보다 오래 걸리고 있어요. 잠시 후 다시 확인해주세요."
_ANALYSIS_ERROR_TEXT = "분석 중 문제가 발생했습니다. 잠시 후 다시 시도해주세요."


async def _analyze_for_job(utterance: str, job_id: str, voice_media_url: str | None) -> dict:
    """분석을 돌리고 끝난 상태를 Job 에 남긴 뒤 사용자에게 보낼 응답을 돌려준다.

    시간 초과와 실패도 응답이다. 콜백 전에 저장해 두므로 전송이 실패해도 "결과 확인"이 같은 응답을 준다.
    """

    started = time.monotonic()
    is_voice = voice_media_url is not None
    analysis = (
        run_voice_analysis(voice_media_url, job_id=job_id) if is_voice
        else run_analysis(utterance, job_id=job_id)
    )

    try:
        result = await asyncio.wait_for(analysis, timeout=CALLBACK_DEADLINE_SECONDS)
    except asyncio.TimeoutError:
        _log.warning(f"[ANALYSIS TIMEOUT] job_id={job_id}")
        result = render_card("r4-unavailable") if is_voice else kakao_response(_ANALYSIS_TIMEOUT_TEXT)
        if is_voice:
            log_voice_stage(job_id, "card", "completed", started, card="r4-unavailable")
        finish_job(job_id, "timeout", result, error="analysis_timeout")
        _log.warning(f"[JOB TIMEOUT] job_id={job_id}")
    except Exception as error:  # noqa: BLE001 — 백그라운드 작업의 맨 바깥이다. 실패도 사용자 응답으로 바꿔 저장한다
        _log.warning(f"[ANALYSIS ERROR] job_id={job_id} {type(error).__name__}")
        result = kakao_response(_ANALYSIS_ERROR_TEXT)
        if is_voice:
            log_voice_stage(job_id, "card", "completed", started, card="analysis-error-message")
        finish_job(job_id, "failed", result, error=type(error).__name__)
        _log.warning(f"[JOB FAILED] job_id={job_id}")
    else:
        finish_job(job_id, "completed", result)
        _log.info(f"[JOB COMPLETED] job_id={job_id}")

    return result


async def run_analysis_and_callback(
    utterance: str,
    callback_url: str,
    user_id: str | None,
    job_id: str,
    *,
    voice_media_url: str | None = None,
) -> None:
    """백그라운드에서 분석한 뒤 카카오 callbackUrl 로 결과를 보낸다.

    `voice_media_url` 이 있으면 통화 녹음 분석이고 `utterance` 는 쓰지 않는다.
    """

    try:
        result = await _analyze_for_job(utterance, job_id, voice_media_url)
        await send_callback(get_http_client, callback_url, result, job_id)
    finally:
        if user_id:
            RUNNING_USERS.discard(user_id)
            _log.info(f"[RUNNING USER REMOVED] job_id={job_id}")


# ============================================================
# FE-BE R1 Card Test
# ============================================================

@app.post("/test/kakao/r1-lookalike", dependencies=[Depends(verify_skill_secret)])
async def test_r1_lookalike():
    """FE-BE 카드 연동 확인용. 분석 없이 R1 카드 템플릿을 그대로 돌려준다."""

    return render_card("r1-lookalike")
