# 서버 기동 시 사례 검색 KB 미리 불러오기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 서버가 요청을 받기 전에 사례 검색 KB·색인을 메모리에 올려, 프로세스의 첫 문자 분석이 KB 로드 때문에 사례 검색 1초 예산을 넘기지 않게 한다.

**Architecture:** `ai` 는 검색이 쓰는 캐시를 채우는 공개 함수 `warm_up_case_search()` 하나만 내놓는다. 이 함수는 `search_cases()` 를 그대로 한 번 돌리므로, 검색이 무엇을 캐시하든(지금은 사례 목록과 TF-IDF 색인) 같이 채워진다. BE 는 새 `app_lifespan` 에서 이 함수를 부른 뒤 기존 `http_client_lifespan` 으로 들어가고, `main.py` 는 lifespan 만 바꿔 끼운다.

**Tech Stack:** Python ≥3.11, FastAPI lifespan(`contextlib.asynccontextmanager`), uvicorn, pytest·pytest-asyncio, `fastapi.testclient.TestClient`.

**Spec:** 별도 설계 문서 없음. 아래 "배경과 결정"이 근거다.

## 실행 결과 (2026-10-03, Codex 인계)

- AI 변경: `602834a`, `refactor/scenario-message-test-2-ai`에 로컬 커밋. 전체 테스트 `877 passed`. 사용자 검수 전이므로 push·PR은 하지 않았다.
- BE 변경: [PR #40 코멘트](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/pull/40#issuecomment-5958548902)에 파일 3개의 diff 전문, 반영 순서, 검증 결과를 게시했다. BE 브랜치에 push하지 않았다.
- 검증 환경은 Python 3.13 저장소 루트 `.venv`를 사용했다. BE 임시 worktree에는 AI 워밍업 커밋 하나 대신 현재 AI 브랜치 전체를 병합했다. BE에 최신 TF-IDF 검색 코드가 없어 단일 cherry-pick만으로는 최신 캐시 테스트를 검증할 수 없기 때문이다. 병합 충돌은 없었다.
- AI·BE 통합 기준선 `940 passed`, 새 lifespan 테스트 `4 passed`, 변경 후 전체 `944 passed`. 기존 Starlette TestClient deprecation 경고 1건이 있다.
- 실제 uvicorn 기동에서 `[KB WARMUP] 0.33s` → `Application startup complete` → `Uvicorn running` 순서와 정상 종료를 확인했다. 포트는 `18764`를 사용했다. 로컬 측정값이며 Render 측정값은 아니다.
- 독립 검토에서 수정이 필요한 문제는 발견되지 않았다. 배포 후 확인은 아래 범위 밖 항목으로 남긴다.

## 배경과 결정

- 증상: Render 로그 2건 모두 문자 분석이 `answer=partial`, `failures=["timeout"]`. 원인은 LLM 이 아니라 사례 검색의 1초 예산 초과(`ai/src/ai/pipeline/analysis.py` `_search_with_budget`).
- KB 캐시는 `ai/src/ai/kb/search.py` 의 `@lru_cache` 라 **프로세스가 살아 있는 동안 유지**된다. 만료 시간(TTL)은 없고, 키가 경로 하나뿐이라 밀려나지도 않는다. 1초를 넘기는 건 프로세스의 첫 검색뿐이다.
- Render 무료 플랜은 15분 동안 요청이 없으면 잠들고, 다음 요청에서 다시 기동한다(약 1분, CPU 0.1개). 그래서 실제로는 상당수 문자가 새 프로세스의 첫 검색이 된다.
- uvicorn 은 `lifespan` 시작을 끝낸 **뒤에** 포트를 연다(`uvicorn/server.py` `Server.startup()` 에서 `await self.lifespan.startup()` 이 `loop.create_server(...)` 보다 먼저). 따라서 lifespan 에서 불러 두면, 잠에서 깬 직후의 첫 요청도 앱에 닿기 전에 캐시가 차 있다.
- 로컬 측정(코어 1개): 첫 호출 0.33~0.36s, 이후 2~3ms. Render(CPU 0.1개) 값은 모른다. 이번 작업이 기동 로그 `[KB WARMUP] x.xxs` 로 그 값을 남긴다.
- 범위 밖: Render 기동(약 1분)이 카카오 스킬 5초를 넘는 문제, YAML 파서를 `CSafeLoader` 로 교체, KB 를 JSON 한 파일로 사전 빌드. `[KB WARMUP]` 실측값을 본 뒤 따로 정한다.

## Global Constraints

- 의존 방향은 `backend` → `ai` 한 방향. `ai/` 안에서 `server`·카카오 관련 모듈을 import 하지 않는다(CLAUDE.md 원칙 4).
- 워밍업이 실패해도 서버는 떠야 한다. 검색은 부가 기능이고 응답은 나가야 한다(CLAUDE.md 원칙 3).
- 사례 검색 예산 `SEARCH_TIMEOUT_SECONDS = 1.0` 은 바꾸지 않는다. 기준 문서는 `docs/latency-budget.md`.
- `search_cases()` 의 시그니처·반환, 유사도 계산, 기준값 `DEFAULT_MIN_SIMILARITY = 0.2102` 는 바꾸지 않는다(바꾸면 RAG 평가를 다시 돌려야 한다).
- BE 로그는 기존 코드처럼 `print("[TAG] ...")` 형식.
- 브랜치(사용자 지시, 2026-10-03):
  - AI 파트(Task 1)는 `refactor/scenario-message-test-2-ai` 에 직접 커밋한다. push·PR 은 하지 않는다. 사용자가 먼저 검수한다.
  - BE 파트(Task 2)는 `refactor/scenario-message-test-2-be`(PR #40) 기준으로 검증하되, 그 브랜치에 push 하지 않고 수정 내용을 PR #40 코멘트로 올린다. 검증은 `origin/refactor/scenario-message-test-2-be` 에 Task 1 커밋을 얹은 임시 worktree 에서 한다(BE 코드가 Task 1 의 `warm_up_case_search` 를 import 하므로).
- 커밋 접두어 `feat:` `docs:` `test:`. Codex가 완료한 커밋에 Claude 공동 작성자 표기는 넣지 않는다.

## 실행 환경

이 PC 에서는 Python 3.12 가 한글 경로(`바탕 화면`)가 든 `.pth` 를 cp949 로 읽다가 죽어서 `uv run`·`ai/.venv` 가 뜨지 않는다. Python 3.13 은 `.pth` 를 UTF-8 로 먼저 읽으므로 저장소 루트에 3.13 가상환경을 만든다. `.venv/` 는 `.gitignore` 에 있다.

```powershell
uv venv .venv --python 3.13
uv pip install --python ./.venv/Scripts/python.exe -r requirements.txt pytest pytest-asyncio
./.venv/Scripts/python.exe -m pytest ai/tests tests -q
```

기준선: `876 passed` = AI 838 + BE 38 (`42734e2`, 2026-10-03 확인). 이하 `PY` 는 `./.venv/Scripts/python.exe` 를 뜻한다. 모든 명령은 저장소 루트에서 실행한다.

## 파일 구조

| 파일 | 변경 | 책임 |
|---|---|---|
| `ai/src/ai/pipeline/analysis.py` | 수정 | `warm_up_case_search()` 추가 |
| `ai/src/ai/pipeline/__init__.py` | 수정 | 공개 목록에 `warm_up_case_search` 추가 |
| `ai/tests/test_warm_up.py` | 생성 | 워밍업 뒤 검색이 KB 를 다시 읽거나 색인을 다시 만들지 않는지 |
| `ai/tests/test_revision_pipeline.py:369-373` | 수정 | 공개 함수 목록 계약 갱신 |
| `docs/ai-be-final-result-schema.md:17, 24` | 수정 | 공개 함수 표에 한 줄 추가 |
| `backend/src/server/lifespan.py` | 생성 | `app_lifespan` = KB 워밍업 + 기존 `http_client_lifespan` |
| `main.py:9-14, 24` | 수정 | lifespan 교체(3줄) |
| `tests/test_app_lifespan.py` | 생성 | 기동 순서, 실패·빈 KB 처리, `main.app` 연결 |
| `docs/latency-budget.md:42-45` | 수정 (Task 1) | 첫 호출을 기동 시점으로 옮기는 함수가 있음을 기록 |

PR #40(`refactor/scenario-message-test-2-be`)과의 관계: #40 은 `backend/src/server/` 를 건드리지 않는다. `main.py` 의 3줄 변경은 #40 의 수정 구간과 겹치지 않는다(같은 변경을 임시 브랜치에 넣고 `git merge-tree` 로 충돌 없음을 확인함). #40 의 `docs/latency-budget.md` 42-45행은 AI 브랜치와 다른 옛 문장(170~290ms)이라, 문서 갱신은 AI 브랜치(Task 1)에서 한다.

**병합 순서:** BE 변경은 `ai.pipeline.warm_up_case_search` 를 import 한다. AI 변경이 통합 브랜치(`refactor/scenario-message-test-2`)에 먼저 들어가야 한다. 거꾸로 되면 서버가 import 오류로 뜨지 않는다.

## Review Focus

1. **워밍업이 예외를 던질 때**(KB 파일 읽기 실패 등): 서버는 뜨고, HTTP 클라이언트도 정상적으로 생성·종료되며, `[KB WARMUP FAILED]` 가 찍혀야 한다. → Task 2 `test_warm_up_failure_does_not_block_startup`
2. **배포본에 KB 가 비어 있을 때**(`case_examples` 누락 등): 서버는 뜨고 `[KB WARMUP EMPTY] status=fallback` 이 찍혀야 한다. 조용히 지나가면 모든 검색이 사례 없이 나간다. → Task 2 `test_empty_kb_is_reported`
3. **워밍업이 검색이 실제로 쓰는 캐시와 다른 것을 채울 때**(예: 사례 목록만 채우고 색인은 안 채움): 첫 요청이 여전히 색인 생성을 떠안는다. → Task 1 `test_search_after_warm_up_does_not_reload_kb` (파싱과 색인 생성을 둘 다 막는다)
4. **`main.py` 가 새 lifespan 을 쓰지 않은 채로 병합될 때**(PR #40 병합 중 되돌아감 등): 모듈 테스트는 통과하는데 실제 앱은 워밍업을 하지 않는다. → Task 2 `test_main_app_warms_kb_on_startup`
5. **워밍업이 실패한 뒤 종료할 때 HTTP 클라이언트가 안 닫힐 때** → Task 2 `test_warm_up_failure_does_not_block_startup` 의 `client.is_closed` 확인

---

### Task 1: ai — `warm_up_case_search()` 공개

**Files:**
- Modify: `ai/src/ai/pipeline/analysis.py:27-36`
- Modify: `ai/src/ai/pipeline/__init__.py`
- Modify: `ai/tests/test_revision_pipeline.py:370-373`
- Modify: `docs/ai-be-final-result-schema.md:17, 24`
- Create: `ai/tests/test_warm_up.py`

**Interfaces:**
- Consumes: `ai.kb.search.search_cases(masked_text: str, ...) -> CaseSearchResult` (기존)
- Produces: `ai.pipeline.warm_up_case_search() -> ai.types.CaseSearchResult` — 동기 함수, 인자 없음. KB 가 있으면 `status=COMPLETED`, 비었으면 `FALLBACK`. KB 를 읽다 난 예외는 잡지 않고 그대로 올린다(BE 가 잡는다).

- [ ] **Step 1: 브랜치와 환경 준비**

```powershell
git switch refactor/scenario-message-test-2-ai
git pull --ff-only
```

새 브랜치는 만들지 않는다. `.venv` 가 없으면 "실행 환경" 절의 명령으로 만들고 기준선 `876 passed` 를 확인한다.

- [ ] **Step 2: 실패하는 테스트 작성**

`ai/tests/test_warm_up.py` 생성:

```python
"""서버 기동 시 사례 검색 KB 를 미리 불러오는 워밍업을 검증한다."""

import pytest

import ai.kb.search as search
from ai.pipeline import warm_up_case_search
from ai.types import AnalysisStatus


@pytest.fixture
def cold_kb():
    """다른 테스트가 채운 캐시를 비워, 워밍업이 직접 채우는지 본다."""
    search._load_cases.cache_clear()
    search._load_index.cache_clear()


def test_search_after_warm_up_does_not_reload_kb(cold_kb, monkeypatch):
    assert warm_up_case_search().status is AnalysisStatus.COMPLETED

    def forbidden(*args, **kwargs):
        raise AssertionError("워밍업 뒤 검색이 KB 를 다시 읽거나 색인을 다시 만들면 안 된다")

    monkeypatch.setattr(search, "_parse_case", forbidden)
    monkeypatch.setattr(search, "build_index", forbidden)

    result = search.search_cases("[CJ대한통운] 배송지 확인이 필요합니다.")

    assert result.status is AnalysisStatus.COMPLETED
```

`ai/tests/test_revision_pipeline.py:370-373` 의 공개 목록 계약을 갱신:

```python
    assert set(public.__all__) == {
        "analyze_message_part", "analyze_environment_part",
        "assemble_analysis", "finalize_analysis", "warm_up_case_search",
    }
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

Run: `PY -m pytest ai/tests/test_warm_up.py ai/tests/test_revision_pipeline.py::test_public_surface_is_independent_and_assembly_is_pure -q`
Expected: `test_warm_up.py` 는 수집 단계에서 `ImportError: cannot import name 'warm_up_case_search' from 'ai.pipeline'`, 공개 목록 테스트는 `AssertionError`(set 에 `warm_up_case_search` 없음).

- [ ] **Step 4: 최소 구현**

`ai/src/ai/pipeline/analysis.py` — `MAX_MESSAGE_CHARS = 8192` 바로 아래에 상수 추가:

```python
SEARCH_TIMEOUT_SECONDS = 1.0
MAX_MESSAGE_CHARS = 8192
# Any non-blank text: search_cases returns before touching the KB on blank input.
_WARM_UP_TEXT = "warm-up"
```

같은 파일, `_search_with_budget` 와 `_failure_code` 사이에 함수 추가:

```python
def warm_up_case_search() -> CaseSearchResult:
    """Fill the caches the case search uses, so no request pays the KB load.

    Runs search_cases itself rather than a loader, so whatever it caches (the
    cases and the TF-IDF index today) is filled by construction.
    """
    return search_cases(_WARM_UP_TEXT)
```

`ai/src/ai/pipeline/__init__.py` 전체를 다음으로 교체:

```python
from ai.pipeline.analysis import (
    analyze_environment_part, analyze_message_part, warm_up_case_search,
)
from ai.pipeline.finalize import finalize_analysis
from ai.pipeline.results import assemble_analysis

__all__ = [
    "analyze_environment_part", "analyze_message_part",
    "assemble_analysis", "finalize_analysis", "warm_up_case_search",
]
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `PY -m pytest ai/tests/test_warm_up.py ai/tests/test_revision_pipeline.py::test_public_surface_is_independent_and_assembly_is_pure -q`
Expected: `2 passed`

참고: 워밍업이 아무것도 안 하는 가짜 구현(`return CaseSearchResult(status=AnalysisStatus.COMPLETED)`)이면 `test_search_after_warm_up_does_not_reload_kb` 가 `forbidden` 의 `AssertionError` 로 실패한다(2026-10-03 확인). 테스트가 실제로 캐시를 검사한다는 뜻이다.

- [ ] **Step 6: 공개 함수 문서 갱신**

`docs/ai-be-final-result-schema.md:17` 의 문장 `현재 공개 API는 다음 네 Python 함수다.` 를 다음으로 바꾼다:

```markdown
현재 공개 API는 다음 다섯 Python 함수다.
```

같은 줄 끝 `페이지 분석과 조립 함수는 기존 호출 호환성과 개별 사용을 위해 유지한다.` 뒤에 한 문장을 붙인다:

```markdown
`warm_up_case_search()`는 분석이 아니라 서버 기동 준비용이다.
```

24행(`assemble_analysis` 행) 바로 아래에 표 행을 추가한다:

```markdown
| `warm_up_case_search() -> CaseSearchResult` | 없음 | 사례 검색이 쓰는 KB·색인 캐시를 채운다. BE 가 서버 기동 시(lifespan) 한 번 부른다. 동기 함수이며 KB 를 읽다 난 예외는 그대로 올린다 |
```

`docs/latency-budget.md` 의 `첫 호출에서 타임아웃되어 문자 분석이 미완료(`timeout`)로 처리됐다.` 줄(45행) 바로 아래, 같은 들여쓰기로 세 줄을 추가한다:

```markdown
  `ai.pipeline.warm_up_case_search()` 를 서버 기동 시(lifespan) 부르면 이 첫 호출이 요청 밖으로
  빠진다. uvicorn 은 lifespan 시작을 끝낸 뒤 포트를 열기 때문에 Render 무료 플랜이 슬립에서
  깨어난 직후의 첫 요청도 같다. BE 연결은 PR #40 코멘트에 있다.
```

- [ ] **Step 7: AI 전체 테스트**

Run: `PY -m pytest ai/tests -q`
Expected: `839 passed` (기준선 AI 838 + 1), 실패 0.

- [ ] **Step 8: Commit**

```powershell
git add ai/src/ai/pipeline/analysis.py ai/src/ai/pipeline/__init__.py ai/tests/test_warm_up.py ai/tests/test_revision_pipeline.py docs/ai-be-final-result-schema.md docs/latency-budget.md
git commit -m "feat: 사례 검색 KB 를 미리 채우는 warm_up_case_search 공개"
```

---

### Task 2: BE — 서버 기동 시 워밍업

**Files:** (`origin/refactor/scenario-message-test-2-be` 기준 임시 worktree. `main.py` 의 lifespan 줄은 그 브랜치에서 26행)
- Create: `backend/src/server/lifespan.py`
- Modify: `main.py:9-14, 26`
- Create: `tests/test_app_lifespan.py`

**Interfaces:**
- Consumes: `ai.pipeline.warm_up_case_search() -> CaseSearchResult` (Task 1), `backend.src.server.urlscan_service.http_client_lifespan(app)` (기존), `get_http_client()` (기존)
- Produces: `backend.src.server.lifespan.app_lifespan(app)` — `@asynccontextmanager`. `FastAPI(lifespan=app_lifespan)` 로 쓴다. `backend.src.server.lifespan.warm_up_kb() -> None` — 예외를 밖으로 내보내지 않는다.

- [ ] **Step 0: 임시 worktree 준비 (Task 1 커밋 이후)**

```bash
SCRATCH="<세션 scratchpad 경로>"
WT="$SCRATCH/wt-be"
git worktree add --detach "$WT" origin/refactor/scenario-message-test-2-be
git -C "$WT" cherry-pick <Task 1 커밋>
```

이 worktree 의 `ai` 를 쓰도록 이후 pytest·uvicorn 명령 앞에 `PYTHONPATH="$WT/ai/src;$WT"` 를 붙이고 `$WT` 에서 실행한다. 먼저 기준선을 잰다: `PY -m pytest ai/tests tests -q` → 실패 0, 개수를 적어 둔다(이하 `BASE_BE`).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_app_lifespan.py` 생성:

```python
"""서버 기동 시 KB 워밍업과 공유 HTTP 클라이언트 수명을 함께 검증한다."""

import pytest
from fastapi.testclient import TestClient

import backend.src.server.lifespan as lifespan
import backend.src.server.urlscan_service as service
from ai.types import AnalysisStatus, CaseSearchResult


@pytest.fixture
def warm_up_calls(monkeypatch):
    calls = []

    def fake():
        calls.append("warm_up")
        return CaseSearchResult(status=AnalysisStatus.COMPLETED)

    monkeypatch.setattr(lifespan, "warm_up_case_search", fake)
    return calls


@pytest.mark.asyncio
async def test_kb_is_warmed_before_serving(warm_up_calls, capsys):
    async with lifespan.app_lifespan(None):
        assert warm_up_calls == ["warm_up"]
        client = service.get_http_client()
        assert not client.is_closed

    assert client.is_closed
    assert "[KB WARMUP]" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_warm_up_failure_does_not_block_startup(monkeypatch, capsys):
    def broken():
        raise OSError("disk gone")

    monkeypatch.setattr(lifespan, "warm_up_case_search", broken)

    async with lifespan.app_lifespan(None):
        client = service.get_http_client()
        assert not client.is_closed

    assert client.is_closed
    assert "[KB WARMUP FAILED] OSError: disk gone" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_empty_kb_is_reported(monkeypatch, capsys):
    monkeypatch.setattr(
        lifespan, "warm_up_case_search",
        lambda: CaseSearchResult(status=AnalysisStatus.FALLBACK),
    )

    async with lifespan.app_lifespan(None):
        pass

    assert "[KB WARMUP EMPTY] status=fallback" in capsys.readouterr().out


def test_main_app_warms_kb_on_startup(warm_up_calls):
    import main

    with TestClient(main.app):
        assert warm_up_calls == ["warm_up"]
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `PY -m pytest tests/test_app_lifespan.py -q`
Expected: 수집 단계에서 `ModuleNotFoundError: No module named 'backend.src.server.lifespan'`

- [ ] **Step 3: lifespan 모듈 구현**

`backend/src/server/lifespan.py` 생성:

```python
"""서버 수명 동안의 준비·정리 작업을 한 곳에 묶는다."""

import time
from contextlib import asynccontextmanager

from ai.pipeline import warm_up_case_search
from ai.types import AnalysisStatus
from backend.src.server.urlscan_service import http_client_lifespan


def warm_up_kb() -> None:
    """사례 검색 KB 를 요청이 오기 전에 불러온다.

    uvicorn 은 lifespan 시작이 끝난 뒤에 포트를 연다. 여기서 불러 두면 Render 가
    슬립에서 깨어난 직후의 첫 요청도 KB 로드를 1초 검색 예산 안에서 떠안지 않는다.
    실패해도 서버는 뜬다. 캐시는 예외를 저장하지 않으므로 첫 검색이 다시 시도한다.
    """
    start = time.monotonic()

    try:
        result = warm_up_case_search()
    except Exception as e:
        print(
            f"[KB WARMUP FAILED] "
            f"{type(e).__name__}: {e}"
        )
        return

    elapsed = time.monotonic() - start

    if result.status is not AnalysisStatus.COMPLETED:
        print(
            f"[KB WARMUP EMPTY] "
            f"status={result.status.value} "
            f"({elapsed:.2f}s)"
        )
        return

    print(f"[KB WARMUP] {elapsed:.2f}s")


@asynccontextmanager
async def app_lifespan(app):
    warm_up_kb()

    async with http_client_lifespan(app):
        yield
```

- [ ] **Step 4: 모듈 테스트 통과, `main` 연결 테스트만 실패 확인**

Run: `PY -m pytest tests/test_app_lifespan.py -q`
Expected: `1 failed, 3 passed` — 실패는 `test_main_app_warms_kb_on_startup` (`assert [] == ['warm_up']`)

- [ ] **Step 5: `main.py` 에 연결**

`main.py:9-14` 의 import 묶음에서 `http_client_lifespan,` 한 줄을 지우고, 묶음 바로 아래에 import 한 줄을 추가한다:

```python
from backend.src.server.urlscan_service import (
    get_http_client,
    submit_url_scan,
    wait_for_url_scan_result,
)
from backend.src.server.lifespan import app_lifespan
```

`main.py:26` 의 lifespan 줄을 바꾼다:

```python
app = FastAPI(lifespan=app_lifespan)
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `PY -m pytest tests/test_app_lifespan.py -q`
Expected: `4 passed, 1 warning`. 경고는 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated` 로, 설치된 Starlette 버전이 내는 것이며 이번 변경과 무관하다.

- [ ] **Step 7: 전체 테스트**

Run: `PY -m pytest ai/tests tests -q`
Expected: `BASE_BE + 4` passed, 실패 0. #40 의 기존 테스트 중 `with TestClient(main.app)` 를 쓰는 것(`test_analysis_job_api.py`, `test_check_result.py`)은 이제 기동 때 실제 KB 를 불러오지만 결과는 같아야 한다.

- [ ] **Step 8: 실제 기동 순서 눈으로 확인**

터미널에서(파이프로 넘기지 말 것. 출력 순서가 버퍼링으로 섞인다):

Run: `PY -m uvicorn main:app --port 8000`
Expected: 아래 순서로 찍힌 뒤 Ctrl+C 로 종료(같은 순서를 최소 앱으로 2026-10-03 확인).

```
INFO:     Started server process [...]
INFO:     Waiting for application startup.
[KB WARMUP] 0.3xs
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

`[KB WARMUP]` 이 `Uvicorn running on` 보다 먼저 나오면 포트가 열리기 전에 KB 가 올라갔다는 뜻이다.

- [ ] **Step 9: PR #40 코멘트 작성**

BE 파일만의 diff 를 뽑는다: `git -C "$WT" diff -- backend/src/server/lifespan.py main.py tests/test_app_lifespan.py` (새 파일은 `git add -N` 후). 코멘트에는 다음을 담는다: 왜(로그의 `timeout` 원인과 캐시 수명), 무엇(파일 3개), **병합 순서**(AI 변경 먼저), 검증 결과(테스트 수, 기동 로그 순서), diff 전문. `gh pr comment 40 --body-file <파일>` 로 올린다.

- [ ] **Step 10: 정리**

`git worktree remove "$WT"`. `-be` 브랜치에는 push 하지 않는다.

---

## 배포 후 확인 (작업 범위 밖, 머지 뒤)

- Render 로그에서 기동 시 `[KB WARMUP] x.xxs` 값을 확인해 `docs/latency-budget.md` 에 적는다. CPU 0.1개에서의 첫 로드 실측값이다.
- 기동 후 첫 문자에서 `[AI MESSAGE RESULT]` 의 `failures` 에 `timeout` 이 더 이상 없는지 본다.
- `[KB WARMUP]` 값이 크면(예: 수 초) 기동이 그만큼 늘어난 것이다. 그때 `CSafeLoader` 교체나 KB JSON 사전 빌드를 따로 검토한다.
