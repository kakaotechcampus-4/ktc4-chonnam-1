import pytest

from ai.types import CheckState, ObservationStatus, PageState


@pytest.mark.parametrize(
    "name,status,page_state",
    [
        ("benign", ObservationStatus.SUCCESS, PageState.RENDERED),
        ("form", ObservationStatus.SUCCESS, PageState.RENDERED),
        ("apk", ObservationStatus.PARTIAL, PageState.RENDERED),
        ("failed", ObservationStatus.FAILED, PageState.UNREACHABLE),
        ("expired", ObservationStatus.SUCCESS, PageState.EXPIRED),
    ],
)
def test_fixture_loads(load_observations, name, status, page_state):
    obs = load_observations(name)

    assert obs.status is status
    assert obs.page_state is page_state
    assert obs.source == "stub"


def test_benign_checks_are_explicitly_absent(load_observations):
    obs = load_observations("benign")

    assert all(
        check.state is CheckState.CHECKED_ABSENT for check in obs.checks.values()
    )


def test_apk_separates_unknown_from_absent(load_observations):
    obs = load_observations("apk")

    assert obs.checks["permissions"].state is CheckState.FOUND
    assert obs.checks["iocs"].state is CheckState.UNKNOWN
    assert obs.checks["form_inputs"].state is CheckState.CHECKED_ABSENT
    assert "commercial_packer" in obs.static_risk_signals
    assert [item.check for item in obs.unchecked] == ["iocs"]


def test_expired_looks_clean_but_is_not_safe(load_observations):
    # 만료 페이지의 함정이 이것이다. 수집은 성공했고 검사도 전부 돌았는데
    # 볼 게 없었을 뿐이다. checked_absent 를 안전으로 읽으면 통과시킨다.
    obs = load_observations("expired")

    assert obs.status is ObservationStatus.SUCCESS
    assert all(
        check.state is CheckState.CHECKED_ABSENT for check in obs.checks.values()
    )
    assert obs.static_risk_signals == []
    assert obs.unchecked == []


def test_failed_marks_everything_unknown(load_observations):
    obs = load_observations("failed")

    assert all(check.state is CheckState.UNKNOWN for check in obs.checks.values())
    assert obs.final_url is None
