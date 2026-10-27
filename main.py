import asyncio
import hmac
import os
import tempfile

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, UploadFile

from backend.src.server.http_client import get_http_client
from backend.src.server.lifespan import app_lifespan
from backend.src.server.url_utils import has_malformed_url, split_message
from backend.src.server.callback_guard import callback_host, is_allowed_callback_url
from backend.src.server.templates.renderer import render_r1_lookalike
from services.official_domain_service import check_link_official
from services.link_resolution import first_real_host, is_shortener
from services.kakao_card_renderer import render_card
from services.result_card_renderer import (
    render_result_card,
    merge_kakao_responses,
    render_detail_carousel,
    render_message_only_card,
)

# AI 연결
from ai.pipeline import analyze_message_part, finalize_analysis
from ai.types import UrlAnalysis

from scanner.isolation_client import collect_url_isolated
from scanner.isolation_page import to_isolated_page
from scanner.models import CollectResult
from urllib.parse import urlparse


app = FastAPI(lifespan=app_lifespan)


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
        print("[SKILL AUTH REJECTED] reason=secret_not_configured")
        raise HTTPException(status_code=401, detail="Skill secret not configured")

    provided = request.headers.get("X-Skill-Secret", "")

    if hmac.compare_digest(provided, secret):
        return

    previous_secret = os.getenv("KAKAO_SKILL_SECRET_PREVIOUS", "")
    if previous_secret and hmac.compare_digest(provided, previous_secret):
        return

    if not provided:
        print("[SKILL AUTH REJECTED] reason=header_missing")
    else:
        print("[SKILL AUTH REJECTED] reason=secret_mismatch")

    raise HTTPException(status_code=401, detail="Invalid skill secret")


# TODO:
# 현재는 콜백 기능 테스트를 위한 임시 인메모리 저장소.
# 추후 PostgreSQL 또는 Redis 기반 작업 상태 관리로 변경.
RUNNING_USERS: set[str] = set()


# TODO:
# 프로토타입 검증용 인메모리 작업 저장소.
# 서버 재시작 시 데이터가 소실된다.
# 추후 PostgreSQL 기반 저장소로 교체한다.
ANALYSIS_JOBS: dict[str, dict] = {}


# 콜백 URL 유효 시간을 고려한 안전 마진
CALLBACK_DEADLINE_SECONDS = 45.0
MESSAGE_SYNC_TIMEOUT_SECONDS = 3.0


# ============================================================
# Store Analysis Job
# ============================================================

def create_analysis_job(
    user_id: str | None
) -> str:
    """
    새로운 분석 작업을 생성하고 job_id를 반환한다.

    현재는 인메모리 저장소를 사용하며,
    추후 PostgreSQL 기반 저장소로 교체한다.
    """

    job_id = str(uuid.uuid4())

    ANALYSIS_JOBS[job_id] = {
        "job_id": job_id,
        "user_id": user_id,
        "status": "running",
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "completed_at": None,
        "result": None,
        # 이슈 #69: "자세히 보기" 클릭 시 캐러셀(+ 공식 확인 방법 카드)을
        # 다시 만들려면 렌더링 끝난 카카오 카드(result)가 아니라 원본
        # 분석 결과가 필요하다. 링크 여러 개면 마지막 것만 남는다 —
        # r7-multiple-urls 라우팅과 마찬가지로 다중 링크는 이번 범위 밖.
        "final_payload": None,
        "error": None,
        "callback_status": "pending",
        "callback_error": None
    }

    print(
        f"[JOB CREATED] "
        f"job_id={job_id} "
        f"user={user_id}"
    )

    return job_id


# ============================================================
# Find Latest Job
# ============================================================

def find_latest_job_by_user(
    user_id: str | None
) -> dict | None:
    if not user_id:
        return None

    user_jobs = [
        job
        for job in ANALYSIS_JOBS.values()
        if job.get("user_id") == user_id
    ]

    if not user_jobs:
        return None

    return max(
        user_jobs,
        key=lambda job: job["created_at"]
    )

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

        time_message = kakao_response(
            f"{completed_time}에 확인한 결과예요."
        )

        return merge_kakao_responses([
            time_message,
            result,
        ])

    if status in ("failed", "timeout", "cancelled"):
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
        return kakao_response(
            "분석 결과를 찾을 수 없어요. 문자를 다시 보내 주세요."
        )

    final_payload = job.get("final_payload")

    if not final_payload:
        return kakao_response(
            "분석 결과를 찾을 수 없어요. 문자를 다시 보내 주세요."
        )

    return render_detail_carousel(final_payload)


