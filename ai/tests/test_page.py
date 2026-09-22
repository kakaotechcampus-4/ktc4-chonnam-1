from __future__ import annotations

import urllib.request

import httpx
import pytest

from ai.page import (
    MAX_ELEMENTS,
    MAX_PAGE_TEXT_CHARS,
    inspect_html,
    select_env_doubt,
)
from ai.types import EnvDoubt, FailureCode


def test_login_form_does_not_claim_submission():
    html = (
        '<form><label>계정<input name="account"></label>'
        '<label>비밀번호<input type="password"></label></form>'
    )

    result = inspect_html(html)

    assert result.failure is None
    assert select_env_doubt(result.elements) is EnvDoubt.LOGIN_FORM
    assert all(item.evidence in html for item in result.elements)


def test_script_comment_and_ignored_content_are_not_observed_forms():
    html = (
        '<p>배송 안내</p><!-- <input type="password"> -->'
        '<script>"<input type=password value=script-token>"</script>'
        '<style>.x::before { content: "결제하세요" }</style>'
        '<template><form><input name="address"></form></template>'
        '<noscript><a href="app.apk">앱 다운로드</a></noscript>'
    )

    result = inspect_html(html)

    assert result.failure is None
    assert select_env_doubt(result.elements) is EnvDoubt.NONE
    assert result.text == "배송 안내"
    assert "script-token" not in result.text


def test_unclosed_fake_form_inside_template_does_not_make_page_partial():
    html = "<p>정상 안내</p><template><form><input type=password>"

    result = inspect_html(html)

    assert result.failure is None
    assert result.text == "정상 안내"
    assert result.elements == ()


@pytest.mark.parametrize(
    ("html", "failure"),
    [
        ("", FailureCode.EMPTY_INPUT),
        ("가" * 50_000, FailureCode.INPUT_TOO_LARGE),
    ],
    ids=("empty", "utf8-byte-limit"),
)
def test_input_boundaries_are_reported_before_success(html: str, failure: FailureCode):
    assert inspect_html(html).failure is failure


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<a href="/client.apk">앱 다운로드</a>', EnvDoubt.APP_LINK),
        ("<button>결제 진행</button>", EnvDoubt.PAYMENT),
        (
            '<form><label>이름<input name="name" autocomplete="name"></label></form>',
            EnvDoubt.PERSONAL_FORM,
        ),
        (
            '<form><label>운송장 조회<input name="tracking"></label></form>',
            EnvDoubt.PARCEL_WIDGET,
        ),
        ('<a href="/photo.jpg">배송 사진 보기</a>', EnvDoubt.DOCUMENT_VIEW),
        (
            '<div><label>인증번호<input autocomplete="one-time-code"></label></div>',
            EnvDoubt.LOGIN_FORM,
        ),
    ],
)
def test_each_supported_structure_requires_real_html_elements(
    html: str, expected: EnvDoubt
):
    result = inspect_html(html)

    assert result.failure is None
    assert select_env_doubt(result.elements) is expected
    assert result.elements[0].evidence in html


def test_address_form_replaces_personal_duplicate_but_keeps_independent_link():
    html = (
        '<form><label>이름<input name="name"></label>'
        '<label>주소<input name="address" autocomplete="street-address"></label></form>'
        '<a href="/app.apk">앱 설치 파일 다운로드</a>'
    )

    result = inspect_html(html)

    assert [item.doubt for item in result.elements] == [
        EnvDoubt.ADDRESS_FORM,
        EnvDoubt.APP_LINK,
    ]
    assert [item.start for item in result.elements] == sorted(
        item.start for item in result.elements
    )
    assert len({item.element_id for item in result.elements}) == 2
    assert [item.element_id for item in inspect_html(html).elements] == [
        item.element_id for item in result.elements
    ]


def test_evidence_offsets_entities_hidden_state_and_attribute_sanitizing():
    html = (
        '<main>안내&amp;확인\n'
        '<form hidden style="display:none" action="/submit" data-token="DATA_SECRET">'
        '<label>주소&nbsp;입력<input name="address" value="INPUT_SECRET" '
        'onclick="steal()"></label></form></main>'
    )
    expected_evidence = html[html.index("<form") : html.index("</form>") + 7]

    result = inspect_html(html)

    assert result.failure is None
    assert result.text == "안내&확인 주소 입력"
    assert len(result.elements) == 1
    element = result.elements[0]
    assert element.start == html.index("<form")
    assert element.evidence == expected_evidence
    assert element.attributes["hidden"] == ""
    assert element.attributes["style"] == "display:none"
    assert element.attributes["action"] == "/submit"
    assert set(element.attributes) <= {
        "type",
        "name",
        "autocomplete",
        "href",
        "action",
        "aria-label",
        "hidden",
        "style",
    }
    digest = result.text + element.text + repr(element.attributes)
    assert "INPUT_SECRET" not in digest
    assert "DATA_SECRET" not in digest
    assert "steal" not in digest


