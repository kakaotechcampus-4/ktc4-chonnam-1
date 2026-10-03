# 자체 URL 점수기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** urlscan 점수를 대체할 메인 서버 점수기(-100~100)를 만들고, urlscan 과 병행 실행해 판정 일치율을 기록한다.

**Architecture:** `backend/src/server/scanner/` 에 URL 특징 조회(RDAP·로컬 목록), 페이지 특징 추출(`ai.page.inspect_html` 재사용), I/O 없는 점수기, 비교 기록을 둔다. `main.py` 의 URL 루프가 urlscan 과 동시에 자체 스캔을 시작하고, urlscan 결과가 나오면 두 점수를 인메모리 비교 저장소에 남긴다.

**Tech Stack:** Python 3.13, httpx(설치됨), PyYAML(설치됨), stdlib(`html.parser`, `ipaddress`, `difflib`, `zipfile`), FastAPI, pytest

**Spec:** `docs/superpowers/specs/2026-09-29-internal-url-scanner-design.md`

## 이 계획의 범위

- **포함:** 스펙 §1 의 ①③④⑥, §2 전부, §3 의 기록·요약·라벨, §4 오류 처리, §5 보안, §6 테스트 중 점수기 쪽.
- **제외 (별도 계획):**
  - ② 격리 서버 쪽 조치(웜 풀, 리소스 차단, 7초 수집). 격리 서버 코드는 이 저장소에 없다. 여기서는 `CollectedPage` 입력 계약과 "연결 전" 수집기만 둔다. 연결 전에는 필수 특징 `credential_form` 을 확인할 수 없으므로 **자체 점수는 항상 `null` 로 기록된다.** 이것이 올바른 동작이다.
  - `scheme.md` 스키마 이행(`url.official` → `DomainMatch`, wire `url.scan`, urlscan 을 응답 경로에서 제거). 현재 `official` 이 urlscan 점수로 만들어지므로, urlscan 을 백그라운드로 내리는 일은 이 이행과 함께 해야 한다. **5~10초 응답 시간 목표는 그 이행 계획에서 달성된다.** 이 계획은 점수기와 비교 기록까지만 만든다.
  - 스펙 §2 의 2단계(로지스틱 회귀 학습). 라벨된 불일치 사례가 수백 건 쌓인 뒤의 일이다. 이 계획은 학습 자료가 될 특징값·라벨을 기록해 둔다.
- **스펙과 다른 점:**
  - 스펙 §4 의 "WHOIS 2초 → RDAP 1초" 대신 **RDAP 단일 조회(2초)** 로 한다. WHOIS(포트 43)는 새 의존성이 필요하다. 비교 기간에 `.kr` 조회 실패율이 높으면 그때 WHOIS 를 추가한다.
  - **서비스를 판매할 예정이므로 상업적 사용이 확인된 데이터 출처만 쓴다.** 2026-09-29 확인 결과:
    - Google Safe Browsing: 비상업 전용(상업용은 유료 Web Risk API) → 사용하지 않는다.
    - OpenPhish Community 피드: 상업적 사용 불가 → `prohibited` 로 기록하고 받지 않는다.
    - Tranco 기본 목록: 비상업(CC BY-NC) 출처(Cloudflare Radar)가 섞여 있다 → 해당 출처를 뺀 사용자 정의 목록만 쓴다.
    - 위협 피드 1순위 후보는 공공데이터포털의 KISA 피싱사이트 URL. 공공누리 유형을 확인하기 전까지는 `unverified` 로 둔다.
  - 그래서 데이터 출처를 `sources.yaml` 에 라이선스 상태와 함께 선언하고, 동기화 스크립트는 `allowed` 인 출처만 받는다 (Task 4).
    **라이선스가 확인된 위협 피드가 생기기 전까지는 필수 특징 `threat_feed` 를 확인할 수 없어 모든 점수가 `null` 이다.** 코드는 완성하되 운영 데이터는 아래 선행 작업이 끝나야 채워진다.
  - 화이트리스트 항목에 확인 출처·날짜를 필수로 두고, 공유 도메인(단축 URL·공용 플랫폼)이 들어가면 서버 기동을 막는다 (Task 2, Task 6).

## 선행 작업 (사람이 하는 일, 코드 작업과 병렬)

코드 작업을 막지는 않지만, 끝나야 점수가 실제로 계산된다.

- [ ] **라이선스 확인 — 위협 피드:** 공공데이터포털 KISA 피싱사이트 URL 데이터의 공공누리 유형을 확인한다. 상업적 이용이 허용되면 `backend/src/server/scanner/data/sources.yaml` 의 `kisa_phishing_urls` 항목에 파일 URL·URL 열 이름을 채우고 `commercial_use: allowed` 로 바꾼다. 허용되지 않으면 유료 피드(예: OpenPhish Premium) 구매를 팀에서 결정한다.
- [ ] **라이선스 확인 — 인기 도메인:** Tranco 에서 비상업 출처를 뺀 사용자 정의 목록을 만들고, 남은 출처들의 라이선스를 확인한 뒤 `sources.yaml` 의 `tranco_custom` 에 URL 을 채운다.
- [ ] **라이선스 확인 — urlscan:** 판매 전에 urlscan 의 상업적 이용 조건을 확인한다. 비교 기간(판매 전)에만 쓰고 전환 후에는 끈다.
- [ ] **화이트리스트 수집:** 택배사별 GitHub 이슈 5개(CJ대한통운, 우체국, 한진, 롯데, 로젠)를 만든다. 등록 도메인 단위로 3~8개씩, 총 15~40개를 예상한다. 출처는 공식 앱·홈페이지·고객센터 안내, 실제 받은 정상 배송 알림 문자의 링크. 항목 형식은 Task 2 의 `whitelist.yaml` 형식을 따른다. `naver.me`·`bit.ly`·`kakao.com` 같은 공유 도메인은 넣지 않는다 (최종 URL 대조로 처리된다).

## Global Constraints

- 새 의존성 추가 금지. httpx, PyYAML, stdlib 만 쓴다.
- 외부 데이터는 `sources.yaml` 에 `commercial_use: allowed` 로 선언된 출처만 운영에 쓴다. Google Safe Browsing·OpenPhish Community 는 쓰지 않는다.
- 화이트리스트 도메인은 사람이 확인한 출처(`source`)와 날짜(`verified_at`)가 있어야 하고, 공유 도메인은 금지한다.
- 의존 방향 `backend` → `ai` 만 허용. `ai/` 는 수정하지 않는다.
- 점수기(`scorer.py`)와 페이지 특징 추출(`page_features.py`)은 네트워크·LLM·이미지 모델을 쓰지 않는다.
- `score` 는 `int` 이고 범위는 [-100, 100], 또는 `None`.
- 필수 특징: `threat_feed`, `new_domain`, `old_domain`, `credential_form`. 하나라도 `None` 이면 `score = None`.
- 비교용 악성 기준: `score >= 50` (wire 에 싣지 않음).
- 시간 상한: RDAP 2초, URL 특징 전체 3초, 수집기 7초, 특징 추출 + 점수 계산 500ms.
- 의심 URL 에 접속하지 않는다. 네트워크는 RDAP 조회(도메인 문자열)와 목록 동기화 스크립트에만 쓴다.
- 테스트는 저장소 루트에서 실행한다: `python -m pytest backend/tests/scanner -q`
- 테스트에서 실제 외부 API 를 호출하지 않는다 (`backend/tests/README.md` 규칙). HTTP 는 `httpx.MockTransport` 로 대체한다.

## Review Focus

1. **대문자·끝 점·포트가 붙은 호스트** (`https://Delivery-Example.COM.:443/a`) — 정규화된 같은 도메인으로 취급해야 한다. → Task 2 테스트
2. **RDAP 가 404, 등록 이벤트 없음, 깨진 JSON, 이상한 날짜를 반환** — 예외 없이 "확인 못 함"(`None`)이 되어야 한다. → Task 3 테스트
3. **IP 주소 URL** — 도메인 나이를 조회할 수 없다. 이것이 "확인 실패"로 처리되어 항상 `null` 이 되면 안 된다. 나이 특징은 해당 없음(`False`)으로 둔다. → Task 3 테스트
4. **상대 경로·빈 값·`javascript:` 폼 action** — 다른 도메인 전송으로 오판하면 안 된다. → Task 5 테스트
5. **자체 스캔 중 예외** — urlscan 경로와 사용자 응답을 깨면 안 된다. 기록만 건너뛴다. → Task 7 테스트

---

## File Structure

