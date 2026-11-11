"""분석 작업(Job) 저장소.

프로토타입용 인메모리 저장소라 서버가 다시 뜨면 사라진다. 지운 적이 없어 계속 쌓인다.
추후 PostgreSQL 이나 Redis 로 바꾼다. 카카오 콜백은 한 번뿐이므로 결과를 여기에 두었다가
"결과 확인", "자세히 보기", Q1·Q2 답 처리에서 다시 꺼낸다.
"""

import uuid
from datetime import datetime, timezone
from typing import Literal, TypedDict

from backend.src.server.log import get_logger

_log = get_logger("server")

JobStatus = Literal["running", "completed", "timeout", "failed"]


class Job(TypedDict, total=False):
    job_id: str
    user_id: str | None
    status: JobStatus
    created_at: str
    completed_at: str | None
    # 사용자에게 보낸 카카오 응답. "결과 확인"이 그대로 다시 보낸다
    result: dict | None
    # 카드로 만들기 전의 분석 결과. "자세히 보기"와 답 처리가 이것으로 카드를 다시 만든다.
    # 여러 링크는 마지막 결과만 남으므로 상세 조회를 막는다(link_count)
    final_payload: dict | None
    error: str | None
    callback_status: Literal["pending", "success", "failed"]
    callback_error: str | None
    # 아래는 분석하면서 붙는다. analysis_kind 가 없으면 링크 문자다
    analysis_kind: Literal["voice", "message_case"]
    link_count: int
    qa_answers: list[str]
    # #89 주의 갈래 카드에 쓴 문자 게이트 결과. '자세히 보기'가 같은 근거를 보여 준다
    link_case: dict | None


ANALYSIS_JOBS: dict[str, Job] = {}
# 분석이 진행 중인 사용자. 같은 사용자의 요청을 겹쳐 받지 않으려고 둔다
RUNNING_USERS: set[str] = set()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_analysis_job(user_id: str | None) -> str:
    job_id = str(uuid.uuid4())
    ANALYSIS_JOBS[job_id] = {
        "job_id": job_id,
        "user_id": user_id,
        "status": "running",
        "created_at": now_iso(),
        "completed_at": None,
        "result": None,
        "final_payload": None,
        "error": None,
        "callback_status": "pending",
        "callback_error": None,
    }
    _log.info(f"[JOB CREATED] job_id={job_id}")
    return job_id


def find_latest_job_by_user(user_id: str | None) -> Job | None:
    if not user_id:
        return None
    user_jobs = [job for job in ANALYSIS_JOBS.values() if job.get("user_id") == user_id]
    if not user_jobs:
        return None
    return max(user_jobs, key=lambda job: job["created_at"])


def finish_job(job_id: str, status: JobStatus, result: dict, error: str | None = None) -> None:
    """분석이 끝난 상태와 사용자에게 보낼 응답을 남긴다.

    콜백을 보내기 전에 부른다. 콜백이 실패해도 "결과 확인"이 같은 응답을 줄 수 있어야 한다.
    """

    job = ANALYSIS_JOBS.get(job_id)
    if job is None:
        return
    job["status"] = status
    job["result"] = result
    job["completed_at"] = now_iso()
    if error is not None:
        job["error"] = error


def set_callback_status(job_id: str, status: Literal["success", "failed"], error: str | None = None) -> None:
    job = ANALYSIS_JOBS.get(job_id)
    if job is None:
        return
    job["callback_status"] = status
    if error is not None:
        job["callback_error"] = error
