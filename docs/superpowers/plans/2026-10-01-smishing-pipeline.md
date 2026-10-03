# 스미싱 확인 파이프라인 Implementation Plan


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** urlscan을 대체할 자체 URL 조사(1단계 문자열·도메인 → 2단계 httpx → 필요할 때만 3단계 Playwright)와 규칙·점수 판정을 LangGraph 파이프라인으로 만들고, urlscan과 그림자 병행 실행해 비교 기록을 쌓는다. 전환 여부는 라벨된 표본(스미싱 탐지율·정상 오탐률)으로 판단한다.

**Architecture:** `backend/src/server/scanner/`에 URL 도우미, 허용 목록, 1단계 특징(RDAP·로컬 목록), 2·3단계 격리 수집기(httpx + 조건부 Playwright, 별도 컨테이너로 실행), 페이지 특징(`ai.page.inspect_html` 재사용), I/O 없는 점수기·판정 규칙, LangGraph 그래프(문자 분석 ∥ URL별 조사 `Send`), 비교 기록을 둔다. `main.py`의 `run_analysis`가 기존 urlscan 경로와 동시에 그래프를 그림자로 실행하고, urlscan 결과와 함께 비교 기록을 남긴다.

**Tech Stack:** Python 3.13, httpx·PyYAML·FastAPI(설치됨), **langgraph·playwright(신규)**, stdlib(`html.parser`, `ipaddress`, `difflib`, `zipfile`), pytest

**Spec:** `docs/superpowers/specs/2026-10-01-smishing-pipeline-design.md` (스미싱 확인 파이프라인 설계)

## 이 계획의 범위

포함: Task 1–11 — 두 묶음 점수기, 허용 목록, 1단계 URL·도메인 특징(로컬 우선 + RDAP 2초), 목록 동기화, 2단계 httpx 수집, 3단계 브라우저와 격리 수집기, 페이지 특징, 4단계 판정 규칙과 URL 조사, LangGraph 파이프라인과 응답 문구, 비교 기록, 그림자 실행 연결.

제외(별도 계획): 사용자 응답 전환과 카카오 콜백(근거가 있으면 10초 안에 응답, 시간 관련 실패만 20초까지 대기, 백그라운드 완료·캐시·"다시 확인" 버튼)은 전환 기준을 채운 뒤. 격리 수집기 서버의 실제 배포는 ADR 0002 합의 뒤. 로지스틱 회귀는 라벨 데이터가 쌓인 뒤. `.kr` WHOIS 보완(공공데이터포털 WHOIS API)은 RDAP 확인 결과에 따라. 실시간 위협 피드는 출처를 찾은 뒤 `sources.yaml`에 추가한다(코드 변경 없음).

이 계획을 끝낸 상태: 사용자 응답은 지금처럼 urlscan 기반이고, 같은 문자에 대한 자체 판정이 비교 기록으로 쌓인다. 문자 분석 어댑터 매핑 전에는 모든 기록이 `check_failed: message`(확인 필요)로 남고, 기록은 프로세스 메모리에 있다.

## 선행 작업 (코드 밖)

1. 라이선스 확인 → `data/sources.yaml` 갱신: KISA 피싱사이트 URL은 이용허락 제한 없음(2026-10-02 확인)이라 `allowed`로 두었다. 파일 직접 다운로드 주소와 실제 헤더만 확인해 채운다. Tranco는 Cloudflare Radar(CC BY-NC)를 뺀 사용자 정의 목록 주소를 채운다. urlscan은 대량 제출이나 상업 서비스 통합 시 사전 협의가 필요하다. `commercial_use: allowed`인 출처만 운영에 쓴다.
2. 실시간 위협 피드 조사: KISA 데이터는 2023-12-31 1회성이라 탐지일 90일 필터 후 0건이다. 상업 이용 가능 여부와 국내 스미싱 포함 여부를 확인해 `sources.yaml`에 추가한다. 없으면 위협 피드 미설정으로 비교 기간을 진행한다.
3. 허용 목록 수집 → `server/data/whitelist.yaml`: 택배사 5곳의 등록 도메인, 허용 경로(조회 페이지만), 문자 속 표기(aliases), 고유 도메인 토큰(tokens), 출처·확인 날짜. 공식 문자가 실제로 쓰는 URL 형태(단축 URL 여부)도 함께 모은다. 공용 플랫폼·단축 URL 도메인은 넣지 않는다(넣으면 기동 실패).
4. 문자 분석 필드 매핑: 기존 `analyze_message_part` 결과에서 사칭 택배사와 요구 행동(`app_install`·`payment`·`personal_info`·`call`)을 꺼내도록 `signals_from_message_analysis`를 채우고 테스트를 추가한다.
5. urlscan 결과 형태·공개 범위 확인: `parse_urlscan_result` 결과에 `score`·`malicious`가 있는지, 결과를 기다리는 기존 함수 이름(Task 11 테스트), 제출 공개 범위가 unlisted 또는 private인지(public이면 URL 속 개인정보가 공개된다).
6. `.kr` 도메인 나이: rdap.org로 `.kr` 도메인 몇 개를 조회해 생성일이 나오는지 본다. 안 나오면 공공데이터포털 WHOIS API(KISA, 인증키 필요, .kr·.한국만, 등록일자 제공)로 보완하는 작업을 따로 계획한다.
7. 라벨 표본 계획: 전환 기준이 스미싱·정상 라벨 각 50건(가설값)이므로, 비교 기간의 예상 문자 수와 라벨 담당·주기를 정한다. 모자라면 팀이 받은 스미싱 문자와 공식 택배 문자로 보충한다.
8. 격리 수집기 배치 합의(ADR 0002): 이 저장소의 `collector_app`을 배포할지, 같은 계약(`POST /collect`)을 격리 담당 쪽에서 구현할지. 컨테이너는 non-root, 메모리 상한, egress 방화벽(사설·링크로컬·메타데이터 대역 차단), 인스턴스 IMDSv2 필수.
9. `SCAN_COMPARE_TOKEN` 발급과 보관 위치 결정.

## Global Constraints

- 새 의존성은 `langgraph`, `playwright` 두 개뿐이다. 브라우저 바이너리는 수집기 컨테이너에만 설치한다.
- 외부 데이터는 `commercial_use: allowed`인 출처만 운영에 쓴다. 위협 피드는 탐지일이 `max_age_days`(90일, 가설값) 이내인 항목만 쓰고, 기간 안 항목이 없으면 미설정으로 둔다.
- 메인 서버는 의심 URL에 접속하지 않는다(RDAP 도메인 조회만). 접속은 격리 수집기(`fetch`·`browser`)만 한다.
- 점수는 −100 ~ 100 정수. 증거 묶음은 감점으로 깎이지 않는다. hard = `threat_feed`·`reported_before`·`lookalike_domain`. 가중치를 바꾸면 새 버전 파일을 만든다.
- 판정은 위험·주의·확인 필요·위험 신호 미발견 4단계. 응답에 "안전"이라는 말을 쓰지 않는다.
- 전환 판단은 라벨된 표본으로 한다. urlscan 일치율은 참고값이다.
- 시간 예산: RDAP 2초, 2단계 3초, 3단계 6초(대기열 포함), 수집기 왕복 10초, URL당 20초(그림자 실행).
- 브라우저: 1개, 동시 2페이지, 50페이지마다 재시작, 이미지·폰트·미디어 차단, 다운로드는 즉시 취소.
- 테스트는 실제 외부 호출을 하지 않는다(`MockTransport`, 가짜 수집기·렌더러·파이프라인).
- 허용 목록 항목은 출처·확인 날짜가 필수이고, 공용 플랫폼 도메인은 금지(기동 실패).
- 확인 실패 코드는 정해진 목록만 쓴다: `fetch`, `fetch_timeout`, `blocked_address`, `redirect_limit`, `browser`, `browser_timeout`, `html_truncated`, `page_inspect`, `collect`, `threat_feed`, `domain_age`, `message`, `url_limit`, `deadline`, `investigate`.
- 비교 API는 토큰이 없거나 틀리면 404다.

## Review Focus

1. 호스트 정규화와 등록 도메인 비교: 문자열 포함 검사로 공식 도메인을 판단하는 곳이 없는지
2. RDAP가 늦거나 실패해도 로컬 hard 신호가 남는지, 허용 목록·공용 플랫폼·IP는 RDAP를 부르지 않는지
3. 증거 묶음이 평판 감점으로 깎이지 않는지 (점수 합산식)
4. 근거 없는 결과가 신호 미발견으로 나오지 않는지: 허용 경로 체인 + 위험 신호 없음 + 확인 실패 없음일 때만
5. 내부 주소 차단: IP 리터럴, DNS 결과, 리다이렉트 홉마다, 요청을 보내기 전에
6. 시간 초과 때 이미 찾은 증거(리다이렉트 경로, 브라우저 다운로드)가 유지되는지
7. 단축 URL: 공식 조회 경로로 끝나면 신호 미발견이 가능한지, 도메인 변경과 이중 계산하지 않는지
8. 그림자 실행 실패가 기존 urlscan 응답을 깨지 않는지, 비교 API가 토큰 없이 열리지 않는지
9. 전환 판단이 urlscan 일치율이 아니라 라벨 표본으로 이뤄지는지, 탐지일이 오래되거나 없는 위협 피드 항목이 hard 신호로 쓰이지 않는지

## File Structure

경로는 `backend/src/server/` 기준이다 (테스트와 `main.py` 제외).

| 파일 | 책임 | Task |
| --- | --- | --- |
| `scanner/__init__.py` | 패키지 | 1 |
| `scanner/scorer.py`, `scanner/weights/v1.yaml` | 두 묶음 점수와 가중치 | 1 |
| `scanner/urls.py` | URL·호스트 도우미, 내부 주소 판별 | 2 |
| `scanner/allowlist.py`, `data/whitelist.yaml` | 허용 목록 (택배사·공식 도메인·허용 경로) | 2 |
| `scanner/data/url_lists.yaml` | 남용 TLD·단축 URL·공용 플랫폼 | 2 |
| `scanner/url_features.py`, `scanner/data/reported_domains.txt` | 1단계 URL·도메인 특징 | 3 |
| `scanner/sync_lists.py`, `scanner/data/sources.yaml` | 외부 목록 동기화 | 4 |
| `scanner/fetch.py` | 2단계 httpx 수집 | 5 |
| `scanner/browser.py` | 3단계 브라우저 | 6 |
| `scanner/collector.py`, `scanner/collector_app.py` | 격리 수집기 (클라이언트·서버) | 6 |
| `scanner/page_features.py` | 페이지 특징 | 7 |
| `scanner/rules.py` | 4단계 판정 규칙 | 8 |
| `scanner/investigate.py` | URL 조사·캐시·마감 | 8 |
| `scanner/graph.py` | LangGraph 파이프라인·응답 문구 | 9 |
| `scanner/compare.py` | 비교 기록·전환 기준 | 10 |
| `main.py` | 그림자 실행 연결·비교 API | 11 |
| `backend/tests/scanner/test_*.py` | 단위 테스트 (외부 호출 없음) | 1–11 |

### Task 1: 두 묶음 점수기와 가중치 v1

**Files:**

- Create: `backend/src/server/scanner/__init__.py`
- Create: `backend/src/server/scanner/scorer.py`
- Create: `backend/src/server/scanner/weights/v1.yaml`
- Test: `backend/tests/scanner/test_scorer.py`

**Interfaces:**

- Consumes: 없음
- Produces:
  - `EVIDENCE_CODES` (11개), `REPUTATION_CODES` (7개), `FEATURE_CODES = EVIDENCE_CODES | REPUTATION_CODES`
  - `@dataclass(frozen=True) class Weights: version: str; evidence: dict[str, int]; reputation: dict[str, int]; hard: frozenset[str]; danger_threshold: int; compare_threshold: int` + `points` (두 묶음을 합친 dict)
  - `@dataclass(frozen=True) class ScoreResult: score: int; evidence: int; reputation: int; hits: dict[str, int]; unknown: tuple[str, ...]`
  - `load_weights(path: Path = WEIGHTS_DIR / "v1.yaml") -> Weights` — 코드 누락·미등록, 정수 아님, 증거 가중치 0 이하, 증거 묶음 밖의 hard, 범위 밖 기준값이면 `ValueError`
  - `score(features: dict[str, bool | None], weights: Weights) -> ScoreResult`

점수 = 증거 합 + 평판 합이고, 증거가 있으면 평판 합은 0 밑으로 내려가지 않는다. 그래서 인기·오래된 도메인 감점이 앱 다운로드·입력 폼 같은 증거를 깎지 못한다. 미확인(`None`) 특징은 0점으로 계산하고 `unknown`에 남긴다. `unverified_domain`은 확인 상태라 0점이다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_scorer.py`:

```python
from pathlib import Path

import pytest

from backend.src.server.scanner.scorer import (
    EVIDENCE_CODES, FEATURE_CODES, REPUTATION_CODES, Weights, load_weights, score,
)

V1 = Path(__file__).parents[2] / "src/server/scanner/weights/v1.yaml"


def all_false() -> dict[str, bool | None]:
    return {code: False for code in FEATURE_CODES}


def test_v1_loads_groups_hard_and_thresholds():
    weights = load_weights()
    assert weights.version == "v1"
    assert set(weights.evidence) == EVIDENCE_CODES and set(weights.reputation) == REPUTATION_CODES
    assert weights.hard == {"threat_feed", "reported_before", "lookalike_domain"}
    assert weights.reputation["unverified_domain"] == 0
    assert (weights.danger_threshold, weights.compare_threshold) == (50, 50)


def test_no_hits_scores_zero():
    result = score(all_false(), load_weights())
    assert (result.score, result.hits, result.unknown) == (0, {}, ())


def test_evidence_and_reputation_are_added():
    result = score(all_false() | {"new_domain": True, "input_form": True}, load_weights())
    assert (result.score, result.evidence, result.reputation) == (80, 40, 40)


def test_negative_reputation_cannot_offset_evidence():
    result = score(all_false() | {"app_download": True, "popular_domain": True, "old_domain": True}, load_weights())
    assert (result.score, result.evidence, result.reputation) == (50, 50, -50)


def test_negative_reputation_offsets_reputation_only():
    assert score(all_false() | {"abused_tld": True, "popular_domain": True}, load_weights()).score == -15
    assert score(all_false() | {"old_domain": True, "popular_domain": True}, load_weights()).score == -50


def test_score_is_clamped_to_range():
    assert score(all_false() | {"threat_feed": True, "app_download": True}, load_weights()).score == 100
    low = Weights("t", {code: 1 for code in EVIDENCE_CODES},
                  {code: 0 for code in REPUTATION_CODES} | {"old_domain": -80, "popular_domain": -80},
                  frozenset(), 50, 50)
    assert score(all_false() | {"old_domain": True, "popular_domain": True}, low).score == -100


def test_unknown_feature_counts_as_zero_and_is_listed():
    result = score(all_false() | {"input_form": None, "new_domain": True}, load_weights())
    assert result.score == 40
    assert result.unknown == ("input_form",)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "w.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("old, new, message", [
    ("  url_shortener: 15\n", "  url_shortener: 15\n  bogus_code: 5\n", "unknown"),
    ("  threat_feed: 100\n", "  threat_feed: 1.5\n", "integer"),
    ("  brand_on_page: 30\n", "  brand_on_page: -5\n", "positive"),
    ("hard: [threat_feed, reported_before, lookalike_domain]", "hard: [new_domain]", "hard"),
    ("danger_threshold: 50", "danger_threshold: 150", "thresholds"),
])
def test_load_rejects_invalid_files(tmp_path, old, new, message):
    text = V1.read_text(encoding="utf-8")
    assert old in text
    with pytest.raises(ValueError, match=message):
        load_weights(write(tmp_path, text.replace(old, new)))
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_scorer.py -q` Expected: FAIL — `ModuleNotFoundError: No module named 'backend.src.server.scanner'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/__init__.py`: 빈 파일.

`backend/src/server/scanner/weights/v1.yaml`:

```yaml
# 초기 가설값. 값을 바꾸면 v2.yaml 을 새로 만든다 (기록된 점수·판정과 버전을 맞추기 위해).
version: v1
# 회색지대에서 이 점수 이상이면 위험. 점수가 최종 판정에 반영되는 비율은 이 값과 가중치로 조정한다.
danger_threshold: 50
# urlscan 일치율 계산에만 쓰는 악성 기준 (wire 에 싣지 않는다).
compare_threshold: 50
# 단독으로 위험을 확정하는 특징 (증거 묶음에 있어야 한다).
hard: [threat_feed, reported_before, lookalike_domain]
# 증거 묶음: 공격의 흔적. 모두 양수이고 감점으로 깎이지 않는다.
evidence:
  threat_feed: 100
  reported_before: 50
  lookalike_domain: 30
  brand_mismatch: 40
  risky_action: 30
  input_form: 40
  app_download: 50
  other_download: 20
  cross_domain_form: 25
  brand_on_page: 30
  cross_domain_redirect: 20
# 평판 묶음: 주소의 성격. 감점은 이 묶음 안에서만 작동하고, 증거가 있으면 이 묶음의 합은 0 밑으로 내려가지 않는다.
reputation:
  new_domain: 40
  old_domain: -20
  popular_domain: -30
  ip_or_port: 30
  abused_tld: 15
  url_shortener: 15
  unverified_domain: 0  # 확인 상태라 위험 점수에 더하지 않는다. 신호 미발견 차단은 허용 경로 조건이 맡는다
```

`backend/src/server/scanner/scorer.py`:

```python
"""특징 → 점수. I/O 없는 결정적 함수라 같은 특징이면 항상 같은 점수가 나온다.

점수 = 증거 합 + 평판 합. 증거가 있으면 평판 합은 0 밑으로 내려가지 않아 감점이 증거를 깎지 못한다.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

WEIGHTS_DIR = Path(__file__).parent / "weights"
EVIDENCE_CODES = frozenset({
    "threat_feed", "reported_before", "lookalike_domain", "brand_mismatch", "risky_action", "input_form",
    "app_download", "other_download", "cross_domain_form", "brand_on_page", "cross_domain_redirect",
})
REPUTATION_CODES = frozenset({
    "new_domain", "old_domain", "popular_domain", "ip_or_port", "abused_tld", "url_shortener", "unverified_domain",
})
FEATURE_CODES = EVIDENCE_CODES | REPUTATION_CODES


@dataclass(frozen=True)
class Weights:
    version: str
    evidence: dict[str, int]
    reputation: dict[str, int]
    hard: frozenset[str]
    danger_threshold: int
    compare_threshold: int

    @property
    def points(self) -> dict[str, int]:
        return self.evidence | self.reputation


@dataclass(frozen=True)
class ScoreResult:
    score: int
    evidence: int  # 증거 묶음 합
    reputation: int  # 평판 묶음 합 (0 하한 적용 전)
    hits: dict[str, int]
    unknown: tuple[str, ...]  # 확인하지 못해 0으로 계산한 특징


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be integer")
    return value


def _group(data: dict, key: str, codes: frozenset[str]) -> dict[str, int]:
    group = data.get(key) or {}
    unknown = set(group) - codes
    if unknown:
        raise ValueError(f"unknown {key} codes: {sorted(unknown)}")
    missing = codes - set(group)
    if missing:
        raise ValueError(f"missing {key} codes: {sorted(missing)}")
    return {code: _integer(value, f"{key}.{code}") for code, value in group.items()}


def load_weights(path: Path = WEIGHTS_DIR / "v1.yaml") -> Weights:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    evidence = _group(data, "evidence", EVIDENCE_CODES)
    reputation = _group(data, "reputation", REPUTATION_CODES)
    not_positive = sorted(code for code, value in evidence.items() if value <= 0)
    if not_positive:
        raise ValueError(f"evidence points must be positive: {not_positive}")
    hard = frozenset(data["hard"])
    if not hard <= EVIDENCE_CODES:
        raise ValueError(f"hard codes must be evidence codes: {sorted(hard - EVIDENCE_CODES)}")
    danger = _integer(data["danger_threshold"], "danger_threshold")
    compare = _integer(data["compare_threshold"], "compare_threshold")
    if not (-100 <= danger <= 100 and -100 <= compare <= 100):
        raise ValueError("thresholds must be within [-100, 100]")
    return Weights(str(data["version"]), evidence, reputation, hard, danger, compare)


def score(features: dict[str, bool | None], weights: Weights) -> ScoreResult:
    points = weights.points
    hits = {code: points[code] for code in sorted(FEATURE_CODES) if features.get(code) is True}
    evidence = sum(value for code, value in hits.items() if code in EVIDENCE_CODES)
    reputation = sum(value for code, value in hits.items() if code in REPUTATION_CODES)
    total = evidence + (max(reputation, 0) if evidence > 0 else reputation)
    unknown = tuple(sorted(code for code in FEATURE_CODES if features.get(code) is None))
    return ScoreResult(max(-100, min(100, total)), evidence, reputation, hits, unknown)
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_scorer.py -q` Expected: 12 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/__init__.py backend/src/server/scanner/scorer.py backend/src/server/scanner/weights/v1.yaml backend/tests/scanner/test_scorer.py
git commit -m "feat: 두 묶음 점수기와 가중치 v1"
```

### Task 2: URL 도우미와 허용 목록 (도메인 + 허용 경로)

**Files:**

- Create: `backend/src/server/scanner/urls.py`
- Create: `backend/src/server/scanner/allowlist.py`
- Create: `backend/src/server/scanner/data/url_lists.yaml`
- Modify: `backend/src/server/data/whitelist.yaml` (상단 주석, 택배사별 `aliases`·`tokens` 키)
- Test: `backend/tests/scanner/test_allowlist.py`

**Interfaces:**

- Consumes: 없음
- Produces:
  - `urls.py`: `hostname(url) -> str` (소문자, 포트·끝 점 제거, 없으면 `""`), `is_ip(host) -> bool`, `is_blocked_ip(value) -> bool` (사설·루프백·링크 로컬·예약·멀티캐스트, IPv4 매핑 포함), `registrable_domain(host) -> str` (IP는 그대로), `url_path(url) -> str`, `has_redirect_param(url) -> bool`, `REDIRECT_PARAMS`
  - `allowlist.py`: `OfficialDomain(domain, paths)`, `Brand(name, aliases, tokens, domains)` + `domain_names`, `Allowlist(brands)` + `official_domains()`, `find_brand(name)`, `brand_of(url)`, `allows(url)`
  - `load_allowlist(path=WHITELIST_PATH) -> Allowlist` — 각 도메인 항목은 `{domain, paths, source, verified_at}`, 하나라도 비거나 경로가 `/`로 시작하지 않으면 `ValueError`
  - `validate_allowlist(allow, shared_hosts: frozenset[str]) -> None` — 공유 도메인이 있으면 `ValueError`

`allows(url)`는 등록 도메인이 공식이고, 경로가 허용 경로 접두어(경로 단위 경계)이며, 리다이렉트 파라미터가 없을 때만 `True`다. 서브도메인은 등록 도메인으로 묶여 같은 허용 경로를 따른다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_allowlist.py`:

```python
import pytest

from backend.src.server.scanner.allowlist import Allowlist, Brand, OfficialDomain, load_allowlist, validate_allowlist
from backend.src.server.scanner.urls import (
    has_redirect_param, hostname, is_blocked_ip, is_ip, registrable_domain,
)

ALLOW = Allowlist((Brand("테스트택배", frozenset({"테스트"}), frozenset({"parcela"}),
                         (OfficialDomain("parcel-a.co.kr", ("/tracking", "/delivery/status/")),)),))


def test_hostname_normalizes_case_trailing_dot_and_port():
    assert hostname("https://Delivery-Example.COM.:443/a") == "delivery-example.com"
    assert hostname("not a url") == ""
    assert hostname("http://[::1") == ""


def test_registrable_domain_handles_korean_second_level():
    assert registrable_domain("a.b.example.co.kr") == "example.co.kr"
    assert registrable_domain("login.example.com") == "example.com"
    assert registrable_domain("192.0.2.1") == "192.0.2.1"


def test_ip_checks():
    assert is_ip("192.0.2.1") and is_ip("::1") and not is_ip("example.com")
    for blocked in ("10.0.0.1", "127.0.0.1", "169.254.169.254", "192.168.1.1", "::1", "::ffff:10.0.0.1"):
        assert is_blocked_ip(blocked), blocked
    assert not is_blocked_ip("8.8.8.8")
    assert not is_blocked_ip("example.com")


def test_redirect_param():
    assert has_redirect_param("https://parcel-a.co.kr/go?url=https://evil.xyz")
    assert has_redirect_param("https://parcel-a.co.kr/tracking?Next=/x")
    assert not has_redirect_param("https://parcel-a.co.kr/tracking?invoice=123")


def test_allows_needs_official_domain_allowed_path_and_no_redirect_param():
    assert ALLOW.allows("https://www.parcel-a.co.kr/tracking?invoice=1")
    assert ALLOW.allows("https://parcel-a.co.kr/tracking/123")
    assert ALLOW.allows("https://parcel-a.co.kr/delivery/status/9")
    assert not ALLOW.allows("https://parcel-a.co.kr/tracking-evil")
    assert not ALLOW.allows("https://parcel-a.co.kr/board/upload/a.apk")
    assert not ALLOW.allows("https://parcel-a.co.kr/tracking?redirect=https://evil.xyz")
    assert not ALLOW.allows("https://parcel-a.co.kr.evil.xyz/tracking")


def test_find_brand_and_brand_of():
    assert ALLOW.find_brand("테스트택배").name == "테스트택배"
    assert ALLOW.find_brand(" 테스트 ").name == "테스트택배"
    assert ALLOW.find_brand("다른택배") is None
    assert ALLOW.brand_of("https://m.parcel-a.co.kr/x").name == "테스트택배"
    assert ALLOW.brand_of("https://evil.xyz/") is None


def write_whitelist(tmp_path, domains_yaml: str):
    path = tmp_path / "whitelist.yaml"
    path.write_text("test:\n  display_name: 테스트택배\n  aliases: [테스트]\n  tokens: [parcela]\n"
                    "  domains:\n" + domains_yaml, encoding="utf-8")
    return path


def test_load_allowlist_reads_entries(tmp_path):
    path = write_whitelist(tmp_path, (
        "    - domain: Parcel-A.co.kr\n"
        "      paths: [/tracking]\n"
        "      source: 공식 홈페이지 고객센터 안내\n"
        "      verified_at: 2026-10-01\n"
    ))
    allow = load_allowlist(path)
    assert allow.official_domains() == {"parcel-a.co.kr"}
    assert allow.brands[0].tokens == {"parcela"}


@pytest.mark.parametrize("entry, missing", [
    ("    - domain: parcel-a.co.kr\n      paths: [/tracking]\n      verified_at: 2026-10-01\n", "source"),
    ("    - domain: parcel-a.co.kr\n      paths: [/tracking]\n      source: 앱\n", "verified_at"),
    ("    - domain: parcel-a.co.kr\n      source: 앱\n      verified_at: 2026-10-01\n", "paths"),
    ("    - domain: parcel-a.co.kr\n      paths: [tracking]\n      source: 앱\n      verified_at: 2026-10-01\n", "paths"),
])
def test_load_allowlist_requires_evidence_and_paths(tmp_path, entry, missing):
    with pytest.raises(ValueError, match=missing):
        load_allowlist(write_whitelist(tmp_path, entry))


def test_validate_rejects_shared_hosts():
    shared = Allowlist((Brand("t", frozenset(), frozenset(), (OfficialDomain("naver.me", ("/",)),)),))
    with pytest.raises(ValueError, match="naver.me"):
        validate_allowlist(shared, frozenset({"naver.me", "bit.ly"}))
    validate_allowlist(ALLOW, frozenset({"naver.me"}))


def test_shipped_whitelist_loads():
    names = {brand.name for brand in load_allowlist().brands}
    assert "CJ대한통운" in names
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_allowlist.py -q` Expected: FAIL — `ModuleNotFoundError: No module named 'backend.src.server.scanner.allowlist'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/urls.py`:

```python
"""URL 문자열 도우미. 네트워크를 쓰지 않는다."""

import ipaddress
from urllib.parse import parse_qsl, urlsplit

# ponytail: 공개 접미사 목록 대신 자주 쓰는 2단계 접미사만. 오판이 보이면 publicsuffix 목록으로 교체.
_TWO_LEVEL_SUFFIXES = frozenset({
    "co.kr", "or.kr", "go.kr", "ne.kr", "re.kr", "pe.kr", "ac.kr", "co.uk", "com.cn",
})
REDIRECT_PARAMS = frozenset({
    "url", "redirect", "redirect_url", "redirect_uri", "next", "return", "returnurl",
    "return_to", "continue", "target", "dest", "destination", "goto",
})


def hostname(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host.rstrip(".")


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def is_blocked_ip(value: str) -> bool:
    """사설·루프백·링크 로컬(메타데이터 169.254.169.254 포함)·예약·멀티캐스트 주소."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return not address.is_global or address.is_multicast


