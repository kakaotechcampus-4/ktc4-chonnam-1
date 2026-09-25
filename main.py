import asyncio

import uuid
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from backend.src.server.urlscan_service import (
    submit_url_scan,
    wait_for_url_scan_result,
)
from backend.src.server.url_utils import split_message
from backend.src.server.templates.renderer import render_r1_lookalike

# AI 연결
from ai.pipeline import analyze_message_part, finalize_analysis
from ai.types import FailureCode, UrlAnalysis


app = FastAPI()


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


# TODO:
# AI-BE 연동 테스트를 위한 임시 threshold.
# 실제 threshold는 urlscan 테스트 후 확정.
TEST_SCORE_THRESHOLD = 0


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
        "error": None,
        "callback_status": "pending"
    }

    print(
        f"[JOB CREATED] "
        f"job_id={job_id} "
        f"user={user_id}"
    )

    return job_id


# ============================================================
# Analysis Job API
# ============================================================

@app.get("/api/analyses/{job_id}")
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
# Kakao Skill
# ============================================================

@app.post("/kakao/skill")
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

    # 문자 내용에서 URL과 일반 메시지 분리
    links, message = split_message(utterance)

    print(f"[LINK COUNT] {len(links)}")
    print(f"[MESSAGE] {message!r}")

    # URL이 없는 경우 즉시 응답
    if not links:
        return kakao_response(
            "URL을 찾을 수 없습니다.\n"
            "http:// 또는 https://로 시작하는 URL을 보내주세요."
        )

    # 같은 사용자의 이전 분석이 아직 진행 중인 경우
    if user_id and user_id in RUNNING_USERS:
        return kakao_response(
            "이전 요청을 아직 확인하고 있어요. "
            "잠시 후 다시 시도해주세요."
        )

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
    return {
        "version": "2.0",
        "useCallback": True,
        "data": {
            "text": (
                "링크를 확인하고 있어요. "
                "분석이 완료되면 결과를 알려드릴게요."
            )
        }
    }


# ============================================================
# Main Analysis
# ============================================================

