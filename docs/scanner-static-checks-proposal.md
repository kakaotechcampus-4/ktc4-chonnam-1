# 1단계 정적 검사 — 모듈 구조와 구현 메모

작성일: 2026-10-05
상태: **구현 완료** (`scanner/static_checks.py`). 아래 "구현 중 내린 결정"은
팀 확인 전 기본값이니, 다르게 정해지면 바로 바꿀 수 있게 전부 주입 가능한
파라미터로 열어뒀다.

## 배경

팀장님·윤여경님과 정리된 방향(2026-10-05): 기존 공식 도메인 비교에 더해

1. KISA 악성 도메인 목록 대조
2. Punycode 분석
3. Unicode 분석을 통한 공식 도메인 사칭 여부 확인

을 추가한다. Punycode 사용 자체는 위험으로 보지 않고 정상 국제화 도메인은 허용한다.
강한 위험 신호로 보는 경우는 두 가지다.

- 정규화/디코딩한 도메인이 KISA 악성 도메인 목록에 해당하는 경우
- 실제 도메인은 공식 도메인과 다르지만 Punycode/Unicode 분석에서 공식 도메인과
  시각적으로 혼동되는 사칭 도메인으로 확인되는 경우

Unicode·Punycode 분석은 둘 다 실행한다. `scanner/fetch.py`(httpx Collector)는
2단계 접속 검사로 그대로 두고, 이번 기능은 그 앞의 **1단계 정적 검사**로 분리했다.

## 1. 재사용한 기존 코드

| 조각 | 위치 | 역할 |
| --- | --- | --- |
| `OFFICIAL_DOMAINS` | `services/official_domains.py` | 비교 대상 공식 도메인 목록. 그대로 재사용 |
| `check_official_domain()` | `services/official_domain_service.py` | 기존 "공식 도메인 비교" 로직. 그대로 호출 |
| `is_same_or_subdomain()` | 〃 | 사칭 판별에서 "진짜 공식 주소"를 제외할 때도 재사용 |
| `collect_url()` | `scanner/fetch.py` | 2단계(접속) — 건드리지 않음 |

**안 쓰는 것:** `backend/src/server/data/whitelist.yaml` — 옛날 설계(지금은 버려진
`backend/src/server/main.py` 경로)가 쓰려던 빈 파일. 코드에서 아무도 참조하지 않는다.

## 2. 구현한 모듈 구조

```text
scanner/
 ├─ fetch.py            (2단계, 기존)
 ├─ models.py            (CollectResult + StaticCheckResult)
 └─ static_checks.py     (신규, 1단계)
```

`fetch.py`와 같은 원칙: 동결 dataclass + 순수 함수, 예외를 던지지 않고 결과에
사실만 담는다. **점수나 "위험" 판정은 하지 않는다** — `kisa_listed`, `lookalike_of`는
scorer가 가중치를 매길 사실일 뿐이다.

```python
@dataclass(frozen=True)
class StaticCheckResult:
    domain: str
    official_match: str          # check_official_domain() 결과 그대로
    is_punycode: bool
    decoded_domain: str | None    # punycode 디코딩 결과. 대상이 아니거나 실패하면 None
    kisa_listed: bool
    lookalike_of: str | None      # 시각적으로 혼동되는 공식 도메인 (없으면 None)
    failures: tuple[str, ...]


def check_static(
    domain: str | None,
    brand: str | None,
    *,
    whitelist: dict[str, set[str]] | None = None,
    kisa_domains: frozenset[str] | None = None,
) -> StaticCheckResult: ...
```

- `official_match`는 `check_official_domain(brand, domain, whitelist=whitelist)` 그대로.
- `find_lookalike()`는 브랜드를 가리지 않고 화이트리스트 전체를 대상으로 본다 —
  사칭은 AI가 식별한 브랜드와 무관하게 아무 공식 도메인이나 흉내 낼 수 있어서다.
  실제 공식 도메인·서브도메인은 "혼동"이 아니라 "진짜"이므로 제외한다.

## 3. Punycode / Unicode 분석 — 구현 내용

- **디코딩**: `idna` 패키지(`requirements.txt`에 명시적으로 추가함 — 원래 httpx의
  전이 의존성으로 들어와 있었지만, 직접 import 하므로 명시적 의존성으로 올렸다).
  `decode_domain()`이 라벨에 `xn--`이 있는지 먼저 보고, 있을 때만 디코딩을 시도한다.
  실패(`idna.IDNAError` 등)해도 예외를 올리지 않고 `(None, True)`로 "Punycode는
  맞지만 내용 확인 실패"를 표현한다.