# ============================================================
# Kakao Skill
# ============================================================

def _field_types(mapping: dict) -> dict[str, str]:
    """민감한 값을 남기지 않고, 필드 이름과 자료형만 돌려준다."""
    return {key: type(value).__name__ for key, value in mapping.items()}


def _log_voice_payload_diagnostics(body: dict) -> None:
    """카카오 음성 메시지가 SkillPayload에 어떤 모양으로 오는지 확인하기
    위한 임시 진단 로그다 — 실제 구현(다운로드·분석·카드 렌더링) 전,
    `userRequest.params`/`action.params`/`action.detailParams`에 음성
    미디어 관련 필드가 있는지만 먼저 확인한다.

    URL·토큰·사용자 ID·음성 내용 같은 민감한 값은 절대 남기지 않는다 —
    필드 이름·자료형·미디어 타입·URL "존재 여부"만 기록한다. 운영에서는
    끈다(APP_ENV가 local/test/staging일 때만 동작).
    """

    app_env = os.getenv("APP_ENV", "production").lower()
    if app_env not in {"local", "test", "staging"}:
        return

    user_request = body.get("userRequest") or {}
    params = user_request.get("params") or {}
    action = body.get("action") or {}
    action_params = action.get("params") or {}
    detail_params = action.get("detailParams") or {}

    print(f"[VOICE DIAG] userRequest.params field_types={_field_types(params)}")
    print(f"[VOICE DIAG] action.params field_types={_field_types(action_params)}")
    print(f"[VOICE DIAG] action.detailParams field_types={_field_types(detail_params)}")

    media = params.get("media")
    if isinstance(media, dict):
        print(
            f"[VOICE DIAG] userRequest.params.media "
            f"type={media.get('type')!r} "
            f"has_url={'url' in media} "
            f"field_types={_field_types(media)}"
        )


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

    print(
        f"[KAKAO] "
        f"user={user_id} "
        f"utterance={utterance}"
    )

    print(
        f"[CALLBACK EXISTS] "
        f"{bool(callback_url)}"
    )

    # 임시 진단 로그 (음성 메시지 Payload 조사용) — 기능 결정 전 조사
    _log_voice_payload_diagnostics(body)
    # 경로에는 1회용 콜백 토큰이 있어 호스트만 남긴다. 허용 목록을 좁힐 때 쓴다.
    print(f"[CALLBACK HOST] {callback_host(callback_url)}")

    # 내부 주소로 결과를 POST 하게 만드는 요청을 막는다 (멘토 리뷰, isolation-security §5.5).
    if callback_url is not None and not is_allowed_callback_url(callback_url):
        print("[CALLBACK REJECTED] 허용되지 않은 callbackUrl")
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

    # 사용자가 결과 확인 요청하면 최근 분석 결과를 리턴
    if intent_name == "결과 확인":
        return handle_check_result(user_id)

    # 문자 내용에서 URL과 일반 메시지 분리
    links, message = split_message(utterance)

    print(f"[LINK COUNT] {len(links)}")
    print(f"[MESSAGE] {message!r}")

    # 잘못된 주소와 빈 입력은 분석 Job을 만들지 않는다.
    if not links:
        if has_malformed_url(utterance):
            return kakao_response("주소 형식을 확인하지 못했어요. http:// 또는 https://로 시작하는 전체 주소를 다시 보내 주세요.")
        if not message.strip():
            return render_card("r6-input-required")

    # 같은 사용자의 이전 분석이 아직 진행 중인 경우
    if user_id and user_id in RUNNING_USERS:
        return render_card("k5-duplicate")

    # callbackUrl이 없는 경우 개발/테스트용 동기 처리
    if not callback_url:
        print("[CALLBACK] callbackUrl 없음 - 동기 처리")
        if not links:
            if user_id:
                RUNNING_USERS.add(user_id)
            try:
                return await asyncio.wait_for(
                    run_message_only_analysis(message),
                    timeout=MESSAGE_SYNC_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                return kakao_response("문자 분석 시간이 초과되었어요. 잠시 후 다시 보내 주세요.")
            finally:
                if user_id:
                    RUNNING_USERS.discard(user_id)

        return await run_analysis(
            links,
            message
        )

    # 사용자 분석 시작 상태 저장
    if user_id:
        RUNNING_USERS.add(user_id)

    # 분석 작업 생성
    job_id = create_analysis_job(
        user_id
    )

    # 오래 걸리는 분석은 background task에서 수행
    callback_options = {}
    if not links:
        ANALYSIS_JOBS[job_id]["analysis_kind"] = "message_only"
        callback_options["message_only"] = True
    background_tasks.add_task(
        run_analysis_and_callback,
        links,
        message,
        callback_url,
        user_id,
        job_id,
        **callback_options,
    )

    # 카카오에는 즉시 callback 사용 응답
    return render_card("r5-analyzing")


# ============================================================
# Main Analysis
# ============================================================

async def run_analysis(
    links: list[str],
    message: str,
    job_id: str | None = None,
) -> dict:
    """Collect each link once while message analysis runs, then save the final evidence."""
    cards: list[dict] = []
    message_task = asyncio.create_task(analyze_message_part(message))
    try:
        for link in links:
            try:
                try:
                    collected = await collect_url_isolated(link)
                except Exception as error:
                    # Keep message evidence even when the collection API is unavailable.
                    print(f"[COLLECTION FAILED] {type(error).__name__}")
                    collected = CollectResult(
                        link, None, (), None, None, "", None, 0, ("collection_failed",),
                    )

                try:
                    message_result = await message_task
                except Exception as error:
                    print(f"[AI MESSAGE ERROR] {type(error).__name__}")
                    message_result = None

                brand = message_result.brand if message_result is not None else None
                url_analysis = build_ai_url_analysis(collected, brand, link)
                page, failure = to_isolated_page(collected)
                final_result = await finalize_analysis(
                    url=url_analysis, message=message_result, page=page, failure=failure,
                )
                final_payload = final_result.model_dump(mode="json")
                cards.append(render_result_card(final_payload, job_id=job_id))
                if job_id is not None and (job := ANALYSIS_JOBS.get(job_id)):
                    job["final_payload"] = final_payload
            except Exception as error:
                print(f"[ANALYSIS ERROR] {type(error).__name__}")
                cards.append(render_card("r4-unavailable"))
    finally:
        if not message_task.done() and not message_task.cancelling():
            message_task.cancel()
        await asyncio.gather(message_task, return_exceptions=True)
    return merge_kakao_responses(cards)


async def run_message_only_analysis(message: str, job_id: str | None = None) -> dict:
    """문자 전용 카드 생성. 실행 예산과 콜백은 호출부가 소유한다."""

    try:
        message_result = await analyze_message_part(message)
    except Exception as error:
        print(f"[AI MESSAGE ERROR] {type(error).__name__}")
        if job_id is not None:
            raise
        return render_card("r4-unavailable")

    payload = message_result.model_dump(mode="json")
    card = render_message_only_card(payload)
    if job_id is not None and (job := ANALYSIS_JOBS.get(job_id)):
        # URL/페이지 결과용 상세 캐러셀에는 문자 단독 payload를 넘기지 않는다.
        job["message_payload"] = payload
    return card


def _http_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        return parsed.hostname if parsed.scheme in {"http", "https"} else None
    except ValueError:
        return None


def build_ai_url_analysis(
    collected: CollectResult,
    brand: str | None,
    input_url: str,
) -> UrlAnalysis:
    """Compare the received/first real host; incomplete or later movement only downgrades."""
    browser = collected.browser
    judged_domain = first_real_host(input_url, collected.redirect_chain)

    # fetch.final_url may name an unvisited blocked/timeout destination.
    final_url = (collected.final_url
                 if collected.status_code is not None and _http_host(collected.final_url)
                 else None)
    if (browser and _http_host(browser.final_url)
            and browser.final_url in browser.navigation_chain):
        final_url = browser.final_url

    official = check_link_official(brand, judged_domain, _http_host(final_url))
    if official == "official":
        # shortcut: browser commits omit HTTP redirect hops; require full request history before confirming.
        incomplete = (browser is not None or not final_url or not collected.html
                      or bool(collected.failures))
        if incomplete:
            official = "unresolved"
        reached_real_host = False
        for url in (input_url, *collected.redirect_chain):
            host = _http_host(url)
            if not reached_real_host and is_shortener(host):
                continue
            reached_real_host = True
            if not host or check_link_official(brand, judged_domain, host) != "official":
                official = "unresolved"
                break

    return UrlAnalysis(
        final_url=final_url,
        domain=judged_domain or _http_host(input_url),
        official=official,
    )


# ============================================================
# Kakao callback
# ============================================================

def _safe_callback_error(error: Exception) -> str:
    """콜백 전송 실패를 안전하게 요약한다.

    httpx.HTTPStatusError·ConnectError 등은 str()에 요청 URL 전체(쿼리
    스트링의 1회성 토큰 포함)를 담는다 — 그대로 로그나 Job에 남기면
    간접적으로 토큰이 샌다. 여기서는 예외 유형과(있으면) HTTP 상태
    코드만 남긴다.
    """

    status_code = getattr(getattr(error, "response", None), "status_code", None)
    if status_code is not None:
        return f"{type(error).__name__} (status={status_code})"
    return type(error).__name__


async def run_analysis_and_callback(
    links: list[str],
    message: str,
    callback_url: str,
    user_id: str | None,
    job_id: str,
    *,
    message_only: bool = False,
):
    """
    백그라운드에서 분석을 수행한 뒤
    카카오 callbackUrl로 최종 결과를 전송한다.
    """

    try:
        # ----------------------------------------------------
        # 1. 분석 수행
        # ----------------------------------------------------

        try:
            result = await asyncio.wait_for(
                run_message_only_analysis(message, job_id=job_id) if message_only else run_analysis(
                    links,
                    message,
                    job_id=job_id
                ),
                timeout=CALLBACK_DEADLINE_SECONDS
            )

            # callback 전송 전에 분석 결과 저장
            job = ANALYSIS_JOBS.get(job_id)
        
            if job:
                job["status"] = "completed"
                job["result"] = result
                job["completed_at"] = datetime.now(
                    timezone.utc
                ).isoformat()
        
                print(
                    f"[JOB COMPLETED] "
                    f"job_id={job_id}"
                )

        except asyncio.TimeoutError:
            print(
                f"[ANALYSIS TIMEOUT] "
                f"user={user_id} "
                f"links={links}"
            )

            result = kakao_response(
                "분석이 예상보다 오래 걸리고 있어요. "
                "잠시 후 다시 확인해주세요."
            )

            job = ANALYSIS_JOBS.get(job_id)

            if job:
                job["status"] = "timeout"
                job["result"] = result
                job["error"] = "analysis_timeout"
                job["completed_at"] = datetime.now(
                    timezone.utc
                ).isoformat()

            print(
                f"[JOB TIMEOUT] "
                f"job_id={job_id}"
            )

        except Exception as e:
            print(
                f"[ANALYSIS ERROR] "
                f"{type(e).__name__}: {e}"
            )

            result = kakao_response(
                "분석 중 문제가 발생했습니다. "
                "잠시 후 다시 시도해주세요."
            )

            job = ANALYSIS_JOBS.get(job_id)

            if job:
                job["status"] = "failed"
                job["result"] = result
                job["error"] = (
                    f"{type(e).__name__}: {e}"
                )
                job["completed_at"] = datetime.now(
                    timezone.utc
                ).isoformat()
        
                print(
                    f"[JOB FAILED] "
                    f"job_id={job_id}"
                )

        # ----------------------------------------------------
        # 2. 카카오 callback 전송
        # ----------------------------------------------------

        print(
            "[CALLBACK] 결과 전송 시작"
        )

        # 입구에서 이미 걸렀지만, 다른 경로로 들어와도 내부 주소로 보내지 않게 한 번 더 본다.
        if not is_allowed_callback_url(callback_url):
            raise ValueError("허용되지 않은 callbackUrl")

        response = await get_http_client().post(
            callback_url,
            json=result,
            timeout=10.0,
            follow_redirects=False,
        )

        print(
            f"[CALLBACK STATUS] "
            f"{response.status_code}"
        )

        # 응답 본문은 로그에 그대로 남기지 않는다 — 콜백 호스트가 응답에
        # 요청 URL을 그대로 반사하면(예: 디버그 echo) 쿼리스트링의 토큰이
        # 로그로 샐 수 있다. 실제로 필요한 결과 상태는 JSON 파싱 후
        # [CALLBACK RESULT STATUS]로 따로 남긴다.

        response.raise_for_status()

        # ----------------------------------------------------
        # 3. Kakao callback 결과 확인
        # ----------------------------------------------------

        try:
            callback_result = response.json()
        
            callback_result_status = callback_result.get(
                "status"
            )
        
            print(
                f"[CALLBACK RESULT STATUS] "
                f"{callback_result_status}"
            )
        
            job = ANALYSIS_JOBS.get(job_id)
        
            if callback_result_status == "SUCCESS":
                if job:
                    job["callback_status"] = "success"
        
                print(
                    f"[JOB CALLBACK SUCCESS] "
                    f"job_id={job_id}"
                )
        
            else:
                if job:
                    job["callback_status"] = "failed"
                    job["callback_error"] = (
                        f"kakao_callback_status={callback_result_status}"
                    )
        
                print(
                    f"[JOB CALLBACK FAILED] "
                    f"job_id={job_id} "
                    f"status={callback_result_status}"
                )
        
        except ValueError:
            job = ANALYSIS_JOBS.get(job_id)
        
            if job:
                job["callback_status"] = "failed"
                job["callback_error"] = "invalid_callback_response"
        
            print(
                "[CALLBACK WARNING] "
                "응답을 JSON으로 파싱할 수 없습니다."
            )
        
            print(
                f"[JOB CALLBACK FAILED] "
                f"job_id={job_id}"
            )

    except asyncio.CancelledError:
        job = ANALYSIS_JOBS.get(job_id)
        if job:
            if job["status"] == "running":
                job["status"] = "cancelled"
                job["error"] = "analysis_cancelled"
                job["completed_at"] = datetime.now(timezone.utc).isoformat()
            if job["callback_status"] == "pending":
                job["callback_status"] = "cancelled"
                job["callback_error"] = "task_cancelled"
        raise
    except Exception as e:
        job = ANALYSIS_JOBS.get(job_id)
        safe_error = _safe_callback_error(e)

        if job:
            job["callback_status"] = "failed"
            job["callback_error"] = safe_error

        print(
            f"[CALLBACK SEND FAILED] "
            f"{safe_error}"
        )
    
        print(
            f"[JOB CALLBACK FAILED] "
            f"job_id={job_id}"
        )

    finally:
        if user_id:
            RUNNING_USERS.discard(
                user_id
            )

            print(
                f"[RUNNING USER REMOVED] "
                f"user={user_id}"
            )


# ============================================================
# Kakao Response
# ============================================================

def kakao_response(
    text: str
) -> dict:
    """
    카카오 SkillResponse simpleText 생성.
    """

    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": text
                    }
                }
            ]
        }
    }


