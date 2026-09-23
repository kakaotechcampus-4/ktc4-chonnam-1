import asyncio

import httpx
from fastapi import BackgroundTasks, FastAPI, Request

from urlscan_service import submit_url_scan, wait_for_url_scan_result
from url_utils import split_message

from templates.renderer import render_r1_lookalike


app = FastAPI()


# TODO:
# 현재는 콜백 기능 테스트를 위한 임시 인메모리 저장소.
# 추후 PostgreSQL 또는 Redis 기반 작업 상태 관리로 변경.
RUNNING_USERS: set[str] = set()

# 콜백 URL의 정확한 유효 시간은 카카오 공식 문서에서도 표현이 엇갈린다
# (개요에는 5분, 에러 표에는 1분이라고 나와 있어 아직 확정하지 않는다).
# 정확한 값이 뭐든, 사용자를 무한정 기다리게 하지 않도록 보수적으로 안전
# 마진을 둔 시간제한을 걸고, 넘으면 "판단 보류"에 준하는 안내라도 반드시
# 보낸다 (docs/latency-budget.md: "예산 초과 시 거기까지의 결과로 응답한다.
# 전체 실패로 만들지 않는다"와 같은 원칙).
CALLBACK_DEADLINE_SECONDS = 45.0


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

    # callbackUrl이 없는 경우
    #
    # 개발/테스트용 fallback.
    # urlscan 분석 시간이 길어지면 일반 Skill 응답 제한 시간을
    # 초과할 수 있으므로 실제 서비스에서는 callback 사용을 전제로 한다.
    if not callback_url:
        print("[CALLBACK] callbackUrl 없음 - 동기 처리")

        return await run_analysis(
            links,
            message
        )

    # 사용자 분석 시작 상태 저장
    if user_id:
        RUNNING_USERS.add(user_id)

    # 오래 걸리는 분석은 background task에서 수행
    background_tasks.add_task(
        run_analysis_and_callback,
        links,
        message,
        callback_url,
        user_id
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


async def run_analysis(
    links: list[str],
    message: str
) -> dict:
    """
    실제 URL 분석 수행.

    URL
      → urlscan 검사 요청
      → 결과 대기
      → 결과 파싱
      → 추후 AI 분석

    최종적으로 카카오 응답 JSON을 반환한다.
    """

    result_lines = []
    scan_results = []

    for link in links:
        try:
            # -----------------------------------
            # 1. urlscan 검사 요청
            # -----------------------------------

            submit_result = await submit_url_scan(link)

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

            # -----------------------------------
            # 2. 검사 결과 대기
            # -----------------------------------

            scan_result = await wait_for_url_scan_result(
                scan_id
            )

            # timeout 등으로 결과를 받지 못한 경우
            if scan_result is None:
                print(
                    f"[URLSCAN TIMEOUT] "
                    f"url={link}"
                )

                result_lines.append(
                    f"⚠️ 검사 시간 초과: {link}"
                )

                continue

            # -----------------------------------
            # 3. 결과 파싱
            # -----------------------------------

            parsed_result = parse_urlscan_result(
                scan_result
            )

            scan_results.append(parsed_result)

            print(
                f"[PARSED RESULT] "
                f"{parsed_result}"
            )

            result_lines.append(
                f"✅ 검사 완료: {link}"
            )

        except Exception as e:
            print(
                f"[URLSCAN ERROR] "
                f"url={link} "
                f"error={e}"
            )

            result_lines.append(
                f"⚠️ 검사 실패: {link}"
            )

    # ---------------------------------------
    # 4. 추후 AI 분석
    # ---------------------------------------

    # TODO:
    #
    # ai_result = await analyze_smishing(
    #     message=message,
    #     links=links,
    #     scan_results=scan_results
    # )

    # 현재는 콜백 기능 자체를 테스트하는 단계이므로
    # AI 결과 대신 고정 문자열 사용
    ai_result = "AI 분석 기능은 아직 연결되지 않았습니다."

    summary = "\n".join(result_lines)

    return kakao_response(
        f"URL 검사가 완료되었습니다. "
        f"(총 {len(links)}건)\n\n"
        f"{summary}\n\n"
        f"{ai_result}"
    )


async def run_analysis_and_callback(
    links: list[str],
    message: str,
    callback_url: str,
    user_id: str | None
):
    """
    백그라운드에서 분석을 수행한 뒤
    카카오 callbackUrl로 최종 결과를 전송한다.

    분석 → 콜백 전송 → 사용자 실행 상태 해제 순서를 반드시 지킨다.
    콜백 전송이 끝나기 전에 RUNNING_USERS를 먼저 비우면, 콜백이
    아직 도착하지 않은 상태에서 같은 사용자가 새 분석을 또 시작할 수 있다.
    """

    try:
        # -----------------------------------
        # 1. 분석 수행 (안전 마진을 둔 시간제한)
        # -----------------------------------

        try:
            result = await asyncio.wait_for(
                run_analysis(links, message),
                timeout=CALLBACK_DEADLINE_SECONDS
            )

        except asyncio.TimeoutError:
            # 콜백 URL이 언제 만료될지 확실치 않으니, 늦더라도 빈손보다는
            # "아직 진행 중"이라는 응답이라도 보낸다.
            print(
                f"[ANALYSIS TIMEOUT] "
                f"user={user_id} links={links}"
            )

            result = kakao_response(
                "분석이 예상보다 오래 걸리고 있어요. "
                "잠시 후 다시 확인해주세요."
            )

        except Exception as e:
            # 예상하지 못한 분석 오류
            print(
                f"[ANALYSIS ERROR] "
                f"{e}"
            )

            result = kakao_response(
                "분석 중 문제가 발생했습니다. "
                "잠시 후 다시 시도해주세요."
            )

        # -----------------------------------
        # 2. 카카오 callback 전송
        # -----------------------------------

        print("[CALLBACK] 결과 전송 시작")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                callback_url,
                json=result,
                timeout=10.0
            )

        # callback 디버깅을 위해 반드시 기록
        print(
            f"[CALLBACK STATUS] "
            f"{response.status_code}"
        )

        print(
            f"[CALLBACK RESPONSE] "
            f"{response.text}"
        )

        # 4xx / 5xx인 경우 예외 발생
        response.raise_for_status()

        # HTTP 200이어도 Kakao 응답의 status를 확인
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
            # JSON이 아닌 응답이 온 경우
            print(
                "[CALLBACK WARNING] "
                "응답을 JSON으로 파싱할 수 없습니다."
            )

    except Exception as e:
        print(
            f"[CALLBACK SEND FAILED] "
            f"{e}"
        )

    finally:
        # 분석뿐 아니라 callback 전송 시도까지 끝난 뒤
        # 사용자 실행 상태 해제
        if user_id:
            RUNNING_USERS.discard(user_id)

            print(
                f"[RUNNING USER REMOVED] "
                f"user={user_id}"
            )


def kakao_response(text: str) -> dict:
    """
    카카오 SkillResponse 형식 생성.
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


def parse_urlscan_result(result: dict) -> dict:
    """
    urlscan Result API 응답 중
    현재 분석에 필요한 데이터만 추출한다.
    """

    task = result.get("task", {})
    page = result.get("page", {})
    verdicts = result.get("verdicts", {})
    urlscan = verdicts.get("urlscan", {})

    return {
        "url": task.get("url"),
        "title": page.get("title"),
        "brands": urlscan.get(
            "brands",
            []
        )
    }

@app.post("/test/kakao/r1-lookalike")
async def test_r1_lookalike():
    """
    FE-BE Kakao 카드 연동 테스트용.

    실제 분석 로직을 거치지 않고
    R1 lookalike 카드를 반환한다.
    """

    return render_r1_lookalike()
