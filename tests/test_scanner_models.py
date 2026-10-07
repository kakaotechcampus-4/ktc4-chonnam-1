from scanner.models import CollectResult, StaticCheckResult, UrlEvidence


def test_url_evidence_keeps_scanner_results_separate():
    input_static = StaticCheckResult(
        domain="link24.kr",
        official_match="not_registered",
        is_punycode=False,
        decoded_domain=None,
        kisa_listed=False,
        lookalike_of=None,
        failures=("kisa_feed_unavailable",),
    )

    final_static = StaticCheckResult(
        domain="www.getbarrel.com",
        official_match="official",
        is_punycode=False,
        decoded_domain=None,
        kisa_listed=False,
        lookalike_of=None,
        failures=("kisa_feed_unavailable",),
    )

    collector = CollectResult(
        input_url="https://link24.kr/test",
        final_url="https://www.getbarrel.com/",
        redirect_chain=("https://www.getbarrel.com/",),
        status_code=200,
        content_type="text/html",
        html="",
        title="BARREL",
        elapsed_ms=100,
    )

    evidence = UrlEvidence(
        input_static=input_static,
        final_static=final_static,
        collector=collector,
        urlscan={
            "domain": "www.getbarrel.com",
            "score": 0,
            "malicious": False,
        },
    )

    assert evidence.input_static.domain == "link24.kr"
    assert evidence.final_static.domain == "www.getbarrel.com"
    assert evidence.collector.final_url == "https://www.getbarrel.com/"
    assert evidence.urlscan["score"] == 0