def registrable_domain(host: str) -> str:
    if not host or is_ip(host):
        return host
    labels = host.split(".")
    size = 3 if ".".join(labels[-2:]) in _TWO_LEVEL_SUFFIXES else 2
    return ".".join(labels[-size:])


def url_path(url: str) -> str:
    try:
        return urlsplit(url).path or "/"
    except ValueError:
        return "/"


def has_redirect_param(url: str) -> bool:
    try:
        query = urlsplit(url).query
    except ValueError:
        return False
    return any(key.lower() in REDIRECT_PARAMS for key, _ in parse_qsl(query, keep_blank_values=True))
```

`backend/src/server/scanner/allowlist.py`:

```python
"""공식 택배사 허용 목록: 등록 도메인 + 허용 경로. 사람이 확인한 근거가 없는 항목은 인정하지 않는다."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from backend.src.server.scanner.urls import has_redirect_param, hostname, registrable_domain, url_path

WHITELIST_PATH = Path(__file__).parents[1] / "data" / "whitelist.yaml"


@dataclass(frozen=True)
class OfficialDomain:
    domain: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Brand:
    name: str
    aliases: frozenset[str]
    tokens: frozenset[str]  # 사칭 탐지용 도메인 토큰 (예: cjlogistics)
    domains: tuple[OfficialDomain, ...]

    @property
    def domain_names(self) -> frozenset[str]:
        return frozenset(entry.domain for entry in self.domains)


def _path_allowed(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p if p.endswith("/") else p + "/") for p in prefixes)


@dataclass(frozen=True)
class Allowlist:
    brands: tuple[Brand, ...]

    def official_domains(self) -> frozenset[str]:
        return frozenset().union(*(brand.domain_names for brand in self.brands))

    def find_brand(self, name: str) -> Brand | None:
        key = name.strip().lower()
        return next((b for b in self.brands if key == b.name.lower() or key in b.aliases), None)

    def brand_of(self, url: str) -> Brand | None:
        domain = registrable_domain(hostname(url))
        return next((b for b in self.brands if domain in b.domain_names), None)

    def allows(self, url: str) -> bool:
        """등록 도메인이 공식이고, 경로가 허용 경로이며, 리다이렉트 파라미터가 없을 때만 True."""
        domain = registrable_domain(hostname(url))
        if not domain or has_redirect_param(url):
            return False
        path = url_path(url)
        return any(
            entry.domain == domain and _path_allowed(path, entry.paths)
            for brand in self.brands for entry in brand.domains
        )


def _text(fields: dict, key: str, entry: object) -> str:
    value = str(fields.get(key) or "").strip()
    if not value:
        raise ValueError(f"whitelist entry needs {key}: {entry!r}")
    return value


def _official_domain(entry: object) -> OfficialDomain:
    fields = entry if isinstance(entry, dict) else {}
    domain = _text(fields, "domain", entry).lower()
    _text(fields, "source", entry)
    _text(fields, "verified_at", entry)
    paths = fields.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p.startswith("/") for p in paths):
        raise ValueError(f"whitelist entry needs paths starting with '/': {entry!r}")
    return OfficialDomain(domain, tuple(paths))


def load_allowlist(path: Path = WHITELIST_PATH) -> Allowlist:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Allowlist(tuple(
        Brand(
            name=str(entry["display_name"]),
            aliases=frozenset(str(a).strip().lower() for a in entry.get("aliases") or []),
            tokens=frozenset(str(t).strip().lower() for t in entry.get("tokens") or []),
            domains=tuple(_official_domain(item) for item in entry.get("domains") or []),
        )
        for entry in data.values()
    ))


def validate_allowlist(allow: Allowlist, shared_hosts: frozenset[str]) -> None:
    """공유 도메인(단축 URL·공용 플랫폼)이 공식으로 등록되면 공격자 페이지도 공식이 되므로 기동을 막는다."""
    banned = sorted(domain for domain in allow.official_domains() if domain in shared_hosts)
    if banned:
        raise ValueError(f"shared domains cannot be official: {banned}")
```

`backend/src/server/scanner/data/url_lists.yaml`:

```yaml
# 초기 목록. 출처·갱신 주기는 스펙 §7 열린 질문 — 비교 기간에 적중률을 보고 조정한다.
abused_tlds: [xyz, top, shop, click, icu, cyou, buzz, rest, vip, live, sbs, cfd]
shorteners: [bit.ly, han.gl, me2.do, vo.la, t.co, tinyurl.com, is.gd, url.kr, buly.kr, c11.kr, naver.me]
# 누구나 페이지·링크를 만들 수 있는 공용 플랫폼. 허용 목록에 넣으면 공격자 페이지도 "공식"이 된다.
shared_hosts: [naver.me, kakao.com, naver.com, google.com, forms.gle, notion.site, github.io,
               blogspot.com, tistory.com, wixsite.com, netlify.app, vercel.app, web.app, pages.dev]
```

`backend/src/server/data/whitelist.yaml` 상단 주석을 다음으로 바꾸고, 택배사 항목마다 `aliases: []`·`tokens: []` 키를 추가한다 (`display_name`과 `domains: []`는 그대로 두고, 값은 선행 작업에서 채운다):

```yaml
# 공식 택배사 허용 목록. 판정의 근거이므로 backend 소유입니다.
# 등록 도메인 단위로 적습니다 (www.cjlogistics.com → cjlogistics.com). 서브도메인은 같은 허용 경로를 따릅니다.
# 공유 도메인(naver.me, bit.ly, kakao.com 등)은 금지 — 서버가 기동하지 않습니다. 최종 URL 대조로 처리됩니다.
# aliases: 문자 속 택배사 이름 표기 (예: CJ, 대한통운). tokens: 사칭 탐지용 도메인 토큰 (택배사 고유 문자열만).
# 항목 형식:
#   domains:
#     - domain: example-parcel.com
#       paths: [/tracking]                   # 허용 경로 접두어 (직접 확인한 조회 페이지만)
#       source: 공식 앱 고객센터 안내 화면     # 사람이 확인한 출처
#       verified_at: 2026-10-01               # 확인 날짜
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_allowlist.py -q` Expected: 13 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/urls.py backend/src/server/scanner/allowlist.py backend/src/server/scanner/data/url_lists.yaml backend/src/server/data/whitelist.yaml backend/tests/scanner/test_allowlist.py
git commit -m "feat: URL 도우미와 도메인·경로 허용 목록"
```

### Task 3: 1단계 URL·도메인 특징

**Files:**

- Create: `backend/src/server/scanner/url_features.py`
- Create: `backend/src/server/scanner/data/reported_domains.txt`
- Test: `backend/tests/scanner/test_url_features.py`

**Interfaces:**

- Consumes: Task 2 `Allowlist`, `hostname`, `is_ip`, `registrable_domain`
- Produces:
  - `URL_FEATURE_CODES` (10개), `DATA_DIR`, `RDAP_TIMEOUT_SECONDS = 2.0`
  - `UrlLists(abused_tlds, shorteners, shared_hosts)` + `shared` (단축 URL ∪ 공용 플랫폼), `load_url_lists(path)`
  - `LocalSets(threat_feed, popular, reported, threat_feed_error=False)`, `load_local_sets(data_dir)` — 피드 파일이 없으면 `threat_feed=None`(미설정), 있는데 못 읽으면 `threat_feed_error=True`
  - `parse_url_features(url, lists, allow, popular) -> dict[str, bool]` — 키 `ip_or_port`, `lookalike_domain`, `abused_tld`, `url_shortener`, `unverified_domain`, `popular_domain`
  - `async lookup_domain_age_days(domain, client, *, now) -> int | None` (성공만 1일 캐시), `domain_age_features(days)`
  - `@dataclass(frozen=True) class UrlStage: features: dict[str, bool | None]; failures: tuple[str, ...]; domain_age_days: int | None`
  - `async collect_url_features(url, *, client, lists, sets, allow, now, rdap_timeout=RDAP_TIMEOUT_SECONDS) -> UrlStage`
  - `combine_url_features(input_features, final_features, hard) -> dict` — hard는 어느 쪽이든 걸리면 걸림, 단축 URL은 입력 기준, 나머지는 최종 URL 기준

로컬 특징(사칭·위협 피드·신고 이력 등)을 먼저 확정하고, RDAP만 `asyncio.wait_for`로 따로 제한한다. RDAP가 늦거나 실패해도 이미 찾은 hard 신호는 남는다. IP·허용 목록·공용 플랫폼(단축 URL 포함)은 도메인 나이가 "해당 없음"이라 RDAP를 부르지 않는다. 공용 플랫폼은 인기 도메인 감점을 받지 않고, 사칭 판단의 인기 예외도 받지 않는다(서브도메인을 누구나 만들 수 있어서). 위협 피드 미설정은 URL 확인 실패가 아니라 시스템 상태(Task 8의 `ScanContext.degraded`)이고, 파일이 있는데 못 읽을 때만 `threat_feed` 실패다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_url_features.py`:

```python
import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from backend.src.server.scanner import url_features
from backend.src.server.scanner.allowlist import Allowlist, Brand, OfficialDomain
from backend.src.server.scanner.url_features import (
    URL_FEATURE_CODES, LocalSets, UrlLists, collect_url_features, combine_url_features,
    domain_age_features, load_local_sets, load_url_lists, lookup_domain_age_days, parse_url_features,
)

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
LISTS = UrlLists(abused_tlds=frozenset({"xyz"}), shorteners=frozenset({"bit.ly"}),
                 shared_hosts=frozenset({"notion.site"}))
ALLOW = Allowlist((Brand("CJ대한통운", frozenset({"cj"}), frozenset({"cjlogistics"}),
                         (OfficialDomain("cjlogistics.com", ("/tracking",)),)),))
EMPTY = LocalSets(frozenset(), frozenset(), frozenset())


@pytest.fixture(autouse=True)
def clear_age_cache():
    url_features._AGE_CACHE.clear()
    yield
    url_features._AGE_CACHE.clear()


def parse(url: str, popular: frozenset[str] = frozenset()) -> dict[str, bool]:
    return parse_url_features(url, LISTS, ALLOW, popular)


def test_ip_or_port():
    assert parse("http://192.0.2.1/x")["ip_or_port"]
    assert parse("https://example.com:8080/")["ip_or_port"]
    assert parse("https://example.com:99999/")["ip_or_port"]
    assert not parse("https://example.com:443/")["ip_or_port"]
    assert not parse("https://example.com/")["ip_or_port"]


def test_lookalike_by_punycode_similarity_or_token():
    assert parse("https://xn--80ak6aa92e.com/")["lookalike_domain"]
    assert parse("https://cjlogistlcs.com/")["lookalike_domain"]
    assert parse("https://cjlogistics.com.evil.xyz/tracking")["lookalike_domain"]
    assert parse("https://my-cjlogistics-kr.top/")["lookalike_domain"]
    assert not parse("https://cjlogistics.com/tracking")["lookalike_domain"]
    assert not parse("https://naver.com/")["lookalike_domain"]
    assert not parse("http://192.0.2.1/")["lookalike_domain"]


def test_popular_domain_is_not_lookalike_but_shared_hosts_are_not_exempt():
    assert not parse("https://cjlogistics-mall.com/", frozenset({"cjlogistics-mall.com"}))["lookalike_domain"]
    shared = parse("https://cjlogistics-notice.notion.site/", frozenset({"notion.site"}))
    assert shared["lookalike_domain"] and not shared["popular_domain"]


def test_tld_shortener_unverified_popular():
    assert parse("https://parcel.xyz/")["abused_tld"]
    assert not parse("http://192.0.2.1/")["abused_tld"]
    assert parse("https://bit.ly/abc")["url_shortener"]
    assert parse("https://evil.xyz/")["unverified_domain"]
    assert not parse("https://www.cjlogistics.com/x")["unverified_domain"]
    assert parse("https://naver.com/", frozenset({"naver.com"}))["popular_domain"]


def test_shipped_url_lists_load():
    lists = load_url_lists()
    assert "xyz" in lists.abused_tlds and "bit.ly" in lists.shorteners
    assert "naver.me" in lists.shared and "notion.site" in lists.shared


def client_returning(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def registered(date: str):
    return lambda request: httpx.Response(200, json={"events": [
        {"eventAction": "last changed", "eventDate": "2026-09-01T00:00:00Z"},
        {"eventAction": "registration", "eventDate": date},
    ]})


def age(handler) -> int | None:
    async def run():
        async with client_returning(handler) as client:
            return await lookup_domain_age_days("example.com", client, now=NOW)
    return asyncio.run(run())


def test_age_from_registration_event():
    assert age(registered("2026-09-21T00:00:00Z")) == 10
    url_features._AGE_CACHE.clear()
    assert age(registered("2026-09-21")) == 10


def test_age_unknown_on_bad_responses():
    assert age(lambda r: httpx.Response(404)) is None
    assert age(lambda r: httpx.Response(200, json={"events": []})) is None
    assert age(lambda r: httpx.Response(200, json=[1, 2])) is None
    assert age(lambda r: httpx.Response(200, text="<html>")) is None
    assert age(registered("not-a-date")) is None


def test_age_unknown_on_http_timeout():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    assert age(slow) is None


def test_age_is_cached_after_success():
    calls = []

    def handler(request):
        calls.append(request)
        return registered("2026-09-21T00:00:00Z")(request)

    assert age(handler) == 10 and age(handler) == 10
    assert len(calls) == 1


def test_domain_age_features():
    assert domain_age_features(None) == {"new_domain": None, "old_domain": None}
    assert domain_age_features(10) == {"new_domain": True, "old_domain": False}
    assert domain_age_features(800) == {"new_domain": False, "old_domain": True}


def test_load_local_sets_separates_missing_feed_from_broken_feed(tmp_path):
    (tmp_path / "tranco_top100k.txt").write_text("naver.com\n", encoding="utf-8")
    (tmp_path / "reported_domains.txt").write_text("# header\n\nEvil.XYZ\n", encoding="utf-8")
    missing = load_local_sets(tmp_path)
    assert missing.threat_feed is None and not missing.threat_feed_error
    assert missing.popular == {"naver.com"} and missing.reported == {"evil.xyz"}
    (tmp_path / "threat_feed.txt").write_bytes(b"\xff\xfe\x00bad")
    assert load_local_sets(tmp_path).threat_feed_error


def collect(url: str, sets: LocalSets, handler=registered("2026-09-21T00:00:00Z"), rdap_timeout: float = 2.0):
    async def run():
        async with client_returning(handler) as client:
            return await collect_url_features(url, client=client, lists=LISTS, sets=sets, allow=ALLOW, now=NOW,
                                              rdap_timeout=rdap_timeout)
    return asyncio.run(run())


def test_collect_all_codes_age_days_and_feed_match_on_host_or_domain():
    sets = LocalSets(frozenset({"evil.xyz"}), frozenset(), frozenset({"reported.com"}))
    stage = collect("https://login.evil.xyz/a", sets)
    assert set(stage.features) == set(URL_FEATURE_CODES)
    assert stage.features["threat_feed"] is True and stage.features["new_domain"] is True
    assert (stage.failures, stage.domain_age_days) == ((), 10)
    assert collect("https://reported.com/", sets).features["reported_before"] is True


def test_missing_feed_is_not_a_failure_but_broken_feed_is():
    stage = collect("https://example.com/", LocalSets(None, frozenset(), frozenset()))
    assert stage.features["threat_feed"] is None and stage.failures == ()
    broken = collect("https://example.com/", LocalSets(None, frozenset(), frozenset(), threat_feed_error=True))
    assert broken.failures == ("threat_feed",)


def test_rdap_failure_is_marked():
    stage = collect("https://example.com/", EMPTY, lambda request: httpx.Response(503))
    assert stage.features["new_domain"] is None and stage.failures == ("domain_age",)


def test_slow_rdap_keeps_local_hard_signals():
    async def slow(request):
        await asyncio.sleep(1)
        return registered("2026-09-21T00:00:00Z")(request)

    stage = collect("https://cjlogistlcs.com/", EMPTY, slow, rdap_timeout=0.05)
    assert stage.features["lookalike_domain"] is True
    assert stage.features["new_domain"] is None and stage.failures == ("domain_age",)


def test_collect_skips_rdap_for_ip_official_and_shared_domains():
    def fail(request):
        raise AssertionError("RDAP must not be called")
    for url in ("http://192.0.2.1/", "https://www.cjlogistics.com/tracking", "https://bit.ly/abc",
                "https://someone.notion.site/page"):
        stage = collect(url, EMPTY, fail)
        assert stage.features["new_domain"] is False and stage.features["old_domain"] is False
        assert stage.failures == () and stage.domain_age_days is None


def test_combine_url_features():
    hard = frozenset({"threat_feed", "lookalike_domain"})
    first = {"threat_feed": False, "lookalike_domain": True, "url_shortener": True, "new_domain": False}
    final = {"threat_feed": None, "lookalike_domain": False, "url_shortener": False, "new_domain": True}
    combined = combine_url_features(first, final, hard)
    assert combined["lookalike_domain"] is True
    assert combined["threat_feed"] is None
    assert combined["url_shortener"] is True and combined["new_domain"] is True
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_url_features.py -q` Expected: FAIL — `ModuleNotFoundError: No module named 'backend.src.server.scanner.url_features'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/data/reported_domains.txt`:

```text
# 자체 신고로 확인된 스미싱 도메인. 한 줄에 하나. 비교 기간에 smishing 으로 라벨된 도메인을 사람이 확인해 추가한다.
```

`backend/src/server/scanner/url_features.py`:

```python
"""1단계: URL·도메인 특징. 의심 URL에 접속하지 않고 문자열·로컬 목록·RDAP로만 판단한다.

