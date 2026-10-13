
import json

import httpx
import pytest

from scanner.isolation_client import (
    IsolationClientError,
    MAX_RESPONSE_BYTES,
    collect_url_isolated,
)
from scanner.models import CollectResult


API_URL = "https://isolation.example.com"
TEST_URL = "https://example.com/suspicious"


@pytest.fixture(autouse=True)
def isolation_env(monkeypatch):
    monkeypatch.setenv("ISOLATION_API_URL", API_URL)
    monkeypatch.setenv("ISOLATION_API_TOKEN", "test-secret")


@pytest.fixture
def valid_payload():
    return {
        "input_url": TEST_URL,
        "final_url": "https://example.com/landing",
        "redirect_chain": ["https://example.com/landing"],
        "status_code": 200,
        "content_type": "text/html",
        "html": "<html><title>Test</title></html>",
        "title": "Test",
        "elapsed_ms": 100,
        "failures": [],
    }


def fake_parser(expected_url, data):
    assert expected_url == TEST_URL

    return CollectResult(
        input_url=expected_url,
        final_url=data["final_url"],
        redirect_chain=tuple(data["redirect_chain"]),
        status_code=data["status_code"],
        content_type=data["content_type"],
        html=data["html"],
        title=data["title"],
        elapsed_ms=data["elapsed_ms"],
        failures=tuple(data["failures"]),
    )


def make_client(handler):
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


@pytest.mark.asyncio
async def test_success(valid_payload):
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/collect"
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert request.headers["Content-Type"] == "application/json"
        assert json.loads(request.content) == {"url": TEST_URL}

        return httpx.Response(200, json=valid_payload)

    async with make_client(handler) as client:
        result = await collect_url_isolated(
            TEST_URL,
            parse_response=fake_parser,
            client=client,
        )

    assert isinstance(result, CollectResult)
    assert result.final_url == "https://example.com/landing"
    assert result.title == "Test"
    assert result.failures == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 500, 503])
async def test_http_error(status):
    def handler(request):
        return httpx.Response(status)

    async with make_client(handler) as client:
        with pytest.raises(
            IsolationClientError,
            match=f"HTTP 오류: {status}",
        ):
            await collect_url_isolated(
                TEST_URL,
                parse_response=fake_parser,
                client=client,
            )


@pytest.mark.asyncio
async def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout("request timed out")

    async with make_client(handler) as client:
        with pytest.raises(
            IsolationClientError,
            match="시간 초과",
        ):
            await collect_url_isolated(
                TEST_URL,
                parse_response=fake_parser,
                client=client,
            )


@pytest.mark.asyncio
async def test_connection_failure():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    async with make_client(handler) as client:
        with pytest.raises(
            IsolationClientError,
            match="연결 실패",
        ):
            await collect_url_isolated(
                TEST_URL,
                parse_response=fake_parser,
                client=client,
            )


@pytest.mark.asyncio
async def test_invalid_json():
    def handler(request):
        return httpx.Response(
            200,
            content=b"{invalid json",
        )

    async with make_client(handler) as client:
        with pytest.raises(
            IsolationClientError,
            match="유효한 UTF-8 JSON",
        ):
            await collect_url_isolated(
                TEST_URL,
                parse_response=fake_parser,
                client=client,
            )


@pytest.mark.asyncio
async def test_invalid_utf8():
    def handler(request):
        return httpx.Response(
            200,
            content=b"\xff\xfe",
        )

    async with make_client(handler) as client:
        with pytest.raises(
            IsolationClientError,
            match="유효한 UTF-8 JSON",
        ):
            await collect_url_isolated(
                TEST_URL,
                parse_response=fake_parser,
                client=client,
            )


@pytest.mark.asyncio
async def test_json_array_response():
    def handler(request):
        return httpx.Response(200, json=[])

    async with make_client(handler) as client:
        with pytest.raises(
            IsolationClientError,
            match="JSON 객체",
        ):
            await collect_url_isolated(
                TEST_URL,
                parse_response=fake_parser,
                client=client,
            )


@pytest.mark.asyncio
async def test_response_too_large():
    def handler(request):
        return httpx.Response(
            200,
            content=b"x" * (MAX_RESPONSE_BYTES + 1),
        )

    async with make_client(handler) as client:
        with pytest.raises(
            IsolationClientError,
            match="응답 크기 초과",
        ):
            await collect_url_isolated(
                TEST_URL,
                parse_response=fake_parser,
                client=client,
            )


@pytest.mark.asyncio
async def test_missing_configuration(monkeypatch):
    monkeypatch.delenv("ISOLATION_API_TOKEN")

    with pytest.raises(
        IsolationClientError,
        match="설정되지 않았습니다",
    ):
        await collect_url_isolated(
            TEST_URL,
            parse_response=fake_parser,
        )


@pytest.mark.asyncio
async def test_reject_http_api_url(monkeypatch):
    monkeypatch.setenv(
        "ISOLATION_API_URL",
        "http://isolation.example.com",
    )

    with pytest.raises(
        IsolationClientError,
        match="HTTPS",
    ):
        await collect_url_isolated(
            TEST_URL,
            parse_response=fake_parser,
        )


@pytest.mark.asyncio
async def test_parser_called(valid_payload):
    received = []

    def recording_parser(expected_url, data):
        received.append((expected_url, data))
        return fake_parser(expected_url, data)

    def handler(request):
        return httpx.Response(200, json=valid_payload)

    async with make_client(handler) as client:
        result = await collect_url_isolated(
            TEST_URL,
            parse_response=recording_parser,
            client=client,
        )

    assert received == [(TEST_URL, valid_payload)]
    assert result.input_url == TEST_URL
