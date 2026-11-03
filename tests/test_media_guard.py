"""backend.src.server.media_guard 테스트.

callback_guard.py와 같은 원칙(호스트 정확 일치, https·443·user@ 없음)에
더해, 허용한 호스트라도 지금 이 순간의 DNS 응답을 다시 확인하는지
검증한다 — 실제 네트워크 호출 없이 주입한 resolver로 테스트한다.
"""

import pytest

from backend.src.server.media_guard import (
    MediaDownloadBlocked,
    assert_resolves_to_public_ip,
    is_allowed_media_url,
    is_public_ipv4,
    media_host,
)


class TestMediaHost:
    def test_extracts_hostname_without_query_string(self):
        assert media_host("https://media.kakao.test/a?token=secret") == "media.kakao.test"

    def test_non_string_returns_none(self):
        assert media_host(None) is None
        assert media_host(123) is None


class TestIsAllowedMediaUrl:
    def test_rejects_when_no_hosts_configured(self, monkeypatch):
        monkeypatch.delenv("KAKAO_MEDIA_HOSTS", raising=False)
        assert is_allowed_media_url("https://media.kakao.test/a") is False

    def test_accepts_exact_configured_host(self, monkeypatch):
        monkeypatch.setenv("KAKAO_MEDIA_HOSTS", "media.kakao.test")
        assert is_allowed_media_url("https://media.kakao.test/a?token=x") is True

    def test_subdomain_is_not_automatically_allowed(self, monkeypatch):
        monkeypatch.setenv("KAKAO_MEDIA_HOSTS", "media.kakao.test")
        assert is_allowed_media_url("https://evil.media.kakao.test/a") is False

    def test_rejects_non_https_scheme(self, monkeypatch):
        monkeypatch.setenv("KAKAO_MEDIA_HOSTS", "media.kakao.test")
        assert is_allowed_media_url("http://media.kakao.test/a") is False

    def test_rejects_non_default_port(self, monkeypatch):
        monkeypatch.setenv("KAKAO_MEDIA_HOSTS", "media.kakao.test")
        assert is_allowed_media_url("https://media.kakao.test:8443/a") is False

    def test_rejects_userinfo(self, monkeypatch):
        monkeypatch.setenv("KAKAO_MEDIA_HOSTS", "media.kakao.test")
        assert is_allowed_media_url("https://user@media.kakao.test/a") is False

    def test_rejects_unlisted_host(self, monkeypatch):
        monkeypatch.setenv("KAKAO_MEDIA_HOSTS", "media.kakao.test")
        assert is_allowed_media_url("https://attacker.example.test/a") is False

    def test_rejects_control_characters(self, monkeypatch):
        monkeypatch.setenv("KAKAO_MEDIA_HOSTS", "media.kakao.test")
        assert is_allowed_media_url("https://media.kakao.test/a\nb") is False

    def test_non_string_input_rejected(self, monkeypatch):
        monkeypatch.setenv("KAKAO_MEDIA_HOSTS", "media.kakao.test")
        assert is_allowed_media_url(None) is False


class TestIsPublicIpv4:
    @pytest.mark.parametrize("address", [
        "169.254.169.254",  # 클라우드 메타데이터
        "127.0.0.1",        # loopback
        "10.0.0.1",         # private
        "172.16.0.1",       # private
        "192.168.0.1",      # private
        "0.0.0.0",          # reserved
        "224.0.0.1",        # multicast
        "not-an-ip",
    ])
    def test_blocks_non_public_addresses(self, address):
        assert is_public_ipv4(address) is False

    def test_accepts_public_address(self):
        assert is_public_ipv4("8.8.8.8") is True  # 실제 공인 IPv4 (Google DNS)

    def test_rejects_ipv6(self):
        assert is_public_ipv4("2001:db8::1") is False


class TestAssertResolvesToPublicIp:
    async def test_passes_when_all_resolved_addresses_are_public(self):
        async def fake_resolve(host):
            return ["8.8.8.8"]

        await assert_resolves_to_public_ip("media.kakao.test", resolve=fake_resolve)

    async def test_blocks_when_any_resolved_address_is_private(self):
        """DNS 리바인딩 방어 — 여러 주소 중 하나라도 내부망이면 전부 거부."""

        async def fake_resolve(host):
            return ["8.8.8.8", "169.254.169.254"]

        with pytest.raises(MediaDownloadBlocked):
            await assert_resolves_to_public_ip("media.kakao.test", resolve=fake_resolve)

    async def test_blocks_when_resolution_returns_nothing(self):
        async def fake_resolve(host):
            return []

        with pytest.raises(MediaDownloadBlocked):
            await assert_resolves_to_public_ip("media.kakao.test", resolve=fake_resolve)

    async def test_blocks_when_resolution_raises(self):
        async def failing_resolve(host):
            raise OSError("dns failure")

        with pytest.raises(MediaDownloadBlocked):
            await assert_resolves_to_public_ip("media.kakao.test", resolve=failing_resolve)
