"""ai 안에서 끝나는 도구. 판정이 읽지 않는 참고 분석이라 에이전트가 고를 때만 돈다."""

import asyncio

from ai.agent.board import Board, Obs, Tool
from ai.case.privacy import mask_pii
from ai.case.steps import SEARCH_BUDGET_S
from ai.kb.numbers import NumberCheck, check_phone_number, find_numbers, resolve_org
from ai.kb.search import search_cases
from ai.types import CaseSearchResult


def case_search_tool(body: str) -> Tool[CaseSearchResult]:
    """링크를 뗀 본문으로 비슷한 피해 사례를 찾는다. 관측은 검색 상태와 찾은 수뿐이다."""

    async def run(_board: Board, _: None) -> CaseSearchResult:
        # ponytail: 문자 분석(analyze_message_part·analyze_message_case)도 같은 검색을 따로 돌린다.
        # 그 검색을 board 에서 꺼내 쓰게 바꾸면 요청당 한 번으로 준다
        # 임베딩을 쓰지 않는다. 임베딩 작업자는 카드가 읽는 검색과 함께 쓰므로, 참고용 검색이 끼어들면
        # 카드용 검색이 마감을 넘겨 TF-IDF 로 떨어지고 카드 근거가 달라질 수 있다
        return await asyncio.wait_for(
            asyncio.to_thread(search_cases, mask_pii(body), use_embedding=False), timeout=SEARCH_BUDGET_S
        )

    def observe(found: CaseSearchResult) -> Obs:
        return {"status": found.status, "matches": len(found.matches)}

    return Tool(
        "search_similar_cases",
        "본문과 비슷한 피해 사례가 있는지 찾는다. 판정에는 쓰이지 않는 참고 정보다",
        False, run, observe,
    )


def official_number_tool(text: str) -> Tool[tuple[NumberCheck, ...]]:
    """글에 적힌 전화번호를 글이 내세운 기관의 공식 대표번호와 대조한다. 관측은 개수뿐이다."""

    def check() -> tuple[NumberCheck, ...]:
        org = resolve_org(text) or ""
        return tuple(check_phone_number(number, org) for number in find_numbers(text))

    async def run(_board: Board, _: None) -> tuple[NumberCheck, ...]:
        # 첫 호출은 대표번호 파일을 읽고, 공격자가 쓴 글에 정규식을 돌린다
        return await asyncio.to_thread(check)

    def observe(checks: tuple[NumberCheck, ...]) -> Obs:
        return {
            "numbers": len(checks),
            "org_listed": any(check.result != "org_not_listed" for check in checks),
            "official": sum(check.result == "official" for check in checks),
            "not_official": sum(check.result == "not_official" for check in checks),
        }

    return Tool(
        "check_official_numbers",
        "본문에 적힌 전화번호가 내세운 기관의 공식 대표번호인지 대조한다. 번호가 적혀 있을 때만 쓸모가 있다",
        False, run, observe,
    )
