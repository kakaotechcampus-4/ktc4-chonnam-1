"""자체 URL Collector 가 돌려주는 자료 모양.

urlscan 을 대체하는 1차 단계: "URL 방문·수집"만 한다. 위험도 `score`나
`malicious` 판정은 여기서 만들지 않는다 (오늘 작업 계획 §담당 B 역할).
그 값은 이후 scorer 단계에서 이 결과를 입력으로 받아 계산한다.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BrowserResult:
    """3단계 브라우저 렌더링 결과. 격리 서버에서만 만든다.

    `CollectResult.browser`가 None이면 브라우저 단계를 돌리지 않은 것이고,
    값이 있는데 `failures`가 비어 있지 않으면 돌렸지만 끝까지 보지 못한 것이다.
    빈 `downloads`·0인 `blocked_requests`는 "본 범위에서 없었다"는 뜻일 뿐 안전 판정이 아니다.
    """

    trigger: str  # 브라우저를 돌린 이유: script_only_page / script_redirect / status_403_404 / always
    final_url: str | None
    navigation_chain: tuple[str, ...]  # 메인 프레임이 이동한 주소 (JS 리다이렉트 포함)
    html: str  # JS 실행 후 DOM. 128 KiB 상한
    title: str | None
    downloads: tuple[str, ...]  # 브라우저가 시작하려던 다운로드 URL. 파일은 받지 않는다
    blocked_requests: int  # 허용 목록 밖 요청·WebSocket·팝업을 막은 횟수
    elapsed_ms: int
    failures: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CollectResult:
    input_url: str
    final_url: str | None
    redirect_chain: tuple[str, ...]
    status_code: int | None
    content_type: str | None
    html: str
    title: str | None
    elapsed_ms: int
    failures: tuple[str, ...] = field(default_factory=tuple)
    # 격리 서버의 3단계 결과. None 이면 돌리지 않음(메인 서버 로컬 수집기는 항상 None).
    browser: BrowserResult | None = None


@dataclass(frozen=True)
class StaticCheckResult:
    """1단계 정적 검사 결과 (docs/scanner-static-checks-proposal.md).

    기존 공식 도메인 비교에 KISA 목록 대조·Punycode/Unicode 사칭 분석을
    더한다. 여기서도 위험 "판정"은 안 한다 — kisa_listed/lookalike_of는
    scorer 가 가중치를 매길 사실일 뿐이다.
    """

    domain: str
    official_match: str  # services.official_domain_service.DomainMatch 값
    is_punycode: bool
    decoded_domain: str | None  # punycode 디코딩 결과. 디코딩 대상이 아니거나 실패하면 None
    kisa_listed: bool
    lookalike_of: str | None  # 시각적으로 혼동되는 공식 도메인 (없으면 None)
    failures: tuple[str, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class UrlEvidence:
    """URL 하나에 대해 BE가 수집한 검사 증거 묶음.

    이 모델은 사실값만 보관하며 위험 점수나 최종 판정을 만들지 않는다.
    scorer가 도입되기 전까지는 관찰 및 비교 용도로 사용한다.
    """

    input_static: StaticCheckResult
    final_static: StaticCheckResult | None
    collector: CollectResult
    urlscan: dict