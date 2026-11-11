"""통화 분석(`voice.py`)과 링크 없는 문자 분석(`message.py`)이 같이 쓰는 단계.

두 흐름에 같은 코드가 따로 있어 한쪽만 고치면 어긋났다. 흐름마다 다른 것(검색 함수, 입력을
만드는 법, 예산이 바닥났을 때의 처리)은 각 모듈에 둔다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ai.case.lexicon import read_with_lexicon
from ai.case.types import ClaimReadingResult, ReadingSource, UncheckedReason
from ai.case.verify import VerifyResult
from ai.kb.numbers import OfficialContact, official_contact
from ai.types import CaseSearchResult, FailureCode

LOGGER = logging.getLogger(__name__)

# 사례 검색 예산 (docs/latency-budget.md). 질의 임베딩 한도(kb/semantic.py)보다 길어야
# 임베딩이 늦어도 TF-IDF 결과를 돌려줄 수 있다
SEARCH_BUDGET_S = 2.5
# read_claims 가 제 시간 초과에서 스스로 어휘표로 폴백하도록, 바깥 한도는 조금 넉넉히 둔다
READ_GRACE_S = 0.5


async def search_within(
    search_cases: Callable[[str], CaseSearchResult], text: str, timeout_s: float, *, label: str,
) -> tuple[CaseSearchResult | None, UncheckedReason | None]:
    """사례를 찾는다. 못 찾은 이유(시간 초과·실패)는 예외가 아니라 둘째 값으로 돌려준다.

    `search_cases` 는 동기 함수라 스레드에서 돌린다. `label` 은 로그에서 통화와 문자를 가른다.
    """
    try:
        found = await asyncio.wait_for(asyncio.to_thread(search_cases, text), timeout=timeout_s)
    except TimeoutError:
        # 검색 예산 전체를 넘겼다(#155). 임베딩 마감 뒤 TF-IDF 폴백과 구분해 남긴다
        LOGGER.warning("%s case search timed out budget=%.1fs", label, timeout_s)
        return None, UncheckedReason.TIMEOUT
    except Exception as exc:  # noqa: BLE001 — 사례는 부가 정보다. 검색이 실패해도 결론은 낸다
        LOGGER.warning("%s case search failed: %s", label, type(exc).__name__)
        return None, UncheckedReason.FAILED
    return found, None


async def read_or_lexicon(
    read_claims: Callable[[ReadingSource], Awaitable[ClaimReadingResult]],
    source: ReadingSource,
    timeout_s: float,
    *,
    label: str,
) -> ClaimReadingResult:
    """주장을 읽는다. 시간 안에 못 읽거나 실패하면 어휘표로 읽은 결과를 돌려준다(원칙 3)."""
    try:
        return await asyncio.wait_for(read_claims(source), timeout=timeout_s)
    except TimeoutError:
        failure = FailureCode.TIMEOUT
    except Exception as exc:  # noqa: BLE001 — LLM 이 실패해도 어휘표로 결론을 낸다
        LOGGER.warning("%s claims reading failed: %s", label, type(exc).__name__)
        failure = FailureCode.LLM_ERROR
    return read_with_lexicon(source, failure)


def official_contact_for(verify: VerifyResult) -> OfficialContact | None:
    """받아들인 기관 주장 중 대표번호 KB 에 있는 첫 기관. 확인 안내에 쓴다.

    번호는 KB 에서만 온다. 누가 말했는지와 상관없이 공식 번호로 직접 확인하라는 안내다.
    """
    for ident in verify.claims.identity:
        if ident.listed_institution:
            contact = official_contact(ident.name) or official_contact(ident.quote)
            if contact:
                return contact
    return None