def test_input_value_and_data_attributes_cannot_create_a_form_classification():
    html = (
        '<form><input value="비밀번호" data-purpose="주소" '
        'data-token="AUTH_SECRET"></form><p>일반 안내</p>'
    )

    result = inspect_html(html)

    assert result.failure is None
    assert result.elements == ()
    assert result.text == "일반 안내"


def test_start_tag_evidence_uses_parser_boundary_when_attribute_contains_angle():
    html = '<input aria-label="주소 > 입력" name="address">'

    result = inspect_html(html)

    assert result.failure is None
    assert result.elements[0].evidence == html
    assert result.elements[0].start == 0


def test_source_order_and_ids_do_not_depend_on_nested_closure_order():
    html = (
        '<form><label>비밀번호<input type="password"></label>'
        '<a href="/app.apk">앱 다운로드</a></form>'
    )

    result = inspect_html(html)

    assert [item.doubt for item in result.elements] == [
        EnvDoubt.LOGIN_FORM,
        EnvDoubt.APP_LINK,
    ]
    assert result.elements[0].start < result.elements[1].start
    assert result.elements[0].element_id < result.elements[1].element_id


def test_unclosed_important_form_preserves_observed_element_as_partial():
    html = '<p>먼저 확인함</p><form><label>비밀번호<input type="password">'

    result = inspect_html(html)

    assert result.failure is FailureCode.PARTIAL_CONTENT
    assert result.text == "먼저 확인함 비밀번호"
    assert [item.doubt for item in result.elements] == [EnvDoubt.LOGIN_FORM]
    assert result.elements[0].evidence == html[html.index("<form") :]


def test_element_limit_preserves_candidates_collected_before_limit():
    login = '<form><label>비밀번호<input type="password"></label></form>'
    html = login + ("<div></div>" * MAX_ELEMENTS)

    result = inspect_html(html)

    assert result.failure is FailureCode.PARTIAL_CONTENT
    assert [item.doubt for item in result.elements] == [EnvDoubt.LOGIN_FORM]
    assert result.elements[0].evidence == login


def test_text_limit_preserves_text_prefix_and_prior_elements():
    login = '<form><label>비밀번호<input type="password"></label></form>'
    html = login + "<p>" + ("가" * (MAX_PAGE_TEXT_CHARS + 20)) + "</p>"

    result = inspect_html(html)

    assert result.failure is FailureCode.PARTIAL_CONTENT
    assert len(result.text) <= MAX_PAGE_TEXT_CHARS
    assert result.text.startswith("비밀번호")
    assert [item.doubt for item in result.elements] == [EnvDoubt.LOGIN_FORM]


def test_script_only_is_empty_but_meaningful_generic_page_is_successful_none():
    script_only = inspect_html('<script>document.write("배송 조회")</script>')
    generic = inspect_html("<h1>배송 조회</h1><p>배송 안내를 확인했습니다.</p>")

    assert script_only.failure is FailureCode.EMPTY_INPUT
    assert script_only.elements == ()
    assert generic.failure is None
    assert generic.elements == ()
    assert select_env_doubt(generic.elements) is EnvDoubt.NONE


def test_completed_payment_text_and_bare_feature_titles_are_not_structures():
    html = "<h1>배송 조회</h1><p>결제 완료</p><p>앱 설치 안내</p>"

    result = inspect_html(html)

    assert result.failure is None
    assert result.elements == ()


def test_inspection_never_opens_links_or_submits_forms(monkeypatch: pytest.MonkeyPatch):
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("HTML inspection must not perform network I/O")

    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    monkeypatch.setattr(httpx.AsyncClient, "get", fail_network)
    html = (
        '<form action="https://invalid.example/submit">'
        '<label>비밀번호<input type="password"></label></form>'
        '<a href="javascript:alert(1)" onclick="alert(2)">앱 다운로드</a>'
    )

    result = inspect_html(html)

    assert result.failure is None
    assert [item.doubt for item in result.elements] == [
        EnvDoubt.LOGIN_FORM,
        EnvDoubt.APP_LINK,
    ]