로컬 특징(사칭·위협 피드·신고 이력 등)을 먼저 확정하고, 느릴 수 있는 RDAP만 따로 시간 제한을 둔다.
RDAP가 늦어도 이미 찾은 신호는 버리지 않는다.
"""

import asyncio
import difflib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

from backend.src.server.scanner.allowlist import Allowlist
from backend.src.server.scanner.urls import hostname, is_ip, registrable_domain

DATA_DIR = Path(__file__).parent / "data"
URL_FEATURE_CODES = (
    "threat_feed", "reported_before", "lookalike_domain", "new_domain", "old_domain",
    "popular_domain", "ip_or_port", "abused_tld", "url_shortener", "unverified_domain",
)
RDAP_URL = "https://rdap.org/domain/{domain}"
RDAP_TIMEOUT_SECONDS = 2.0
AGE_CACHE_TTL = timedelta(days=1)
# ponytail: 프로세스 메모리 캐시라 재시작 시 비워진다. 조회 실패는 저장하지 않는다.
_AGE_CACHE: dict[str, tuple[datetime, datetime]] = {}


@dataclass(frozen=True)
class UrlLists:
    abused_tlds: frozenset[str]
    shorteners: frozenset[str]
    shared_hosts: frozenset[str] = frozenset()

    @property
    def shared(self) -> frozenset[str]:
        """누구나 페이지·링크를 만들 수 있는 도메인. 도메인 나이·인기도가 그 페이지의 성격을 말해주지 않는다."""
        return self.shorteners | self.shared_hosts


def load_url_lists(path: Path = DATA_DIR / "url_lists.yaml") -> UrlLists:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return UrlLists(
        frozenset(data["abused_tlds"]), frozenset(data["shorteners"]),
        frozenset(data.get("shared_hosts") or []),
    )


@dataclass(frozen=True)
class LocalSets:
    threat_feed: frozenset[str] | None  # None = 쓸 수 있는 피드 없음
    popular: frozenset[str]
    reported: frozenset[str]
    threat_feed_error: bool = False  # 파일은 있는데 못 읽음 = URL 확인 실패. 파일이 없으면 미설정 = 시스템 상태


def _read_set(path: Path) -> frozenset[str] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    return frozenset(line.strip().lower() for line in lines if line.strip() and not line.startswith("#"))


def load_local_sets(data_dir: Path = DATA_DIR) -> LocalSets:
    feed_path = data_dir / "threat_feed.txt"
    feed = _read_set(feed_path)
    return LocalSets(
        threat_feed=feed,
        popular=_read_set(data_dir / "tranco_top100k.txt") or frozenset(),
        reported=_read_set(data_dir / "reported_domains.txt") or frozenset(),
        threat_feed_error=feed is None and feed_path.exists(),
    )


def parse_url_features(url: str, lists: UrlLists, allow: Allowlist, popular: frozenset[str]) -> dict[str, bool]:
    host = hostname(url)
    ip = is_ip(host)
    try:
        port = urlsplit(url).port
    except ValueError:
        port = -1  # 잘못된 포트도 비표준으로 본다
    domain = registrable_domain(host)
    shared = domain in lists.shared
    official = allow.official_domains()
    is_official = domain in official
    is_popular = domain in popular and not shared  # 공용 플랫폼은 인기 도메인 감점을 받지 않는다
    similar = any(
        domain != item and difflib.SequenceMatcher(None, domain, item).ratio() >= 0.8 for item in official
    )
    punycode = any(label.startswith("xn--") for label in host.split("."))
    token = any(t in host for brand in allow.brands for t in brand.tokens)
    return {
        "ip_or_port": ip or port not in (None, 80, 443),
        # 인기 도메인은 사칭으로 보지 않는다 (택배사 이름이 들어간 정상 서비스 오탐 방지). 공용 플랫폼은 예외 없음.
        "lookalike_domain": not ip and not is_official and not is_popular and (punycode or similar or token),
        "abused_tld": not ip and host.rsplit(".", 1)[-1] in lists.abused_tlds,
        "url_shortener": domain in lists.shorteners,
        "unverified_domain": not is_official,
        "popular_domain": is_popular,
    }


async def _registration_date(domain: str, client: httpx.AsyncClient) -> datetime | None:
    try:
        response = await client.get(RDAP_URL.format(domain=domain), timeout=RDAP_TIMEOUT_SECONDS,
                                    follow_redirects=True)
        if response.status_code != 200:
            return None
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    events = data.get("events") if isinstance(data, dict) else None
    for event in events if isinstance(events, list) else []:
        if isinstance(event, dict) and event.get("eventAction") == "registration":
            try:
                created = datetime.fromisoformat(str(event.get("eventDate")).replace("Z", "+00:00"))
            except ValueError:
                return None
            return created if created.tzinfo else created.replace(tzinfo=timezone.utc)
    return None


async def lookup_domain_age_days(domain: str, client: httpx.AsyncClient, *, now: datetime) -> int | None:
    cached = _AGE_CACHE.get(domain)
    if cached and cached[0] > now:
        created = cached[1]
    else:
        created = await _registration_date(domain, client)
        if created is None:
            return None
        _AGE_CACHE[domain] = (now + AGE_CACHE_TTL, created)
    return (now - created).days


def domain_age_features(days: int | None) -> dict[str, bool | None]:
    if days is None:
        return {"new_domain": None, "old_domain": None}
    return {"new_domain": days <= 30, "old_domain": days >= 730}


@dataclass(frozen=True)
class UrlStage:
    features: dict[str, bool | None]
    failures: tuple[str, ...]
    domain_age_days: int | None  # 구간·가중치 조정용 기록


async def collect_url_features(
    url: str, *, client: httpx.AsyncClient, lists: UrlLists, sets: LocalSets, allow: Allowlist, now: datetime,
    rdap_timeout: float = RDAP_TIMEOUT_SECONDS,
) -> UrlStage:
    host = hostname(url)
    domain = registrable_domain(host)
    # 1) 로컬 특징을 먼저 확정한다. 아래 RDAP가 늦거나 실패해도 이 값은 남는다.
    features: dict[str, bool | None] = dict(parse_url_features(url, lists, allow, sets.popular))
    failures: list[str] = []
    if sets.threat_feed is None:
        features["threat_feed"] = None
        if sets.threat_feed_error:
            failures.append("threat_feed")
    else:
        features["threat_feed"] = host in sets.threat_feed or domain in sets.threat_feed
    features["reported_before"] = host in sets.reported or domain in sets.reported
    # 2) 도메인 나이. IP·허용 목록·공용 플랫폼은 "해당 없음"이라 조회하지 않는다.
    if is_ip(host) or domain in allow.official_domains() or domain in lists.shared:
        features.update(new_domain=False, old_domain=False)
        return UrlStage(features, tuple(failures), None)
    try:
        days = await asyncio.wait_for(lookup_domain_age_days(domain, client, now=now), max(rdap_timeout, 0.001))
    except TimeoutError:
        days = None
    features.update(domain_age_features(days))
    if days is None:
        failures.append("domain_age")
    return UrlStage(features, tuple(failures), days)


def combine_url_features(
    input_features: dict[str, bool | None], final_features: dict[str, bool | None], hard: frozenset[str],
) -> dict[str, bool | None]:
    """입력·최종 URL 특징을 합친다. hard는 어느 쪽이든 걸리면 걸림, 단축 URL은 입력 기준, 나머지는 최종 기준."""
    combined = dict(final_features)
    for code in hard:
        values = (input_features.get(code), final_features.get(code))
        combined[code] = True if True in values else (None if None in values else False)
    combined["url_shortener"] = input_features.get("url_shortener")
    return combined
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_url_features.py backend/tests/scanner/test_allowlist.py -q` Expected: 30 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/url_features.py backend/src/server/scanner/data/reported_domains.txt backend/tests/scanner/test_url_features.py
git commit -m "feat: 1단계 URL·도메인 특징 (로컬 특징 우선, RDAP 별도 제한)"
```

### Task 4: 라이선스 검사를 거치는 목록 동기화 CLI

기존 계획의 블랙리스트 출처 방식을 쓰되, 탐지일 열이 있는 출처는 `max_age_days`보다 오래된 항목을 버린다. 오래된 피싱 도메인은 만료·재등록됐을 수 있어 hard 신호로 쓰면 오탐이 된다. 기간 안 항목이 0건이면 빈 파일 대신 파일을 지워 위협 피드 미설정(시스템 상태)으로 둔다. 빈 목록은 "확인했지만 없음"으로 읽히기 때문이다. `hostname`은 `urls.py`에서 가져온다.

**Files:**

- Create: `backend/src/server/scanner/sync_lists.py`
- Create: `backend/src/server/scanner/data/sources.yaml`
- Modify: `.gitignore` (동기화 산출물 제외)
- Test: `backend/tests/scanner/test_sync_lists.py`

**Interfaces:**

- Consumes: Task 2 `hostname`, Task 3 `DATA_DIR`
- Produces:
  - `Source(name, url, format, commercial_use, column, date_column, max_age_days)`, `load_sources(path) -> tuple[list[Source], list[Source]]`
  - `usable(source, *, include_unverified) -> bool`, `decode_text(bytes) -> str`
  - `feed_domains(text)`, `csv_domains(text, column, *, date_column="", max_age_days=0, today=None)` — 기간 제한이 있으면 탐지일이 없거나 기간 밖인 행은 버림, `parse_date(value)`, `tranco_domains(zip_bytes, limit)`
  - CLI `python -m backend.src.server.scanner.sync_lists [--include-unverified]` → `threat_feed.txt`, `tranco_top100k.txt` (기간 안 항목이 0건이면 파일 삭제 = 미설정)

* [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_sync_lists.py`:

```python
import io
import zipfile
from datetime import date

import httpx

from backend.src.server.scanner import sync_lists
from backend.src.server.scanner.sync_lists import (
    Source, csv_domains, decode_text, feed_domains, load_sources, tranco_domains, usable,
)


def test_feed_domains_accepts_urls_and_bare_domains():
    text = "https://Login.Evil.xyz/a?b=1\\n\\n# c\\nplain.example.com\\nhttps://login.evil.xyz/other\\n"
    assert feed_domains(text) == ["login.evil.xyz", "plain.example.com"]


def test_csv_domains_reads_named_column():
    text = "번호,홈페이지주소,등록일\\n1,https://evil.xyz/a,2026-09-01\\n2,,2026-09-02\\n3,bad.example.com,2026-09-03\\n"
    assert csv_domains(text, "홈페이지주소") == ["bad.example.com", "evil.xyz"]


def test_csv_domains_drops_old_or_undated_rows_when_age_limited():
    text = "날짜,홈페이지주소\\n20231231,https://old.example/\\n20260920,https://fresh.example/a\\n,https://nodate.example/\\n"
    recent = csv_domains(text, "홈페이지주소", date_column="날짜", max_age_days=90, today=date(2026, 10, 2))
    assert recent == ["fresh.example"]
    assert csv_domains(text, "홈페이지주소") == ["fresh.example", "nodate.example", "old.example"]


def test_sync_removes_feed_when_no_recent_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_lists, "DATA_DIR", tmp_path)
    source = Source("kisa", "https://feed.example/kisa.csv", "csv", "allowed", "홈페이지주소", "날짜", 90)

    def run(body: str) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body.encode("utf-8")))
        with httpx.Client(transport=transport) as client:
            sync_lists._sync(client, [source], "threat_feed.txt", False, today=date(2026, 10, 2))

    run("날짜,홈페이지주소\\n20260920,https://fresh.example/a\\n")
    assert (tmp_path / "threat_feed.txt").read_text(encoding="utf-8") == "fresh.example\\n"
    run("날짜,홈페이지주소\\n20231231,https://old.example/\\n")
    assert not (tmp_path / "threat_feed.txt").exists()  # 빈 목록 대신 미설정


def test_decode_text_handles_utf8_bom_and_cp949():
    assert decode_text("\\ufeff홈페이지주소".encode("utf-8")) == "홈페이지주소"
    assert decode_text("홈페이지주소".encode("cp949")) == "홈페이지주소"


def test_tranco_domains_reads_rank_csv_in_order():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("top-1m.csv", "1,google.com\\n2,naver.com\\n3,daum.net\\n")
    assert tranco_domains(buffer.getvalue(), limit=2) == ["google.com", "naver.com"]


def test_usable_follows_license_state():
    def source(state: str, url: str = "https://example.com/feed") -> Source:
        return Source("s", url, "lines", state)

    assert usable(source("allowed"), include_unverified=False)
    assert not usable(source("unverified"), include_unverified=False)
    assert usable(source("unverified"), include_unverified=True)
    assert not usable(source("prohibited"), include_unverified=True)
    assert not usable(source("allowed", url=""), include_unverified=True)


def test_shipped_sources_block_non_commercial_feeds():
    threat, popular = load_sources()
    states = {s.name: s.commercial_use for s in threat + popular}
    assert states["openphish_community"] == "prohibited"
    assert states["kisa_phishing_urls"] == "allowed"
    assert states["tranco_custom"] == "unverified"
    kisa = next(s for s in threat if s.name == "kisa_phishing_urls")
    assert (kisa.column, kisa.date_column, kisa.max_age_days) == ("홈페이지주소", "날짜", 90)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_sync_lists.py -q` Expected: FAIL — `ModuleNotFoundError: No module named 'backend.src.server.scanner.sync_lists'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/data/sources.yaml`:

```yaml
# 외부 데이터 출처와 라이선스 상태. 서비스를 판매할 예정이므로 상업적 사용이 확인된 출처만 운영에 쓴다.
# commercial_use: allowed(상업적 사용 확인) | unverified(확인 전) | prohibited(금지 확인)
# sync_lists 는 allowed 만 받는다. --include-unverified 는 로컬 실험용. prohibited 는 절대 받지 않는다.
# Google Safe Browsing 은 비상업 전용이라 목록에 두지 않는다 (상업용은 유료 Web Risk API).
threat_feeds:
  # 실시간 피드 후보는 아직 없다 (상업 이용 가능 여부와 국내 스미싱 포함 여부를 확인한 뒤 추가).
  - name: kisa_phishing_urls
    # 공공데이터포털 15109780. 이용허락범위 제한 없음 (2026-10-02 확인). 2023-12-31 기준 1회성 스냅샷(27,582행)이라
    # max_age_days 를 적용하면 현재 0건 → 위협 피드 미설정. 새 파일이 올라오면 그대로 반영된다.
    url: ""        # 파일 직접 다운로드 주소 확인 후 채운다
    format: csv
    column: 홈페이지주소   # 컬럼 정의서 기준. 실제 파일 헤더 확인
    date_column: 날짜     # 탐지일 (YYYYMMDD)
    max_age_days: 90     # 가설값: 이보다 오래된 피싱 도메인은 만료·재등록됐을 수 있다
    commercial_use: allowed
  - name: openphish_community
    url: https://openphish.com/feed.txt
    format: lines
    commercial_use: prohibited   # Community 피드는 상업적 사용 불가 (2026-09-29 공식 페이지 확인)
popular_domains:
  - name: tranco_custom
    url: ""        # 비상업 출처(Cloudflare Radar, CC BY-NC)를 뺀 Tranco 사용자 정의 목록 zip 주소
    format: tranco_zip
    commercial_use: unverified
```

`backend/src/server/scanner/sync_lists.py`:

```python
"""sources.yaml 에서 라이선스가 허용된 출처만 내려받아 scanner/data 에 쓴다.

실행: python -m backend.src.server.scanner.sync_lists [--include-unverified]
ponytail: 주기 실행은 OS 스케줄러(cron·작업 스케줄러)로 1시간마다. 서버는 기동 시에만 목록을 읽으므로
동기화 후 재시작해야 반영된다. 재시작 없이 반영해야 하면 파일 mtime 기반 재로딩을 추가.
"""

import argparse
import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import yaml

from backend.src.server.scanner.url_features import DATA_DIR
from backend.src.server.scanner.urls import hostname


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    format: str
    commercial_use: str
    column: str = ""
    date_column: str = ""  # 탐지일 열. 있으면 max_age_days 보다 오래된 항목은 버린다
    max_age_days: int = 0  # 0 = 기간 제한 없음


DATE_FORMATS = ("%Y%m%d", "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d")


def load_sources(path: Path = DATA_DIR / "sources.yaml") -> tuple[list[Source], list[Source]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    def parse(items: list[dict] | None) -> list[Source]:
        return [
            Source(item["name"], item.get("url") or "", item["format"], item["commercial_use"],
                   item.get("column") or "", item.get("date_column") or "", int(item.get("max_age_days") or 0))
            for item in items or []
        ]

    return parse(data.get("threat_feeds")), parse(data.get("popular_domains"))


def usable(source: Source, *, include_unverified: bool) -> bool:
    if not source.url or source.commercial_use == "prohibited":
        return False
    return source.commercial_use == "allowed" or (include_unverified and source.commercial_use == "unverified")


def decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp949")  # 공공데이터포털 CSV 는 CP949 인 경우가 많다


def _host(value: str) -> str:
    value = value.strip()
    return hostname(value if "://" in value else f"http://{value}") if value else ""


def feed_domains(text: str) -> list[str]:
    lines = (line for line in text.splitlines() if not line.strip().startswith("#"))
    return sorted({host for line in lines if (host := _host(line))})


def parse_date(value: str) -> date | None:
    value = value.strip()[:10]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def csv_domains(text: str, column: str, *, date_column: str = "", max_age_days: int = 0,
                today: date | None = None) -> list[str]:
    cutoff = (today or date.today()) - timedelta(days=max_age_days) if date_column and max_age_days else None
    hosts: set[str] = set()
    for row in csv.DictReader(io.StringIO(text)):
        if cutoff is not None:
            found = parse_date(row.get(date_column) or "")
            if found is None or found < cutoff:
                continue  # 오래된 피싱 도메인은 만료·재등록됐을 수 있어 hard 신호로 쓰지 않는다
        if host := _host(row.get(column) or ""):
            hosts.add(host)
    return sorted(hosts)


def tranco_domains(zip_bytes: bytes, limit: int = 100_000) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        rows = archive.read(archive.namelist()[0]).decode("utf-8").splitlines()
    return [row.split(",", 1)[1].strip().lower() for row in rows[:limit] if "," in row]


def _domains(client: httpx.Client, source: Source, today: date | None) -> list[str]:
    response = client.get(source.url)
    response.raise_for_status()
    if source.format == "tranco_zip":
        return tranco_domains(response.content)
    text = decode_text(response.content)
    if source.format == "csv":
        return csv_domains(text, source.column, date_column=source.date_column,
                           max_age_days=source.max_age_days, today=today)
    return feed_domains(text)


def _sync(client: httpx.Client, sources: list[Source], filename: str, include_unverified: bool, *,
          today: date | None = None) -> None:
    path = DATA_DIR / filename
    chosen = [s for s in sources if usable(s, include_unverified=include_unverified)]
    if not chosen:
        # 파일을 쓰지 않는다. 위협 피드 파일이 없으면 시스템 상태(degraded: threat_feed)로 기록된다.
        print(f"[SYNC] {filename}: 사용 가능한 출처 없음 (sources.yaml 의 commercial_use·url 확인)")
        return
    domains = sorted({d for source in chosen for d in _domains(client, source, today)})
    if not domains:
        # 기간 안의 항목이 없으면 빈 목록 대신 미설정으로 둔다 (빈 목록은 "확인했지만 없음"으로 읽힌다).
        path.unlink(missing_ok=True)
        print(f"[SYNC] {filename}: 기간 안의 항목 없음 → 파일 삭제 (미설정)")
        return
    path.write_text("\\n".join(domains) + "\\n", encoding="utf-8")
    print(f"[SYNC] {filename}: {len(domains)} domains from {[s.name for s in chosen]}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-unverified", action="store_true",
                        help="라이선스 확인 전 출처도 받는다 (로컬 실험용, 운영 금지)")
    args = parser.parse_args(argv)
    threat, popular = load_sources()
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        _sync(client, threat, "threat_feed.txt", args.include_unverified)
        _sync(client, popular, "tranco_top100k.txt", args.include_unverified)


if __name__ == "__main__":
    main()
```

`.gitignore`에 추가:

```text
backend/src/server/scanner/data/threat_feed.txt
backend/src/server/scanner/data/tranco_top100k.txt
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_sync_lists.py -q` Expected: 8 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/sync_lists.py backend/src/server/scanner/data/sources.yaml backend/tests/scanner/test_sync_lists.py .gitignore
git commit -m "feat: 라이선스·탐지일 검사를 거치는 목록 동기화"
```

### Task 5: 2단계 httpx 수집

**Files:**

- Create: `backend/src/server/scanner/fetch.py`
- Test: `backend/tests/scanner/test_fetch.py`

**Interfaces:**

- Consumes: Task 2 `hostname`, `is_ip`, `is_blocked_ip`
- Produces:
  - `@dataclass(frozen=True) class FetchResult: final_url: str; redirect_chain: tuple[str, ...]; status: int | None; html: str; html_truncated: bool; app_download: bool; other_download: bool; failure: str | None`
  - `failure` 값: `fetch`(접속 불가·HTTP 오류·http(s) 아님), `fetch_timeout`(예산 초과, 그때까지의 경로 유지), `blocked_address`(내부 주소), `redirect_limit`(5홉 초과), `http_status`(최종 응답 400 이상, HTML은 담음)
  - `async fetch(url, client, *, resolve=system_resolve, max_hops=MAX_HOPS, budget=FETCH_BUDGET_SECONDS) -> FetchResult`
  - `Resolver = Callable[[str], Awaitable[list[str]]]`, `async system_resolve(host)`, `async address_blocked(host, resolve) -> bool`
  - `classify_download(url, headers) -> tuple[bool, bool]` — (앱·실행 파일, 그 밖의 파일)
  - `meta_refresh_target(html) -> str | None`
  - `needs_browser(result) -> str | None` — `script_only` | `js_redirect` | `http_status`(403·404) | `None`
  - 상수 `MOBILE_HEADERS`, `MAX_HOPS = 5`, `HTML_LIMIT = 131_072`, `FETCH_BUDGET_SECONDS = 3.0`

리다이렉트는 `follow_redirects=False`로 직접 따라가며, 홉마다 요청 전에 호스트의 DNS 결과를 검사한다. 다운로드 응답은 헤더와 확장자만 보고 본문을 읽지 않는다. 3초 예산은 DNS 검사, 응답 헤더 대기, 본문 읽기에 모두 `asyncio.wait_for`로 건다. httpx의 `timeout`은 연결·읽기 단계별 제한이라 전체 시간을 보장하지 않기 때문이다. 예산을 넘기면 그때까지 따라간 리다이렉트 경로를 담아 `fetch_timeout`으로 돌려준다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_fetch.py`:

```python
import asyncio

import httpx

from backend.src.server.scanner.fetch import (
    HTML_LIMIT, FetchResult, classify_download, fetch, meta_refresh_target, needs_browser,
)

PUBLIC = "93.184.216.34"


async def resolve(host: str) -> list[str]:
    return {"internal.test": ["10.0.0.5"]}.get(host, [PUBLIC])


def run(url: str, routes: dict[str, httpx.Response], *, slow: frozenset[str] = frozenset(),
        budget: float = 3.0) -> tuple[FetchResult, list[str]]:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if str(request.url) in slow:
            await asyncio.sleep(1)
        if str(request.url) not in routes:
            raise httpx.ConnectError("unreachable", request=request)
        return routes[str(request.url)]

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch(url, client, resolve=resolve, budget=budget)

    return asyncio.run(go()), seen


def redirect(to: str) -> httpx.Response:
    return httpx.Response(302, headers={"location": to})


def page(html: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, headers={"content-type": "text/html; charset=utf-8"}, text=html)


def test_follows_redirects_manually_and_records_chain():
    result, _ = run("https://bit.ly/a", {
        "https://bit.ly/a": redirect("https://hop.example/b"),
        "https://hop.example/b": redirect("/c"),
        "https://hop.example/c": page("<p>hello</p>"),
    })
    assert result.final_url == "https://hop.example/c"
    assert result.redirect_chain == ("https://hop.example/b", "https://hop.example/c")
    assert (result.status, result.failure) == (200, None)
    assert "hello" in result.html


def test_redirect_limit():
    routes = {f"https://r.example/{i}": redirect(f"https://r.example/{i + 1}") for i in range(7)}
    result, _ = run("https://r.example/0", routes)
    assert result.failure == "redirect_limit"
    assert len(result.redirect_chain) == 6


def test_follows_meta_refresh():
    result, _ = run("https://a.example/", {
        "https://a.example/": page('<meta content="0; url=https://b.example/x" http-equiv="refresh">'),
        "https://b.example/x": page("<p>landing</p>"),
    })
    assert result.final_url == "https://b.example/x"
    assert result.redirect_chain == ("https://b.example/x",)


def test_blocks_internal_ip_literal_without_request():
    result, seen = run("http://169.254.169.254/latest/meta-data/", {})
    assert result.failure == "blocked_address"
    assert seen == []


def test_blocks_redirect_to_host_resolving_internal():
    result, seen = run("https://a.example/", {"https://a.example/": redirect("http://internal.test/admin")})
    assert result.failure == "blocked_address"
    assert result.final_url == "http://internal.test/admin"
    assert seen == ["https://a.example/"]


def test_detects_app_download_without_reading_page():
    apk = httpx.Response(200, headers={"content-type": "application/vnd.android.package-archive"}, content=b"PK")
    result, _ = run("https://evil.example/app", {"https://evil.example/app": apk})
    assert (result.app_download, result.other_download, result.html, result.failure) == (True, False, "", None)


def test_classify_download():
    assert classify_download("https://e.example/a.APK", {}) == (True, False)
    assert classify_download("https://e.example/d", {"content-disposition": "attachment; filename=\"update.apk\""}) == (True, False)
    assert classify_download("https://e.example/d", {"content-disposition": "attachment; filename*=UTF-8''%EC%95%B1.apk"}) == (True, False)
    assert classify_download("https://e.example/notice.pdf", {}) == (False, True)
    assert classify_download("https://e.example/d", {"content-disposition": "attachment"}) == (False, True)
    assert classify_download("https://e.example/", {"content-type": "text/html"}) == (False, False)


def test_truncates_html_over_limit():
    result, _ = run("https://big.example/", {"https://big.example/": page("a" * (HTML_LIMIT + 10))})
    assert result.html_truncated and len(result.html) == HTML_LIMIT


def test_http_status_and_connect_failures():
    result, _ = run("https://gone.example/", {"https://gone.example/": page("<p>Not Found</p>", 404)})
    assert (result.failure, result.status) == ("http_status", 404)
    assert "Not Found" in result.html
    assert run("https://down.example/", {})[0].failure == "fetch"
    assert run("ftp://files.example/a", {})[0].failure == "fetch"


def test_timeout_keeps_redirect_chain():
    result, _ = run("https://bit.ly/a", {
        "https://bit.ly/a": redirect("https://evil.example/pay"),
        "https://evil.example/pay": page("<p>late</p>"),
    }, slow=frozenset({"https://evil.example/pay"}), budget=0.2)
    assert result.failure == "fetch_timeout"
    assert result.redirect_chain == ("https://evil.example/pay",)
    assert result.final_url == "https://evil.example/pay"


def test_meta_refresh_target():
    assert meta_refresh_target('<META HTTP-EQUIV="Refresh" CONTENT="3;URL=\'/next\'">') == "/next"
    assert meta_refresh_target("<meta charset='utf-8'><p>x</p>") is None


def result_with(html: str = "", *, status: int | None = 200, failure: str | None = None,
                app: bool = False) -> FetchResult:
    return FetchResult("https://x.example/", (), status, html, False, app, False, failure)


def test_needs_browser_reasons():
    assert needs_browser(result_with("<div id=app></div><script src=/main.js></script>")) == "script_only"
    long_text = "<p>" + "배송 조회 안내 " * 40 + "</p>"
    assert needs_browser(result_with(long_text + "<script>window.location.href='https://evil.xyz'</script>")) == "js_redirect"
    assert needs_browser(result_with("<p>Forbidden</p>", status=403, failure="http_status")) == "http_status"
    assert needs_browser(result_with(long_text)) is None
    assert needs_browser(result_with("<p>error</p>", status=500, failure="http_status")) is None
    assert needs_browser(result_with(failure="fetch", status=None)) is None
    assert needs_browser(result_with(app=True)) is None
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_fetch.py -q` Expected: FAIL — `ModuleNotFoundError: No module named 'backend.src.server.scanner.fetch'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/fetch.py`:

```python
"""2단계: httpx 수집. 의심 URL에 접속하는 코드라 격리 수집기 안에서만 실행한다."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from backend.src.server.scanner.urls import hostname, is_blocked_ip, is_ip