# ============================================================
# FE-BE R1 Card Test
# ============================================================

@app.post("/test/kakao/r1-lookalike", dependencies=[Depends(verify_skill_secret)])
async def test_r1_lookalike():
    """
    FE-BE Kakao 카드 연동 테스트용.

    실제 분석 로직을 거치지 않고
    R1 lookalike 카드를 반환한다.
    """

    return render_r1_lookalike()


# ============================================================
# Voice Case Upload (신규 시나리오 — 보이스피싱 녹음 업로드)
#
# docs/designs/agent-loop-plan-v2.md §9 B4 설계를 참고하되, 이번 주
# 범위는 "업로드 → 목업 처리 → 조회"가 끝까지 도는 수준까지만이다.
# 실제 STT·AI 분석은 다음 주 AI 팀 연동 때 mock_analyze_voice_case()만
# 교체하면 되도록 인터페이스를 분리해 둔다. 카카오와는 무관한 일반
# 웹 업로드라 verify_skill_secret()을 쓰지 않는다 — 사용자 식별용
# 서명 토큰은 설계 문서에 남아 있는 다음 작업이다(아직 없음, 알려진 공백).
# ============================================================

ALLOWED_VOICE_EXTENSIONS = (".m4a", ".mp3", ".wav", ".webm")
MAX_VOICE_FILE_BYTES = 25 * 1024 * 1024  # 25MB (설계 문서 §9 B4)
_VOICE_UPLOAD_CHUNK_BYTES = 1024 * 1024  # 1MB씩 읽어 상한 넘으면 바로 중단

