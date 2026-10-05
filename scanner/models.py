"""자체 URL Collector 가 돌려주는 자료 모양.

urlscan 을 대체하는 1차 단계: "URL 방문·수집"만 한다. 위험도 `score`나
`malicious` 판정은 여기서 만들지 않는다 (오늘 작업 계획 §담당 B 역할).
그 값은 이후 scorer 단계에서 이 결과를 입력으로 받아 계산한다.
"""

from dataclasses import dataclass, field


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
