"""업무별 공식 안내 고르기.

판정이 아닙니다. 최종 분석 결과의 정해진 값(brand·category·doubts·official)만 보고
검토를 마친(curated) norm_rules 의 확인 경로·공식 절차를 고릅니다. 문자 원문·페이지
같은 자유 텍스트와 LLM 은 쓰지 않습니다. 결과는 result·answer·카드 선택에 쓰지 않습니다.
설계: docs/superpowers/specs/2026-10-11-norm-guidance-design.md
"""

import logging
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

LOGGER = logging.getLogger(__name__)

NORM_DIR = Path(__file__).resolve().parent / "norm_rules"
MATCH_KEYS = frozenset({"category", "brand", "doubts", "official"})
# "자세히 보기" 캐러셀 카드 1장이 128자라 많이 넘겨도 다 보여 줄 수 없다.
MAX_GUIDANCE = 3
_KIND_ORDER = {"verification": 0, "procedure": 1}
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
_LOAD_LOCK = threading.Lock()


@dataclass(frozen=True)
class Guidance:
    kind: str
    text: str
    source: str
    published_at: str
    rule_id: str


@dataclass(frozen=True)
class _Rule:
    guidance: Guidance
    match: tuple[dict[str, str], ...]


def _parse_rule(path: Path) -> _Rule | None:
    try:
        match = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
        meta = yaml.safe_load(match.group(1)) if match else None
    except (OSError, yaml.YAMLError) as exc:
        LOGGER.warning("norm rule skipped: %s", type(exc).__name__)
        return None
    if not isinstance(meta, dict) or meta.get("status") != "curated":
        return None
    kind = meta.get("kind")
    conditions = meta.get("match")
    sources = meta.get("sources")
    text = str(meta.get("statement") or "").strip()
    if (kind not in _KIND_ORDER or not text or not isinstance(conditions, list)
            or not conditions or not isinstance(sources, list) or not sources
            or not isinstance(sources[0], dict)):
        return None
    parsed = []
    for condition in conditions:
        if not isinstance(condition, dict) or not condition or not set(condition) <= MATCH_KEYS:
            return None
        parsed.append({str(key): str(value) for key, value in condition.items()})
    first = sources[0]
    return _Rule(
        guidance=Guidance(
            kind=kind, text=text, source=str(first.get("name") or ""),
            published_at=str(first.get("published_at") or ""),
            rule_id=str(meta.get("id") or path.stem),
        ),
        match=tuple(parsed),
    )


def load_rules(norm_dir: Path = NORM_DIR) -> tuple[_Rule, ...]:
    """search.load_cases 와 같이 프로세스 수명 동안 캐시한다. 자료를 고치면 재기동한다."""
    with _LOAD_LOCK:
        return _load_rules(Path(norm_dir))


@lru_cache(maxsize=8)
def _load_rules(norm_dir: Path) -> tuple[_Rule, ...]:
    if not norm_dir.is_dir():
        return ()
    rules = (_parse_rule(path) for path in sorted(norm_dir.glob("NR-*.md")))
    return tuple(rule for rule in rules if rule is not None)


def _strings(value: Any) -> set[str]:
    return {value} if isinstance(value, str) else set()


def _facts(result: Any) -> dict[str, set[str]] | None:
    if isinstance(result, BaseModel):
        result = result.model_dump(mode="json")
    if not isinstance(result, Mapping):
        return None
    facts: dict[str, set[str]] = {key: set() for key in MATCH_KEYS}
    for part_name in ("message", "env"):
        part = result.get(part_name)
        if isinstance(part, Mapping):
            facts["brand"] |= _strings(part.get("brand"))
            facts["category"] |= _strings(part.get("category"))
    message = result.get("message")
    details = message.get("details") if isinstance(message, Mapping) else None
    doubts = details.get("doubts") if isinstance(details, Mapping) else None
    for doubt in doubts if isinstance(doubts, list) else ():
        if isinstance(doubt, Mapping):
            facts["doubts"] |= _strings(doubt.get("value"))
    url = result.get("url")
    if isinstance(url, Mapping):
        facts["official"] |= _strings(url.get("official"))
    return facts


def find_guidance(result: Any, norm_dir: Path = NORM_DIR) -> list[Guidance]:
    """맞는 확인 경로·공식 절차를 최대 3개 돌려준다. 예외를 던지지 않는다."""
    try:
        facts = _facts(result)
        if facts is None:
            return []
        hits = [
            rule.guidance for rule in load_rules(norm_dir)
            if any(all(value in facts[key] for key, value in condition.items())
                   for condition in rule.match)
        ]
    except Exception as exc:  # 설명용 부가 기능이라 실패해도 응답을 막지 않는다 (원칙 3)
        LOGGER.warning("guidance lookup failed: %s", type(exc).__name__)
        return []
    hits.sort(key=lambda item: (_KIND_ORDER[item.kind], item.rule_id))
    return hits[:MAX_GUIDANCE]
