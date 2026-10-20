"""#101: extraction, collection and shortener failures stay distinct; no network."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import main
from ai.pipeline import finalize_analysis
from ai.types import MessagePart, PageProposal
from backend.src.server.url_utils import split_message
from scanner.isolation_client import collect_url_isolated
from services.link_resolution import first_real_host, is_shortener
from services.result_card_renderer import render_detail_carousel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra" / "isolation"))
from collector.policy import NetworkPolicy  # noqa: E402


UNKNOWN = "https://cjlogistics.com.delivery-check.example/address"
OFFICIAL = "https://www.cjlogistics.com/"


@pytest.fixture(autouse=True)
def state(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(main, "ANALYSIS_JOBS", {})
    monkeypatch.setattr(main, "RUNNING_USERS", set())


@pytest.mark.parametrize("text,expected", [
    ("일반 문자", "no-link"), ("010-1234-5678", "no-link"),
    ("https://", "malformed"), ("https:/example.com", "malformed"),
    ("https://example.com:bad/path", "malformed"),
])
async def test_no_link_and_bad_format_have_different_skill_responses(text, expected, monkeypatch):
    analysis = AsyncMock()
    monkeypatch.setattr(main, "run_analysis", analysis)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://test") as client:
        response = await client.post("/kakao/skill", json={"userRequest": {"utterance": text}})
    assert response.status_code == 200
    body = response.json()
    if expected == "no-link":
        assert body == main.render_card("r6-input-required")
    else:
        assert "주소 형식" in json.dumps(body, ensure_ascii=False)
        assert body != main.render_card("r6-input-required")
    analysis.assert_not_awaited()
    assert not main.ANALYSIS_JOBS


@pytest.mark.parametrize("text,collection,risk", [
    (UNKNOWN, "connection_failed", False), (UNKNOWN, "connection_failed", True),
    ("https://example.com/path", "connection_failed", False),
    ("http://parcel.example/path", "blocked_address", False),
    ("https://bit.ly/AbCd", "clean", False), ("bit.ly/AbCd", "clean", False),
    ("https://bit.ly/AbCd", "connection_failed", False),
    ("bit.ly/AbCd", "connection_failed", True),
    ("https://example.com/path", "transport_error", False),
    ("https://bit.ly/AbCd", "transport_error", True),
    ("https://bit.ly/AbCd", "mismatch", False),
])
async def test_recognized_url_reaches_collection_and_keeps_message_evidence(text, collection, risk, monkeypatch):
    requested_url = split_message(text)[0][0]
    monkeypatch.setenv("ISOLATION_API_URL", "https://collector.example")
    monkeypatch.setenv("ISOLATION_API_TOKEN", "synthetic-token")
    message = MessagePart.model_validate({
        "brand": "CJ대한통운" if risk or collection in {"clean", "mismatch"} else None,
        "answer": "risk_found" if risk else "no_risk_found",
        "details": {"signals": [{"code": "install_prompt", "evidence": "앱을 설치해 주세요"}] if risk else [],
                    "reason": {"text": "검증된 문자 분석", "failures": []}},
    })
    monkeypatch.setattr(main, "analyze_message_part", AsyncMock(return_value=message))
    llm = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(refusal=None, parsed=PageProposal()))])
    ))))
    payloads = []
    async def finalize(**kwargs):
        result = await finalize_analysis(**kwargs, client=llm, model="test")
        payloads.append(result.model_dump(mode="json"))
        return result
    monkeypatch.setattr(main, "finalize_analysis", finalize)
    calls = []
    def handler(request):
        assert str(request.url) == "https://collector.example/collect"
        assert json.loads(request.content) == {"url": requested_url}
        calls.append(requested_url)
        if collection == "transport_error":
            raise httpx.ConnectError("synthetic connection failure", request=request)
        clean = collection in {"clean", "mismatch"}
        chain = ["https://delivery-check.example/", OFFICIAL] if collection == "mismatch" else [OFFICIAL]
        return httpx.Response(200, json={
            "input_url": requested_url, "final_url": OFFICIAL if clean else None,
            "redirect_chain": chain if clean else [], "status_code": 200 if clean else None,
            "content_type": "text/html" if clean else None, "html": "<p>배송 조회 안내</p>" if clean else "",
            "title": None, "elapsed_ms": 1, "failures": [] if clean else [collection], "browser": None,
        })
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as collector_client:
        async def collect(link):
            return await collect_url_isolated(link, client=collector_client)
        monkeypatch.setattr(main, "collect_url_isolated", collect)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://test") as client:
            # Use the real skill path, not run_analysis alone: prevents regressions to R6.
            response = await client.post("/kakao/skill", json={"userRequest": {"utterance": f"앱을 설치해 주세요 {text}"}})
    assert response.status_code == 200
    assert calls == [requested_url]
    assert response.json() != main.render_card("r6-input-required")
    rendered = json.dumps(response.json(), ensure_ascii=False)
    payload = payloads[0]
    detail = json.dumps(render_detail_carousel(payload), ensure_ascii=False)
    if risk:
        assert "위험한 문자" in rendered
        assert payload["message"]["details"]["signals"] == message.model_dump(mode="json")["details"]["signals"]
        assert "앱을 설치해 주세요" in detail
    elif collection == "mismatch":
        assert "공식 주소가 아니에요" in rendered
    elif collection != "clean":
        assert "판단하기 어려워요" in rendered
        assert "페이지 내용 (열지 못했어요)" in rendered
        assert "페이지: 페이지를 열어보려 했지만 열지 못했어요." in detail
    if is_shortener(requested_url.split("/")[2]) and collection not in {"clean", "mismatch"} and not risk:
        assert "단축 주소라 실제 주소를 확인하지 못했어요" in rendered
        assert "단축 주소라 실제 주소를 확인하지 못했어요" in detail
        assert payload["url"]["official"] == "unresolved"
    if collection == "clean":
        assert "공식 주소와 같아요" in rendered
        assert first_real_host(requested_url, [OFFICIAL]) == "www.cjlogistics.com"
        assert payload["url"]["domain"] == "www.cjlogistics.com"
        assert payload["url"]["official"] == "official"
    elif collection == "mismatch":
        assert payload["url"]["domain"] == "delivery-check.example"
        assert payload["url"]["official"] == "brand_mismatch"
    else:
        assert payload["env"]["answer"] == "failed"
        assert payload["result"] is False
        llm.chat.completions.parse.assert_not_awaited()


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/path", "https://10.0.0.1/path", "https://192.168.0.1/path",
    "https://169.254.169.254/latest", "https://[::1]/path", "https://localhost/path",
    "https://api.service.example/path", "https://sub.api.service.example/path",
    "http://public.example/path",
])
async def test_url_recognition_does_not_bypass_collection_policy(url):
    assert split_message(url)[0] == [url]
    async def no_dns(host):
        pytest.fail("blocked destination must not reach DNS")
    policy = NetworkPolicy(mode="public", protected_hosts=frozenset({"api.service.example"}),
                           protected_ips=frozenset({"8.8.8.8"}), resolve=no_dns)
    assert not policy.url_allowed(url)


async def test_extracted_public_host_with_private_dns_is_blocked():
    url = "https://public-looking.example/path"
    assert split_message(url)[0] == [url]
    async def private_dns(host):
        return ["10.0.0.1"]
    policy = NetworkPolicy(mode="public", protected_hosts=frozenset({"api.service.example"}),
                           protected_ips=frozenset({"8.8.8.8"}), resolve=private_dns)
    with pytest.raises(ValueError, match="no permitted"):
        await policy.connect_ip("public-looking.example", 443)