| 파일 | 책임 |
|---|---|
| `backend/src/server/scanner/__init__.py` | 패키지 표시 (빈 파일) |
| `backend/src/server/scanner/scorer.py` | 가중치 로딩·검증, 특징 → 점수 (순수 함수) |
| `backend/src/server/scanner/weights/v1.yaml` | 초기 가중치와 필수 특징 |
| `backend/src/server/scanner/url_features.py` | 호스트 정규화, URL 문자열 특징, 로컬 목록, RDAP 도메인 나이, URL 특징 수집 |
| `backend/src/server/scanner/data/url_lists.yaml` | 남용 TLD·단축 URL·공유 플랫폼 도메인 목록 |
| `backend/src/server/scanner/data/reported_domains.txt` | 자체 신고 도메인 (초기 빈 목록) |
| `backend/src/server/scanner/data/sources.yaml` | 외부 데이터 출처와 라이선스 상태 |
| `backend/src/server/scanner/sync_lists.py` | `allowed` 출처만 내려받는 동기화 CLI |
| `backend/src/server/data/whitelist.yaml` (수정) | 도메인 항목 형식(출처·확인 날짜) 주석 |
| `backend/src/server/scanner/page_features.py` | `CollectedPage` 계약, 페이지 특징 추출 |
| `backend/src/server/scanner/service.py` | 수집기 타입, 스캔 실행(`scan_url`), 기본 컨텍스트 |
| `backend/src/server/scanner/compare.py` | 비교 기록 저장소, 라벨, 요약, 스캔 태스크 기록 |
| `main.py` (수정) | URL 루프에 자체 스캔 병행, 비교 API 2개 |
| `.gitignore` (수정) | 내려받은 목록 파일 제외 |
| `backend/tests/scanner/test_*.py` | 각 모듈 테스트 |

---

### Task 1: 점수기와 가중치 v1

**Files:**
- Create: `backend/src/server/scanner/__init__.py`
- Create: `backend/src/server/scanner/scorer.py`
- Create: `backend/src/server/scanner/weights/v1.yaml`
- Test: `backend/tests/scanner/test_scorer.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `FEATURE_CODES: frozenset[str]` (14개 코드)
  - `@dataclass(frozen=True) class Weights: version: str; points: dict[str, int]; required: frozenset[str]`
  - `@dataclass(frozen=True) class ScoreResult: score: int | None; hits: dict[str, int]; null_reason: str | None`
  - `load_weights(path: Path = WEIGHTS_DIR / "v1.yaml") -> Weights` (잘못되면 `ValueError`)
  - `score(features: dict[str, bool | None], weights: Weights) -> ScoreResult`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_scorer.py`:

```python
from pathlib import Path

import pytest

from backend.src.server.scanner.scorer import (
    FEATURE_CODES, ScoreResult, Weights, load_weights, score,
)


def all_false() -> dict[str, bool | None]:
    return {code: False for code in FEATURE_CODES}


def test_v1_loads_with_all_codes_and_required():
    weights = load_weights()
    assert weights.version == "v1"
    assert set(weights.points) == FEATURE_CODES
    assert weights.required == {"threat_feed", "new_domain", "old_domain", "credential_form"}


def test_no_hits_scores_zero():
    assert score(all_false(), load_weights()) == ScoreResult(0, {}, None)


def test_hits_are_summed_with_their_points():
    features = all_false() | {"new_domain": True, "credential_form": True}
    result = score(features, load_weights())
    assert result.score == 80
    assert result.hits == {"credential_form": 40, "new_domain": 40}


def test_score_is_clamped_to_range():
    features = all_false() | {"threat_feed": True, "app_download": True}
    assert score(features, load_weights()).score == 100
    low = Weights("t", {code: 0 for code in FEATURE_CODES} | {"old_domain": -80, "popular_domain": -80},
                  frozenset())
    features = all_false() | {"old_domain": True, "popular_domain": True}
    assert score(features, low).score == -100


def test_unknown_required_feature_gives_null_with_reason():
    features = all_false() | {"credential_form": None, "threat_feed": None}
    result = score(features, load_weights())
    assert result.score is None
    assert result.null_reason == "required_unknown:credential_form,threat_feed"


def test_unknown_optional_feature_counts_as_zero():
    features = all_false() | {"popular_domain": None, "new_domain": True}
    assert score(features, load_weights()).score == 40


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "w.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_rejects_unknown_code(tmp_path):
    text = (Path(__file__).parents[2] / "src/server/scanner/weights/v1.yaml").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        load_weights(write(tmp_path, text + "  bogus_code: 5\n"))


def test_load_rejects_non_integer_points(tmp_path):
    text = (Path(__file__).parents[2] / "src/server/scanner/weights/v1.yaml").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="integer"):
        load_weights(write(tmp_path, text.replace("threat_feed: 100", "threat_feed: 1.5")))
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_scorer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.src.server.scanner'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/__init__.py`: 빈 파일.

`backend/src/server/scanner/weights/v1.yaml`:

```yaml
# 초기 가설값. 값을 바꾸면 v2.yaml 을 새로 만든다 (기록된 점수와 버전을 맞추기 위해).
version: v1
required: [threat_feed, new_domain, old_domain, credential_form]
points:
  threat_feed: 100
  new_domain: 40
  old_domain: -20
  popular_domain: -30
  ip_or_port: 30
  homoglyph: 30
  abused_tld: 15
  url_shortener: 15
  reported_before: 50
  credential_form: 40
  app_download: 50
  cross_domain_form: 25
  brand_on_page: 30
  cross_domain_redirect: 20
```

`backend/src/server/scanner/scorer.py`:

```python
"""특징 → 점수. I/O 없는 결정적 함수라 같은 특징이면 항상 같은 점수가 나온다."""

from dataclasses import dataclass
from pathlib import Path

import yaml

WEIGHTS_DIR = Path(__file__).parent / "weights"
FEATURE_CODES = frozenset({
    "threat_feed", "new_domain", "old_domain", "popular_domain", "ip_or_port",
    "homoglyph", "abused_tld", "url_shortener", "reported_before",
    "credential_form", "app_download", "cross_domain_form", "brand_on_page",
    "cross_domain_redirect",
})


@dataclass(frozen=True)
class Weights:
    version: str
    points: dict[str, int]
    required: frozenset[str]


@dataclass(frozen=True)
class ScoreResult:
    score: int | None
    hits: dict[str, int]
    null_reason: str | None


def load_weights(path: Path = WEIGHTS_DIR / "v1.yaml") -> Weights:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    points = data["points"]
    required = frozenset(data["required"])
    unknown = (set(points) | required) - FEATURE_CODES
    if unknown:
        raise ValueError(f"unknown feature codes: {sorted(unknown)}")
    missing = FEATURE_CODES - set(points)
    if missing:
        raise ValueError(f"missing feature codes: {sorted(missing)}")
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in points.values()):
        raise ValueError("points must be integer")
    return Weights(str(data["version"]), dict(points), required)


def score(features: dict[str, bool | None], weights: Weights) -> ScoreResult:
    unknown = sorted(code for code in weights.required if features.get(code) is None)
    if unknown:
        return ScoreResult(None, {}, "required_unknown:" + ",".join(unknown))
    hits = {code: weights.points[code] for code in sorted(FEATURE_CODES) if features.get(code) is True}
    return ScoreResult(max(-100, min(100, sum(hits.values()))), hits, None)
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_scorer.py -q`
Expected: 8 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/__init__.py backend/src/server/scanner/scorer.py backend/src/server/scanner/weights/v1.yaml backend/tests/scanner/test_scorer.py
git commit -m "feat: 자체 URL 점수기와 가중치 v1"
```

---

### Task 2: URL 문자열 특징

**Files:**
- Create: `backend/src/server/scanner/url_features.py`
- Create: `backend/src/server/scanner/data/url_lists.yaml`
- Modify: `backend/src/server/data/whitelist.yaml:1-3` (상단 주석)
- Test: `backend/tests/scanner/test_url_features.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `DATA_DIR: Path` (`scanner/data`)
  - `hostname(url: str) -> str` — 소문자, 포트·끝 점 제거, 없으면 `""`
  - `registrable_domain(host: str) -> str` — IP 는 그대로
  - `is_ip(host: str) -> bool`
  - `@dataclass(frozen=True) class UrlLists: abused_tlds: frozenset[str]; shorteners: frozenset[str]; shared_hosts: frozenset[str] = frozenset()`
  - `load_url_lists(path: Path = DATA_DIR / "url_lists.yaml") -> UrlLists`
  - `load_brands(path: Path = WHITELIST_PATH) -> dict[str, frozenset[str]]` — 표시 이름 → 공식 도메인. 각 도메인 항목은 `{domain, source, verified_at}` 이고 셋 중 하나라도 비면 `ValueError`
  - `validate_whitelist(brands: dict[str, frozenset[str]], lists: UrlLists) -> None` — 공유 도메인(단축 URL·공용 플랫폼)이 있으면 `ValueError`
  - `parse_url_features(url: str, lists: UrlLists, official_domains: frozenset[str]) -> dict[str, bool]` — 키 `ip_or_port`, `homoglyph`, `abused_tld`, `url_shortener`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_url_features.py`:

```python
import pytest

from backend.src.server.scanner.url_features import (
    UrlLists, hostname, is_ip, load_brands, load_url_lists, parse_url_features, registrable_domain,
    validate_whitelist,
)

LISTS = UrlLists(abused_tlds=frozenset({"xyz"}), shorteners=frozenset({"bit.ly"}))
OFFICIAL = frozenset({"cjlogistics.com"})


def features(url: str) -> dict[str, bool]:
    return parse_url_features(url, LISTS, OFFICIAL)


