"""에이전트에게 보여 줄 관측과 조사 기록.

관측 값은 bool·int·Enum·None 뿐이다. 문자 원문·주소·HTML 은 여기를 지나 LLM 으로 가지 않는다.
"""

from dataclasses import asdict

from ai.agent import Obs, Trace
from ai.case.types import MessageCaseResult
from ai.types import MessagePart, UrlAnalysis
from backend.src.server.jobs import ANALYSIS_JOBS
from backend.src.server.log import get_logger
from scanner.models import CollectResult

_log = get_logger("server")


def see_collection(collected: CollectResult) -> Obs:
    browser = collected.browser
    return {
        "reached": collected.status_code is not None,
        "redirects": len(collected.redirect_chain),
        "has_page": bool(collected.html),
        "browser_used": browser is not None,
        "downloads": len(browser.downloads) if browser else 0,
        "failed": bool(collected.failures),
    }


def see_signals(part: MessagePart | None) -> Obs:
    if part is None:
        return {"answer": None, "brand_named": False, "signals": 0}
    return {
        "answer": part.answer,
        "brand_named": bool(part.brand) and part.brand != "unknown",
        "signals": len(part.details.signals),
    }


def see_official(url: UrlAnalysis) -> Obs:
    return {"official": url.official}


def see_claims(case: MessageCaseResult) -> Obs:
    return {
        "conclusion": case.conclusion,
        "caution": case.caution,
        "institution_claimed": any(claim.kind == "institution" for claim in case.claims.identity),
        "demands": len(case.claims.demands),
    }


def record_trace(job_id: str | None, trace: Trace) -> None:
    """어떤 분석을 누가 불렀는지 남긴다. 도구 이름·링크 번호·상태·시간뿐이다."""

    if job_id is not None and (job := ANALYSIS_JOBS.get(job_id)):
        job["investigation"] = asdict(trace)
    steps = ",".join(
        f"{step.tool}{f'#{step.link}' if step.link else ''}"
        f"/{'+'.join(step.askers)}/{step.status}/{step.elapsed_ms}"
        for step in trace.steps
    )
    _log.info(f"[AGENT] stop={trace.stop} turns={trace.turns} steps={steps}")