# TODO: 인메모리 저장소. 서버 재시작 시 소실된다 (ANALYSIS_JOBS와 같은 한계).
VOICE_CASES: dict[str, dict] = {}


def _has_allowed_voice_extension(filename: str | None) -> bool:
    if not filename:
        return False
    return filename.lower().endswith(ALLOWED_VOICE_EXTENSIONS)


async def _save_voice_upload(file: UploadFile) -> tuple[str, int]:
    """업로드를 1MB씩 읽어 임시 파일에 쓰면서 누적 크기를 그 자리에서 검증한다.

    `await file.read()`로 전체를 한 번에 메모리에 올리면, 25MB 업로드가
    동시에 여러 건 들어올 때 메모리 사용량이 그만큼 배로 늘어난다
    (윤여경 리뷰). 청크 단위로 읽어 디스크에 바로 흘려보내고, 상한을
    넘는 순간 남은 바이트를 더 읽지 않고 바로 끊는다.
    """

    fd, path = tempfile.mkstemp(suffix="_voice_upload")
    total = 0
    try:
        with os.fdopen(fd, "wb") as destination:
            while True:
                chunk = await file.read(_VOICE_UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_VOICE_FILE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="파일이 너무 커요. 25MB 이하 파일만 업로드할 수 있어요.",
                    )
                destination.write(chunk)
    except Exception:
        os.remove(path)
        raise
    return path, total