# 국내 택배 스미싱은 아이폰·PC에 다른 페이지를 보여주는 경우가 있어 안드로이드 모바일로 접속한다.
MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; SM-S921N) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
MAX_HOPS = 5
HTML_LIMIT = 131_072
FETCH_BUDGET_SECONDS = 3.0
TEXT_MIN_CHARS = 200
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
APP_TYPES = frozenset({
    "application/vnd.android.package-archive", "application/x-msdownload",
    "application/x-msdos-program", "application/x-dosexec",
})
OTHER_TYPES = frozenset({
    "application/pdf", "application/zip", "application/x-zip-compressed",
    "application/vnd.rar", "application/x-rar-compressed", "application/x-7z-compressed",
})
APP_EXTENSIONS = (".apk", ".xapk", ".apks", ".exe", ".msi", ".dex")
OTHER_EXTENSIONS = (".pdf", ".zip", ".rar", ".7z", ".doc", ".docx", ".xls", ".xlsx", ".hwp")
JS_REDIRECT = re.compile(
    r"(?:window|document|top|self)\.location(?:\.href)?\s*=|location\.(?:replace|assign)\s*\(|location\.href\s*=",
    re.IGNORECASE,
)
Resolver = Callable[[str], Awaitable[list[str]]]


@dataclass(frozen=True)
class FetchResult:
    final_url: str
    redirect_chain: tuple[str, ...]  # 입력 이후 방문한 URL (마지막 = final_url)
    status: int | None
    html: str
    html_truncated: bool
    app_download: bool
    other_download: bool
    failure: str | None  # fetch | fetch_timeout | blocked_address | redirect_limit | http_status


async def system_resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    return [info[4][0] for info in infos]


async def address_blocked(host: str, resolve: Resolver) -> bool:
    if not host or host == "localhost":
        return True
    if is_ip(host):
        return is_blocked_ip(host)
    try:
        addresses = await resolve(host)
    except OSError:
        return False  # DNS 실패는 요청 단계에서 접속 실패(fetch)로 처리된다
    # ponytail: 검사와 실제 접속 사이 DNS 재바인딩은 막지 못한다. 수집기 컨테이너 egress 방화벽이 최종 방어선.
    return any(is_blocked_ip(address) for address in addresses)


def _filename(disposition: str) -> str:
    match = re.search(r"filename\*?\s*=\s*(?:utf-8'')?\"?([^\";]+)", disposition, re.IGNORECASE)
    return unquote(match.group(1)).strip().lower() if match else ""


def classify_download(url: str, headers: httpx.Headers | dict[str, str]) -> tuple[bool, bool]:
    """(앱·실행 파일 다운로드, 그 밖의 파일 다운로드). 파일 본문은 보지 않는다."""
    content_type = (headers.get("content-type") or "").split(";")[0].strip().lower()
    disposition = (headers.get("content-disposition") or "").lower()
    names = (urlsplit(url).path.lower(), _filename(disposition))
    app = content_type in APP_TYPES or any(name.endswith(APP_EXTENSIONS) for name in names)
    other = not app and (
        content_type in OTHER_TYPES or "attachment" in disposition
        or any(name.endswith(OTHER_EXTENSIONS) for name in names)
    )
    return app, other


