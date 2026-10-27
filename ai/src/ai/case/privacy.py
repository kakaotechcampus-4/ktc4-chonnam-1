"""개인정보 가리기 (스펙 D2, v3 §4)."""

from __future__ import annotations

import re
from typing import Protocol

import httpx

from ai.case.types import TranscriptResult

# 주민 → 카드 → 계좌 순서로 적용한다. 앞 규칙이 긴 숫자열을 먼저 가져가야
# 카드번호가 계좌번호로 잘못 가려지지 않는다.
_RRN = re.compile(r"(?<!\d)\d{6}-?[1-4]\d{6}(?!\d)")
# 13~16자리. 구분자는 모든 자리에서 같아야 한다(인접한 전화번호를 가로질러 잡지 않으려고)
_CARD = re.compile(r"(?<![\d-])\d{4}([ -]?)\d{4}\1\d{4}\1\d{1,4}(?![\d-])")
# 10~14자리 숫자열, 하이픈 허용
_ACCOUNT = re.compile(r"(?<![\d-])\d(?:-?\d){9,13}(?![\d-])")
# 전화번호(대표번호 포함)와 같은 모양은 근거로 써야 하므로 남긴다
_PHONE = re.compile(r"^(?:0\d{1,2}-?\d{3,4}-?\d{4}|1\d{3}(?:-?\d{4})?)$")


def _mask_unless_phone(label: str):
    def repl(m: re.Match[str]) -> str:
        s = m.group(0)
        return s if _PHONE.match(s) else label

    return repl


# URL 안의 숫자열(경로·쿼리)은 주소의 일부라 가리지 않는다
_URL = re.compile(r"https?://\S+|(?:www\.)\S+", re.IGNORECASE)


def _mask_plain(text: str) -> str:
    text = _RRN.sub("<주민번호>", text)
    text = _CARD.sub(_mask_unless_phone("<카드번호>"), text)
    text = _ACCOUNT.sub(_mask_unless_phone("<계좌번호>"), text)
    return text


def mask_pii(text: str) -> str:
    out: list[str] = []
    pos = 0
    for m in _URL.finditer(text):
        out.append(_mask_plain(text[pos : m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_mask_plain(text[pos:]))
    return "".join(out)


def mask_transcript(t: TranscriptResult) -> TranscriptResult:
    return t.model_copy(
        update={
            "segments": [
                s.model_copy(update={"text": mask_pii(s.text)}) for s in t.segments
            ]
        }
    )


class Masker(Protocol):
    async def mask(self, texts: list[str]) -> list[str]: ...


class EliceMasker:
    """엘리스 마스킹 API. 저장·외부 전송용이다.

    기관명·전화번호까지 가려 버리므로 분석 경로에서는 쓰지 않는다
    (분석은 둘 다 근거로 써야 한다). 분석 경로는 mask_pii 를 쓴다.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        # 테스트가 MockTransport 를 끼울 수 있게 열어 둔다
        self._transport = transport

    async def mask(self, texts: list[str]) -> list[str]:
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            resp = await client.post(
                f"{self._base_url}/mask",
                json={"text": texts},
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            resp.raise_for_status()
            return [item["masked_text"] for item in resp.json()]
