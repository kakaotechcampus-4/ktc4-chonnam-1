"""평가용 문자 추출(ai.llm.analyze_message) 캐시와 수법 요약 문장.

운영 추출을 그대로 부르고 결과를 저장소 밖 파일에 둔다. 키는 LLM_MODEL 과 문장의 sha256 이다.

    python eval/extract_cache.py            # KB variant 와 평가 문자 전부를 채운다
"""

import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import embed_cache  # noqa: F401  (평가 실행용 환경변수 적재)
from ai.llm._client import create_client
from ai.llm.analyze import TIMEOUT_SECONDS, analyze_message
from ai.types import AnalysisStatus, MessageAnalysis

CONCURRENCY = 8


def _path() -> Path:
    model = os.getenv("LLM_MODEL", "unknown").replace("/", "_")
    return embed_cache.DEFAULT_DIR / f"extract-{model}.jsonl"


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load() -> dict[str, MessageAnalysis]:
    path = _path()
    found = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                found[item["k"]] = MessageAnalysis.model_validate(item["v"])
    return found


async def fill(texts: Sequence[str]) -> dict[str, MessageAnalysis]:
    found = load()
    missing = [t for t in dict.fromkeys(texts) if _key(t) not in found]
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    gate = asyncio.Semaphore(CONCURRENCY)
    client = create_client(TIMEOUT_SECONDS)
    done = 0

    async def one(text: str, out) -> None:
        nonlocal done
        async with gate:
            result = await analyze_message(text, client=client)
        # 실패는 저장하지 않는다. 다시 돌리면 그 문장만 다시 부른다.
        if result.analysis_status is AnalysisStatus.COMPLETED:
            found[_key(text)] = result
            out.write(json.dumps({"k": _key(text), "v": result.model_dump(mode="json")}, ensure_ascii=False) + "\n")
            out.flush()
        done += 1
        if done % 100 == 0:
            print(f"{done}/{len(missing)}", file=sys.stderr)

    with path.open("a", encoding="utf-8") as out:
        await asyncio.gather(*(one(text, out) for text in missing))
    return found


def summary(analysis: MessageAnalysis | None) -> str:
    """추출 결과를 한 줄 수법 요약으로. 근거 문자열은 원문에 있는 것만 남아 있다."""
    if analysis is None:
        return ""
    parts = []
    if analysis.claimed_sender.value:
        parts.append(f"발신: {analysis.claimed_sender.value}")
    if analysis.claimed_purpose.value:
        parts.append(f"명분: {analysis.claimed_purpose.value}")
    actions = [a.value for a in analysis.requested_actions if a.value]
    if actions:
        parts.append("요구: " + ", ".join(actions))
    persuasion = sorted({p.code.value for p in analysis.persuasion_signals})
    if persuasion:
        parts.append("압박: " + ", ".join(persuasion))
    return " / ".join(parts)


def main() -> int:
    import rag_eval
    from ai.kb.search import CASES_DIR, load_cases

    texts = [v for case in load_cases(CASES_DIR) for v in case.variants]
    texts += [row["message"] for row in rag_eval.drop_excluded(rag_eval.load_rows(with_licensed=True))]
    found = asyncio.run(fill(texts))
    print(f"{sum(_key(t) in found for t in texts)}/{len(set(texts))} 추출됨", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