async def run_analysis(
    links: list[str],
    message: str
) -> dict:
    """
    현재 AI-BE 연결 테스트 흐름

    message
      → AI 문자 분석

    URL
      → urlscan
      → score 추출
      → official 계산
      → UrlAnalysis 생성

    official=True
      → finalize_analysis(url)

    official=False
      → finalize_analysis(
            url,
            message,
            page=None,
            failure=COLLECTION_FAILED
        )

    현재 격리 페이지 수집기는 아직 연결하지 않는다.
    """

    result_lines = []

    # ========================================================
    # 1. 문자 AI 분석
    # ========================================================

    print("========== AI MESSAGE ANALYSIS ==========")
    print(f"[AI MESSAGE INPUT] {message!r}")

    try:
        message_result = await analyze_message_part(
            message
        )

        print(
            "[AI MESSAGE RESULT]",
            message_result.model_dump(
                mode="json"
            )
        )

    except Exception as e:
        print(
            f"[AI MESSAGE ERROR] "
            f"{type(e).__name__}: {e}"
        )

        # finalize_analysis는 message=None도 처리 가능
        message_result = None

    print("=========================================")

    # ========================================================
    # 2. URL별 분석
    # ========================================================

    for link in links:
        try:
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

                result_lines.append(
                    f"⚠️ 검사 요청 실패: {link}"
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

                result_lines.append(
                    f"⚠️ 검사 시간 초과: {link}"
                )

                continue

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
            # 2-4. urlscan score → official
            # ------------------------------------------------

            url_analysis = build_ai_url_analysis(
                parsed_result
            )

            print(
                "========== URLSCAN SCORE TEST =========="
            )
            print(
                f"[INPUT URL]   "
                f"{parsed_result.get('url')}"
            )
            print(
                f"[FINAL URL]   "
                f"{url_analysis.final_url}"
            )
            print(
                f"[DOMAIN]      "
                f"{url_analysis.domain}"
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
                f"[TEST T]      "
                f"{TEST_SCORE_THRESHOLD}"
            )
            print(
                f"[OFFICIAL?]   "
                f"{url_analysis.official}"
            )
            print(
                "========================================"
            )

            # =================================================
            # 3. AI 최종 분석
            # =================================================

            print(
                "========== AI FINAL ANALYSIS =========="
            )

            if url_analysis.official:
                # official=True이면 AI 계약상
                # 문자/페이지 분석 없이 조기 반환 가능

                print(
                    "[AI FINAL] "
                    "official=True → early return"
                )

                final_result = await finalize_analysis(
                    url=url_analysis
                )

            else:
                # 현재는 격리 페이지 수집기를 연결하지 않았으므로
                # COLLECTION_FAILED로 명시

                print(
                    "[AI FINAL] "
                    "official=False → "
                    "message + collection failure"
                )

                final_result = await finalize_analysis(
                    url=url_analysis,
                    message=message_result,
                    page=None,
                    failure=FailureCode.COLLECTION_FAILED
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
            # 4. 테스트용 Kakao 출력
            # ------------------------------------------------

            result_lines.append(
                format_ai_result(
                    link,
                    final_payload,
                    parsed_result.get("score")
                )
            )

        except Exception as e:
            print(
                f"[ANALYSIS ERROR] "
                f"url={link} "
                f"{type(e).__name__}: {e}"
            )

            result_lines.append(
                f"⚠️ 분석 실패: {link}"
            )

    # ========================================================
    # 5. Kakao 응답
    # ========================================================

    if not result_lines:
        return kakao_response(
            "분석 결과를 생성하지 못했습니다."
        )

    return kakao_response(
        "\n\n".join(result_lines)
    )


# ============================================================
# URLSCAN → AI Adapter
# ============================================================

def build_ai_url_analysis(
    parsed_result: dict
) -> UrlAnalysis:
    """
    urlscan 결과를 AI UrlAnalysis 계약으로 변환한다.

    현재 테스트 정책:

        score <= TEST_SCORE_THRESHOLD
            → official=True

        score > TEST_SCORE_THRESHOLD
            → official=False

    실제 운영 threshold는 추후 확정한다.
    """

    final_url = parsed_result.get(
        "final_url"
    )

    domain = parsed_result.get(
        "domain"
    )

    score = parsed_result.get(
        "score"
    )

    if not final_url:
        raise ValueError(
            "urlscan 결과에 final_url이 없습니다."
        )

    if not domain:
        raise ValueError(
            "urlscan 결과에 domain이 없습니다."
        )

    if score is None:
        raise ValueError(
            "urlscan 결과에 score가 없습니다."
        )

    official = (
        score <= TEST_SCORE_THRESHOLD
    )

    raw_url_result = {
        "final_url": final_url,
        "domain": domain,
        "official": official
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
                    message
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

        async with httpx.AsyncClient() as client:
            response = await client.post(
                callback_url,
                json=result,
                timeout=10.0
            )

        print(
            f"[CALLBACK STATUS] "
            f"{response.status_code}"
        )

        print(
            f"[CALLBACK RESPONSE] "
            f"{response.text}"
        )

        response.raise_for_status()

        # ----------------------------------------------------
        # 3. Kakao callback 결과 확인
        # ----------------------------------------------------

        try:
            callback_result = response.json()

            callback_status = callback_result.get(
                "status"
            )

            print(
                f"[CALLBACK RESULT STATUS] "
                f"{callback_status}"
            )

            if (
                callback_status is not None
                and callback_status != "SUCCESS"
            ):
                print(
                    "[CALLBACK WARNING] "
                    f"Kakao callback status="
                    f"{callback_status}"
                )

        except ValueError:
            print(
                "[CALLBACK WARNING] "
                "응답을 JSON으로 파싱할 수 없습니다."
            )

    except Exception as e:
        print(
            f"[CALLBACK SEND FAILED] "
            f"{type(e).__name__}: {e}"
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
# AI Result → 테스트용 Kakao 문자열
# ============================================================

def format_ai_result(
    link: str,
    payload: dict,
    score: int | float | None
) -> str:

    url_result = payload.get(
        "url",
        {}
    ) or {}

    message_result = payload.get(
        "message",
        {}
    ) or {}

    env_result = payload.get(
        "env",
        {}
    ) or {}

    message_details = message_result.get(
        "details",
        {}
    ) or {}

    env_details = env_result.get(
        "details",
        {}
    ) or {}

    final_result = payload.get(
        "result"
    )

    def display(value):
        """
        테스트 출력용.
        None인 경우 알아보기 쉽게 표시한다.
        """
        return "없음" if value is None else str(value)

    return (
        "🔎 AI 분석 완료\n\n"

        "[URL 분석]\n"
        f"입력 URL: {link}\n"
        f"최종 URL: "
        f"{display(url_result.get('final_url'))}\n"
        f"도메인: "
        f"{display(url_result.get('domain'))}\n"
        f"urlscan score: "
        f"{display(score)}\n"
        f"official: "
        f"{display(url_result.get('official'))}\n\n"

        "[문자 분석]\n"
        f"brand: "
        f"{display(message_result.get('brand'))}\n"
        f"category: "
        f"{display(message_result.get('category'))}\n"
        f"answer: "
        f"{display(message_result.get('answer'))}\n"
        f"doubt: "
        f"{display(message_details.get('doubt'))}\n"
        f"reason: "
        f"{display(message_details.get('reason'))}\n\n"

        "[환경 분석]\n"
        f"brand: "
        f"{display(env_result.get('brand'))}\n"
        f"category: "
        f"{display(env_result.get('category'))}\n"
        f"answer: "
        f"{display(env_result.get('answer'))}\n"
        f"doubt: "
        f"{display(env_details.get('doubt'))}\n"
        f"reason: "
        f"{display(env_details.get('reason'))}\n\n"

        "[최종 판정]\n"
        f"result: "
        f"{display(final_result)}"
    )


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

@app.post("/test/kakao/r1-lookalike")
async def test_r1_lookalike():
    """
    FE-BE Kakao 카드 연동 테스트용.

    실제 분석 로직을 거치지 않고
    R1 lookalike 카드를 반환한다.
    """

    return render_r1_lookalike()