class _MetaRefresh(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.target: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta" or self.target is not None:
            return
        values = {key.lower(): value or "" for key, value in attrs}
        if values.get("http-equiv", "").lower() != "refresh":
            return
        match = re.search(r"url\s*=\s*['\"]?([^'\"]+)", values.get("content", ""), re.IGNORECASE)
        if match:
            self.target = match.group(1).strip()


def meta_refresh_target(html: str) -> str | None:
    parser = _MetaRefresh()
    parser.feed(html)
    parser.close()
    return parser.target


class _Probe(HTMLParser):
    """보이는 텍스트 길이와 스크립트 내용을 센다 (3단계로 넘길지 판단용)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_chars = 0
        self.scripts = 0
        self.script_text: list[str] = []
        self._inside: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts += 1
            self._inside = tag
        elif tag in ("style", "noscript", "template"):
            self._inside = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self._inside:
            self._inside = None

    def handle_data(self, data: str) -> None:
        if self._inside == "script":
            self.script_text.append(data)
        elif self._inside is None:
            self.text_chars += len(data.strip())


def needs_browser(result: FetchResult) -> str | None:
    """3단계(브라우저)가 필요한 이유. 필요 없으면 None."""
    if result.app_download or result.other_download:
        return None
    if result.failure == "http_status":
        return "http_status" if result.status in (403, 404) else None  # 비공식 도메인의 403·404 = 클로킹 의심
    if result.failure is not None:
        return None
    probe = _Probe()
    probe.feed(result.html)
    probe.close()
    if probe.scripts and probe.text_chars < TEXT_MIN_CHARS:
        return "script_only"
    if JS_REDIRECT.search("".join(probe.script_text)):
        return "js_redirect"
    return None


async def _read_limited(response: httpx.Response, limit: int = HTML_LIMIT) -> tuple[bytes, bool]:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > limit:
            return bytes(body[:limit]), True
    return bytes(body), False


def _decode(body: bytes, encoding: str | None) -> str:
    try:
        return body.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _failed(url: str, chain: list[str], failure: str) -> FetchResult:
    return FetchResult(url, tuple(chain), None, "", False, False, False, failure)


async def fetch(
    url: str, client: httpx.AsyncClient, *, resolve: Resolver = system_resolve, max_hops: int = MAX_HOPS,
    budget: float = FETCH_BUDGET_SECONDS,
) -> FetchResult:
    """리다이렉트와 meta refresh를 직접 따라가며 홉마다 내부 주소를 검사한다. 다운로드 본문은 읽지 않는다.

    시간 예산을 넘기면 그때까지 따라간 리다이렉트 경로를 담아 fetch_timeout 으로 돌려준다.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget

    def remaining() -> float:
        return max(deadline - loop.time(), 0.001)

    chain: list[str] = []
    current = url
    try:
        for _ in range(max_hops + 1):
            if deadline <= loop.time():
                return _failed(current, chain, "fetch_timeout")
            if urlsplit(current).scheme not in ("http", "https"):
                return _failed(current, chain, "fetch")
            if await asyncio.wait_for(address_blocked(hostname(current), resolve), remaining()):
                return _failed(current, chain, "blocked_address")
            request = client.build_request("GET", current, headers=MOBILE_HEADERS, timeout=httpx.Timeout(remaining()))
            response = await asyncio.wait_for(client.send(request, stream=True, follow_redirects=False), remaining())
            try:
                location = response.headers.get("location")
                if response.status_code in REDIRECT_STATUSES and location:
                    current = urljoin(current, location)
                    chain.append(current)
                    continue
                app, other = classify_download(current, response.headers)
                if app or other:
                    return FetchResult(current, tuple(chain), response.status_code, "", False, app, other, None)
                body, truncated = await asyncio.wait_for(_read_limited(response), remaining())
                status, encoding = response.status_code, response.charset_encoding
            finally:
                await response.aclose()
            html = _decode(body, encoding)
            target = meta_refresh_target(html) if status < 400 else None
            if target:
                current = urljoin(current, target)
                chain.append(current)
                continue
            failure = "http_status" if status >= 400 else None
            return FetchResult(current, tuple(chain), status, html, truncated, False, False, failure)
    except (TimeoutError, httpx.TimeoutException):
        return _failed(current, chain, "fetch_timeout")
    except httpx.HTTPError:
        return _failed(current, chain, "fetch")
    return _failed(current, chain, "redirect_limit")
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_fetch.py -q` Expected: 12 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/fetch.py backend/tests/scanner/test_fetch.py
git commit -m "feat: 2단계 httpx 수집 (내부 주소 차단, 시간 예산)"
```

### Task 6: 3단계 브라우저와 격리 수집기

**Files:**

- Create: `backend/src/server/scanner/browser.py`
- Create: `backend/src/server/scanner/collector.py`
- Create: `backend/src/server/scanner/collector_app.py`
- Modify: 의존성 파일(`requirements.txt` 또는 `pyproject.toml`)에 `playwright` 추가
- Test: `backend/tests/scanner/test_collector.py`

**Interfaces:**

- Consumes: Task 2 `hostname`, `is_ip`, `is_blocked_ip`, Task 5 `fetch`, `needs_browser`, `Resolver`, `system_resolve`, `FETCH_BUDGET_SECONDS`, `APP_EXTENSIONS`, `HTML_LIMIT`
- Produces:
  - `browser.py`: `RenderResult(final_url, redirect_chain, html, html_truncated, app_download, other_download, timed_out=False)`, `should_block(resource_type, url) -> bool`, `download_kind(filename) -> tuple[bool, bool]`, `BrowserPool(*, max_pages=2, recycle_after=50)` + `start()`, `render(url, *, budget=BROWSER_BUDGET_SECONDS) -> RenderResult`, `close()`, `BROWSER_BUDGET_SECONDS = 6.0`
  - `collector.py`: `@dataclass(frozen=True) class CollectedPage: input_url; final_url; redirect_chain: tuple[str, ...]; html; html_truncated; app_download; other_download; used_browser: bool; browser_reason: str | None; stage2_html: str | None; failures: tuple[str, ...]; collected_at: str` — `failures` 값은 `fetch`, `fetch_timeout`, `blocked_address`, `redirect_limit`, `browser`, `browser_timeout`, `html_truncated`
  - `async collect(url, *, client, renderer, browser_allowed, resolve=system_resolve, now=None) -> CollectedPage`
  - `Collector = Callable[[str, bool], Awaitable[CollectedPage | None]]`, `HttpCollector(base_url, *, transport=None)`, `collector_not_connected`, `page_from_dict(data)`
  - `SAFETY_SECONDS = 1.0`, `COLLECT_TIMEOUT_SECONDS = 10.0` (2단계 3초 + 3단계 6초 + 여유 1초)
  - `collector_app.py`: FastAPI `app`, `POST /collect {url, browser_allowed}` → `CollectedPage` JSON

`collect`는 2단계를 돌리고, `browser_allowed`이고 `needs_browser`가 이유를 주면 입력 URL부터 브라우저로 다시 연다. `render`는 대기열에서 기다린 시간까지 6초 예산에 넣는다. 대기열이 예산을 넘기면 `TimeoutError`가 나서 `browser_timeout`을 남기고 2단계 결과로 계속한다. 렌더링 도중 예산을 넘기면 그때까지 본 화면과 다운로드를 `timed_out=True`로 돌려주고, 수집기는 그 증거를 유지한 채 `browser_timeout`을 남긴다(렌더링 화면이 비면 2단계 HTML로 검사). 크래시는 `browser`로 남기고 2단계 결과로 계속한다. 브라우저를 쓴 경우 2단계 HTML을 `stage2_html`로 함께 돌려줘, 브라우저가 판정을 바꿨는지 비교 기록에 남길 수 있게 한다. `BrowserPool`의 실제 렌더링은 실행 후 수동 확인에서 검증한다 (Playwright 1.56에서 API 이름과 `Pixel 7` 기기 설정은 확인함).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_collector.py`:

```python
import asyncio
from dataclasses import asdict

import httpx
from fastapi.testclient import TestClient

from backend.src.server.scanner import collector, collector_app
from backend.src.server.scanner.browser import RenderResult, download_kind, should_block
from backend.src.server.scanner.collector import CollectedPage, HttpCollector, collect, page_from_dict

SCRIPT_ONLY = "<div id=app></div><script src=/main.js></script>"
NORMAL = "<p>" + "배송 조회 안내 " * 40 + "</p>"


async def resolve(host: str) -> list[str]:
    return ["93.184.216.34"]


def page(html: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, headers={"content-type": "text/html; charset=utf-8"}, text=html)


def run(response: httpx.Response, *, renderer=None, browser_allowed: bool = True) -> CollectedPage:
    async def go():
        transport = httpx.MockTransport(lambda request: response)
        async with httpx.AsyncClient(transport=transport) as client:
            return await collect("https://x.example/", client=client, renderer=renderer,
                                 browser_allowed=browser_allowed, resolve=resolve)
    return asyncio.run(go())


def rendered(html: str = '<form><input type="password"></form>') -> RenderResult:
    return RenderResult("https://y.example/login", ("https://y.example/login",), html, False, False, False)


def test_normal_page_skips_browser():
    async def renderer(url):
        raise AssertionError("browser must not run")
    result = run(page(NORMAL), renderer=renderer)
    assert (result.used_browser, result.browser_reason, result.failures) == (False, None, ())
    assert result.final_url == "https://x.example/"


def test_script_only_page_uses_browser_and_keeps_stage2_html():
    async def renderer(url):
        return rendered()
    result = run(page(SCRIPT_ONLY), renderer=renderer)
    assert (result.used_browser, result.browser_reason) == (True, "script_only")
    assert result.final_url == "https://y.example/login"
    assert "password" in result.html and result.stage2_html == SCRIPT_ONLY
    assert result.failures == ()


def test_browser_not_allowed_or_missing():
    async def renderer(url):
        raise AssertionError("browser must not run")
    assert not run(page(SCRIPT_ONLY), renderer=renderer, browser_allowed=False).used_browser
    assert run(page(SCRIPT_ONLY), renderer=None).browser_reason is None


def test_browser_failure_falls_back_to_stage2():
    async def crashed(url):
        raise RuntimeError("crashed")

    async def queue_full(url):
        raise TimeoutError

    result = run(page(SCRIPT_ONLY), renderer=crashed)
    assert (result.used_browser, result.browser_reason, result.failures) == (False, "script_only", ("browser",))
    assert result.html == SCRIPT_ONLY
    assert run(page(SCRIPT_ONLY), renderer=queue_full).failures == ("browser_timeout",)


def test_browser_timeout_keeps_evidence_seen_so_far():
    async def renderer(url):
        return RenderResult("https://y.example/app", (), "", False, True, False, timed_out=True)
    result = run(page(SCRIPT_ONLY), renderer=renderer)
    assert result.used_browser and result.app_download
    assert result.html == SCRIPT_ONLY  # 렌더링 화면이 비면 2단계 HTML로 검사한다
    assert result.failures == ("browser_timeout",)


def test_cloaked_403_recovered_by_browser_else_fetch_failure():
    async def renderer(url):
        return rendered()
    assert run(page("<p>Forbidden</p>", 403), renderer=renderer).failures == ()
    assert run(page("<p>Forbidden</p>", 403), renderer=renderer, browser_allowed=False).failures == ("fetch",)


def test_truncated_html_is_a_failure():
    result = run(page("a" * 140_000))
    assert result.html_truncated and result.failures == ("html_truncated",)


def test_fetch_budget_timeout(monkeypatch):
    monkeypatch.setattr(collector, "FETCH_BUDGET_SECONDS", 0.05)

    async def slow(request):
        await asyncio.sleep(1)
        return page(NORMAL)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
            return await collect("https://x.example/", client=client, renderer=None, browser_allowed=False,
                                 resolve=resolve)

    assert asyncio.run(go()).failures == ("fetch_timeout",)


def sample_page() -> CollectedPage:
    return CollectedPage("https://x.example/", "https://y.example/", ("https://y.example/",), "<p>x</p>",
                         False, False, False, True, "js_redirect", "<p>s</p>", ("browser",), "2026-10-01T00:00:00+00:00")


def test_http_collector_round_trip_and_failure():
    sent = []

    def handler(request):
        sent.append(request.read())
        return httpx.Response(200, json=asdict(sample_page()))

    page_result = asyncio.run(HttpCollector("http://collector:8100/", transport=httpx.MockTransport(handler))(
        "https://x.example/", True))
    assert page_result == sample_page()
    assert b'"browser_allowed":true' in sent[0].replace(b" ", b"")
    broken = HttpCollector("http://collector:8100", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert asyncio.run(broken("https://x.example/", False)) is None


def test_collector_app_serializes_page(monkeypatch):
    async def fake_collect(url, **kwargs):
        assert kwargs["browser_allowed"] is True
        return sample_page()

    monkeypatch.setattr(collector_app, "collect", fake_collect)
    response = TestClient(collector_app.app).post("/collect", json={"url": "https://x.example/", "browser_allowed": True})
    assert response.status_code == 200
    assert page_from_dict(response.json()) == sample_page()


def test_browser_helpers():
    assert should_block("image", "https://x.example/a.png")
    assert should_block("xhr", "http://169.254.169.254/latest")
    assert should_block("document", "http://localhost:8000/")
    assert not should_block("document", "https://x.example/")
    assert not should_block("script", "http://93.184.216.34/app.js")
    assert download_kind("Update.APK") == (True, False)
    assert download_kind("notice.pdf") == (False, True)
    assert download_kind("") == (False, False)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_collector.py -q` Expected: FAIL — `ModuleNotFoundError: No module named 'backend.src.server.scanner.browser'`

- [ ] **Step 3: 구현**

`playwright`를 의존성 파일에 추가한다 (`pip install playwright`). 브라우저 바이너리(`playwright install chromium`)는 수집기 컨테이너에서만 설치한다. 단위 테스트는 바이너리 없이 돈다.

`backend/src/server/scanner/browser.py`:

```python
"""3단계: Playwright 렌더링. 2단계가 JS 의존 신호를 보일 때만 쓴다. 격리 수집기 안에서만 실행한다."""

import asyncio
from dataclasses import dataclass

from backend.src.server.scanner.fetch import APP_EXTENSIONS, HTML_LIMIT, OTHER_EXTENSIONS
from backend.src.server.scanner.urls import hostname, is_blocked_ip, is_ip

BLOCKED_RESOURCE_TYPES = frozenset({"image", "font", "media"})
DEVICE = "Pixel 7"
IDLE_WAIT_MS = 1_500
BROWSER_BUDGET_SECONDS = 6.0  # 대기열에서 기다린 시간도 포함
MAX_PAGES = 2       # t3.medium 기준 동시 페이지 상한 (가설값, 부하 측정으로 조정)
RECYCLE_AFTER = 50  # 메모리 누수 대비 브라우저 재시작 주기


@dataclass(frozen=True)
class RenderResult:
    final_url: str
    redirect_chain: tuple[str, ...]
    html: str
    html_truncated: bool
    app_download: bool
    other_download: bool
    timed_out: bool = False  # 예산 안에 다 못 열었지만, 그때까지 본 증거는 담는다


def should_block(resource_type: str, url: str) -> bool:
    """이미지·폰트·미디어와 내부 주소(IP 리터럴, localhost) 요청은 보내지 않는다."""
    if resource_type in BLOCKED_RESOURCE_TYPES:
        return True
    host = hostname(url)
    return host == "localhost" or (is_ip(host) and is_blocked_ip(host))


def download_kind(filename: str) -> tuple[bool, bool]:
    name = filename.strip().lower()
    app = name.endswith(APP_EXTENSIONS)
    return app, not app and bool(name)


def _truncate(html: str) -> tuple[str, bool]:
    encoded = html.encode("utf-8")
    if len(encoded) <= HTML_LIMIT:
        return html, False
    return encoded[:HTML_LIMIT].decode("utf-8", errors="ignore"), True


class BrowserPool:
    """웜 풀: 브라우저 1개를 띄워 두고 요청마다 새 컨텍스트를 연다. 속도용이며 메모리 절약용이 아니다."""

    def __init__(self, *, max_pages: int = MAX_PAGES, recycle_after: int = RECYCLE_AFTER) -> None:
        self._pages = asyncio.Semaphore(max_pages)
        self._lock = asyncio.Lock()
        self._recycle_after = recycle_after
        self._playwright = None
        self._browser = None
        self._used = 0
        self._active = 0

    async def start(self) -> None:
        async with self._lock:
            await self._launch()

    async def _launch(self) -> None:
        if self._playwright is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
        if self._browser is None:
            self._browser = await self._playwright.chromium.launch(args=["--disable-dev-shm-usage", "--disable-gpu"])
            self._used = 0

    async def _acquire(self):
        async with self._lock:
            if self._browser is not None and self._used >= self._recycle_after and self._active == 0:
                await self._browser.close()
                self._browser = None
            await self._launch()
            self._used += 1
            self._active += 1
            return self._browser

    async def render(self, url: str, *, budget: float = BROWSER_BUDGET_SECONDS) -> RenderResult:
        """대기열이 예산을 넘기면 TimeoutError. 렌더링 중 예산을 넘기면 그때까지 본 결과를 timed_out 으로 돌려준다."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget
        await asyncio.wait_for(self._pages.acquire(), budget)
        try:
            browser = await self._acquire()
            try:
                return await self._render(browser, url, deadline)
            finally:
                async with self._lock:
                    self._active -= 1
        finally:
            self._pages.release()

    async def _render(self, browser, url: str, deadline: float) -> RenderResult:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        loop = asyncio.get_running_loop()

        def remaining_ms() -> int:
            return max(int((deadline - loop.time()) * 1000), 1)

        devices = self._playwright.devices
        device = {k: v for k, v in (devices.get(DEVICE) or devices["Pixel 5"]).items() if k != "default_browser_type"}
        context = await browser.new_context(**device, locale="ko-KR", timezone_id="Asia/Seoul", accept_downloads=True)
        try:
            async def route(request_route) -> None:
                request = request_route.request
                if should_block(request.resource_type, request.url):
                    await request_route.abort()
                else:
                    await request_route.continue_()

            await context.route("**/*", route)
            page = await context.new_page()
            navigations: list[str] = []
            downloads = []
            page.on("framenavigated", lambda frame: navigations.append(frame.url) if frame == page.main_frame else None)
            page.on("download", downloads.append)
            hops: list[str] = []
            timed_out = False
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=remaining_ms())
                request = response.request if response else None
                while request is not None and request.redirected_from is not None:
                    request = request.redirected_from
                    hops.insert(0, request.url)
            except PlaywrightTimeout:
                timed_out = True  # 부분 화면과 다운로드 이벤트는 버리지 않는다
            except PlaywrightError:
                if not downloads:  # 다운로드로 시작한 이동은 goto 가 오류를 낸다
                    raise
            if not timed_out:
                try:
                    await page.wait_for_load_state("networkidle", timeout=min(IDLE_WAIT_MS, remaining_ms()))
                except PlaywrightTimeout:
                    pass  # 광고·추적 스크립트는 기다리지 않는다
            app = other = False
            for download in downloads:
                kind = download_kind(download.suggested_filename)
                app, other = app or kind[0], other or kind[1]
                await download.cancel()  # 파일은 실행하지도 보관하지도 않는다
            html, truncated = "", False
            if not downloads:
                try:
                    html, truncated = _truncate(await page.content())
                except PlaywrightError:
                    timed_out = True
            chain = [u for u in hops[1:] + navigations if u and u != "about:blank"]
            final_url = page.url if page.url != "about:blank" else url
            return RenderResult(final_url, tuple(dict.fromkeys(chain)), html, truncated, app, other, timed_out)
        finally:
            await context.close()

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
```

`backend/src/server/scanner/collector.py`:

```python
"""격리 수집기: 2단계 httpx → 필요할 때만 3단계 브라우저. 메인 서버는 HTTP로 결과만 받는다."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from backend.src.server.scanner.browser import BROWSER_BUDGET_SECONDS, RenderResult
from backend.src.server.scanner.fetch import FETCH_BUDGET_SECONDS, Resolver, fetch, needs_browser, system_resolve

SAFETY_SECONDS = 1.0  # 각 단계가 스스로 예산을 지키지 못했을 때를 위한 여유
COLLECT_TIMEOUT_SECONDS = FETCH_BUDGET_SECONDS + BROWSER_BUDGET_SECONDS + SAFETY_SECONDS  # 메인 서버 → 수집기 왕복 상한
Renderer = Callable[[str], Awaitable[RenderResult]]


@dataclass(frozen=True)
class CollectedPage:
    input_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    html: str
    html_truncated: bool
    app_download: bool
    other_download: bool
    used_browser: bool
    browser_reason: str | None
    stage2_html: str | None  # 브라우저를 썼을 때 2단계 HTML (브라우저가 판정을 바꿨는지 비교용)
    # fetch | fetch_timeout | blocked_address | redirect_limit | browser | browser_timeout | html_truncated
    failures: tuple[str, ...]
    collected_at: str


Collector = Callable[[str, bool], Awaitable[CollectedPage | None]]


async def collect(
    url: str, *, client: httpx.AsyncClient, renderer: Renderer | None, browser_allowed: bool,
    resolve: Resolver = system_resolve, now: datetime | None = None,
) -> CollectedPage:
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    try:
        fetched = await asyncio.wait_for(
            fetch(url, client, resolve=resolve, budget=FETCH_BUDGET_SECONDS), FETCH_BUDGET_SECONDS + SAFETY_SECONDS,
        )
    except TimeoutError:
        return CollectedPage(url, url, (), "", False, False, False, False, None, None, ("fetch_timeout",), stamp)
    reason = needs_browser(fetched) if browser_allowed and renderer is not None else None
    failures: list[str] = []
    if reason is not None:
        try:
            rendered = await asyncio.wait_for(renderer(url), BROWSER_BUDGET_SECONDS + SAFETY_SECONDS)
            failure = None
        except TimeoutError:  # 대기열이 꽉 찼거나 예산을 넘김
            rendered, failure = None, "browser_timeout"
        except Exception:  # 브라우저 크래시 등: 2단계 결과로 계속한다
            rendered, failure = None, "browser"
        if rendered is not None:
            found = [code for code, hit in (("browser_timeout", rendered.timed_out),
                                            ("html_truncated", rendered.html_truncated)) if hit]
            return CollectedPage(
                url, rendered.final_url, rendered.redirect_chain, rendered.html or fetched.html,
                rendered.html_truncated, rendered.app_download or fetched.app_download,
                rendered.other_download or fetched.other_download, True, reason, fetched.html, tuple(found), stamp,
            )
        failures.append(failure)
    if fetched.failure is not None:
        failures.insert(0, "fetch" if fetched.failure == "http_status" else fetched.failure)
    if fetched.html_truncated:
        failures.append("html_truncated")
    return CollectedPage(
        url, fetched.final_url, fetched.redirect_chain, fetched.html, fetched.html_truncated,
        fetched.app_download, fetched.other_download, False, reason, None, tuple(failures), stamp,
    )


def page_from_dict(data: dict) -> CollectedPage:
    return CollectedPage(**{
        **data, "redirect_chain": tuple(data["redirect_chain"]), "failures": tuple(data["failures"]),
    })


class HttpCollector:
    """메인 서버 쪽 클라이언트. 격리 수집기의 POST /collect 를 부른다."""

    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    async def __call__(self, url: str, browser_allowed: bool) -> CollectedPage | None:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=COLLECT_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{self._base_url}/collect", json={"url": url, "browser_allowed": browser_allowed},
                )
                response.raise_for_status()
                return page_from_dict(response.json())
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None


async def collector_not_connected(url: str, browser_allowed: bool) -> CollectedPage | None:
    """수집기 주소가 설정되지 않았을 때. 조사 단계가 check_failed: collect 로 처리한다."""
    return None
```

`backend/src/server/scanner/collector_app.py`:

```python
"""격리 수집기 서버. 수집기 컨테이너에서만 실행한다.

실행: uvicorn backend.src.server.scanner.collector_app:app --host 0.0.0.0 --port 8100
"""

from contextlib import asynccontextmanager
from dataclasses import asdict

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from backend.src.server.scanner.browser import BrowserPool
from backend.src.server.scanner.collector import collect
from backend.src.server.scanner.fetch import FETCH_BUDGET_SECONDS

POOL = BrowserPool()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await POOL.start()  # 웜 풀: 첫 요청 전에 브라우저를 띄워 둔다
    yield
    await POOL.close()


app = FastAPI(lifespan=lifespan)


class CollectRequest(BaseModel):
    url: str
    browser_allowed: bool = False


@app.post("/collect")
async def collect_endpoint(body: CollectRequest) -> dict:
    async with httpx.AsyncClient(timeout=FETCH_BUDGET_SECONDS) as client:
        page = await collect(body.url, client=client, renderer=POOL.render, browser_allowed=body.browser_allowed)
    return asdict(page)
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_collector.py -q` Expected: 11 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/browser.py backend/src/server/scanner/collector.py backend/src/server/scanner/collector_app.py backend/tests/scanner/test_collector.py requirements.txt
git commit -m "feat: 조건부 3단계 브라우저와 격리 수집기 (시간 초과 시 증거 유지)"
```

### Task 7: 페이지 특징 추출 (입력칸 판별)

**Files:**

- Create: `backend/src/server/scanner/page_features.py`
- Test: `backend/tests/scanner/test_page_features.py`

**Interfaces:**

- Consumes: Task 2 `Allowlist`, `hostname`, `registrable_domain`, Task 6 `CollectedPage`, 기존 `ai.page.inspect_html`, `ai.types.EnvDoubt`
- Produces:
  - `PAGE_FEATURE_CODES` (6개: `input_form`, `app_download`, `other_download`, `cross_domain_form`, `brand_on_page`, `cross_domain_redirect`)
  - `detect_input_form(fields, label_for) -> bool`
  - `extract_page_features(page, allow, *, shorteners=frozenset()) -> tuple[dict[str, bool | None], tuple[str, ...]]` — 실패 단계는 `page_inspect`뿐이다 (수집 단계 실패는 `page.failures`에 이미 있다)

입력칸(`input`·`select`·`textarea`, 폼 태그 밖 포함)마다 `name`·`id`·`placeholder`·`aria-label`·`autocomplete`·`title`과 연결된 `label` 글자(`for` 연결 또는 감싸는 라벨)를 모아 종류를 정한다. 민감 칸(비밀번호·카드·주민번호·생년월일·계좌·인증번호)이 하나라도 있거나, 연락처 칸(이름·전화·주소)이 두 종류 이상이면 `input_form`이다. 이메일 칸은 연락처로 세지 않는다("이메일 주소"가 주소로 잡히는 것 방지). `inspect_html`의 `LOGIN_FORM`도 입력 폼으로 친다. 단축 URL 경유는 `cross_domain_redirect`에서 뺀다(`url_shortener`와 이중 계산 방지). 다운로드 응답(HTML 없음)은 실패가 아니다. `brand_on_page`는 공식 도메인이 등록된 브랜드만 본다. `inspect_html`에 기대는 동작은 기존과 같다: 빈 입력은 `failure`, 128KiB 초과는 `INPUT_TOO_LARGE`, 인코딩 불가 입력은 `ValueError`, `.apk` 링크는 `APP_LINK`, `text`는 보이는 텍스트.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_page_features.py`:

```python
import time

import pytest

from backend.src.server.scanner.allowlist import Allowlist, Brand, OfficialDomain
from backend.src.server.scanner.collector import CollectedPage
from backend.src.server.scanner.page_features import PAGE_FEATURE_CODES, extract_page_features

ALLOW = Allowlist((
    Brand("CJ대한통운", frozenset({"대한통운"}), frozenset(), (OfficialDomain("cjlogistics.com", ("/tracking",)),)),
    Brand("우체국택배", frozenset(), frozenset(), ()),
))


def page(html: str, *, final: str = "https://evil.xyz/a", chain: tuple[str, ...] = (),
         app: bool = False, other: bool = False, start: str | None = None) -> CollectedPage:
    return CollectedPage(start or final, final, chain, html, False, app, other, False, None, None, (), "t")


def features(p: CollectedPage, shorteners: frozenset[str] = frozenset()):
    return extract_page_features(p, ALLOW, shorteners=shorteners)


def test_plain_page_has_no_hits():
    result, failures = features(page("<p>배송 안내</p>"))
    assert set(result) == set(PAGE_FEATURE_CODES)
    assert not any(result.values()) and failures == ()


@pytest.mark.parametrize("fields, expected", [
    ('<input type="password">', True),
    ('<input name="card_no" placeholder="카드번호 16자리">', True),
    ('<input autocomplete="one-time-code">', True),
    ('<label for="n">이름</label><input id="n"><label for="p">휴대폰 번호</label><input id="p">', True),
    ('<label>휴대폰 번호 <input type="text"></label><label>주소<input name="addr1"></label>', True),
    ('<input type="tel" name="phone">', False),
    ('<input name="name"><input placeholder="이메일 주소">', False),
    ('<input name="invoice" placeholder="송장번호">', False),
    ('<input type="hidden" name="password_hint"><input name="q">', False),
])
def test_input_form_detection(fields, expected):
    assert features(page(f"<p>본인 확인</p>{fields}"))[0]["input_form"] is expected


def test_cross_domain_form_action():
    assert features(page('<p>x</p><form action="https://collect.top/save"><input name=q></form>'))[0]["cross_domain_form"]
    assert not features(page('<p>x</p><form action="/save"><input name=q></form>'))[0]["cross_domain_form"]


def test_app_link_and_download_flags():
    assert features(page('<p>앱 설치</p><a href="/files/update.apk">설치</a>'))[0]["app_download"]
    assert features(page("<p>x</p>", other=True))[0]["other_download"]


def test_download_only_response_is_not_a_failure():
    result, failures = features(page("", app=True))
    assert result["app_download"] is True and result["input_form"] is False
    assert failures == ()


def test_brand_on_page_needs_known_official_domains():
    assert features(page("<p>CJ대한통운 배송 조회</p>"))[0]["brand_on_page"]
    assert features(page("<p>대한통운 고객님</p>"))[0]["brand_on_page"]
    assert not features(page("<p>CJ대한통운 배송 조회</p>", final="https://www.cjlogistics.com/tracking"))[0]["brand_on_page"]
    assert not features(page("<p>우체국택배 안내</p>"))[0]["brand_on_page"]


def test_cross_domain_redirect_ignores_shortener_hops():
    p = page("<p>x</p>", start="https://bit.ly/a", final="https://evil.xyz/a", chain=("https://evil.xyz/a",))
    assert features(p)[0]["cross_domain_redirect"]
    assert not features(p, frozenset({"bit.ly"}))[0]["cross_domain_redirect"]
    hop = page("<p>x</p>", start="https://bit.ly/a", final="https://evil.xyz/a",
               chain=("https://hop.example/b", "https://evil.xyz/a"))
    assert features(hop, frozenset({"bit.ly"}))[0]["cross_domain_redirect"]


def test_inspect_failure_marks_page_inspect():
    for html in ("", "<p>\ud800</p>", "a" * 140_000):
        result, failures = features(page(html, start="https://bit.ly/a"))
        assert failures == ("page_inspect",)
        assert result["input_form"] is None and result["cross_domain_redirect"] is True


def test_extraction_is_fast_on_large_html():
    html = "<p>" + "가" * 40_000 + "</p>" + '<form action="https://x.top/"><input type="password"></form>'
    started = time.perf_counter()
    result, _ = features(page(html))
    assert time.perf_counter() - started < 0.5
    assert result["input_form"]
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_page_features.py -q` Expected: FAIL — `ModuleNotFoundError: No module named 'backend.src.server.scanner.page_features'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/page_features.py`:

```python
"""수집 결과 → 페이지 특징. 수집 HTML은 신뢰하지 않는 데이터라 ai/page.py 의 검사 제한을 재사용한다."""

from html.parser import HTMLParser
from urllib.parse import urljoin

from ai.page import inspect_html
from ai.types import EnvDoubt

from backend.src.server.scanner.allowlist import Allowlist
from backend.src.server.scanner.collector import CollectedPage
from backend.src.server.scanner.urls import hostname, registrable_domain

PAGE_FEATURE_CODES = (
    "input_form", "app_download", "other_download", "cross_domain_form", "brand_on_page", "cross_domain_redirect",
)
SKIPPED_TYPES = frozenset({
    "hidden", "submit", "button", "reset", "image", "checkbox", "radio", "file", "search", "range", "color",
})
# 하나만 있어도 입력 폼으로 보는 칸: 비밀번호·카드·주민번호·생년월일·계좌·인증번호
SENSITIVE_HINTS = (
    "비밀번호", "password", "passwd", "카드", "card", "cc-number", "cc-exp", "cc-csc", "cvc", "cvv", "유효기간",
    "주민", "생년월일", "birth", "bday", "ssn", "계좌", "account", "인증번호", "otp", "one-time-code",
)
# 두 종류 이상 함께 있으면 입력 폼으로 보는 연락처 칸
CONTACT_HINTS = {
    "name": ("이름", "성명", "fullname", "full-name", "given-name", "family-name"),
    "phone": ("휴대폰", "휴대전화", "전화", "연락처", "phone", "mobile"),
    "address": ("주소", "우편번호", "address", "postcode", "postal", "zipcode"),
}


class _Fields(HTMLParser):
    """입력칸(input·select·textarea)과 라벨 글자를 모은다. 폼 태그 밖의 칸(JS 화면)도 센다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: list[dict[str, str]] = []
        self.actions: list[str] = []
        self.label_for: dict[str, str] = {}
        self._label: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "form" and values.get("action"):
            self.actions.append(values["action"])
        elif tag == "label":
            self._label = {"for": values.get("for", ""), "text": [], "fields": []}
        elif tag in ("input", "select", "textarea"):
            kind = values.get("type", "text").lower() if tag == "input" else tag
            if kind not in SKIPPED_TYPES:
                field = values | {"type": kind, "label": ""}
                self.fields.append(field)
                if self._label is not None:
                    self._label["fields"].append(field)

    def handle_data(self, data: str) -> None:
        if self._label is not None:
            self._label["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "label" and self._label is not None:
            text = " ".join("".join(self._label["text"]).split())
            if self._label["for"]:
                self.label_for[self._label["for"]] = text
            for field in self._label["fields"]:
                field["label"] = text
            self._label = None


def _field_kinds(field: dict[str, str], label_for: dict[str, str]) -> set[str]:
    hint = " ".join(field.get(key, "") for key in ("name", "id", "placeholder", "aria-label", "autocomplete", "title"))
    hint = f"{hint} {field['label']} {label_for.get(field.get('id', ''), '')}".lower()
    kinds = set()
    if field["type"] == "password" or any(word in hint for word in SENSITIVE_HINTS):
        kinds.add("sensitive")
    if field["type"] == "email" or "email" in hint or "이메일" in hint:
        return kinds  # "이메일 주소"가 주소 칸으로 잡히지 않게 연락처 판단에서 뺀다
    if field["type"] == "tel" or field.get("autocomplete", "").startswith("tel"):
        kinds.add("phone")
    if field.get("name") == "name" or field.get("id") == "name" or field.get("autocomplete") == "name":
        kinds.add("name")
    kinds.update(kind for kind, words in CONTACT_HINTS.items() if any(word in hint for word in words))
    return kinds


def detect_input_form(fields: list[dict[str, str]], label_for: dict[str, str]) -> bool:
    """민감 칸이 하나라도 있거나, 연락처 칸(이름·전화·주소)이 두 종류 이상이면 True."""
    kinds = set().union(*(_field_kinds(field, label_for) for field in fields)) if fields else set()
    return "sensitive" in kinds or len(kinds & set(CONTACT_HINTS)) >= 2


def _domain(url: str) -> str:
    return registrable_domain(hostname(url))


def extract_page_features(
    page: CollectedPage, allow: Allowlist, *, shorteners: frozenset[str] = frozenset(),
) -> tuple[dict[str, bool | None], tuple[str, ...]]:
    """(페이지 특징, check_failed 단계). 다운로드 응답은 볼 페이지가 없을 뿐 실패가 아니다."""
    page_domain = _domain(page.final_url)
    # 단축 URL 경유는 도메인 변경으로 세지 않는다 (단축 URL 특징과 이중 계산 방지).
    chain = {_domain(url) for url in (page.input_url, *page.redirect_chain, page.final_url)} - {""} - shorteners
    redirect = len(chain) > 1
    if not page.html and (page.app_download or page.other_download):
        return {
            "input_form": False, "app_download": page.app_download, "other_download": page.other_download,
            "cross_domain_form": False, "brand_on_page": False, "cross_domain_redirect": redirect,
        }, ()
    try:
        inspection = inspect_html(page.html)
    except ValueError:  # 잘못된 서로게이트 등 인코딩 불가 입력
        inspection = None
    if inspection is None or inspection.failure is not None:
        unknown = dict.fromkeys(PAGE_FEATURE_CODES)
        unknown.update(
            cross_domain_redirect=redirect,
            app_download=True if page.app_download else None,
            other_download=True if page.other_download else None,
        )
        return unknown, ("page_inspect",)
    doubts = {element.doubt for element in inspection.elements}
    parsed = _Fields()
    parsed.feed(page.html)
    parsed.close()
    actions = {_domain(urljoin(page.final_url, action)) for action in parsed.actions}
    text = inspection.text.lower()
    brand_hit = any(
        brand.domain_names and page_domain not in brand.domain_names
        and any(name and name in text for name in (brand.name.lower(), *brand.aliases))
        for brand in allow.brands
    )
    return {
        "input_form": EnvDoubt.LOGIN_FORM in doubts or detect_input_form(parsed.fields, parsed.label_for),
        "app_download": page.app_download or EnvDoubt.APP_LINK in doubts,
        "other_download": page.other_download,
        "cross_domain_form": any(domain and domain != page_domain for domain in actions),
        "brand_on_page": brand_hit,
        "cross_domain_redirect": redirect,
    }, ()
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_page_features.py -q` Expected: 17 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/page_features.py backend/tests/scanner/test_page_features.py
git commit -m "feat: 페이지 특징 (입력칸 판별, 단축 URL 이중 계산 제거)"
```

### Task 8: 4단계 판정 규칙과 URL 조사

**Files:**

- Create: `backend/src/server/scanner/rules.py`
- Create: `backend/src/server/scanner/investigate.py`
- Test: `backend/tests/scanner/test_rules.py`
- Test: `backend/tests/scanner/test_investigate.py`

**Interfaces:**

- Consumes: Task 1 `Weights`, `score`, `load_weights`, Task 2 `Allowlist`, `load_allowlist`, `validate_allowlist`, `hostname`, `registrable_domain`, Task 3 `collect_url_features`, `combine_url_features`, `load_url_lists`, `load_local_sets`, `RDAP_TIMEOUT_SECONDS`, Task 6 `Collector`, `collector_not_connected`, `COLLECT_TIMEOUT_SECONDS`, Task 7 `extract_page_features`
- Produces:
  - `rules.py`: `Verdict = Literal["danger", "caution", "check_needed", "no_signal"]`, `RANK`, `RISKY_ACTIONS`, `TIME_FAILURES = {"fetch_timeout", "browser_timeout", "deadline"}`, `MessageSignals(claimed_brand, requested_actions)`, `signals_from_message_analysis(result) -> MessageSignals | None`, `message_features(signals, allow, final_url) -> tuple[dict, tuple[str, ...]]`, `url_verdict(features, *, allowlisted_chain, failures, weights) -> tuple[Verdict, ScoreResult]`, `worst(verdicts) -> Verdict` (빈 목록이면 `check_needed`)
  - `investigate.py`: `URL_BUDGET_SECONDS = 20.0`, `LinkResult(url, final_url, features, stage2_features, allowlisted_chain, failures, used_browser, domain_age_days, timings)`, `LinkCache` + `get(url, now)`, `put(result, weights, now)`, `ScanContext(lists, sets, allow, weights, collector, cache)` + `degraded`, `load_default_context(collector=collector_not_connected)`, `allowlisted_chain(url, chain, final_url, ctx) -> bool`, `async investigate(url, ctx, client, *, now=None, budget=URL_BUDGET_SECONDS) -> LinkResult`

`url_verdict`는 위에서부터 처음 맞는 조건으로 정한다: hard 특징이면 다른 단계가 실패했어도 위험 → 허용 경로 체인이고 위험 신호(+ 점수 특징)와 실패가 없으면 위험 신호 미발견 → 점수 50 이상이면 위험 → 위험 신호가 있으면 주의 → 나머지는 확인 필요. `TIME_FAILURES`는 시간을 더 주면 풀릴 수 있는 실패로, 전환 후 응답 연장과 비교 기록에 쓴다.

`investigate`는 판정을 내리지 않고 링크 특징만 모아 캐시한다. URL마다 마감 시각(그림자 실행 20초)을 두고 남은 시간으로 RDAP·수집기 호출을 제한하며, 시간이 모자라면 `deadline`을 남기고 그때까지 찾은 특징을 돌려준다. 최종 URL이 허용 경로이고 경유지가 허용 경로이거나 단축 URL이면 허용 경로 체인으로 보고, 이때는 `url_shortener`를 끈다(목적지가 공식 조회 경로로 확인됨). 판정 계산 시간에는 페이지 특징 추출 시간(`page_features`)만 넣고 최종 URL의 RDAP 시간(`final_stage1`)은 넣지 않는다. 위협 피드 미설정은 `ScanContext.degraded`로만 기록한다. `signals_from_message_analysis`는 선행 작업에서 필드 매핑을 확인할 때까지 `None`을 돌려준다(그 전에는 `check_failed: message`).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_rules.py`:

```python
from backend.src.server.scanner.allowlist import Allowlist, Brand, OfficialDomain
from backend.src.server.scanner.rules import (
    MessageSignals, message_features, signals_from_message_analysis, url_verdict, worst,
)
from backend.src.server.scanner.scorer import FEATURE_CODES, load_weights

W = load_weights()
ALLOW = Allowlist((
    Brand("CJ대한통운", frozenset({"cj"}), frozenset(), (OfficialDomain("cjlogistics.com", ("/tracking",)),)),
    Brand("우체국택배", frozenset(), frozenset(), ()),
))


def clean(**hits: bool | None) -> dict[str, bool | None]:
    return {code: False for code in FEATURE_CODES} | hits


def verdict(features, *, allowlisted: bool = False, failures: tuple[str, ...] = ()):
    return url_verdict(features, allowlisted_chain=allowlisted, failures=failures, weights=W)[0]


def test_hard_feature_is_danger_even_with_failures():
    assert verdict(clean(lookalike_domain=True, old_domain=True, popular_domain=True)) == "danger"
    assert verdict(clean(reported_before=True), failures=("fetch_timeout", "message")) == "danger"


def test_no_signal_needs_allowlisted_chain_no_signal_and_no_failure():
    assert verdict(clean(), allowlisted=True) == "no_signal"
    assert verdict(clean(risky_action=True), allowlisted=True) == "caution"
    assert verdict(clean(), allowlisted=True, failures=("message",)) == "check_needed"


def test_no_evidence_outside_allowlist_is_check_needed_not_normal():
    assert verdict(clean()) == "check_needed"
    assert verdict(clean(unverified_domain=True)) == "check_needed"  # 0점: 확인 상태일 뿐 위험 근거가 아님
    assert verdict(clean(old_domain=True, popular_domain=True)) == "check_needed"


def test_gray_zone_uses_danger_threshold_and_two_groups():
    assert verdict(clean(new_domain=True)) == "caution"  # 40
    assert verdict(clean(new_domain=True, brand_mismatch=True)) == "danger"  # 80
    assert verdict(clean(app_download=True, popular_domain=True)) == "danger"  # 50 + max(-30, 0)
    assert verdict(clean(input_form=True, cross_domain_form=True, old_domain=True, popular_domain=True)) == "danger"


def test_strong_evidence_wins_even_if_steps_failed():
    assert verdict(clean(brand_mismatch=True, new_domain=True), failures=("fetch_timeout",)) == "danger"


def test_score_result_is_returned():
    _, result = url_verdict(clean(new_domain=True), allowlisted_chain=False, failures=(), weights=W)
    assert result.score == 40 and result.hits == {"new_domain": 40}


def test_worst():
    assert worst(["no_signal", "danger", "caution"]) == "danger"
    assert worst(["no_signal", "check_needed"]) == "check_needed"
    assert worst(["check_needed", "caution"]) == "caution"
    assert worst([]) == "check_needed"


def test_message_features():
    features, failures = message_features(None, ALLOW, "https://evil.xyz/")
    assert features == {"brand_mismatch": None, "risky_action": None} and failures == ("message",)
    signals = MessageSignals("CJ대한통운", frozenset({"payment"}))
    assert message_features(signals, ALLOW, "https://evil.xyz/")[0] == {"brand_mismatch": True, "risky_action": True}
    assert message_features(signals, ALLOW, "https://www.cjlogistics.com/tracking")[0]["brand_mismatch"] is False
    assert message_features(MessageSignals("cj", frozenset()), ALLOW, "https://evil.xyz/")[0]["brand_mismatch"] is True
    assert message_features(MessageSignals("우체국택배", frozenset()), ALLOW, "https://evil.xyz/")[0]["brand_mismatch"] is None
    assert message_features(MessageSignals("모르는택배", frozenset()), ALLOW, "https://evil.xyz/")[0]["brand_mismatch"] is None
    assert message_features(MessageSignals(None, frozenset({"track"})), ALLOW, "https://evil.xyz/") == (
        {"brand_mismatch": False, "risky_action": False}, ())


def test_message_adapter_is_unmapped_until_fields_are_confirmed():
    assert signals_from_message_analysis({"anything": 1}) is None
```

`backend/tests/scanner/test_investigate.py`:

```python
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend.src.server.scanner import investigate as investigate_module
from backend.src.server.scanner import url_features
from backend.src.server.scanner.allowlist import Allowlist, Brand, OfficialDomain
from backend.src.server.scanner.collector import CollectedPage
from backend.src.server.scanner.investigate import LinkCache, ScanContext, investigate, load_default_context
from backend.src.server.scanner.scorer import load_weights
from backend.src.server.scanner.url_features import LocalSets, UrlLists

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
LISTS = UrlLists(frozenset({"xyz"}), frozenset({"bit.ly"}))
ALLOW = Allowlist((Brand("CJ대한통운", frozenset(), frozenset({"cjlogistics"}),
                         (OfficialDomain("cjlogistics.com", ("/tracking",)),)),))
FORM = '<p>본인 확인</p><form><input type="password"></form>'


@pytest.fixture(autouse=True)
def clear_age_cache():
    url_features._AGE_CACHE.clear()


def collected(url: str, final: str, html: str = "<p>배송 안내</p>", **extra) -> CollectedPage:
    fields = dict(redirect_chain=(final,) if final != url else (), html_truncated=False, app_download=False,
                  other_download=False, used_browser=False, browser_reason=None, stage2_html=None, failures=(),
                  collected_at="t")
    fields.update(extra)
    return CollectedPage(url, final, html=html, **fields)


class FakeCollector:
    def __init__(self, page_for, delay: float = 0.0):
        self.page_for = page_for
        self.delay = delay
        self.calls: list[tuple[str, bool]] = []

    async def __call__(self, url: str, browser_allowed: bool):
        self.calls.append((url, browser_allowed))
        await asyncio.sleep(self.delay)
        return self.page_for(url)


def context(collector, sets=None) -> ScanContext:
    sets = sets or LocalSets(frozenset({"evil.xyz"}), frozenset(), frozenset({"reported.top"}))
    return ScanContext(LISTS, sets, ALLOW, load_weights(), collector, LinkCache())


def rdap(request: httpx.Request) -> httpx.Response:
    if "cjlogistics" in str(request.url) or "bit.ly" in str(request.url):
        raise AssertionError("allowlisted or shared domains must not hit RDAP")
    created = "2020-01-01T00:00:00Z" if "old.example" in str(request.url) else "2026-09-21T00:00:00Z"
    return httpx.Response(200, json={"events": [{"eventAction": "registration", "eventDate": created}]})


def run(url: str, ctx: ScanContext, now: datetime = NOW, budget: float = 20.0):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(rdap)) as client:
            return await investigate(url, ctx, client, now=now, budget=budget)
    return asyncio.run(go())


def test_hard_feature_skips_collection():
    collector = FakeCollector(lambda url: pytest.fail("must not collect"))
    result = run("https://reported.top/a", context(collector))
    assert result.features["reported_before"] is True
    assert result.features["input_form"] is None
    assert collector.calls == [] and not result.allowlisted_chain


def test_unofficial_link_collects_with_browser_and_rechecks_final_domain():
    collector = FakeCollector(lambda url: collected(url, "https://login.evil.xyz/pay", FORM))
    result = run("https://bit.ly/abc", context(collector))
    assert collector.calls == [("https://bit.ly/abc", True)]
    assert result.final_url == "https://login.evil.xyz/pay"
    assert result.features["threat_feed"] is True  # 최종 도메인에서 걸림
    assert result.features["url_shortener"] is True and result.features["input_form"] is True
    assert result.features["cross_domain_redirect"] is False  # 단축 URL 경유는 이중 계산하지 않는다
    assert result.domain_age_days == 10
    assert result.failures == () and not result.allowlisted_chain


def test_allowlisted_link_disables_browser_and_marks_chain():
    collector = FakeCollector(lambda url: collected(url, url))
    result = run("https://www.cjlogistics.com/tracking?invoice=1", context(collector))
    assert collector.calls == [("https://www.cjlogistics.com/tracking?invoice=1", False)]
    assert result.allowlisted_chain and result.failures == ()


def test_shortened_link_to_official_page_is_judged_by_final_url():
    collector = FakeCollector(lambda url: collected(url, "https://www.cjlogistics.com/tracking/123"))
    result = run("https://bit.ly/cj", context(collector))
    assert result.allowlisted_chain
    assert result.features["url_shortener"] is False and result.features["cross_domain_redirect"] is False


def test_redirect_out_of_allowlist_breaks_chain():
    collector = FakeCollector(lambda url: collected(url, "https://evil.xyz/x"))
    result = run("https://www.cjlogistics.com/tracking", context(collector))
    assert not result.allowlisted_chain
    assert result.features["threat_feed"] is True


def test_collector_unavailable_is_a_failure():
    result = run("https://plain.example/", context(FakeCollector(lambda url: None)))
    assert "collect" in result.failures
    assert result.features["input_form"] is None


def test_deadline_keeps_stage1_features():
    collector = FakeCollector(lambda url: collected(url, url), delay=1.0)
    result = run("https://plain.example/", context(collector), budget=0.2)
    assert "deadline" in result.failures
    assert result.features["new_domain"] is True and result.features["input_form"] is None


def test_stage2_features_recorded_when_browser_used():
    page = lambda url: collected(url, url, FORM, used_browser=True, browser_reason="script_only",
                                 stage2_html="<script src=a.js></script><p>x</p>")
    result = run("https://plain.example/", context(FakeCollector(page)))
    assert result.used_browser and result.features["input_form"] is True
    assert result.stage2_features["input_form"] is False


def test_cache_ttl_depends_on_link_risk_and_skips_failures():
    collector = FakeCollector(lambda url: collected(url, url, FORM if "fresh" in url else "<p>x</p>"))
    ctx = context(collector)
    run("https://old.example/", ctx)  # 오래된 도메인, 신호 없음 → 3시간
    run("https://old.example/", ctx, NOW + timedelta(hours=2))
    assert len(collector.calls) == 1
    run("https://old.example/", ctx, NOW + timedelta(hours=4))
    assert len(collector.calls) == 2
    run("https://fresh.example/", ctx)  # 신규 도메인 + 입력 폼 = 80 → 위험 신호 → 24시간
    run("https://fresh.example/", ctx, NOW + timedelta(hours=20))
    assert len(collector.calls) == 3
    run("https://fresh.example/", ctx, NOW + timedelta(hours=25))
    assert len(collector.calls) == 4
    failing = context(FakeCollector(lambda url: None))
    run("https://plain.example/", failing)
    run("https://plain.example/", failing)
    assert len(failing.collector.calls) == 2


def test_degraded_when_threat_feed_is_not_configured():
    assert context(None, LocalSets(None, frozenset(), frozenset())).degraded == ("threat_feed",)
    assert context(None, LocalSets(None, frozenset(), frozenset(), threat_feed_error=True)).degraded == ()
    assert context(None).degraded == ()


def test_default_context_rejects_shared_domains(monkeypatch):
    load_default_context()  # 저장소 기본 파일은 통과
    bad = Allowlist((Brand("t", frozenset(), frozenset(), (OfficialDomain("naver.me", ("/",)),)),))
    monkeypatch.setattr(investigate_module, "load_allowlist", lambda: bad)
    with pytest.raises(ValueError, match="naver.me"):
        load_default_context()
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_rules.py backend/tests/scanner/test_investigate.py -q` Expected: FAIL — `ModuleNotFoundError: No module named 'backend.src.server.scanner.rules'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/rules.py`:

```python
"""판정 규칙. I/O 없는 결정적 함수.

위험 근거와 확인 범위를 나눠 본다: 근거가 있으면 위험·주의, 근거가 없으면 확인 범위에 따라 신호 미발견·확인 필요.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from backend.src.server.scanner.allowlist import Allowlist
from backend.src.server.scanner.scorer import ScoreResult, Weights, score
from backend.src.server.scanner.urls import hostname, registrable_domain

Verdict = Literal["danger", "caution", "check_needed", "no_signal"]
RANK = {"no_signal": 0, "check_needed": 1, "caution": 2, "danger": 3}
RISKY_ACTIONS = frozenset({"app_install", "payment", "personal_info", "call"})
# 시간을 더 주면 풀릴 수 있는 실패. 응답 연장(근거 없을 때 20초까지)과 비교 기간 측정에 쓴다.
TIME_FAILURES = frozenset({"fetch_timeout", "browser_timeout", "deadline"})


@dataclass(frozen=True)
class MessageSignals:
    claimed_brand: str | None
    requested_actions: frozenset[str]


def signals_from_message_analysis(result: object) -> MessageSignals | None:
    """기존 메시지 분석 결과 → MessageSignals.

    ponytail: 필드 매핑은 선행 작업에서 확인한 뒤 채운다. 그 전에는 None → check_failed: message.
    """
    return None


def message_features(
    signals: MessageSignals | None, allow: Allowlist, final_url: str,
) -> tuple[dict[str, bool | None], tuple[str, ...]]:
    if signals is None:
        return {"brand_mismatch": None, "risky_action": None}, ("message",)
    mismatch: bool | None = False
    if signals.claimed_brand:
        brand = allow.find_brand(signals.claimed_brand)
        # 목록에 없는 택배사이거나 공식 도메인이 아직 없으면 불일치를 판단할 수 없다 (미확인이지만 실패는 아님).
        if brand is None or not brand.domain_names:
            mismatch = None
        else:
            mismatch = registrable_domain(hostname(final_url)) not in brand.domain_names
    return {"brand_mismatch": mismatch, "risky_action": bool(signals.requested_actions & RISKY_ACTIONS)}, ()


def url_verdict(
    features: dict[str, bool | None], *, allowlisted_chain: bool, failures: tuple[str, ...], weights: Weights,
) -> tuple[Verdict, ScoreResult]:
    result = score(features, weights)
    if any(features.get(code) is True for code in weights.hard):
        return "danger", result  # 다른 단계가 실패했어도 확실한 근거면 위험
    signals = any(points > 0 for points in result.hits.values())
    if allowlisted_chain and not signals and not failures:
        return "no_signal", result
    if result.score >= weights.danger_threshold:
        return "danger", result
    return ("caution" if signals else "check_needed"), result


def worst(verdicts: Iterable[Verdict]) -> Verdict:
    return max(verdicts, key=RANK.__getitem__, default="check_needed")
```

`backend/src/server/scanner/investigate.py`:

```python
"""URL 하나 조사: 캐시 → 1단계 → (hard면 방문 생략) → 격리 수집기(2·3단계) → 페이지 특징 → 최종 URL 재계산.

URL마다 마감 시각을 두고 남은 시간으로 각 단계를 제한한다. 시간이 모자라도 이미 찾은 특징은 남긴다.
"""

import asyncio
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import httpx

from backend.src.server.scanner.allowlist import Allowlist, load_allowlist, validate_allowlist
from backend.src.server.scanner.collector import COLLECT_TIMEOUT_SECONDS, Collector, collector_not_connected
from backend.src.server.scanner.page_features import PAGE_FEATURE_CODES, extract_page_features
from backend.src.server.scanner.scorer import Weights, load_weights, score
from backend.src.server.scanner.url_features import (
    RDAP_TIMEOUT_SECONDS, LocalSets, UrlLists, collect_url_features, combine_url_features, load_local_sets,
    load_url_lists,
)
from backend.src.server.scanner.urls import hostname, registrable_domain

# 그림자 실행 기준 URL당 상한. 전환 후 응답은 근거가 있으면 10초 안에, 없을 때만 20초까지 기다린다 (응답 단계에서 적용).
URL_BUDGET_SECONDS = 20.0
RISKY_TTL = timedelta(hours=24)
DEFAULT_TTL = timedelta(hours=3)
CACHE_LIMIT = 10_000


@dataclass(frozen=True)
class LinkResult:
    """문자와 무관한 링크 조사 결과 (캐시 대상). 판정은 문자 특징과 합친 뒤 규칙이 내린다."""

    url: str
    final_url: str
    features: dict[str, bool | None]  # URL 특징 + 페이지 특징
    stage2_features: dict[str, bool | None] | None  # 브라우저를 썼을 때 2단계 HTML만으로 본 특징
    allowlisted_chain: bool
    failures: tuple[str, ...]
    used_browser: bool
    domain_age_days: int | None
    timings: dict[str, float]


class LinkCache:
    """판정이 아니라 링크 특징을 저장한다. ponytail: 프로세스 메모리라 재시작 시 비워진다."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[datetime, LinkResult]] = {}

    def get(self, url: str, now: datetime) -> LinkResult | None:
        item = self._items.get(url)
        if item is None or item[0] <= now:
            self._items.pop(url, None)
            return None
        return item[1]

    def put(self, result: LinkResult, weights: Weights, now: datetime) -> None:
        if result.failures:
            return  # 확인 실패한 결과는 저장하지 않는다
        risky = any(result.features.get(code) is True for code in weights.hard) or (
            score(result.features, weights).score >= weights.danger_threshold
        )
        if len(self._items) >= CACHE_LIMIT:
            self._items.pop(next(iter(self._items)))
        self._items[result.url] = (now + (RISKY_TTL if risky else DEFAULT_TTL), result)


@dataclass(frozen=True)
class ScanContext:
    lists: UrlLists
    sets: LocalSets
    allow: Allowlist
    weights: Weights
    collector: Collector
    cache: LinkCache

    @property
    def degraded(self) -> tuple[str, ...]:
        """URL과 무관한 시스템 상태. 위협 피드가 미설정이면 기록만 하고 URL 확인 실패로 치지 않는다."""
        return ("threat_feed",) if self.sets.threat_feed is None and not self.sets.threat_feed_error else ()


def load_default_context(collector: Collector = collector_not_connected) -> ScanContext:
    """기동 시 한 번. 목록·가중치 파일이 잘못되면 예외로 기동을 막는다."""
    lists = load_url_lists()
    allow = load_allowlist()
    validate_allowlist(allow, lists.shared)
    return ScanContext(lists, load_local_sets(), allow, load_weights(), collector, LinkCache())


def allowlisted_chain(url: str, chain: tuple[str, ...], final_url: str, ctx: ScanContext) -> bool:
    """최종 URL이 허용 경로이고, 경유지는 허용 경로이거나 단축 URL일 때만 True (단축 URL은 최종 URL로 대조)."""
    def passable(item: str) -> bool:
        return ctx.allow.allows(item) or registrable_domain(hostname(item)) in ctx.lists.shorteners
    return ctx.allow.allows(final_url) and all(passable(item) for item in (url, *chain))


async def investigate(
    url: str, ctx: ScanContext, client: httpx.AsyncClient, *, now: datetime | None = None,
    budget: float = URL_BUDGET_SECONDS,
) -> LinkResult:
    now = now or datetime.now(timezone.utc)
    cached = ctx.cache.get(url, now)
    if cached is not None:
        return cached
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    started = time.perf_counter()

    def left() -> float:
        return deadline - loop.time()

    async def url_stage(target: str):
        return await collect_url_features(target, client=client, lists=ctx.lists, sets=ctx.sets, allow=ctx.allow,
                                          now=now, rdap_timeout=min(RDAP_TIMEOUT_SECONDS, left()))

    first = await url_stage(url)
    url_features, failures, age_days = first.features, list(first.failures), first.domain_age_days
    timings = {"stage1": time.perf_counter() - started}
    page_features: dict[str, bool | None] = dict.fromkeys(PAGE_FEATURE_CODES)
    stage2_page: dict[str, bool | None] | None = None
    final_url, chain, used_browser = url, (), False
    if not any(url_features.get(code) is True for code in ctx.weights.hard):  # hard면 방문하지 않는다
        page = None
        if left() <= 0:
            failures.append("deadline")
        else:
            mark = time.perf_counter()
            try:
                page = await asyncio.wait_for(ctx.collector(url, not ctx.allow.allows(url)),
                                              min(COLLECT_TIMEOUT_SECONDS, left()))
                if page is None:
                    failures.append("collect")
            except TimeoutError:
                failures.append("deadline")
            timings["collect"] = time.perf_counter() - mark
        if page is not None:
            mark = time.perf_counter()
            failures += page.failures
            shorteners = ctx.lists.shorteners
            page_features, page_failures = extract_page_features(page, ctx.allow, shorteners=shorteners)
            failures += page_failures
            if page.used_browser and page.stage2_html is not None:
                stage2_page = extract_page_features(replace(page, html=page.stage2_html), ctx.allow,
                                                    shorteners=shorteners)[0]
            timings["page_features"] = time.perf_counter() - mark  # 판정 계산 시간에 들어가는 부분
            final_url, chain, used_browser = page.final_url, page.redirect_chain, page.used_browser
            if registrable_domain(hostname(final_url)) != registrable_domain(hostname(url)):
                if left() <= 0:
                    failures.append("deadline")
                else:
                    mark = time.perf_counter()
                    final = await url_stage(final_url)
                    timings["final_stage1"] = time.perf_counter() - mark
                    url_features = combine_url_features(url_features, final.features, ctx.weights.hard)
                    failures += final.failures
                    age_days = final.domain_age_days
    allowed = allowlisted_chain(url, chain, final_url, ctx)
    if allowed:
        url_features = url_features | {"url_shortener": False}  # 목적지가 공식 조회 경로로 확인됨
    timings["total"] = time.perf_counter() - started
    result = LinkResult(
        url=url,
        final_url=final_url,
        features=url_features | page_features,
        stage2_features=None if stage2_page is None else url_features | stage2_page,
        allowlisted_chain=allowed,
        failures=tuple(dict.fromkeys(failures)),
        used_browser=used_browser,
        domain_age_days=age_days,
        timings=timings,
    )
    ctx.cache.put(result, ctx.weights, now)
    return result
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_rules.py backend/tests/scanner/test_investigate.py -q` Expected: 20 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/rules.py backend/src/server/scanner/investigate.py backend/tests/scanner/test_rules.py backend/tests/scanner/test_investigate.py
git commit -m "feat: 4단계 판정 규칙과 URL 조사 (URL당 마감, 단축 URL 최종 대조)"
```

### Task 9: LangGraph 파이프라인과 응답 문구

**Files:**

- Create: `backend/src/server/scanner/graph.py`
- Modify: 의존성 파일에 `langgraph` 추가
- Test: `backend/tests/scanner/test_graph.py`

**Interfaces:**

- Consumes: Task 1 `FEATURE_CODES`, Task 8 `investigate`, `LinkResult`, `ScanContext`, `MessageSignals`, `Verdict`, `message_features`, `url_verdict`, `worst`
- Produces:
  - `MAX_URLS = 5`, `MessageAnalyzer = Callable[[str], Awaitable[MessageSignals | None]]`
  - `class State(TypedDict, total=False): message; urls; message_signals; links: Annotated[list[LinkResult], operator.add]; url_verdicts: list[dict]; verdict: Verdict; degraded: list[str]; reply: str`
  - `judge(state, ctx) -> dict` — `url_verdicts` 항목 키: `url`, `final_url`, `verdict`, `score`, `evidence`, `reputation`, `hits`, `features`, `failures`, `used_browser`, `browser_changed`, `domain_age_days`, `timings`(`decide` 포함)
  - `compose_reply(verdict, per_url, ctx) -> str`, 문구 상수 `REASONS`, `HEADLINES`, `FAILURE_TEXT`, `GUIDE`, `KISA`
  - `build_graph(ctx, analyze_message)` (컴파일된 그래프), `async run_pipeline(graph, message, urls) -> State`

START에서 `analyze_message` 엣지와 `fan_out`(URL별 `Send`) 조건부 엣지를 함께 건다. 두 갈래가 같은 단계에서 돌고 `merge`는 한 번만 실행된다(LangGraph 1.2.12에서 확인). `links`는 `operator.add`로 모은다. 한 URL의 예외는 그 URL만 `investigate` 실패(확인 필요)로 만들고, 5개를 넘는 URL은 `url_limit`으로 확인 필요에 남긴다. 응답은 판정 한 줄 → 위험 근거(점수 큰 순 최대 3개) → "확인하지 못한 것"(최대 2개) → 행동 가이드 순이고, 118 안내는 위험·주의에만 붙인다. "안전"이라는 말은 쓰지 않는다. 그림자 기간에는 `reply`를 사용자에게 보내지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_graph.py`:

```python
import asyncio

import pytest

from backend.src.server.scanner import graph as graph_module
from backend.src.server.scanner.allowlist import Allowlist, Brand, OfficialDomain
from backend.src.server.scanner.investigate import LinkCache, LinkResult, ScanContext
from backend.src.server.scanner.rules import MessageSignals
from backend.src.server.scanner.scorer import FEATURE_CODES, load_weights
from backend.src.server.scanner.url_features import LocalSets, UrlLists

OFFICIAL = "https://www.cjlogistics.com/tracking?invoice=1"
EVIL = "https://bit.ly/evil"
ALLOW = Allowlist((Brand("CJ대한통운", frozenset(), frozenset(), (OfficialDomain("cjlogistics.com", ("/tracking",)),)),))
CTX = ScanContext(UrlLists(frozenset(), frozenset()), LocalSets(None, frozenset(), frozenset()), ALLOW,
                  load_weights(), None, LinkCache())


def link(url: str, *, final: str | None = None, allowlisted: bool = False, stage2=None, failures=(), **hits) -> LinkResult:
    features = {code: False for code in FEATURE_CODES} | {"unverified_domain": not allowlisted} | hits
    return LinkResult(url, final or url, features, stage2, allowlisted, tuple(failures), stage2 is not None, 10,
                      {"page_features": 0.001})


LINKS = {
    OFFICIAL: link(OFFICIAL, allowlisted=True),
    EVIL: link(EVIL, final="https://login.evil.xyz/", url_shortener=True, input_form=True),
    "https://plain.example/": link("https://plain.example/"),
    "https://gone.example/": link("https://gone.example/", failures=("fetch",)),
}


@pytest.fixture
def calls(monkeypatch):
    seen: list[str] = []

    async def fake_investigate(url, ctx, client, now=None):
        seen.append(url)
        if url == "https://boom.example/":
            raise RuntimeError("boom")
        return LINKS.get(url) or link(url)

    monkeypatch.setattr(graph_module, "investigate", fake_investigate)
    return seen


def run(urls: list[str], analyzer) -> dict:
    graph = graph_module.build_graph(CTX, analyzer)
    return asyncio.run(graph_module.run_pipeline(graph, "[CJ대한통운] 주소지 확인 바랍니다", urls))


async def no_risk(message: str) -> MessageSignals:
    return MessageSignals(None, frozenset())


async def broken(message: str):
    raise RuntimeError("LLM down")


def test_worst_verdict_across_links_and_input_order(calls):
    state = run([OFFICIAL, EVIL], no_risk)
    assert [item["url"] for item in state["url_verdicts"]] == [OFFICIAL, EVIL]
    assert [item["verdict"] for item in state["url_verdicts"]] == ["no_signal", "danger"]
    assert state["verdict"] == "danger" and state["degraded"] == ["threat_feed"]
    assert state["url_verdicts"][1]["evidence"] == 40 and state["url_verdicts"][1]["reputation"] == 15
    assert "위험" in state["reply"] and "카드번호" in state["reply"] and "118" in state["reply"]
    assert sorted(calls) == sorted([OFFICIAL, EVIL])


def test_no_signal_reply_names_brand_without_kisa(calls):
    state = run([OFFICIAL], no_risk)
    assert state["verdict"] == "no_signal"
    assert "CJ대한통운 공식 주소" in state["reply"] and "118" not in state["reply"]
    assert "공식 앱" in state["reply"]


def test_message_failure_gives_check_needed_with_reason(calls):
    state = run([OFFICIAL], broken)
    assert state["verdict"] == "check_needed"
    assert state["url_verdicts"][0]["failures"] == ["message"]
    assert "확인하지 못한 것:" in state["reply"] and "문자 내용을 분석하지 못했어요" in state["reply"]


def test_unofficial_link_without_signals_is_check_needed(calls):
    state = run(["https://plain.example/"], no_risk)
    assert state["verdict"] == "check_needed"
    assert "확인된 택배사 공식 주소가 아니에요" in state["reply"] and "118" not in state["reply"]


def test_closed_site_says_why(calls):
    state = run(["https://gone.example/"], no_risk)
    assert state["verdict"] == "check_needed"
    assert "이미 닫혔거나 응답이 없어요" in state["reply"]


def test_brand_mismatch_from_message(calls):
    async def claims_cj(message: str) -> MessageSignals:
        return MessageSignals("CJ대한통운", frozenset({"payment"}))
    item = run([EVIL], claims_cj)["url_verdicts"][0]
    assert item["features"]["brand_mismatch"] is True and item["features"]["risky_action"] is True


def test_url_limit_marks_extra_urls(calls):
    urls = [f"https://site{i}.example/" for i in range(7)]
    state = run(urls, no_risk)
    assert len(calls) == 5
    assert [item["failures"] for item in state["url_verdicts"][5:]] == [["url_limit"], ["url_limit"]]
    assert all(item["verdict"] == "check_needed" for item in state["url_verdicts"][5:])


def test_investigate_exception_is_isolated(calls):
    state = run(["https://boom.example/", OFFICIAL], no_risk)
    first, second = state["url_verdicts"]
    assert first["failures"] == ["investigate"] and first["verdict"] == "check_needed"
    assert second["verdict"] == "no_signal"


def test_browser_changed_is_recorded(calls):
    stage2 = {code: False for code in FEATURE_CODES} | {"unverified_domain": True}
    LINKS["https://spa.example/"] = link("https://spa.example/", stage2=stage2, input_form=True, new_domain=True)
    item = run(["https://spa.example/"], no_risk)["url_verdicts"][0]
    assert item["used_browser"] and item["browser_changed"] is True  # 2단계만이면 확인 필요, 브라우저 후 위험
    assert item["timings"]["decide"] >= 0.001 and item["domain_age_days"] == 10
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_graph.py -q` Expected: FAIL — `ImportError: cannot import name 'graph' from 'backend.src.server.scanner'`

- [ ] **Step 3: 구현**

`langgraph`를 의존성 파일에 추가한다 (`pip install langgraph`).

`backend/src/server/scanner/graph.py`:

```python
"""LangGraph 그래프: 문자 분석 ∥ URL별 조사(Send) → 합치기·판정 → 응답 문구."""

import operator
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from backend.src.server.scanner.investigate import LinkResult, ScanContext, investigate
from backend.src.server.scanner.rules import MessageSignals, Verdict, message_features, url_verdict, worst
from backend.src.server.scanner.scorer import FEATURE_CODES

MAX_URLS = 5
MessageAnalyzer = Callable[[str], Awaitable[MessageSignals | None]]
REASONS = {
    "threat_feed": "알려진 피싱 사이트 목록에 있는 주소예요",
    "reported_before": "이전에 스미싱으로 확인된 주소예요",
    "lookalike_domain": "택배사 공식 주소를 흉내 낸 주소예요",
    "app_download": "앱 설치 파일(APK 등)을 내려받게 해요",
    "brand_mismatch": "문자 속 택배사의 공식 주소가 아니에요",
    "new_domain": "만든 지 30일이 안 된 주소예요",
    "input_form": "비밀번호·카드번호·인증번호나 이름·전화번호·주소를 입력하게 해요",
    "risky_action": "문자가 앱 설치·결제·개인정보·전화를 요구해요",
    "brand_on_page": "페이지가 택배사를 내세우지만 공식 주소가 아니에요",
    "ip_or_port": "일반적이지 않은 주소 형식(IP·포트)이에요",
    "cross_domain_form": "입력한 정보를 다른 사이트로 보내요",
    "cross_domain_redirect": "다른 사이트로 넘어가요",
    "other_download": "파일을 내려받게 해요",
    "url_shortener": "단축 주소라 실제 목적지가 가려져 있어요",
    "abused_tld": "스미싱에 자주 쓰이는 도메인 끝자리예요",
    "unverified_domain": "확인된 택배사 주소가 아니에요",
}
HEADLINES = {
    "danger": "⚠️ 위험: 이 링크는 누르지 마세요.",
    "caution": "⚠️ 주의: 위험 신호가 있어요. 누르기 전에 한 번 더 확인하세요.",
    "check_needed": "❔ 확인 필요: 위험 신호는 찾지 못했지만, 믿을 수 있는 주소인지도 확인하지 못했어요.",
    "no_signal": "🔍 확인한 범위에서 위험 신호를 찾지 못했어요.",
}
FAILURE_TEXT = {
    "fetch": "페이지에 접속하지 못했어요 (이미 닫혔거나 응답이 없어요)",
    "fetch_timeout": "페이지가 제시간에 응답하지 않았어요",
    "blocked_address": "내부망 주소로 이동하려 해서 확인을 멈췄어요",
    "redirect_limit": "다른 주소로 너무 여러 번 넘어가요",
    "browser": "화면을 끝까지 열어 보지 못했어요",
    "browser_timeout": "화면을 제시간에 끝까지 열지 못했어요",
    "html_truncated": "페이지가 너무 커서 일부만 검사했어요",
    "page_inspect": "페이지 내용을 검사하지 못했어요",
    "collect": "페이지 확인 서버에 연결하지 못했어요",
    "threat_feed": "피싱 사이트 목록을 읽지 못했어요",
    "domain_age": "도메인 생성일을 확인하지 못했어요",
    "message": "문자 내용을 분석하지 못했어요",
    "url_limit": "링크가 많아 앞의 5개만 확인했어요",
    "deadline": "시간 안에 끝까지 확인하지 못했어요",
    "investigate": "확인 중 오류가 났어요",
}
GUIDE = "결제·개인정보 입력은 문자 속 링크 대신 택배사 공식 앱이나 직접 찾은 공식 사이트에서 하세요."
KISA = "의심되면 118(한국인터넷진흥원 상담센터)에 문의하세요."


class State(TypedDict, total=False):
    message: str
    urls: list[str]
    message_signals: MessageSignals | None
    links: Annotated[list[LinkResult], operator.add]
    url_verdicts: list[dict]
    verdict: Verdict
    degraded: list[str]
    reply: str


class UrlTask(TypedDict):
    url: str


def judge(state: State, ctx: ScanContext) -> dict:
    """URL별로 링크 특징 + 문자 특징을 합쳐 규칙 판정. 최종 판정은 가장 위험한 것."""
    signals = state.get("message_signals")
    order = {url: index for index, url in enumerate(state["urls"])}
    per_url: list[dict] = []
    for link in sorted(state.get("links", []), key=lambda item: order.get(item.url, len(order))):
        mark = time.perf_counter()
        message, message_failures = message_features(signals, ctx.allow, link.final_url)
        failures = link.failures + message_failures
        features = link.features | message
        verdict, result = url_verdict(features, allowlisted_chain=link.allowlisted_chain, failures=failures,
                                      weights=ctx.weights)
        changed = None
        if link.stage2_features is not None:
            before, _ = url_verdict(link.stage2_features | message, allowlisted_chain=link.allowlisted_chain,
                                    failures=failures, weights=ctx.weights)
            changed = before != verdict
        decide = link.timings.get("page_features", 0.0) + time.perf_counter() - mark
        per_url.append({
            "url": link.url, "final_url": link.final_url, "verdict": verdict, "score": result.score,
            "evidence": result.evidence, "reputation": result.reputation, "hits": result.hits,
            "features": features, "failures": list(failures), "used_browser": link.used_browser,
            "browser_changed": changed, "domain_age_days": link.domain_age_days,
            "timings": link.timings | {"decide": decide},
        })
    for url in state["urls"][MAX_URLS:]:  # 조사하지 않은 URL은 확인 실패로 남긴다
        per_url.append({
            "url": url, "final_url": url, "verdict": "check_needed", "score": 0, "evidence": 0, "reputation": 0,
            "hits": {}, "features": {}, "failures": ["url_limit"], "used_browser": False, "browser_changed": None,
            "domain_age_days": None, "timings": {},
        })
    return {"url_verdicts": per_url, "verdict": worst(item["verdict"] for item in per_url),
            "degraded": list(ctx.degraded)}


def compose_reply(verdict: Verdict, per_url: list[dict], ctx: ScanContext) -> str:
    """판정 + 위험 근거 + 확인하지 못한 것 + 행동 가이드. 근거와 미확인 항목을 섞지 않고 따로 보여준다."""
    target = next((item for item in per_url if item["verdict"] == verdict), None)
    lines = [HEADLINES[verdict]]
    if target is not None:
        hits = sorted(target["hits"].items(), key=lambda item: -item[1])
        lines += [f"- {REASONS[code]}" for code, points in hits if points > 0 and code in REASONS][:3]
        if verdict == "check_needed" and not target["failures"]:
            lines.append("- 확인된 택배사 공식 주소가 아니에요")
        brand = ctx.allow.brand_of(target["final_url"])
        if verdict == "no_signal" and brand is not None:
            lines.append(f"- {brand.name} 공식 주소의 조회 페이지예요")
        unchecked = list(dict.fromkeys(FAILURE_TEXT.get(code, FAILURE_TEXT["investigate"])
                                       for code in target["failures"]))[:2]
        if unchecked:
            lines.append("확인하지 못한 것:")
            lines += [f"- {text}" for text in unchecked]
    lines.append(GUIDE)
    if verdict in ("danger", "caution"):
        lines.append(KISA)
    return "\n".join(lines)


def _failed_link(url: str) -> LinkResult:
    return LinkResult(url, url, dict.fromkeys(FEATURE_CODES), None, False, ("investigate",), False, None, {})


def build_graph(ctx: ScanContext, analyze_message: MessageAnalyzer):
    async def analyze_message_node(state: State) -> dict:
        try:
            signals = await analyze_message(state["message"])
        except Exception:
            signals = None  # 문자 분석 실패 → check_failed: message
        return {"message_signals": signals}

    def fan_out(state: State) -> list[Send]:
        return [Send("investigate", {"url": url}) for url in state["urls"][:MAX_URLS]]

    async def investigate_node(task: UrlTask) -> dict:
        try:
            async with httpx.AsyncClient() as client:
                link = await investigate(task["url"], ctx, client)
        except Exception:
            link = _failed_link(task["url"])  # 한 URL의 예외가 다른 URL 판정을 막지 않는다
        return {"links": [link]}

    def merge_node(state: State) -> dict:
        return judge(state, ctx)

    def reply_node(state: State) -> dict:
        return {"reply": compose_reply(state["verdict"], state["url_verdicts"], ctx)}

    graph = StateGraph(State)
    graph.add_node("analyze_message", analyze_message_node)
    graph.add_node("investigate", investigate_node)
    graph.add_node("merge", merge_node)
    graph.add_node("reply", reply_node)
    graph.add_edge(START, "analyze_message")
    graph.add_conditional_edges(START, fan_out, ["investigate"])
    graph.add_edge("analyze_message", "merge")
    graph.add_edge("investigate", "merge")
    graph.add_edge("merge", "reply")
    graph.add_edge("reply", END)
    return graph.compile()


async def run_pipeline(graph, message: str, urls: list[str]) -> State:
    return await graph.ainvoke({"message": message, "urls": urls})
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_graph.py -q` Expected: 9 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/graph.py backend/tests/scanner/test_graph.py requirements.txt
git commit -m "feat: LangGraph 파이프라인과 4단계 응답 문구"
```

### Task 10: 비교 기록과 전환 기준 요약

**Files:**

- Create: `backend/src/server/scanner/compare.py`
- Test: `backend/tests/scanner/test_compare.py`

**Interfaces:**

- Consumes: Task 1 `Weights`, Task 8 `TIME_FAILURES`, Task 9 `url_verdicts` 항목
- Produces:
  - `LABELS = {"smishing", "benign", "unknown"}`, `MIN_LABELED = 50`(가설값), `RECORDS: dict[str, dict]`, `RESPONSE_TARGET_SECONDS = 10.0`, `EXTENDED_SECONDS = 20.0`
  - `urlscan_outcome(parsed) -> dict | None` — `parse_urlscan_result` 결과(평평한 `score`·`malicious`) 또는 urlscan 원본(`verdicts.overall`)
  - `record_comparison(item, urlscan, *, weights, degraded, now=None) -> dict`
  - `set_label(record_id, label) -> dict` — 없는 id는 `KeyError`, 잘못된 라벨은 `ValueError`
  - `summarize(records=None) -> dict` — `records`, `pairs`, `agreement_rate`(참고), `disagreements`, `labeled_disagreements`, `own_correct_rate`(참고), `labeled_smishing`, `labeled_benign`, `smishing_caught_rate`, `false_danger_rate`, `check_failed_rate`, `check_needed_rate`, `time_failure_rate`, `browser_rate`, `browser_changed_rate`, `over_10s_rate`, `resolved_10_to_20s_rate`, `decide_p50`, `decide_p95`, `total_p95`, `verdicts`, `transition_ready`
  - `async record_pipeline(pipeline, urlscan_by_url, weights) -> list[dict]`, `spawn(coroutine) -> asyncio.Task`, `async drain()`

`transition_ready`는 라벨된 표본으로 판단한다: 스미싱·정상 라벨 각각 `MIN_LABELED`건 이상, 스미싱 라벨 중 위험·주의로 판정한 비율(`smishing_caught_rate`) 90% 이상, 정상 라벨 중 위험으로 판정한 비율(`false_danger_rate`) 5% 이하, 판정 계산 p50 1초 미만·p95 3초 이하, 전체 p95 10초 이하, `check_failed` 비율 5% 이하. urlscan 일치율(자체 `score >= compare_threshold` vs urlscan `malicious`, 확인 실패가 없고 urlscan 값이 있을 때만)은 참고값으로만 남긴다. urlscan은 추적 브랜드만 피싱으로 판정해, 국내 택배 사칭을 우리가 맞게 잡아도 불일치로 집계될 수 있기 때문이다. `resolved_10_to_20s_rate`는 10초를 넘겼지만 20초 안에 확인을 마쳐 확인 필요를 면한 비율로, 응답 연장 효과를 잰다. 그림자 실행 실패는 기록만 하고 삼킨다. 기록은 프로세스 메모리에 둔다(비교 기간이 길어지면 DB로 옮긴다).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_compare.py`:

```python
import asyncio

import pytest

from backend.src.server.scanner import compare
from backend.src.server.scanner.scorer import load_weights

W = load_weights()


@pytest.fixture(autouse=True)
def clear_records():
    compare.RECORDS.clear()
    yield
    compare.RECORDS.clear()


def item(url: str = "https://x.example/", *, score: int = 60, verdict: str = "danger", failures=(),
         browser: bool = False, changed=None, decide: float = 0.1, total: float = 3.0) -> dict:
    return {"url": url, "final_url": url, "verdict": verdict, "score": score, "evidence": score, "reputation": 0,
            "hits": {}, "features": {}, "failures": list(failures), "used_browser": browser,
            "browser_changed": changed, "domain_age_days": 5, "timings": {"decide": decide, "total": total}}


def test_urlscan_outcome_reads_flat_or_raw_results():
    assert compare.urlscan_outcome({"score": 100, "malicious": True}) == {"score": 100, "malicious": True}
    raw = {"verdicts": {"overall": {"score": 0, "malicious": False}}}
    assert compare.urlscan_outcome(raw) == {"score": 0, "malicious": False}
    assert compare.urlscan_outcome({"other": 1}) is None
    assert compare.urlscan_outcome(None) is None


def test_agreement_only_when_checks_completed_and_urlscan_present():
    agree = compare.record_comparison(item(score=60), {"score": 100, "malicious": True}, weights=W, degraded=[])
    differ = compare.record_comparison(item(score=10, verdict="caution"), {"score": 100, "malicious": True},
                                       weights=W, degraded=[])
    failed = compare.record_comparison(item(failures=["fetch"]), {"score": 100, "malicious": True},
                                       weights=W, degraded=[])
    missing = compare.record_comparison(item(), None, weights=W, degraded=["threat_feed"])
    assert (agree["agree"], differ["agree"], failed["agree"], missing["agree"]) == (True, False, None, None)
    assert missing["own"]["degraded"] == ["threat_feed"] and agree["own"]["weights_version"] == "v1"


def test_set_label_validates():
    record = compare.record_comparison(item(), None, weights=W, degraded=[])
    assert compare.set_label(record["id"], "smishing")["label"] == "smishing"
    with pytest.raises(ValueError):
        compare.set_label(record["id"], "maybe")
    with pytest.raises(KeyError):
        compare.set_label("nope", "benign")


def test_summary_rates_and_percentiles():
    compare.record_comparison(item(score=60), {"malicious": True}, weights=W, degraded=[])
    wrong = compare.record_comparison(item(score=10, verdict="caution"), {"malicious": True}, weights=W, degraded=[])
    compare.set_label(wrong["id"], "benign")  # 자체(악성 아님)가 맞음
    compare.record_comparison(item(verdict="check_needed", score=0, failures=["fetch_timeout"], total=15.0),
                              None, weights=W, degraded=[])
    compare.record_comparison(item(browser=True, changed=True, total=12.0), None, weights=W, degraded=[])
    summary = compare.summarize()
    assert (summary["records"], summary["pairs"], summary["disagreements"]) == (4, 2, 1)
    assert summary["agreement_rate"] == 0.5 and summary["own_correct_rate"] == 1.0
    assert (summary["labeled_smishing"], summary["labeled_benign"]) == (0, 1)
    assert summary["smishing_caught_rate"] is None and summary["false_danger_rate"] == 0.0
    assert summary["check_failed_rate"] == 0.25 and summary["time_failure_rate"] == 0.25
    assert summary["check_needed_rate"] == 0.25
    assert summary["browser_rate"] == 0.25 and summary["browser_changed_rate"] == 1.0
    assert summary["over_10s_rate"] == 0.5 and summary["resolved_10_to_20s_rate"] == 0.25
    assert summary["total_p95"] == 15.0 and summary["verdicts"] == {"danger": 2, "caution": 1, "check_needed": 1}
    assert not summary["transition_ready"]


def label(record_item: dict, value: str) -> None:
    compare.set_label(compare.record_comparison(record_item, None, weights=W, degraded=[])["id"], value)


def test_transition_needs_labeled_samples_not_urlscan_agreement():
    for _ in range(200):
        compare.record_comparison(item(score=60), {"malicious": True}, weights=W, degraded=[])
    first = compare.summarize()
    assert first["agreement_rate"] == 1.0 and not first["transition_ready"]  # 일치율만으로는 전환하지 않는다
    for _ in range(compare.MIN_LABELED):
        label(item(score=60), "smishing")
        label(item(score=0, verdict="no_signal"), "benign")
    summary = compare.summarize()
    assert (summary["labeled_smishing"], summary["labeled_benign"]) == (50, 50)
    assert summary["smishing_caught_rate"] == 1.0 and summary["false_danger_rate"] == 0.0
    assert summary["transition_ready"]


def test_false_danger_on_benign_blocks_transition():
    for _ in range(compare.MIN_LABELED):
        label(item(score=60), "smishing")
        label(item(score=60), "benign")
    summary = compare.summarize()
    assert summary["false_danger_rate"] == 1.0 and not summary["transition_ready"]


def test_record_pipeline_pairs_urls_and_swallows_failures():
    async def ok():
        return {"degraded": [], "url_verdicts": [item("https://a.example/"), item("https://b.example/", score=0,
                                                                                     verdict="check_needed")]}

    async def boom():
        raise RuntimeError("graph failed")

    async def go():
        urlscan = {"https://a.example/": {"score": 100, "malicious": True}, "https://b.example/": None}
        first = await compare.record_pipeline(asyncio.create_task(ok()), urlscan, W)
        second = await compare.record_pipeline(asyncio.create_task(boom()), urlscan, W)
        return first, second

    first, second = asyncio.run(go())
    assert [r["agree"] for r in first] == [True, None] and second == []


def test_spawn_and_drain_finish_background_work():
    async def go():
        compare.spawn(asyncio.sleep(0.01, result=None))
        await compare.drain()
        return len(compare._BACKGROUND)
    assert asyncio.run(go()) == 0
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_compare.py -q` Expected: FAIL — `ImportError: cannot import name 'compare' from 'backend.src.server.scanner'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/compare.py`:

```python
"""비교 기간 기록: 자체 판정 vs urlscan. 판정 일치율·전환 기준·불일치 라벨.

ponytail: 프로세스 메모리에 저장해 재시작 시 사라진다. 비교 기간이 길어지면 BE 작업 저장소(DB)로 옮긴다.
"""

import asyncio
import math
from collections import Counter
from collections.abc import Coroutine
from datetime import datetime, timezone
from uuid import uuid4

from backend.src.server.scanner.rules import TIME_FAILURES
from backend.src.server.scanner.scorer import Weights

LABELS = frozenset({"smishing", "benign", "unknown"})
MIN_LABELED = 50  # 가설값: 전환 판단에 필요한 스미싱·정상 라벨 각각의 최소 건수
RESPONSE_TARGET_SECONDS = 10.0
EXTENDED_SECONDS = 20.0
RECORDS: dict[str, dict] = {}
_BACKGROUND: set[asyncio.Task] = set()


def urlscan_outcome(parsed: object) -> dict | None:
    """parse_urlscan_result 결과(평평한 score·malicious) 또는 urlscan 원본(verdicts.overall)에서 꺼낸다."""
    if not isinstance(parsed, dict):
        return None
    verdicts = parsed.get("verdicts")
    overall = verdicts.get("overall") if isinstance(verdicts, dict) else None
    overall = overall if isinstance(overall, dict) else {}
    score = parsed.get("score", overall.get("score"))
    malicious = parsed.get("malicious", overall.get("malicious"))
    if score is None and malicious is None:
        return None
    return {"score": score, "malicious": None if malicious is None else bool(malicious)}


def record_comparison(
    item: dict, urlscan: dict | None, *, weights: Weights, degraded: list[str], now: datetime | None = None,
) -> dict:
    own_malicious = item["score"] >= weights.compare_threshold
    malicious = urlscan.get("malicious") if urlscan else None
    record = {
        "id": uuid4().hex,
        "recorded_at": (now or datetime.now(timezone.utc)).isoformat(),
        "url": item["url"],
        "final_url": item["final_url"],
        "own": {
            "score": item["score"], "malicious": own_malicious, "verdict": item["verdict"],
            "weights_version": weights.version, "evidence": item.get("evidence"),
            "reputation": item.get("reputation"), "features": item["features"], "hits": item["hits"],
            "failures": item["failures"], "used_browser": item["used_browser"],
            "browser_changed": item["browser_changed"], "domain_age_days": item.get("domain_age_days"),
            "degraded": list(degraded),
        },
        "urlscan": urlscan,
        "timings": item["timings"],
        # 확인 실패가 있거나 urlscan 값이 없으면 일치율에서 뺀다
        "agree": None if item["failures"] or malicious is None else own_malicious == malicious,
        "label": None,
    }
    RECORDS[record["id"]] = record
    return record


def set_label(record_id: str, label: str) -> dict:
    if label not in LABELS:
        raise ValueError(f"label must be one of {sorted(LABELS)}")
    record = RECORDS[record_id]  # 없으면 KeyError
    record["label"] = label
    return record


def _rate(part: int, whole: int) -> float | None:
    return None if whole == 0 else part / whole


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def summarize(records: list[dict] | None = None) -> dict:
    items = list(RECORDS.values()) if records is None else records
    pairs = [r for r in items if r["agree"] is not None]
    disagreements = [r for r in pairs if not r["agree"]]
    labeled_disagree = [r for r in disagreements if r["label"] in ("smishing", "benign")]
    own_correct = [r for r in labeled_disagree if r["own"]["malicious"] == (r["label"] == "smishing")]
    smishing = [r for r in items if r["label"] == "smishing"]
    benign = [r for r in items if r["label"] == "benign"]
    caught = [r for r in smishing if r["own"]["verdict"] in ("danger", "caution")]
    false_danger = [r for r in benign if r["own"]["verdict"] == "danger"]
    decide = [r["timings"]["decide"] for r in items if r["timings"].get("decide") is not None]
    total = [r["timings"]["total"] for r in items if r["timings"].get("total") is not None]
    browser = [r for r in items if r["own"]["used_browser"]]
    slow = [r for r in items if (r["timings"].get("total") or 0) > RESPONSE_TARGET_SECONDS]
    late_resolved = [r for r in slow if r["timings"]["total"] <= EXTENDED_SECONDS and r["own"]["verdict"] != "check_needed"]
    summary = {
        "records": len(items),
        "pairs": len(pairs),
        # urlscan 은 추적 브랜드만 피싱으로 판정해 국내 택배 사칭을 놓칠 수 있다 → 일치율은 참고값
        "agreement_rate": _rate(len(pairs) - len(disagreements), len(pairs)),
        "disagreements": len(disagreements),
        "labeled_disagreements": len(labeled_disagree),
        "own_correct_rate": _rate(len(own_correct), len(labeled_disagree)),
        "labeled_smishing": len(smishing),
        "labeled_benign": len(benign),
        "smishing_caught_rate": _rate(len(caught), len(smishing)),  # 스미싱 라벨 중 위험·주의로 잡은 비율
        "false_danger_rate": _rate(len(false_danger), len(benign)),  # 정상 라벨 중 위험으로 판정한 비율
        "check_failed_rate": _rate(sum(1 for r in items if r["own"]["failures"]), len(items)),
        "check_needed_rate": _rate(sum(1 for r in items if r["own"]["verdict"] == "check_needed"), len(items)),
        "time_failure_rate": _rate(sum(1 for r in items if set(r["own"]["failures"]) & TIME_FAILURES), len(items)),
        "browser_rate": _rate(len(browser), len(items)),
        "browser_changed_rate": _rate(sum(1 for r in browser if r["own"]["browser_changed"]), len(browser)),
        "over_10s_rate": _rate(len(slow), len(items)),
        "resolved_10_to_20s_rate": _rate(len(late_resolved), len(items)),  # 20초 연장으로 확인 필요를 면한 비율
        "decide_p50": _percentile(decide, 0.50),
        "decide_p95": _percentile(decide, 0.95),
        "total_p95": _percentile(total, 0.95),
        "verdicts": dict(Counter(r["own"]["verdict"] for r in items)),
    }
    # 전환 기준은 urlscan 일치율이 아니라 라벨된 표본으로 본다
    summary["transition_ready"] = bool(
        len(smishing) >= MIN_LABELED and len(benign) >= MIN_LABELED
        and summary["smishing_caught_rate"] >= 0.90 and summary["false_danger_rate"] <= 0.05
        and summary["decide_p50"] is not None and summary["decide_p50"] < 1.0 and summary["decide_p95"] <= 3.0
        and summary["total_p95"] is not None and summary["total_p95"] <= 10.0
        and summary["check_failed_rate"] is not None and summary["check_failed_rate"] <= 0.05
    )
    return summary


async def record_pipeline(pipeline: asyncio.Task, urlscan_by_url: dict[str, object], weights: Weights) -> list[dict]:
    """그림자 그래프 결과와 urlscan 결과를 URL별로 짝지어 기록한다. 실패해도 응답 경로에 영향이 없다."""
    try:
        state = await pipeline
    except Exception as error:  # 그림자 실행 실패는 기록만 하고 삼킨다
        print(f"[SCAN_COMPARE] pipeline failed: {error!r}")
        return []
    degraded = state.get("degraded", [])
    return [
        record_comparison(item, urlscan_outcome(urlscan_by_url.get(item["url"])), weights=weights, degraded=degraded)
        for item in state.get("url_verdicts", [])
    ]


def spawn(coroutine: Coroutine) -> asyncio.Task:
    """응답을 기다리게 하지 않는 백그라운드 작업. 참조를 들고 있어 도중에 사라지지 않게 한다."""
    task = asyncio.create_task(coroutine)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


async def drain() -> None:
    while _BACKGROUND:
        await asyncio.gather(*list(_BACKGROUND), return_exceptions=True)
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_compare.py -q` Expected: 8 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/compare.py backend/tests/scanner/test_compare.py
git commit -m "feat: 비교 기록과 전환 기준 요약"
```

### Task 11: 그림자 실행 연결과 비교 API

**Files:**

- Modify: `main.py` (스킬 서버 진입점, `run_analysis`가 있는 파일)
- Test: `backend/tests/scanner/test_main_shadow.py`

**Interfaces:**

- Consumes: Task 6 `HttpCollector`, `collector_not_connected`, Task 8 `load_default_context`, `signals_from_message_analysis`, Task 9 `build_graph`, `run_pipeline`, Task 10 `compare`
- Produces:
  - 환경 변수 `SCAN_COMPARE`(기본 `1` = 그림자 실행 켬), `SCAN_COMPARE_TOKEN`(없으면 비교 API 404), `COLLECTOR_URL`(없으면 `check_failed: collect`)
  - 모듈 전역 `SCAN_CONTEXT`, `PIPELINE`, `shadow_analyze_message(message)`
  - `GET /api/scan-comparison` → `{summary, records(최근 100개)}`, `POST /api/scan-comparison/{record_id}/label {label}` — 둘 다 헤더 `x-scan-compare-token` 필요, 잘못된 라벨 400, 없는 id 404

사용자 응답은 바꾸지 않는다. `run_analysis` 시작 때 그래프를 백그라운드 작업으로 띄우고, 기존 urlscan 루프의 각 분기에서 URL별 결과를 `urlscan_by_url`에 모은 뒤, 반환 직전에 `compare.spawn`으로 기록을 맡긴다. 그래프가 실패해도 응답은 그대로다. 기존 `main.py`는 문서로 확인된 범위(링크 루프, 실패 문구 3종, `parse_urlscan_result`, `analyze_message_part`)를 기준으로 했다. urlscan 결과를 기다리는 기존 함수 이름이 `get_scan_result`가 아니면 테스트의 `fakes`에서 그 이름만 바꾸고, `import main`으로 서버 모듈을 찾지 못하면 기존 테스트의 import 방식에 맞춘다. 비교 기간에는 문자 분석 LLM을 한 번 더 부른다(호출 2배, 전환 후에는 그래프가 유일한 경로).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_main_shadow.py`:

```python
import asyncio

import pytest
from fastapi.testclient import TestClient

import main
from backend.src.server.scanner import compare

LINKS = ["https://a.example/", "https://b.example/"]


def verdict_item(url: str, score: int) -> dict:
    return {"url": url, "final_url": url, "verdict": "danger" if score >= 50 else "check_needed", "score": score,
            "evidence": score, "reputation": 0, "hits": {}, "features": {}, "failures": [], "used_browser": False,
            "browser_changed": None, "domain_age_days": None, "timings": {"decide": 0.01, "total": 2.0}}


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    compare.RECORDS.clear()

    async def submit(link):
        return None if "b.example" in link else "uuid-a"

    async def result(uuid):
        return {"verdicts": {"overall": {"score": 100, "malicious": True}}}

    async def analyze(message):
        return {"summary": "택배 사칭 의심"}

    monkeypatch.setattr(main, "submit_url_scan", submit)
    monkeypatch.setattr(main, "get_scan_result", result)
    monkeypatch.setattr(main, "analyze_message_part", analyze)
    yield
    compare.RECORDS.clear()


async def fake_pipeline(graph, message, urls):
    return {"degraded": ["threat_feed"], "url_verdicts": [verdict_item(urls[0], 60), verdict_item(urls[1], 0)]}


async def broken_pipeline(graph, message, urls):
    raise RuntimeError("graph failed")


def run(monkeypatch, *, compare_on: bool, pipeline=fake_pipeline) -> str:
    monkeypatch.setattr(main, "SCAN_COMPARE", compare_on)
    monkeypatch.setattr(main, "run_pipeline", pipeline)

    async def go():
        reply = await main.run_analysis(LINKS, "[CJ대한통운] 주소지 확인 바랍니다")
        await compare.drain()
        return reply

    return asyncio.run(go())


def test_shadow_run_records_without_changing_response(monkeypatch):
    baseline = run(monkeypatch, compare_on=False)
    assert compare.RECORDS == {}
    assert run(monkeypatch, compare_on=True) == baseline
    records = sorted(compare.RECORDS.values(), key=lambda r: r["url"])
    assert [r["agree"] for r in records] == [True, None]  # b 는 urlscan 요청 실패라 일치율에서 제외
    assert records[0]["own"]["degraded"] == ["threat_feed"]


def test_pipeline_failure_does_not_break_response(monkeypatch):
    baseline = run(monkeypatch, compare_on=False)
    assert run(monkeypatch, compare_on=True, pipeline=broken_pipeline) == baseline
    assert compare.RECORDS == {}


def test_comparison_api_needs_token_and_labels(monkeypatch):
    run(monkeypatch, compare_on=True)
    client = TestClient(main.app)
    assert client.get("/api/scan-comparison").status_code == 404  # 토큰 미설정이면 숨김
    monkeypatch.setattr(main, "SCAN_COMPARE_TOKEN", "secret")
    assert client.get("/api/scan-comparison", headers={"x-scan-compare-token": "wrong"}).status_code == 404
    headers = {"x-scan-compare-token": "secret"}
    body = client.get("/api/scan-comparison", headers=headers).json()
    assert body["summary"]["records"] == 2 and len(body["records"]) == 2
    record_id = body["records"][0]["id"]
    assert client.post(f"/api/scan-comparison/{record_id}/label", json={"label": "maybe"}, headers=headers).status_code == 400
    assert client.post("/api/scan-comparison/nope/label", json={"label": "benign"}, headers=headers).status_code == 404
    labeled = client.post(f"/api/scan-comparison/{record_id}/label", json={"label": "smishing"}, headers=headers)
    assert labeled.status_code == 200 and labeled.json()["label"] == "smishing"
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_main_shadow.py -q` Expected: FAIL — `AttributeError: ... has no attribute 'SCAN_COMPARE'`

- [ ] **Step 3: 구현**

(1) 기존 함수 정의 뒤, `run_analysis` 앞에 추가한다 (`asyncio`·`os`·`HTTPException`·`Request` import가 없으면 함께 추가):

```python
from backend.src.server.scanner import compare
from backend.src.server.scanner.collector import HttpCollector, collector_not_connected
from backend.src.server.scanner.graph import build_graph, run_pipeline
from backend.src.server.scanner.investigate import load_default_context
from backend.src.server.scanner.rules import signals_from_message_analysis

SCAN_COMPARE = os.getenv("SCAN_COMPARE", "1") == "1"
SCAN_COMPARE_TOKEN = os.getenv("SCAN_COMPARE_TOKEN", "")
COLLECTOR_URL = os.getenv("COLLECTOR_URL", "")
SCAN_CONTEXT = load_default_context(HttpCollector(COLLECTOR_URL) if COLLECTOR_URL else collector_not_connected)


async def shadow_analyze_message(message: str):
    # ponytail: 비교 기간에는 기존 응답과 별도로 문자 분석을 한 번 더 부른다 (LLM 호출 2배). 전환 후 그래프가 유일한 경로가 된다.
    return signals_from_message_analysis(await analyze_message_part(message))


PIPELINE = build_graph(SCAN_CONTEXT, shadow_analyze_message)
```

(2) `run_analysis`에 `# Task 11` 줄만 넣는다. 나머지는 기존 코드 그대로다:

```python
async def run_analysis(links: list[str], message: str) -> str:
    pipeline = asyncio.create_task(run_pipeline(PIPELINE, message, list(links))) if SCAN_COMPARE else None  # Task 11
    urlscan_by_url: dict[str, object] = {}  # Task 11
    ...  # 기존 코드
    for link in links:
        try:
            ...
            if not uuid:
                urlscan_by_url[link] = None  # Task 11
                ...  # 기존 "⚠️ 검사 요청 실패" 처리
                continue
            ...
            if scan_result is None:
                urlscan_by_url[link] = None  # Task 11
                ...  # 기존 "⚠️ 검사 시간 초과" 처리
                continue
            parsed_result = parse_urlscan_result(scan_result)
            urlscan_by_url[link] = parsed_result  # Task 11
            ...
        except Exception as e:
            urlscan_by_url[link] = None  # Task 11
            ...  # 기존 "⚠️ 분석 실패" 처리
    if pipeline is not None:  # Task 11: 응답을 기다리게 하지 않고 백그라운드에서 기록
        compare.spawn(compare.record_pipeline(pipeline, urlscan_by_url, SCAN_CONTEXT.weights))
    ...  # 기존 반환
```

(3) 비교 기록 API를 추가한다:

```python
def _check_compare_token(request: Request) -> None:
    if not SCAN_COMPARE_TOKEN or request.headers.get("x-scan-compare-token") != SCAN_COMPARE_TOKEN:
        raise HTTPException(status_code=404)


@app.get("/api/scan-comparison")
async def scan_comparison(request: Request) -> dict:
    _check_compare_token(request)
    records = list(compare.RECORDS.values())
    return {"summary": compare.summarize(records), "records": records[-100:]}


@app.post("/api/scan-comparison/{record_id}/label")
async def label_scan_comparison(record_id: str, request: Request) -> dict:
    _check_compare_token(request)
    body = await request.json()
    try:
        return compare.set_label(record_id, str(body.get("label")))
    except KeyError:
        raise HTTPException(status_code=404, detail="record not found")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_main_shadow.py -q` Expected: 3 passed

Run: `python -m pytest backend/tests/scanner -q` Expected: 130 passed

- [ ] **Step 5: 커밋**

```bash
git add main.py backend/tests/scanner/test_main_shadow.py
git commit -m "feat: 자체 파이프라인 그림자 실행과 비교 API"
```

## 실행 후 수동 확인

1. 목록 동기화: `python -m backend.src.server.scanner.sync_lists` — 허용된 출처가 없거나 탐지일 90일 이내 항목이 0건이면 그 사실을 출력하고 `threat_feed.txt`를 만들지 않는지, 이때 비교 기록의 `degraded`에 `threat_feed`가 남는지
2. 수집기 컨테이너: `playwright install chromium` 후 `uvicorn backend.src.server.scanner.collector_app:app --port 8100`, `curl -X POST localhost:8100/collect -H 'content-type: application/json' -d '{"url": "<확인할 URL>", "browser_allowed": true}'`로 일반 페이지·JS 렌더링 페이지·APK 링크를 확인. `http://169.254.169.254/`가 `blocked_address`로 끝나는지
3. 그림자 실행: `COLLECTOR_URL`·`SCAN_COMPARE_TOKEN`을 설정하고 실제 문자 몇 건을 보낸 뒤, 사용자 응답이 그대로인지와 `GET /api/scan-comparison`(헤더 `x-scan-compare-token`)에 기록이 쌓이는지
4. `.kr` RDAP: 기록의 `domain_age` 실패 비율
5. 공용 플랫폼 도메인을 허용 목록에 넣으면 서버가 기동하지 않는지
6. 브라우저 자원: 동시 2페이지 부하에서 컨테이너 메모리, 50페이지 재시작 뒤 메모리가 돌아오는지
7. 비교 기간 지표를 주기적으로 본다: `labeled_smishing`·`labeled_benign`(라벨 표본이 모이는 속도), `smishing_caught_rate`, `false_danger_rate`, `check_failed_rate`, `check_needed_rate`, `time_failure_rate`, `resolved_10_to_20s_rate`, `browser_changed_rate`. `agreement_rate`는 참고로만 본다
8. 전체 테스트: `python -m pytest backend/tests/scanner -q` → 130 passed