async def mock_analyze_voice_case(audio_path: str, filename: str) -> dict:
    """실제 STT·AI 분석을 흉내 내는 자리 표시자.

    다음 주 AI 연동 전까지, BE가 업로드→처리→결과 조회 흐름 전체를
    끝까지 테스트할 수 있도록 고정된 결과를 돌려준다. 진짜 분석이
    들어오면 이 함수만 교체하면 된다 — 호출부(process_voice_case)는
    바뀌지 않는다. 입력을 메모리의 bytes가 아니라 파일 경로로 받는다 —
    실제 STT 연동 때도 25MB 파일을 통째로 들고 있을 필요가 없도록
    미리 맞춰 둔 경계다. 정확한 입출력 형식은 다음 주 AI 팀과 협의해
    확정한다.
    """

    await asyncio.sleep(0)  # 실제 작업이면 여기서 await 체인이 이어진다

    return {
        "scenario": "voice_phishing",
        "conclusion": "주의 필요",
        "evidence": [
            {"text": "(목업) 실제 STT·분석 연동 전 고정 결과입니다.", "source": "mock"},
        ],
        "guidance": (
            "실제 기관은 전화로 송금이나 계좌 정보를 요구하지 않아요. "
            "의심되면 끊고 공식 대표번호로 직접 확인하세요."
        ),
        "mock": True,
    }