def test_hostname_normalizes_case_trailing_dot_and_port():
    assert hostname("https://Delivery-Example.COM.:443/a") == "delivery-example.com"
    assert hostname("not a url") == ""


def test_registrable_domain_handles_korean_second_level():
    assert registrable_domain("a.b.example.co.kr") == "example.co.kr"
    assert registrable_domain("login.example.com") == "example.com"
    assert registrable_domain("192.0.2.1") == "192.0.2.1"


def test_is_ip():
    assert is_ip("192.0.2.1") and is_ip("::1")
    assert not is_ip("example.com")


def test_ip_or_port():
    assert features("http://192.0.2.1/x")["ip_or_port"]
    assert features("https://example.com:8080/")["ip_or_port"]
    assert features("https://example.com:99999/")["ip_or_port"]  # 잘못된 포트도 비표준으로 본다
    assert not features("https://example.com:443/")["ip_or_port"]
    assert not features("https://example.com/")["ip_or_port"]


def test_homoglyph_by_punycode_or_similarity():
    assert features("https://xn--80ak6aa92e.com/")["homoglyph"]
    assert features("https://cjlogistlcs.com/")["homoglyph"]
    assert not features("https://cjlogistics.com/")["homoglyph"]
    assert not features("https://naver.com/")["homoglyph"]


def test_abused_tld_and_shortener():
    assert features("https://parcel.xyz/")["abused_tld"]
    assert not features("http://192.0.2.1/")["abused_tld"]
    assert features("https://bit.ly/abc")["url_shortener"]
    assert not features("https://example.com/")["url_shortener"]


def test_shipped_lists_and_brands_load():
    lists = load_url_lists()
    assert "xyz" in lists.abused_tlds and "bit.ly" in lists.shorteners
    assert "naver.me" in lists.shared_hosts
    brands = load_brands()
    assert "CJ대한통운" in brands
    validate_whitelist(brands, lists)


def write_whitelist(tmp_path, domains_yaml: str):
    path = tmp_path / "whitelist.yaml"
    path.write_text("test:\n  display_name: 테스트택배\n  domains:\n" + domains_yaml, encoding="utf-8")
    return path


def test_load_brands_reads_domain_entries(tmp_path):
    path = write_whitelist(tmp_path, (
        "    - domain: Test-Parcel.com\n"
        "      source: 공식 홈페이지 고객센터 안내\n"
        "      verified_at: 2026-09-29\n"
    ))
    assert load_brands(path) == {"테스트택배": frozenset({"test-parcel.com"})}


def test_load_brands_requires_source_and_date(tmp_path):
    path = write_whitelist(tmp_path, "    - domain: test-parcel.com\n")
    with pytest.raises(ValueError, match="source"):
        load_brands(path)


def test_validate_whitelist_rejects_shared_hosts():
    lists = UrlLists(frozenset(), frozenset({"bit.ly"}), frozenset({"naver.me"}))
    with pytest.raises(ValueError, match="naver.me"):
        validate_whitelist({"테스트택배": frozenset({"naver.me"})}, lists)
    with pytest.raises(ValueError, match="bit.ly"):
        validate_whitelist({"테스트택배": frozenset({"bit.ly"})}, lists)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_url_features.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.src.server.scanner.url_features'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/data/url_lists.yaml`:

```yaml
# 초기 목록. 출처·갱신 주기는 스펙 §7 열린 질문 — 비교 기간에 적중률을 보고 조정한다.
abused_tlds: [xyz, top, shop, click, icu, cyou, buzz, rest, vip, live, sbs, cfd]
shorteners: [bit.ly, han.gl, me2.do, vo.la, t.co, tinyurl.com, is.gd, url.kr, buly.kr, c11.kr, naver.me]
# 누구나 페이지·링크를 만들 수 있는 공용 플랫폼. 화이트리스트에 넣으면 공격자 페이지도 "공식"이 된다.
shared_hosts: [naver.me, kakao.com, naver.com, google.com, forms.gle, notion.site, github.io,
               blogspot.com, tistory.com, wixsite.com, netlify.app, vercel.app, web.app, pages.dev]
```

`backend/src/server/data/whitelist.yaml` 상단 주석 3줄을 다음으로 바꾼다 (택배사 항목과 `domains: []` 는 그대로 둔다):

```yaml
# 공식 택배사 도메인. 판정의 근거이므로 backend 소유입니다.
# 알림 발송용 별도 도메인·단축 도메인까지 모아야 합니다. 수집은 택배사별 이슈로 쪼개세요.
# 등록 도메인 단위로 적습니다 (www.cjlogistics.com → cjlogistics.com). 서브도메인은 자동 포함됩니다.
# 공유 도메인(naver.me, bit.ly, kakao.com 등)은 금지 — 서버가 기동하지 않습니다. 최종 URL 대조로 처리됩니다.
# 항목 형식:
#   domains:
#     - domain: example-parcel.com
#       source: 공식 앱 고객센터 안내 화면     # 사람이 확인한 출처
#       verified_at: 2026-09-29               # 확인 날짜
```

`backend/src/server/scanner/url_features.py`:

```python
"""URL·도메인 특징. 의심 URL 에 접속하지 않고 문자열·로컬 목록·RDAP 로만 판단한다."""

import difflib
import ipaddress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

DATA_DIR = Path(__file__).parent / "data"
WHITELIST_PATH = Path(__file__).parents[1] / "data" / "whitelist.yaml"
# ponytail: 공개 접미사 목록 대신 자주 쓰는 2단계 접미사만. 오판이 보이면 publicsuffix 목록으로 교체.
_TWO_LEVEL_SUFFIXES = frozenset({
    "co.kr", "or.kr", "go.kr", "ne.kr", "re.kr", "pe.kr", "ac.kr", "co.uk", "com.cn",
})


@dataclass(frozen=True)
class UrlLists:
    abused_tlds: frozenset[str]
    shorteners: frozenset[str]
    shared_hosts: frozenset[str] = frozenset()


def load_url_lists(path: Path = DATA_DIR / "url_lists.yaml") -> UrlLists:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return UrlLists(
        frozenset(data["abused_tlds"]), frozenset(data["shorteners"]),
        frozenset(data.get("shared_hosts") or []),
    )


def _whitelist_domain(entry: object) -> str:
    """사람이 확인한 근거가 없는 도메인은 공식으로 인정하지 않는다."""
    fields = entry if isinstance(entry, dict) else {}
    for key in ("domain", "source", "verified_at"):
        if not str(fields.get(key) or "").strip():
            raise ValueError(f"whitelist entry needs {key}: {entry!r}")
    return str(fields["domain"]).strip().lower()


def load_brands(path: Path = WHITELIST_PATH) -> dict[str, frozenset[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        entry["display_name"]: frozenset(_whitelist_domain(item) for item in entry.get("domains") or [])
        for entry in data.values()
    }


def validate_whitelist(brands: dict[str, frozenset[str]], lists: UrlLists) -> None:
    shared = lists.shorteners | lists.shared_hosts
    banned = sorted(domain for domains in brands.values() for domain in domains if domain in shared)
    if banned:
        raise ValueError(f"shared domains cannot be official: {banned}")


def hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").rstrip(".")


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def registrable_domain(host: str) -> str:
    if is_ip(host):
        return host
    labels = host.split(".")
    size = 3 if ".".join(labels[-2:]) in _TWO_LEVEL_SUFFIXES else 2
    return ".".join(labels[-size:])


def parse_url_features(url: str, lists: UrlLists, official_domains: frozenset[str]) -> dict[str, bool]:
    host = hostname(url)
    ip = is_ip(host)
    try:
        port = urlsplit(url).port
    except ValueError:
        port = -1
    domain = registrable_domain(host)
    similar = any(
        domain != official and difflib.SequenceMatcher(None, domain, official).ratio() >= 0.8
        for official in official_domains
    )
    return {
        "ip_or_port": ip or port not in (None, 80, 443),
        "homoglyph": any(label.startswith("xn--") for label in host.split(".")) or similar,
        "abused_tld": not ip and host.rsplit(".", 1)[-1] in lists.abused_tlds,
        "url_shortener": domain in lists.shorteners,
    }
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_url_features.py -q`
Expected: 10 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/url_features.py backend/src/server/scanner/data/url_lists.yaml backend/src/server/data/whitelist.yaml backend/tests/scanner/test_url_features.py
git commit -m "feat: 점수기 URL 문자열 특징"
```

---

### Task 3: 로컬 목록, RDAP 도메인 나이, URL 특징 수집

**Files:**
- Modify: `backend/src/server/scanner/url_features.py` (끝에 추가)
- Create: `backend/src/server/scanner/data/reported_domains.txt`
- Test: `backend/tests/scanner/test_url_lookup.py`

**Interfaces:**
- Consumes: Task 2 의 `hostname`, `is_ip`, `registrable_domain`, `parse_url_features`, `UrlLists`, `DATA_DIR`
- Produces:
  - `URL_FEATURE_CODES: tuple[str, ...]` (9개)
  - `@dataclass(frozen=True) class LocalSets: threat_feed: frozenset[str] | None; popular: frozenset[str]; reported: frozenset[str]` — `threat_feed is None` 은 미러를 읽지 못했다는 뜻
  - `load_local_sets(data_dir: Path = DATA_DIR) -> LocalSets`
  - `RDAP_URL: str`, `RDAP_TIMEOUT_SECONDS = 2.0`
  - `async lookup_domain_age_days(domain: str, client: httpx.AsyncClient, *, now: datetime) -> int | None`
  - `domain_age_features(days: int | None) -> dict[str, bool | None]` — 키 `new_domain`, `old_domain`
  - `async collect_url_features(url: str, *, client: httpx.AsyncClient, lists: UrlLists, sets: LocalSets, official_domains: frozenset[str], now: datetime) -> dict[str, bool | None]` — 키는 `URL_FEATURE_CODES`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_url_lookup.py`:

```python
import asyncio
from datetime import datetime, timezone

import httpx

from backend.src.server.scanner.url_features import (
    URL_FEATURE_CODES, LocalSets, UrlLists, collect_url_features, domain_age_features,
    load_local_sets, lookup_domain_age_days,
)

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)
LISTS = UrlLists(frozenset(), frozenset())


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
    assert age(registered("2026-09-19T00:00:00Z")) == 10
    assert age(registered("2026-09-19")) == 10  # 시간대 없는 날짜


def test_age_unknown_on_bad_responses():
    assert age(lambda r: httpx.Response(404)) is None
    assert age(lambda r: httpx.Response(200, json={"events": []})) is None
    assert age(lambda r: httpx.Response(200, json=[1, 2])) is None
    assert age(lambda r: httpx.Response(200, text="<html>")) is None
    assert age(registered("not-a-date")) is None


def test_age_unknown_on_timeout():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    assert age(slow) is None


def test_domain_age_features():
    assert domain_age_features(None) == {"new_domain": None, "old_domain": None}
    assert domain_age_features(10) == {"new_domain": True, "old_domain": False}
    assert domain_age_features(800) == {"new_domain": False, "old_domain": True}


def test_load_local_sets_missing_feed_is_none(tmp_path):
    (tmp_path / "tranco_top100k.txt").write_text("naver.com\n", encoding="utf-8")
    sets = load_local_sets(tmp_path)
    assert sets.threat_feed is None
    assert sets.popular == {"naver.com"}
    assert sets.reported == frozenset()


def test_load_local_sets_skips_comments_and_blanks(tmp_path):
    (tmp_path / "threat_feed.txt").write_text("# header\n\nEvil.XYZ\n", encoding="utf-8")
    assert load_local_sets(tmp_path).threat_feed == {"evil.xyz"}


def collect(url: str, sets: LocalSets, handler=registered("2026-09-19T00:00:00Z")):
    async def run():
        async with client_returning(handler) as client:
            return await collect_url_features(url, client=client, lists=LISTS, sets=sets,
                                              official_domains=frozenset(), now=NOW)
    return asyncio.run(run())


def test_collect_all_codes_and_feed_match_on_host_or_domain():
    sets = LocalSets(frozenset({"evil.xyz"}), frozenset(), frozenset({"reported.com"}))
    features = collect("https://login.evil.xyz/a", sets)
    assert set(features) == set(URL_FEATURE_CODES)
    assert features["threat_feed"] is True
    assert features["new_domain"] is True
    assert collect("https://reported.com/", sets)["reported_before"] is True


def test_collect_feed_unknown_when_mirror_missing():
    features = collect("https://example.com/", LocalSets(None, frozenset(), frozenset()))
    assert features["threat_feed"] is None


def test_collect_ip_host_does_not_query_rdap():
    def fail(request):
        raise AssertionError("RDAP must not be called for IP hosts")
    features = collect("http://192.0.2.1/", LocalSets(frozenset(), frozenset(), frozenset()), fail)
    assert features["new_domain"] is False and features["old_domain"] is False
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_url_lookup.py -q`
Expected: FAIL — `ImportError: cannot import name 'URL_FEATURE_CODES'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/data/reported_domains.txt`:

```text
# 자체 신고로 확인된 스미싱 도메인. 한 줄에 하나. 신고 절차가 생기면 채운다.
```

`backend/src/server/scanner/url_features.py` 상단 import 에 추가:

```python
from datetime import datetime, timezone

import httpx
```

파일 끝에 추가:

```python
URL_FEATURE_CODES = (
    "threat_feed", "new_domain", "old_domain", "popular_domain", "ip_or_port",
    "homoglyph", "abused_tld", "url_shortener", "reported_before",
)
RDAP_URL = "https://rdap.org/domain/{domain}"
RDAP_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class LocalSets:
    threat_feed: frozenset[str] | None
    popular: frozenset[str]
    reported: frozenset[str]


def _read_set(path: Path) -> frozenset[str] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    return frozenset(
        line.strip().lower() for line in lines if line.strip() and not line.startswith("#")
    )


def load_local_sets(data_dir: Path = DATA_DIR) -> LocalSets:
    return LocalSets(
        threat_feed=_read_set(data_dir / "threat_feed.txt"),
        popular=_read_set(data_dir / "tranco_top100k.txt") or frozenset(),
        reported=_read_set(data_dir / "reported_domains.txt") or frozenset(),
    )


async def lookup_domain_age_days(domain: str, client: httpx.AsyncClient, *, now: datetime) -> int | None:
    try:
        response = await client.get(
            RDAP_URL.format(domain=domain), timeout=RDAP_TIMEOUT_SECONDS, follow_redirects=True,
        )
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
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return (now - created).days
    return None


def domain_age_features(days: int | None) -> dict[str, bool | None]:
    if days is None:
        return {"new_domain": None, "old_domain": None}
    return {"new_domain": days <= 30, "old_domain": days >= 730}


async def collect_url_features(
    url: str, *, client: httpx.AsyncClient, lists: UrlLists, sets: LocalSets,
    official_domains: frozenset[str], now: datetime,
) -> dict[str, bool | None]:
    host = hostname(url)
    domain = registrable_domain(host)
    features: dict[str, bool | None] = dict(parse_url_features(url, lists, official_domains))
    features["threat_feed"] = (
        None if sets.threat_feed is None else host in sets.threat_feed or domain in sets.threat_feed
    )
    features["popular_domain"] = domain in sets.popular
    features["reported_before"] = domain in sets.reported
    if is_ip(host):
        # 도메인이 없으니 나이는 "확인 실패"가 아니라 "해당 없음"이다.
        features.update(new_domain=False, old_domain=False)
    else:
        features.update(domain_age_features(await lookup_domain_age_days(domain, client, now=now)))
    return features
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_url_lookup.py backend/tests/scanner/test_url_features.py -q`
Expected: 19 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/url_features.py backend/src/server/scanner/data/reported_domains.txt backend/tests/scanner/test_url_lookup.py
git commit -m "feat: 점수기 RDAP 도메인 나이와 로컬 목록 조회"
```

---

### Task 4: 라이선스 검사를 거치는 목록 동기화 CLI

**Files:**
- Create: `backend/src/server/scanner/data/sources.yaml`
- Create: `backend/src/server/scanner/sync_lists.py`
- Modify: `.gitignore` (끝에 추가)
- Test: `backend/tests/scanner/test_sync_lists.py`

**Interfaces:**
- Consumes: Task 2 의 `hostname`, `DATA_DIR`
- Produces:
  - `@dataclass(frozen=True) class Source: name: str; url: str; format: str; commercial_use: str; column: str = ""` — `format` 은 `lines` | `csv` | `tranco_zip`, `commercial_use` 는 `allowed` | `unverified` | `prohibited`
  - `load_sources(path: Path = DATA_DIR / "sources.yaml") -> tuple[list[Source], list[Source]]` — (위협 피드 출처들, 인기 도메인 출처들)
  - `usable(source: Source, *, include_unverified: bool) -> bool`
  - `decode_text(data: bytes) -> str` — UTF-8(BOM 포함) 실패 시 CP949
  - `feed_domains(text: str) -> list[str]` — URL 또는 도메인 줄 → 정렬된 중복 없는 호스트
  - `csv_domains(text: str, column: str) -> list[str]`
  - `tranco_domains(zip_bytes: bytes, limit: int = 100_000) -> list[str]`
  - `main(argv: list[str] | None = None) -> None` — `python -m backend.src.server.scanner.sync_lists [--include-unverified]`

`--include-unverified` 는 라이선스 확인 전 로컬 실험용이다. `prohibited` 출처는 어떤 옵션으로도 받지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_sync_lists.py`:

```python
import io
import zipfile

from backend.src.server.scanner.sync_lists import (
    Source, csv_domains, decode_text, feed_domains, load_sources, tranco_domains, usable,
)


def test_feed_domains_accepts_urls_and_bare_domains():
    text = "https://Login.Evil.xyz/a?b=1\n\n# c\nplain.example.com\nhttps://login.evil.xyz/other\n"
    assert feed_domains(text) == ["login.evil.xyz", "plain.example.com"]


def test_csv_domains_reads_named_column():
    text = "번호,홈페이지주소,등록일\n1,https://evil.xyz/a,2026-09-01\n2,,2026-09-02\n3,bad.example.com,2026-09-03\n"
    assert csv_domains(text, "홈페이지주소") == ["bad.example.com", "evil.xyz"]


def test_decode_text_handles_utf8_bom_and_cp949():
    assert decode_text("﻿홈페이지주소".encode("utf-8")) == "홈페이지주소"
    assert decode_text("홈페이지주소".encode("cp949")) == "홈페이지주소"


def test_tranco_domains_reads_rank_csv_in_order():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("top-1m.csv", "1,google.com\n2,naver.com\n3,daum.net\n")
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
    assert states["kisa_phishing_urls"] == "unverified"
    assert states["tranco_custom"] == "unverified"
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_sync_lists.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.src.server.scanner.sync_lists'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/data/sources.yaml`:

```yaml
# 외부 데이터 출처와 라이선스 상태. 서비스를 판매할 예정이므로 상업적 사용이 확인된 출처만 운영에 쓴다.
# commercial_use: allowed(상업적 사용 확인) | unverified(확인 전) | prohibited(금지 확인)
# sync_lists 는 allowed 만 받는다. --include-unverified 는 로컬 실험용. prohibited 는 절대 받지 않는다.
# Google Safe Browsing 은 비상업 전용이라 목록에 두지 않는다 (상업용은 유료 Web Risk API).
threat_feeds:
  - name: kisa_phishing_urls
    url: ""        # 공공데이터포털 KISA 피싱사이트 URL 파일 주소. 공공누리 유형 확인 후 채운다
    format: csv
    column: ""     # URL 이 들어 있는 열 이름. 파일을 받아 확인 후 채운다
    commercial_use: unverified
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
from pathlib import Path

import httpx
import yaml

from backend.src.server.scanner.url_features import DATA_DIR, hostname


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    format: str
    commercial_use: str
    column: str = ""


def load_sources(path: Path = DATA_DIR / "sources.yaml") -> tuple[list[Source], list[Source]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    def parse(items: list[dict] | None) -> list[Source]:
        return [
            Source(item["name"], item.get("url") or "", item["format"], item["commercial_use"],
                   item.get("column") or "")
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


def csv_domains(text: str, column: str) -> list[str]:
    rows = csv.DictReader(io.StringIO(text))
    return sorted({host for row in rows if (host := _host(row.get(column) or ""))})


def tranco_domains(zip_bytes: bytes, limit: int = 100_000) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        rows = archive.read(archive.namelist()[0]).decode("utf-8").splitlines()
    return [row.split(",", 1)[1].strip().lower() for row in rows[:limit] if "," in row]


def _domains(client: httpx.Client, source: Source) -> list[str]:
    response = client.get(source.url)
    response.raise_for_status()
    if source.format == "tranco_zip":
        return tranco_domains(response.content)
    text = decode_text(response.content)
    return csv_domains(text, source.column) if source.format == "csv" else feed_domains(text)


def _sync(client: httpx.Client, sources: list[Source], filename: str, include_unverified: bool) -> None:
    chosen = [s for s in sources if usable(s, include_unverified=include_unverified)]
    if not chosen:
        # 파일을 쓰지 않는다. 위협 피드가 없으면 threat_feed 가 미확인이 되어 점수가 null 이다.
        print(f"[SYNC] {filename}: 사용 가능한 출처 없음 (sources.yaml 의 commercial_use·url 확인)")
        return
    domains = sorted({d for source in chosen for d in _domains(client, source)})
    (DATA_DIR / filename).write_text("\n".join(domains) + "\n", encoding="utf-8")
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

`.gitignore` 끝에 추가:

```gitignore

# ── 자체 URL 점수기 내려받은 목록 (sync_lists 로 생성) ──
backend/src/server/scanner/data/threat_feed.txt
backend/src/server/scanner/data/tranco_top100k.txt
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_sync_lists.py -q`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/data/sources.yaml backend/src/server/scanner/sync_lists.py .gitignore backend/tests/scanner/test_sync_lists.py
git commit -m "feat: 라이선스 검사를 거치는 점수기 목록 동기화"
```

---

### Task 5: 페이지 특징 추출

**Files:**
- Create: `backend/src/server/scanner/page_features.py`
- Test: `backend/tests/scanner/test_page_features.py`

**Interfaces:**
- Consumes: Task 2 의 `hostname`, `registrable_domain`; `ai.page.inspect_html(info: str) -> PageInspection` (`.text: str`, `.elements[].doubt: EnvDoubt`, `.failure: FailureCode | None`); `ai.types.EnvDoubt.LOGIN_FORM`, `EnvDoubt.APP_LINK`; Task 1 의 `score`, `load_weights`, `FEATURE_CODES` (시간 상한 테스트용)
- Produces:
  - `@dataclass(frozen=True) class CollectedPage: input_url: str; final_url: str; redirect_chain: tuple[str, ...]; html: str; download_detected: bool; collected_at: str` — 격리 서버 입력 계약
  - `PAGE_FEATURE_CODES: tuple[str, ...]` (5개)
  - `extract_page_features(page: CollectedPage | None, brands: dict[str, frozenset[str]]) -> dict[str, bool | None]`

`inspect_html` 은 텍스트가 없는 HTML 에 `EMPTY_INPUT`, 크기·요소 수 제한에 `INPUT_TOO_LARGE`·`PARTIAL_CONTENT` 를 돌려준다. 어떤 실패든 폼이 없다는 것을 확정할 수 없으므로 모든 페이지 특징을 `None` 으로 둔다. 그러면 필수 특징 규칙에 따라 점수가 `null` 이 된다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_page_features.py`:

```python
import time

from backend.src.server.scanner.page_features import (
    PAGE_FEATURE_CODES, CollectedPage, extract_page_features,
)
from backend.src.server.scanner.scorer import FEATURE_CODES, load_weights, score

BRANDS = {"CJ대한통운": frozenset({"cjlogistics.com"})}


def page(html: str, *, final="https://parcel.example.com/login", chain=(), download=False):
    return CollectedPage("https://parcel.example.com/a", final, chain, html, download,
                         "2026-09-29T10:00:00Z")


def test_missing_page_gives_all_unknown():
    assert extract_page_features(None, BRANDS) == dict.fromkeys(PAGE_FEATURE_CODES)


def test_credential_form_and_cross_domain_action():
    html = ('<html><head><title>배송 조회</title></head><body>'
            '<form action="https://collect.evil.xyz/p"><input type="password" name="pw"></form></body></html>')
    features = extract_page_features(page(html), BRANDS)
    assert features["credential_form"] is True
    assert features["cross_domain_form"] is True
    assert features["app_download"] is False


def test_form_actions_that_are_not_other_domains():
    html = ('<p>주소 확인</p><form action="/submit"></form><form action=""></form>'
            '<form action="javascript:void(0)"></form><form action="https://www.example.com/x"></form>')
    assert extract_page_features(page(html), BRANDS)["cross_domain_form"] is False


def test_app_download_by_link_or_collector_flag():
    assert extract_page_features(page('<a href="/app.apk">앱 설치</a>'), BRANDS)["app_download"] is True
    assert extract_page_features(page("<p>안내문</p>", download=True), BRANDS)["app_download"] is True


def test_brand_on_page_only_when_domain_is_not_official():
    html = "<title>CJ대한통운 배송조회</title><p>안내</p>"
    assert extract_page_features(page(html), BRANDS)["brand_on_page"] is True
    official = page(html, final="https://www.cjlogistics.com/track")
    assert extract_page_features(official, BRANDS)["brand_on_page"] is False


def test_cross_domain_redirect():
    same = page("<p>안내</p>", chain=("https://parcel.example.com/b",))
    moved = page("<p>안내</p>", chain=("https://bit.ly/x",))
    assert extract_page_features(same, BRANDS)["cross_domain_redirect"] is False
    assert extract_page_features(moved, BRANDS)["cross_domain_redirect"] is True


def test_partial_or_empty_inspection_gives_all_unknown():
    too_large = "<p>" + "a" * 140_000 + "</p>"
    assert extract_page_features(page(too_large), BRANDS) == dict.fromkeys(PAGE_FEATURE_CODES)
    assert extract_page_features(page(""), BRANDS) == dict.fromkeys(PAGE_FEATURE_CODES)


def test_invalid_utf8_text_gives_all_unknown():
    assert extract_page_features(page("<p>\ud800</p>"), BRANDS) == dict.fromkeys(PAGE_FEATURE_CODES)


def test_extraction_and_scoring_within_500ms_on_max_size_html():
    html = '<form action="/a"><input type="password" name="p"></form>' + "<p>text</p>" * 11_000
    assert len(html.encode("utf-8")) < 131_072
    weights = load_weights()
    start = time.monotonic()
    features = {code: False for code in FEATURE_CODES} | extract_page_features(page(html), BRANDS)
    score(features, weights)
    assert time.monotonic() - start < 0.5
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_page_features.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.src.server.scanner.page_features'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/page_features.py`:

```python
"""격리 서버가 수집한 페이지 → 특징. 네트워크를 쓰지 않고, 페이지 속 URL 로 접속하지 않는다."""

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from ai.page import inspect_html
from ai.types import EnvDoubt

from backend.src.server.scanner.url_features import hostname, registrable_domain

PAGE_FEATURE_CODES = (
    "credential_form", "app_download", "cross_domain_form", "brand_on_page", "cross_domain_redirect",
)


@dataclass(frozen=True)
class CollectedPage:
    """격리 서버 반환 계약. redirect_chain 은 입력 URL 이후 거친 URL (최종 URL 포함 가능)."""

    input_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    html: str
    download_detected: bool
    collected_at: str


class _FormActions(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.actions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "form":
            self.actions.append(dict(attrs).get("action") or "")


def extract_page_features(
    page: CollectedPage | None, brands: dict[str, frozenset[str]],
) -> dict[str, bool | None]:
    unknown = dict.fromkeys(PAGE_FEATURE_CODES)
    if page is None:
        return unknown
    try:
        inspection = inspect_html(page.html)
    except ValueError:
        return unknown
    if inspection.failure is not None:
        return unknown

    parser = _FormActions()
    parser.feed(page.html)
    parser.close()
    final_domain = registrable_domain(hostname(page.final_url))
    targets = [urlsplit(urljoin(page.final_url, action)) for action in parser.actions]
    doubts = {element.doubt for element in inspection.elements}
    chain = {registrable_domain(hostname(url)) for url in (page.input_url, *page.redirect_chain, page.final_url)}
    return {
        "credential_form": EnvDoubt.LOGIN_FORM in doubts,
        "app_download": EnvDoubt.APP_LINK in doubts or page.download_detected,
        "cross_domain_form": any(
            target.scheme in ("http", "https") and target.hostname
            and registrable_domain(target.hostname.rstrip(".")) != final_domain
            for target in targets
        ),
        "brand_on_page": any(
            name in inspection.text and final_domain not in domains for name, domains in brands.items()
        ),
        "cross_domain_redirect": len(chain) > 1,
    }
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_page_features.py -q`
Expected: 9 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/page_features.py backend/tests/scanner/test_page_features.py
git commit -m "feat: 점수기 페이지 특징 추출"
```

---

### Task 6: 스캔 실행 서비스

**Files:**
- Create: `backend/src/server/scanner/service.py`
- Test: `backend/tests/scanner/test_service.py`

**Interfaces:**
- Consumes: Task 1 `Weights`, `score`, `load_weights`; Task 2·3 `UrlLists`, `LocalSets`, `URL_FEATURE_CODES`, `collect_url_features`, `load_url_lists`, `load_local_sets`, `load_brands`, `validate_whitelist`; Task 5 `CollectedPage`, `extract_page_features`
- Produces:
  - `PageCollector = Callable[[str], Awaitable[CollectedPage | None]]`
  - `async collector_not_connected(url: str) -> None`
  - `COLLECT_TIMEOUT_SECONDS = 7.0`, `URL_FEATURES_TIMEOUT_SECONDS = 3.0`
  - `@dataclass(frozen=True) class ScanContext: lists: UrlLists; sets: LocalSets; brands: dict[str, frozenset[str]]; weights: Weights; collector: PageCollector`
  - `@dataclass(frozen=True) class ScanOutcome: url: str; score: int | None; scanned_at: str; weights_version: str; features: dict[str, bool | None]; hits: dict[str, int]; null_reason: str | None; timings: dict[str, float]` — `timings` 키 `inputs`, `score`, `total` (초)
  - `async scan_url(url: str, ctx: ScanContext, client: httpx.AsyncClient, *, now: datetime | None = None) -> ScanOutcome`
  - `load_default_context(collector: PageCollector = collector_not_connected) -> ScanContext` — 가중치 오류이거나 화이트리스트에 공유 도메인·근거 없는 항목이 있으면 `ValueError`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_service.py`:

```python
import asyncio
import time
from datetime import datetime, timezone

import httpx

from backend.src.server.scanner import service
from backend.src.server.scanner.page_features import CollectedPage
from backend.src.server.scanner.scorer import load_weights
from backend.src.server.scanner.url_features import LocalSets, UrlLists

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)
FORM = '<p>배송 조회</p><form action="/p"><input type="password" name="pw"></form>'


def rdap_10_days(request):
    return httpx.Response(200, json={"events": [
        {"eventAction": "registration", "eventDate": "2026-09-19T00:00:00Z"}]})


def context(collector) -> service.ScanContext:
    return service.ScanContext(
        lists=UrlLists(frozenset(), frozenset()),
        sets=LocalSets(frozenset(), frozenset(), frozenset()),
        brands={},
        weights=load_weights(),
        collector=collector,
    )


def run(collector, handler=rdap_10_days) -> service.ScanOutcome:
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await service.scan_url("https://parcel.example.com/a", context(collector), client, now=NOW)
    return asyncio.run(go())


async def collected(url: str) -> CollectedPage:
    return CollectedPage(url, url, (), FORM, False, "2026-09-29T10:00:00Z")


def test_full_inputs_give_score_and_record():
    outcome = run(collected)
    assert outcome.score == 80  # new_domain 40 + credential_form 40
    assert outcome.hits == {"credential_form": 40, "new_domain": 40}
    assert outcome.null_reason is None
    assert outcome.weights_version == "v1"
    assert outcome.scanned_at.startswith("2026-09-29")
    assert set(outcome.timings) == {"inputs", "score", "total"}


def test_collector_not_connected_gives_null():
    outcome = run(service.collector_not_connected)
    assert outcome.score is None
    assert "credential_form" in outcome.null_reason


def test_collector_error_gives_null_not_exception():
    async def broken(url):
        raise RuntimeError("isolation down")
    assert run(broken).score is None


def test_rdap_failure_gives_null():
    assert run(collected, lambda request: httpx.Response(503)).score is None


def test_slow_collector_is_cut_at_timeout(monkeypatch):
    monkeypatch.setattr(service, "COLLECT_TIMEOUT_SECONDS", 0.05)

    async def slow(url):
        await asyncio.sleep(10)

    start = time.monotonic()
    outcome = run(slow)
    assert outcome.score is None
    assert time.monotonic() - start < 2


def test_default_context_loads_shipped_files():
    ctx = service.load_default_context()
    assert ctx.weights.version == "v1"
    assert ctx.collector is service.collector_not_connected
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_service.py -q`
Expected: FAIL — `ImportError: cannot import name 'service'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/service.py`:

```python
"""URL 하나를 스캔한다: URL 특징 ∥ 페이지 수집 → 특징 추출 → 점수."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic

import httpx

from backend.src.server.scanner.page_features import CollectedPage, extract_page_features
from backend.src.server.scanner.scorer import Weights, load_weights, score
from backend.src.server.scanner.url_features import (
    URL_FEATURE_CODES, LocalSets, UrlLists, collect_url_features, load_brands, load_local_sets,
    load_url_lists, validate_whitelist,
)

PageCollector = Callable[[str], Awaitable[CollectedPage | None]]
COLLECT_TIMEOUT_SECONDS = 7.0
URL_FEATURES_TIMEOUT_SECONDS = 3.0


async def collector_not_connected(url: str) -> None:
    """격리 서버 연결 전. 연결되면 격리 서버 HTTP 클라이언트로 교체한다."""
    return None


@dataclass(frozen=True)
class ScanContext:
    lists: UrlLists
    sets: LocalSets
    brands: dict[str, frozenset[str]]
    weights: Weights
    collector: PageCollector


@dataclass(frozen=True)
class ScanOutcome:
    url: str
    score: int | None
    scanned_at: str
    weights_version: str
    features: dict[str, bool | None]
    hits: dict[str, int]
    null_reason: str | None
    timings: dict[str, float]


def load_default_context(collector: PageCollector = collector_not_connected) -> ScanContext:
    lists, brands = load_url_lists(), load_brands()
    validate_whitelist(brands, lists)  # 공유 도메인이 공식으로 등록되면 기동을 막는다
    return ScanContext(lists, load_local_sets(), brands, load_weights(), collector)


async def _within(awaitable: Awaitable, seconds: float):
    """시간 초과·오류를 "확인 못 함"(None)으로 바꾼다. 필수 특징이면 점수가 null 이 된다."""
    try:
        return await asyncio.wait_for(awaitable, seconds)
    except Exception as error:  # noqa: BLE001 — 수집기·조회 실패 종류와 무관하게 미확인으로 기록
        print(f"[OWN SCAN INPUT FAILED] {type(error).__name__}: {error}")
        return None


async def scan_url(
    url: str, ctx: ScanContext, client: httpx.AsyncClient, *, now: datetime | None = None,
) -> ScanOutcome:
    now = now or datetime.now(timezone.utc)
    start = monotonic()
    official = frozenset().union(*ctx.brands.values())
    url_features, page = await asyncio.gather(
        _within(
            collect_url_features(url, client=client, lists=ctx.lists, sets=ctx.sets,
                                 official_domains=official, now=now),
            URL_FEATURES_TIMEOUT_SECONDS,
        ),
        _within(ctx.collector(url), COLLECT_TIMEOUT_SECONDS),
    )
    inputs_done = monotonic()
    features = (url_features or dict.fromkeys(URL_FEATURE_CODES)) | extract_page_features(page, ctx.brands)
    result = score(features, ctx.weights)
    done = monotonic()
    return ScanOutcome(
        url=url, score=result.score, scanned_at=now.isoformat(), weights_version=ctx.weights.version,
        features=features, hits=result.hits, null_reason=result.null_reason,
        timings={"inputs": inputs_done - start, "score": done - inputs_done, "total": done - start},
    )
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_service.py -q`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/service.py backend/tests/scanner/test_service.py
git commit -m "feat: 점수기 스캔 실행 서비스"
```

---

### Task 7: 비교 기록·라벨·요약

**Files:**
- Create: `backend/src/server/scanner/compare.py`
- Test: `backend/tests/scanner/test_compare.py`

**Interfaces:**
- Consumes: Task 6 `ScanOutcome`; urlscan 쪽은 `main.parse_urlscan_result()` 가 돌려주는 dict (`"score"`, `"malicious"` 키 사용)
- Produces:
  - `MALICIOUS_THRESHOLD = 50`, `LABELS = frozenset({"smishing", "benign", "unknown"})`
  - `SCAN_RECORDS: dict[str, dict]` — 인메모리 저장소
  - `record_comparison(outcome: ScanOutcome, urlscan: dict | None) -> dict` — 키 `record_id`, `url`, `recorded_at`(ISO 8601, urlscan 결과를 받은 시각), `own`(ScanOutcome dict), `urlscan`(`{"score", "malicious"}` 또는 None), `agree`(bool | None), `label`(None)
  - `set_label(record_id: str, label: str) -> dict` — 없으면 `KeyError`, 잘못된 라벨이면 `ValueError`
  - `summarize(records: list[dict]) -> dict` — 키 `records`, `pairs`, `agreement_rate`, `disagreements`, `labeled_disagreements`, `own_correct_rate`, `null_rate`, `score_p50`, `score_p95`, `total_p95`
  - `async record_scan_task(task: asyncio.Task | None, urlscan: dict | None) -> None` — 태스크 예외는 로그만 남기고 삼킨다

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_compare.py`:

```python
import asyncio

import pytest

from backend.src.server.scanner import compare
from backend.src.server.scanner.service import ScanOutcome


@pytest.fixture(autouse=True)
def clean_store():
    compare.SCAN_RECORDS.clear()
    yield
    compare.SCAN_RECORDS.clear()


def outcome(score: int | None, *, score_time: float = 0.01, total: float = 1.0) -> ScanOutcome:
    return ScanOutcome("https://a.example/", score, "2026-09-29T10:00:00+00:00", "v1", {}, {}, None,
                       {"inputs": total - score_time, "score": score_time, "total": total})


def test_agreement_uses_threshold_50_against_urlscan_malicious():
    assert compare.record_comparison(outcome(60), {"score": 80, "malicious": True})["agree"] is True
    assert compare.record_comparison(outcome(49), {"score": 80, "malicious": True})["agree"] is False
    assert compare.record_comparison(outcome(50), {"score": 0, "malicious": False})["agree"] is False


def test_agreement_unknown_without_both_values():
    assert compare.record_comparison(outcome(None), {"score": 80, "malicious": True})["agree"] is None
    assert compare.record_comparison(outcome(60), None)["agree"] is None
    assert compare.record_comparison(outcome(60), {"score": 1, "malicious": "yes"})["agree"] is None


def test_record_is_stored_and_labelable():
    record = compare.record_comparison(outcome(60), {"score": 0, "malicious": False})
    assert compare.SCAN_RECORDS[record["record_id"]] is record
    assert record["recorded_at"].endswith("+00:00")
    assert compare.set_label(record["record_id"], "smishing")["label"] == "smishing"
    with pytest.raises(ValueError):
        compare.set_label(record["record_id"], "evil")
    with pytest.raises(KeyError):
        compare.set_label("missing", "benign")


def test_summarize_rates_and_percentiles():
    agree = compare.record_comparison(outcome(80, score_time=0.02, total=5.0), {"score": 90, "malicious": True})
    miss_own_right = compare.record_comparison(outcome(70), {"score": 0, "malicious": False})
    miss_own_wrong = compare.record_comparison(outcome(10), {"score": 90, "malicious": True})
    compare.record_comparison(outcome(None), {"score": 90, "malicious": True})
    compare.set_label(miss_own_right["record_id"], "smishing")
    compare.set_label(miss_own_wrong["record_id"], "benign")  # 자체 10점 = 정상 판정 → 맞음
    summary = compare.summarize(list(compare.SCAN_RECORDS.values()))
    assert summary["records"] == 4
    assert summary["pairs"] == 3
    assert summary["agreement_rate"] == pytest.approx(1 / 3)
    assert summary["disagreements"] == 2
    assert summary["labeled_disagreements"] == 2
    assert summary["own_correct_rate"] == 1.0
    assert summary["null_rate"] == 0.25
    assert summary["score_p95"] == 0.02
    assert summary["total_p95"] == 5.0
    assert agree["agree"] is True


def test_summarize_empty():
    summary = compare.summarize([])
    assert summary["records"] == 0 and summary["agreement_rate"] is None and summary["null_rate"] is None


def test_record_scan_task_records_and_swallows_errors():
    async def ok():
        return outcome(60)

    async def broken():
        raise RuntimeError("scan crashed")

    async def go():
        await compare.record_scan_task(asyncio.create_task(ok()), {"score": 80, "malicious": True})
        await compare.record_scan_task(asyncio.create_task(broken()), None)
        await compare.record_scan_task(None, None)

    asyncio.run(go())
    assert len(compare.SCAN_RECORDS) == 1
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_compare.py -q`
Expected: FAIL — `ImportError: cannot import name 'compare'`

- [ ] **Step 3: 구현**

`backend/src/server/scanner/compare.py`:

```python
"""urlscan 과 자체 점수 비교 기록. 전환 기준(스펙 §3) 판단 자료."""

import asyncio
import math
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from backend.src.server.scanner.service import ScanOutcome

MALICIOUS_THRESHOLD = 50
LABELS = frozenset({"smishing", "benign", "unknown"})
# ponytail: 인메모리라 재시작 시 소실. ANALYSIS_JOBS 를 영속 저장소로 옮길 때 함께 옮긴다.
SCAN_RECORDS: dict[str, dict] = {}


def record_comparison(outcome: ScanOutcome, urlscan: dict | None) -> dict:
    ours = None if outcome.score is None else outcome.score >= MALICIOUS_THRESHOLD
    theirs = urlscan.get("malicious") if urlscan else None
    theirs = theirs if isinstance(theirs, bool) else None
    record = {
        "record_id": str(uuid.uuid4()),
        "url": outcome.url,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "own": asdict(outcome),
        "urlscan": {"score": urlscan.get("score"), "malicious": theirs} if urlscan else None,
        "agree": None if ours is None or theirs is None else ours == theirs,
        "label": None,
    }
    SCAN_RECORDS[record["record_id"]] = record
    return record


def set_label(record_id: str, label: str) -> dict:
    if label not in LABELS:
        raise ValueError(f"label must be one of {sorted(LABELS)}")
    record = SCAN_RECORDS[record_id]
    record["label"] = label
    return record


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def summarize(records: list[dict]) -> dict:
    pairs = [r for r in records if r["agree"] is not None]
    disagreements = [r for r in pairs if not r["agree"]]
    labeled = [r for r in disagreements if r["label"] in ("smishing", "benign")]
    own_correct = sum(
        (r["label"] == "smishing") == (r["own"]["score"] >= MALICIOUS_THRESHOLD) for r in labeled
    )
    scored = [r["own"] for r in records if r["own"]["score"] is not None]
    return {
        "records": len(records),
        "pairs": len(pairs),
        "agreement_rate": sum(r["agree"] for r in pairs) / len(pairs) if pairs else None,
        "disagreements": len(disagreements),
        "labeled_disagreements": len(labeled),
        "own_correct_rate": own_correct / len(labeled) if labeled else None,
        "null_rate": sum(r["own"]["score"] is None for r in records) / len(records) if records else None,
        "score_p50": _percentile([o["timings"]["score"] for o in scored], 0.5),
        "score_p95": _percentile([o["timings"]["score"] for o in scored], 0.95),
        "total_p95": _percentile([o["timings"]["total"] for o in scored], 0.95),
    }


async def record_scan_task(task: asyncio.Task | None, urlscan: dict | None) -> None:
    if task is None:
        return
    try:
        outcome = await task
    except Exception as error:  # noqa: BLE001 — 자체 스캔 실패가 urlscan 경로를 깨면 안 된다
        print(f"[OWN SCAN ERROR] {type(error).__name__}: {error}")
        return
    record = record_comparison(outcome, urlscan)
    print(f"[OWN SCAN] url={outcome.url} score={outcome.score} agree={record['agree']} "
          f"null_reason={outcome.null_reason}")
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner/test_compare.py -q`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/src/server/scanner/compare.py backend/tests/scanner/test_compare.py
git commit -m "feat: 점수기 urlscan 비교 기록과 요약"
```

---

### Task 8: main.py 연결과 비교 API

**Files:**
- Modify: `main.py` — import 블록(1~19행 부근), `run_analysis` 의 URL 루프(`for link in links:` 부터 루프 끝 `except Exception as e:` 블록까지), 파일 끝에 엔드포인트 2개
- Test: `backend/tests/scanner/test_main_scan.py`

**Interfaces:**
- Consumes: Task 6 `load_default_context`, `scan_url`; Task 7 `SCAN_RECORDS`, `record_scan_task`, `set_label`, `summarize`
- Produces:
  - `SCAN_COMPARE: bool` (환경 변수 `SCAN_COMPARE`, 기본 `"1"`)
  - `SCAN_CONTEXT` — 모듈 import 시 로딩. 가중치 파일이 잘못되면 서버가 기동하지 않는다
  - `async run_own_scan(url: str) -> ScanOutcome`
  - `GET /api/scan-comparison` → `{"success": True, "summary": {...}, "disagreements": [...]}`
  - `POST /api/scan-comparison/{record_id}/label` body `{"label": "smishing" | "benign" | "unknown"}` → `{"success": True, "record": {...}}`, 404 / 400

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/scanner/test_main_scan.py`:

```python
import pytest
from fastapi.testclient import TestClient

import main
from backend.src.server.scanner import compare
from backend.src.server.scanner.service import ScanOutcome

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean_store():
    compare.SCAN_RECORDS.clear()
    yield
    compare.SCAN_RECORDS.clear()


def outcome(score):
    return ScanOutcome("https://a.example/", score, "2026-09-29T10:00:00+00:00", "v1", {}, {}, None,
                       {"inputs": 1.0, "score": 0.01, "total": 1.01})


def test_comparison_summary_and_disagreements():
    compare.record_comparison(outcome(80), {"score": 90, "malicious": True})
    miss = compare.record_comparison(outcome(10), {"score": 90, "malicious": True})
    body = client.get("/api/scan-comparison").json()
    assert body["summary"]["pairs"] == 2
    assert [r["record_id"] for r in body["disagreements"]] == [miss["record_id"]]


def test_label_endpoint():
    record = compare.record_comparison(outcome(10), {"score": 90, "malicious": True})
    url = f"/api/scan-comparison/{record['record_id']}/label"
    assert client.post(url, json={"label": "benign"}).json()["record"]["label"] == "benign"
    assert client.post(url, json={"label": "evil"}).status_code == 400
    assert client.post("/api/scan-comparison/missing/label", json={"label": "benign"}).status_code == 404


def test_run_analysis_records_own_scan_even_when_urlscan_fails(monkeypatch):
    import asyncio

    async def fake_scan(url):
        return outcome(None)

    async def failing_submit(url):
        return {}  # uuid 없음 → urlscan 요청 실패 경로

    async def fake_message(message):
        raise RuntimeError("message analysis not needed here")

    monkeypatch.setattr(main, "SCAN_COMPARE", True)
    monkeypatch.setattr(main, "run_own_scan", fake_scan)
    monkeypatch.setattr(main, "submit_url_scan", failing_submit)
    monkeypatch.setattr(main, "analyze_message_part", fake_message)

    import asyncio
    asyncio.run(main.run_analysis(["https://a.example/"], "문자"))
    assert len(compare.SCAN_RECORDS) == 1
    assert next(iter(compare.SCAN_RECORDS.values()))["urlscan"] is None


def test_run_analysis_survives_own_scan_crash(monkeypatch):
    async def crashing_scan(url):
        raise RuntimeError("scanner bug")

    async def failing_submit(url):
        return {}

    async def fake_message(message):
        raise RuntimeError("not needed")

    monkeypatch.setattr(main, "SCAN_COMPARE", True)
    monkeypatch.setattr(main, "run_own_scan", crashing_scan)
    monkeypatch.setattr(main, "submit_url_scan", failing_submit)
    monkeypatch.setattr(main, "analyze_message_part", fake_message)

    import asyncio
    response = asyncio.run(main.run_analysis(["https://a.example/"], "문자"))
    assert "검사 요청 실패" in str(response)
    assert compare.SCAN_RECORDS == {}
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest backend/tests/scanner/test_main_scan.py -q`
Expected: FAIL — `/api/scan-comparison` 가 404 를 돌려주고, `main` 에 `run_own_scan` 이 없어 `AttributeError`

- [ ] **Step 3: 구현**

`main.py` import 블록의 `import asyncio` 아래에 추가:

```python
import os
```

`from backend.src.server.templates.renderer import render_r1_lookalike` 아래에 추가:

```python
from backend.src.server.scanner.compare import (
    SCAN_RECORDS,
    record_scan_task,
    set_label,
    summarize,
)
from backend.src.server.scanner.service import (
    ScanOutcome,
    load_default_context,
    scan_url,
)
```

`app = FastAPI()` 아래에 추가:

```python
# 자체 URL 점수기를 urlscan 과 병행해 비교 기록을 남길지 (스펙 §3).
SCAN_COMPARE = os.getenv("SCAN_COMPARE", "1") == "1"

# 기동 시 가중치·목록·화이트리스트를 읽어 검증한다. 잘못되면 여기서 기동이 실패한다.
SCAN_CONTEXT = load_default_context()


async def run_own_scan(url: str) -> ScanOutcome:
    async with httpx.AsyncClient() as client:
        return await scan_url(url, SCAN_CONTEXT, client)
```

`run_analysis` 의 URL 루프 시작부:

```python
    for link in links:
        try:
            urlscan_start = time.monotonic()
```

를 다음으로 바꾼다:

```python
    for link in links:
        # 자체 점수기를 urlscan 과 동시에 시작한다. 결과는 비교 기록에만 쓰고 응답에는 쓰지 않는다.
        scan_task = (
            asyncio.create_task(run_own_scan(link))
            if SCAN_COMPARE else None
        )

        try:
            urlscan_start = time.monotonic()
```

uuid 가 없는 분기의 `continue` 바로 앞(`f"⚠️ 검사 요청 실패: {link}"` 를 append 한 직후)에 추가:

```python
                await record_scan_task(scan_task, None)
                scan_task = None
```

urlscan 시간 초과 분기의 `continue` 바로 앞(`f"⚠️ 검사 시간 초과: {link}"` 를 append 한 직후)에 추가:

```python
                await record_scan_task(scan_task, None)
                scan_task = None
```

`parsed_result = parse_urlscan_result(scan_result)` 와 그 뒤 `print(f"[PARSED RESULT] ...")` 바로 다음에 추가:

```python
            await record_scan_task(scan_task, parsed_result)
            scan_task = None
```

URL 루프의 바깥 `except Exception as e:` 블록(`f"⚠️ 분석 실패: {link}"` 를 append 하는 블록) 끝에 추가:

```python
            await record_scan_task(scan_task, None)
```

파일 끝에 추가:

```python
# ============================================================
# 자체 URL 점수기 비교 API (비교 기간 운영용)
# ============================================================

@app.get("/api/scan-comparison")
async def get_scan_comparison():
    records = list(SCAN_RECORDS.values())
    return {
        "success": True,
        "summary": summarize(records),
        "disagreements": [r for r in records if r["agree"] is False],
    }


@app.post("/api/scan-comparison/{record_id}/label")
async def label_scan_record(record_id: str, request: Request):
    body = await request.json()
    try:
        record = set_label(record_id, body.get("label"))
    except KeyError:
        raise HTTPException(status_code=404, detail="Scan record not found")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"success": True, "record": record}
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest backend/tests/scanner -q`
Expected: 모든 scanner 테스트 통과 (Task 1~8 합계 58 passed)

- [ ] **Step 5: 커밋**

```bash
git add main.py backend/tests/scanner/test_main_scan.py
git commit -m "feat: 자체 URL 점수기를 urlscan 과 병행 실행하고 비교 API 추가"
```

---

## 실행 후 수동 확인

1. `python -m backend.src.server.scanner.sync_lists` 를 실행한다. 선행 작업의 라이선스 확인 전이면 "사용 가능한 출처 없음" 이 출력되고 파일이 생기지 않는 것이 정상이다. 이 상태에서는 `threat_feed` 가 미확인이라 모든 점수가 `null` 이다.
   라이선스 확인 전에 로컬에서 동작만 보려면 `--include-unverified` 를 쓴다 (운영 금지).
2. 서버를 띄워 실제 URL 로 한 번 분석한다. 로그에 `[OWN SCAN] ... score=None null_reason=required_unknown:...` 이 찍히는지 본다. 격리 서버 연결·위협 피드 확보 전에는 이것이 정상이다.
3. `GET /api/scan-comparison` 에서 `records` 가 늘어나는지 확인한다.
4. `whitelist.yaml` 에 `bit.ly` 같은 공유 도메인을 임시로 넣고 서버를 띄우면 기동이 실패하는지 확인한 뒤 되돌린다.
