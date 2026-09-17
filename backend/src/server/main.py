import asyncio
import httpx

from fastapi import BackgroundTasks, FastAPI, Request

from urlscan_service import submit_url_scan, wait_for_url_scan_result
from url_utils import split_message


app = FastAPI()

RUNNING_USERS: set[str] = set()


@app.post("/kakao/skill")
async def kakao_skill(
    request: Request,
    background_tasks: BackgroundTasks
):
    print("========== CALLBACK TEST VERSION 1 ==========")

    # 1. 카카오 요청 파싱
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

    # 2. 메시지에서 URL 추출
    links, message = split_message(utterance)

    print(f"[LINK COUNT] {len(links)}")

    if not links:
        return kakao_response(
            "URL을 찾을 수 없습니다.\n"
            "http:// 또는 https://로 시작하는 URL을 보내주세요."
        )

    # 3. callbackUrl 확인
    if not callback_url:
        return kakao_response(
            "Callback URL을 전달받지 못했습니다."
        )

    # 4. 백그라운드 작업 등록
    print("[KAKAO] BACKGROUND TASK ADD")

    background_tasks.add_task(
        run_analysis_and_callback,
        links,
        message,
        callback_url,
        user_id
    )

    print("[KAKAO] BACKGROUND TASK ADDED")

    # 5. 카카오에는 즉시 응답
    return {
        "version": "2.0",
        "useCallback": True,
        "data": {
            "text": "콜백 테스트 중입니다."
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
    """
    print("[BACKGROUND] started")

    try:
        print("[BACKGROUND] 3초 대기")

        await asyncio.sleep(3)

        result = kakao_response(
            "콜백 테스트 성공!"
        )

        print("[CALLBACK] 결과 전송 시작")

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

    except Exception as e:
        print(
            f"[CALLBACK ERROR] "
            f"{type(e).__name__}: {e}"
        )

    finally:
        if user_id:
            RUNNING_USERS.discard(user_id)

        print("[BACKGROUND] finished")


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