- **시각적 혼동 판별(`skeleton()`)**: NFKC 정규화 후 자주 쓰이는 Cyrillic·Greek
  동형이의 문자(소문자 15개, 대문자 13개+8개)를 라틴 알파벳으로 치환하고
  소문자로 맞춘 근사 스켈레톤이다. 완전한 Unicode Technical Standard #39
  알고리즘이 아니라 **좁은 범위의 1차 구현**이다.
- **실제 동작 확인**: `cjlogistics.com`의 `c`·`o`를 Cyrillic `с`·`о`로 바꿔
  Punycode 인코딩한 도메인(`xn--...`)을 넣었을 때 `lookalike_of == "cjlogistics.com"`로
  정확히 잡히는 것, 반대로 `한글.com`처럼 실제로 사칭이 아닌 국제화 도메인은
  `kisa_listed`/`lookalike_of` 둘 다 비어서 위험 신호로 안 뜨는 것을 테스트와
  수동 확인 양쪽으로 검증했다.

### 구현 중 내린 결정 (팀 확인 전 기본값)

제안 문서에서 열어뒀던 질문들에 대해 아래처럼 잠정 결정하고 구현했다. **전부
파라미터로 주입 가능해서, 다른 방향으로 정해지면 호출부만 바꾸면 된다.**

1. **`confusable_homoglyphs` 패키지 추가 여부** → 추가하지 않았다. 외부 런타임
   의존성을 새로 들이는 결정이라 팀 확인이 먼저 필요하다고 판단해, 제안서에 적은
   대로 "자체 좁은 치환 테이블"로 시작했다(위 15+13+8개 문자). 넓은 커버리지가
   필요해지면 `confusable_homoglyphs`(PyPI) 전환이 쉽다 — `skeleton()` 함수
   내부만 바뀌고 나머지 인터페이스는 그대로다.
2. **KISA 데이터 연결 방법** → 아직 실제 데이터를 연결하지 않았다.
   `check_static()`의 `kisa_domains` 파라미터로 목록을 주입받는 구조만 만들어뒀고,
   `None`(미주입)이면 `kisa_listed=False`로 두되 `failures=("kisa_feed_unavailable",)`를
   남겨서 "확인했는데 없음"과 "확인할 목록 자체가 없음"을 구분한다. 팀장님 설계
   문서(`docs/superpowers/specs/2026-10-01-smishing-pipeline-design.md` §7)에
   이미 조사 기록이 있다 — 공공데이터포털 "KISA 피싱사이트 URL"은
   **2023-12-31 기준 1회성 스냅샷(27,582행)**이라 탐지일 90일 기준으로 보면 현재
   유효 건수 0건. 지금 말하는 "KISA 악성 도메인 목록"이 이것과 같은 데이터셋인지
   아직 확인이 안 됐다 — **이 부분은 여전히 팀 확인이 필요하다.**
3. **`idna` 패키지 사용** → 그대로 사용. `requirements.txt`에 명시적으로 추가.
4. **모듈을 쪼갤지** → 처음 제안대로 `scanner/static_checks.py` 하나로 구현했다.
   KISA 대조·사칭 분석 함수(`is_kisa_listed`, `find_lookalike`, `decode_domain`,
   `skeleton`)는 각각 독립 함수라 나중에 파일을 쪼개도 비용이 크지 않다.

## 4. 테스트

`tests/test_scanner_static_checks.py` 21개: Punycode 디코딩(정상/실패/서브도메인),
skeleton 정규화, 사칭 탐지(진짜 공식 도메인·서브도메인은 제외, Cyrillic 치환
탐지, 무관한 도메인은 통과), KISA 대조(원본/디코딩 후/대소문자·trailing dot),
`check_static()` 통합(공식 도메인 깨끗함, Punycode 자체는 안전 취급, 사칭 탐지,
KISA 탐지, KISA 피드 없음 기록, 빈 도메인 처리). 전체 테스트 스위트 162개 통과.

## 5. 아직 남은 것

- KISA 데이터 실제 연결(위 3절 2번) — 팀 확인 필요.
- `scanner/static_checks.py`를 `main.py`의 shadow 흐름에 연결하는 것은 이번
  범위에 포함하지 않았다(Collector shadow 연결은 윤여경님 작업).
- `confusable_homoglyphs` 전환 여부 — 치환 테이블 커버리지가 실제로 부족하다고
  판단되면 그때 진행.
