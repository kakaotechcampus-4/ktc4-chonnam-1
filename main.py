import asyncio
import hmac
import os
import time

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request

from backend.src.server.shadow_tasks import track_shadow_task
from backend.src.server.urlscan_service import (
    get_http_client,
    submit_url_scan,
    wait_for_url_scan_result,
)
from backend.src.server.lifespan import app_lifespan
from backend.src.server.url_utils import split_message
from backend.src.server.callback_guard import callback_host, is_allowed_callback_url
from backend.src.server.templates.renderer import render_r1_lookalike
from services.official_domain_service import check_link_official
from services.link_resolution import is_shortener, resolve_real_host
from services.kakao_card_renderer import render_card
from services.result_card_renderer import (
    render_result_card,
    merge_kakao_responses,
    render_detail_carousel,
)

# AI 연결
from ai.pipeline import analyze_message_part, finalize_analysis
from ai.types import UrlAnalysis

from scanner.isolation_client import collect_url_isolated
from scanner.static_checks import check_static
from scanner.models import UrlEvidence
from urllib.parse import urlparse


app = FastAPI(lifespan=app_lifespan)


# ============================================================
# Skill Request Auth (이슈 #66 BE 할 일 2)
# ============================================================

def verify_skill_secret(request: Request) -> None:
    """카카오 스킬 서버로 위장한 요청을 막는다.

    local/test 환경에서는 매번 비밀값을 셋업하기 번거로우므로 검사를
    생략한다 — staging/production은 카카오 관리자센터가 실제로 호출하는
    환경이라 생략하지 않는다 (ENABLE_URL_COLLECTOR_SHADOW의 local/test/
    staging 구분과는 다른 기준).

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


# Collector Shadow Mode는 기본적으로 비활성화한다.
# 테스트 환경에서만 명시적으로 활성화한다.
ENABLE_URL_COLLECTOR_SHADOW = (
    os.getenv("ENABLE_URL_COLLECTOR_SHADOW", "false").lower() == "true"
    and os.getenv("APP_ENV", "production").lower() in {"local", "test", "staging"}
)

async def log_collector_shadow(
    collector_task: asyncio.Task,
    link: str,
    parsed_result: dict,
    urlscan_elapsed: float,
):
    try:
        collector_result = await collector_task

        collector_domain = None

        if collector_result.final_url:
            collector_domain = urlparse(
                collector_result.final_url
            ).hostname

        print("========== URL COLLECTOR SHADOW ==========")
        print(f"[INPUT URL]             {collector_result.input_url}")
        print(f"[URLSCAN FINAL URL]     {parsed_result.get('final_url')}")
        print(f"[COLLECTOR FINAL URL]   {collector_result.final_url}")
        print(
            f"[FINAL URL MATCH]       "
            f"{parsed_result.get('final_url') == collector_result.final_url}"
        )
        print(f"[URLSCAN DOMAIN]        {parsed_result.get('domain')}")
        print(f"[COLLECTOR DOMAIN]      {collector_domain}")
        print(
            f"[DOMAIN MATCH]          "
            f"{parsed_result.get('domain') == collector_domain}"
        )
        print(f"[URLSCAN ELAPSED]       {urlscan_elapsed:.2f}s")
        print(f"[COLLECTOR ELAPSED]     {collector_result.elapsed_ms}ms")
        print(f"[COLLECTOR STATUS]      {collector_result.status_code}")
        print(f"[COLLECTOR TITLE]       {collector_result.title}")
        print(f"[COLLECTOR REDIRECTS]   {collector_result.redirect_chain}")
        print(f"[COLLECTOR FAILURES]    {collector_result.failures}")
        print("==========================================")

    except asyncio.CancelledError:
        print(f"[COLLECTOR SHADOW CANCELLED] url={link}")
        raise

    except Exception as e:
        print(
            f"[COLLECTOR SHADOW ERROR] "
            f"url={link} "
            f"{type(e).__name__}: {e}"
        )


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

    # URL이 없는 경우 즉시 응답
    if not links:
        return render_card("r6-input-required")

    # 같은 사용자의 이전 분석이 아직 진행 중인 경우
    if user_id and user_id in RUNNING_USERS:
        return render_card("k5-duplicate")

    # callbackUrl이 없는 경우 개발/테스트용 동기 처리
    if not callback_url:
        print("[CALLBACK] callbackUrl 없음 - 동기 처리")

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
    background_tasks.add_task(
        run_analysis_and_callback,
        links,
        message,
        callback_url,
        user_id,
        job_id
    )

    # 카카오에는 즉시 callback 사용 응답
    return render_card("r5-analyzing")


# ============================================================
# Main Analysis
# ============================================================

async def run_analysis(
    links: list[str],
    message: str,
    job_id: str | None = None
) -> dict:
    """
    현재 AI-BE 연결 테스트 흐름

    message
      → AI 문자 분석

    URL
      → urlscan
      → score 추출
      → 문자 brand + 최종 domain 기반 official 계산
      → UrlAnalysis 생성

    최종 분석
      → message 결과 전달
      → 격리 페이지 수집기는 아직 미연결
      → page=None으로 전달하여 not_run 처리
      → finalize_analysis()

    문자 AI 분석과 urlscan은 독립적인 작업이므로
    asyncio.create_task를 이용해 병렬로 실행한다.

    job_id는 결과 카드의 "자세히 보기" 버튼에 꽂혀서,
    해당 버튼 클릭 시 어떤 분석 결과를 보여줄지 찾는 데 쓰인다
    (Job 조회 자체는 이 함수의 책임이 아니다).
    """

    card_responses: list[dict] = []

    total_start = time.monotonic()

    # ========================================================
    # 1. 문자 AI 분석 — urlscan과 독립적이므로 "시작만" 해두고
    #    결과가 실제로 필요한 시점(2-4 이후)에 가서 기다린다.
    # ========================================================

    print("========== AI MESSAGE ANALYSIS (병렬 시작) ==========")
    print(f"[AI MESSAGE INPUT] {message!r}")

    message_start = time.monotonic()
    message_task = asyncio.create_task(
        analyze_message_part(message)
    )
    message_logged = False

    # ========================================================
    # 2. URL별 분석 (문자 분석과 동시에 진행)
    # ========================================================

    try:
        for link in links:
            collector_task = None
            collector_handed_off = False
            collector_result = None
            shortener_task = None
            try:
                input_domain = urlparse(link).hostname

                # 단축 주소면 격리 서버로 첫 실제 주소를 찾는다 (urlscan 과 병렬).
                # 메인 서버는 사용자 링크에 직접 접속하지 않는다.
                if is_shortener(input_domain):
                    shortener_task = asyncio.create_task(
                        resolve_real_host(link)
                    )

                input_static = check_static(
                    domain=input_domain or "",
                    brand=None,
                )

                print(
                    "[STATIC INPUT]",
                    {
                        "domain": input_static.domain,
                        "official_match": input_static.official_match,
                        "is_punycode": input_static.is_punycode,
                        "decoded_domain": input_static.decoded_domain,
                        "kisa_listed": input_static.kisa_listed,
                        "lookalike_of": input_static.lookalike_of,
                        "failures": input_static.failures,
                    }
                )

                # 자체 URL Collector를 urlscan과 병렬 실행한다.
                # 현재는 Shadow Mode이므로 실제 분석 결과에는 사용하지 않는다.

                if ENABLE_URL_COLLECTOR_SHADOW:
                    collector_task = asyncio.create_task(
                        collect_url_isolated(link)
                    )
                    track_shadow_task(collector_task)
                urlscan_start = time.monotonic()

                # ------------------------------------------------
                # 2-1. urlscan 요청
                # ------------------------------------------------

                submit_result = await submit_url_scan(
                    link
                )

                scan_id = submit_result.get("uuid")

                if not scan_id:
                    print(
                        f"[URLSCAN ERROR] "
                        f"url={link} uuid 없음"
                    )

                    card_responses.append(
                        render_card("r4-unavailable")
                    )

                    continue

                print(
                    f"[URLSCAN] "
                    f"url={link} "
                    f"scan_id={scan_id}"
                )

                # ------------------------------------------------
                # 2-2. urlscan 결과 대기
                # ------------------------------------------------

                scan_result = (
                    await wait_for_url_scan_result(
                        scan_id
                    )
                )

                if scan_result is None:
                    print(
                        f"[URLSCAN TIMEOUT] "
                        f"url={link}"
                    )

                    card_responses.append(
                        render_card("r4-unavailable")
                    )

                    continue

                urlscan_elapsed = (
                    time.monotonic() - urlscan_start
                )

                print(
                    f"[TIMING] urlscan 소요: "
                    f"{urlscan_elapsed:.2f}s "
                    f"({link})"
                )

                # ------------------------------------------------
                # 2-3. 결과 파싱
                # ------------------------------------------------

                parsed_result = parse_urlscan_result(
                    scan_result
                )

                print(
                    f"[PARSED RESULT] "
                    f"{parsed_result}"
                )
                # ------------------------------------------------
                # 2-3-1. 자체 Collector Shadow 비교
                # ------------------------------------------------

                if collector_task is not None:
                    shadow_log_task = asyncio.create_task(
                        log_collector_shadow(
                            collector_task,
                            link,
                            parsed_result,
                            urlscan_elapsed,
                        )
                    )
                    # Shadow 로그 작업을 백그라운드 작업 목록에 등록한다.
                    track_shadow_task(shadow_log_task)
                    # Collector의 소유권을 Shadow 로그 작업으로 넘긴다.
                    # 이후 AI 분석에서 예외가 발생해도 Collector를 취소하지 않는다.
                    collector_handed_off = True

                # ------------------------------------------------
                # 2-4. print urlscan result
                # ------------------------------------------------

                print(
                    "========== URLSCAN RESULT =========="
                )
                print(
                    f"[INPUT URL]   "
                    f"{parsed_result.get('url')}"
                )
                print(
                    f"[FINAL URL]   "
                    f"{parsed_result.get('final_url')}"
                )
                print(
                    f"[DOMAIN]      "
                    f"{parsed_result.get('domain')}"
                )
                print(
                    f"[SCORE]       "
                    f"{parsed_result.get('score')}"
                )
                print(
                    f"[MALICIOUS]   "
                    f"{parsed_result.get('malicious')}"
                )
                print(
                    f"[CATEGORIES]  "
                    f"{parsed_result.get('categories')}"
                )
                print(
                    f"[BRANDS]      "
                    f"{parsed_result.get('brands')}"
                )
                print(
                    "===================================="
                )

                # ------------------------------------------------
                # 2-5. 병렬로 시작해둔 문자 분석 결과 대기
                #
                # urlscan이 문자 분석보다 훨씬 오래 걸리므로, 여기 도착할
                # 때는 이미 message_task가 끝나 있을 가능성이 높다 —
                # 이 await는 대부분 즉시 반환된다 (한 번 끝난 task는
                # 몇 번을 다시 await해도 캐시된 결과를 즉시 돌려준다).
                # ------------------------------------------------

                message_wait_start = time.monotonic()

                try:
                    message_result = await message_task

                    if not message_logged:
                        message_elapsed = (
                            time.monotonic() - message_start
                        )

                        print(
                            f"[TIMING] 문자 분석 총 소요: "
                            f"{message_elapsed:.2f}s "
                            f"(urlscan과 겹친 시간 포함, "
                            f"이 지점에서 실제로 기다린 시간: "
                            f"{time.monotonic() - message_wait_start:.2f}s)"
                        )

                        print(
                            "[AI MESSAGE RESULT]",
                            message_result.model_dump(
                                mode="json"
                            )
                        )

                        message_logged = True

                except Exception as e:
                    if not message_logged:
                        print(
                            f"[AI MESSAGE ERROR] "
                            f"{type(e).__name__}: {e}"
                        )

                        message_logged = True

                    # finalize_analysis는 message=None도 처리 가능
                    message_result = None

                # ============================================================
                # BE 공식 도메인 대조
                # ============================================================

                brand = (
                    message_result.brand
                    if message_result is not None
                    else None
                )
                # ============================================================
                # BE URL scanner evidence 조합 (Shadow Mode)
                # ============================================================

                # 문자 분석이 끝났으므로 brand를 반영해 입력 도메인을 다시 검사한다.
                input_static = check_static(
                    domain=input_domain or "",
                    brand=brand,
                )

                final_static = None

                # Collector가 확인한 최종 URL이 있으면 redirect 이후 도메인도 검사한다.
                if collector_result is not None:
                    final_domain = None

                    if collector_result.final_url:
                        final_domain = urlparse(
                            collector_result.final_url
                        ).hostname

                    if final_domain:
                        final_static = check_static(
                            domain=final_domain,
                            brand=brand,
                        )

                    url_evidence = UrlEvidence(
                        input_static=input_static,
                        final_static=final_static,
                        collector=collector_result,
                        urlscan=parsed_result,
                    )

                    print(
                        "[URL EVIDENCE]",
                        {
                            "input_domain": url_evidence.input_static.domain,
                            "input_official": url_evidence.input_static.official_match,
                            "input_lookalike": url_evidence.input_static.lookalike_of,
                            "final_domain": (
                                url_evidence.final_static.domain
                                if url_evidence.final_static
                                else None
                            ),
                            "final_official": (
                                url_evidence.final_static.official_match
                                if url_evidence.final_static
                                else None
                            ),
                            "final_lookalike": (
                                url_evidence.final_static.lookalike_of
                                if url_evidence.final_static
                                else None
                            ),
                            "collector_failures": url_evidence.collector.failures,
                            "urlscan_score": url_evidence.urlscan.get("score"),
                            "urlscan_malicious": url_evidence.urlscan.get("malicious"),
                        }
                    )

                judged_domain = input_domain
                if shortener_task is not None:
                    judged_domain = await shortener_task
                    print(
                        "[SHORTENER]",
                        {"input": input_domain, "real_host": judged_domain},
                    )

                url_analysis = build_ai_url_analysis(
                    parsed_result,
                    brand,
                    link,
                    judged_domain=judged_domain,
                )

                print(
                    "[DOMAIN MATCH]",
                    {
                        "brand": brand,
                        "domain": url_analysis.domain,
                        "official": url_analysis.official
                    }
                )

                # =================================================
                # 3. AI 최종 분석
                # =================================================

                print(
                    "========== AI FINAL ANALYSIS =========="
                )

                print(
                    "[AI FINAL] "
                    f"domain_match={url_analysis.official} → "
                    "message + environment not_run"
                )

                final_result = await finalize_analysis(
                    url=url_analysis,
                    message=message_result,
                    page=None
                )

                final_payload = final_result.model_dump(
                    mode="json"
                )

                print(
                    "[AI FINAL RESULT]",
                    final_payload
                )

                print(
                    "======================================="
                )

                # ------------------------------------------------
                # 4. Kakao 결과 카드
                # ------------------------------------------------

                card_responses.append(
                    render_result_card(
                        final_payload,
                        job_id=job_id
                    )
                )

                # 이슈 #69: "자세히 보기" 클릭 시 render_detail_carousel()에
                # 다시 넘길 수 있도록 원본 결과를 Job에 남겨 둔다.
                if job_id is not None:
                    job = ANALYSIS_JOBS.get(job_id)
                    if job:
                        job["final_payload"] = final_payload

            except Exception as e:
                print(
                    f"[ANALYSIS ERROR] "
                    f"url={link} "
                    f"{type(e).__name__}: {e}"
                )

                card_responses.append(
                    render_card("r4-unavailable")
                )
            finally:
                # 인계 전 Collector는 이 URL 분석이 소유한다. 부모 취소나
                # continue 경로에서도 완료까지 회수하고, 인계된 Shadow는 유지한다.
                if collector_task is not None and not collector_handed_off:
                    if not collector_task.done() and not collector_task.cancelling():
                        collector_task.cancel()
                    await asyncio.gather(collector_task, return_exceptions=True)
                # shortener_task는 인계하지 않는다 — try 안에서 끝까지 await하거나,
                # 그 전에 끝나면(continue·예외) 여기서 회수한다.
                if shortener_task is not None and not shortener_task.done():
                    if not shortener_task.cancelling():
                        shortener_task.cancel()
                    await asyncio.gather(shortener_task, return_exceptions=True)

    finally:
        # 문자 작업은 인계하지 않는다. 취소를 삼키지 않으면서 모든 종료
        # 경로에서 자식을 회수한다. 이미 취소 중인 자식은 재취소하지 않는다.
        if not message_task.done() and not message_task.cancelling():
            message_task.cancel()
        message_outcome, = await asyncio.gather(message_task, return_exceptions=True)
        if isinstance(message_outcome, Exception) and not message_logged:
            print(
                f"[AI MESSAGE ERROR] "
                f"{type(message_outcome).__name__}: {message_outcome}"
            )

    # ========================================================
    # 5. Kakao 응답
    # ========================================================

    total_elapsed = time.monotonic() - total_start

    print(
        f"[TIMING] run_analysis 전체 소요: "
        f"{total_elapsed:.2f}s"
    )

    return merge_kakao_responses(card_responses)


# ============================================================
# URLSCAN → AI Adapter
# ============================================================

_FROM_INPUT_URL = object()


def build_ai_url_analysis(
    parsed_result: dict,
    brand: str | None,
    input_url: str,
    *,
    judged_domain=_FROM_INPUT_URL,
) -> UrlAnalysis:
    """
    URL 분석 결과를 AI UrlAnalysis 계약으로 변환한다.

    official은 urlscan score로 추론하지 않고,
    문자 분석에서 확인한 brand와 **문자 속 링크(input_url)의 도메인**을
    BE 화이트리스트로 대조하여 결정한다. urlscan 이 따라간 최종 도메인은
    공식 판정을 깎는 데만 쓴다 (check_link_official 참고).
    리다이렉트로 최종 페이지만 공식 사이트인 링크를 공식으로 안내하지 않기 위해서다.

    judged_domain: 문자 속 링크가 단축 주소일 때 격리 서버로 찾은 첫 실제 호스트.
    찾지 못했으면 None을 넘기고, 그러면 판단 보류(unresolved)가 된다.
    넘기지 않으면 문자 속 링크의 호스트를 쓴다.

    urlscan score는 판정과 분리된 기록용 값이다.
    """

    final_url = parsed_result.get("final_url")
    domain = parsed_result.get("domain")
    score = parsed_result.get("score")

    if not final_url:
        raise ValueError(
            "urlscan 결과에 final_url이 없습니다."
        )

    if not domain:
        raise ValueError(
            "urlscan 결과에 domain이 없습니다."
        )

    if judged_domain is _FROM_INPUT_URL:
        input_domain = urlparse(input_url).hostname
    else:
        input_domain = judged_domain

    official = check_link_official(
        brand,
        input_domain,
        domain,
    )

    raw_url_result = {
        "final_url": final_url,
        # 카드가 말하는 "문자 속 주소"와 맞춘다. 입력 주소를 해석하지 못하면 최종 도메인.
        "domain": input_domain or domain,
        "official": official,
        "scan": {
            "score": score,
            "scanned_at": datetime.now(
                timezone.utc
            ).isoformat()
        }
    }

    print(
        "[AI URL INPUT]",
        raw_url_result
    )

    return UrlAnalysis.model_validate(
        raw_url_result
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
    job_id: str
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
                run_analysis(
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
# URLSCAN Result Parser
# ============================================================

def parse_urlscan_result(
    result: dict
) -> dict:

    task = result.get(
        "task",
        {}
    )

    page = result.get(
        "page",
        {}
    )

    verdicts = result.get(
        "verdicts",
        {}
    )

    urlscan = verdicts.get(
        "urlscan",
        {}
    )

    engines = verdicts.get(
        "engines",
        {}
    )

    overall = verdicts.get(
        "overall",
        {}
    )

    print(
        "[URLSCAN VERDICTS]",
        verdicts
    )

    return {
        # 기본 URL 정보
        "url": task.get("url"),
        "title": page.get("title"),
        "final_url": page.get("url"),
        "domain": page.get("domain"),

        # urlscan 자체 verdict
        "score": urlscan.get("score"),
        "malicious": urlscan.get(
            "malicious"
        ),
        "categories": urlscan.get(
            "categories",
            []
        ),
        "brands": urlscan.get(
            "brands",
            []
        ),

        # 비교/디버깅용 ML 정보
        "ml_score": engines.get(
            "score"
        ),
        "ml_malicious": engines.get(
            "malicious"
        ),

        # 비교/디버깅용 overall 정보
        "overall_score": overall.get(
            "score"
        ),
        "overall_malicious": overall.get(
            "malicious"
        )
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