def create_voice_case() -> str:
    case_id = str(uuid.uuid4())

    VOICE_CASES[case_id] = {
        "case_id": case_id,
        "status": "processing",
        "result": None,
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    print(f"[VOICE CASE CREATED] case_id={case_id}")

    return case_id


async def process_voice_case(case_id: str, audio_path: str, filename: str) -> None:
    """분석을 실행하고, 성공·실패와 무관하게 업로드 파일을 반드시 지운다."""

    case = VOICE_CASES.get(case_id)

    try:
        if case is None:
            return

        try:
            result = await mock_analyze_voice_case(audio_path, filename)
            case["status"] = "completed"
            case["result"] = result
            print(f"[VOICE CASE COMPLETED] case_id={case_id}")
        except Exception as error:
            print(f"[VOICE CASE ERROR] case_id={case_id} {type(error).__name__}")
            case["status"] = "failed"
            case["error"] = type(error).__name__
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass


@app.post("/api/voice-cases", status_code=202)
async def upload_voice_case(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """보이스피싱 녹음 파일을 업로드받아 분석을 백그라운드로 시작한다.

    이 함수는 업로드를 받아 즉시 `case_id`를 반환하고(202), 실제 분석은
    `background_tasks`로 넘겨 응답 이후에 처리한다 — 다음 주 실제
    STT·AI 분석이 몇 초 이상 걸려도 요청을 붙들고 있지 않는다.
    """

    if not _has_allowed_voice_extension(file.filename):
        raise HTTPException(
            status_code=415,
            detail="지원하지 않는 파일 형식입니다. m4a/mp3/wav/webm만 업로드할 수 있어요.",
        )

    path, size = await _save_voice_upload(file)

    if size == 0:
        os.remove(path)
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    case_id = create_voice_case()

    background_tasks.add_task(
        process_voice_case, case_id, path, file.filename
    )

    return {"case_id": case_id, "status": "processing"}


@app.get("/api/voice-cases/{case_id}")
async def get_voice_case(case_id: str):
    case = VOICE_CASES.get(case_id)

    if case is None:
        raise HTTPException(status_code=404, detail="Voice case not found")

    return case
