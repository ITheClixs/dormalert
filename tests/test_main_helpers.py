from __future__ import annotations

from src.main import _health_problems, _heartbeat_title, _sites_crossing_failure_threshold
from src.persistence.sqlite_store import SiteRuntimeRecord
from src.utils.time import parse_utc_iso


def _runtime(
    site_id: str,
    consecutive_failures: int,
    state: str = "closed",
    last_checked_at: str = "2026-07-13T12:00:00Z",
) -> SiteRuntimeRecord:
    return SiteRuntimeRecord(
        site_id=site_id,
        display_name=site_id.title(),
        last_page_state=state,
        last_workflow_state=state,
        last_confidence=0.99,
        last_fingerprint="fp",
        last_checked_at=last_checked_at,
        consecutive_failures=consecutive_failures,
        last_transition_at="2026-07-13T12:00:00Z",
        updated_at="2026-07-13T12:00:00Z",
    )


def test_sites_crossing_failure_threshold_flags_exact_crossing() -> None:
    records = (
        _runtime("livingscience", 3, state="failed"),
        _runtime("studentvillage", 0),
    )
    assert _sites_crossing_failure_threshold(records, threshold=3) == ["livingscience"]


def test_sites_crossing_failure_threshold_ignores_ongoing_episode() -> None:
    records = (_runtime("livingscience", 7, state="failed"),)
    assert _sites_crossing_failure_threshold(records, threshold=3) == []


_NOW = parse_utc_iso("2026-07-13T12:10:00Z")


def test_health_problems_empty_when_recent_and_healthy() -> None:
    records = (_runtime("livingscience", 0, last_checked_at="2026-07-13T12:05:00Z"),)
    assert (
        _health_problems(records, expected_site_ids=["livingscience"], threshold=3, max_age_minutes=60, now=_NOW)
        == []
    )


def test_health_problems_flags_missing_site_state() -> None:
    problems = _health_problems((), expected_site_ids=["livingscience"], threshold=3, max_age_minutes=60, now=_NOW)
    assert len(problems) == 1
    assert "livingscience" in problems[0]
    assert "no detection state" in problems[0]


def test_health_problems_flags_failing_detector() -> None:
    records = (_runtime("livingscience", 5, state="failed", last_checked_at="2026-07-13T12:05:00Z"),)
    problems = _health_problems(records, expected_site_ids=["livingscience"], threshold=3, max_age_minutes=60, now=_NOW)
    assert len(problems) == 1
    assert "failing" in problems[0]


def test_health_problems_flags_stale_detection() -> None:
    records = (_runtime("livingscience", 0, last_checked_at="2026-07-13T09:00:00Z"),)
    problems = _health_problems(records, expected_site_ids=["livingscience"], threshold=3, max_age_minutes=60, now=_NOW)
    assert len(problems) == 1
    assert "last checked" in problems[0]


def test_heartbeat_title_flags_failing_detector() -> None:
    healthy = {"last_page_state": "closed", "consecutive_failures": 0}
    failing = {"last_page_state": "failed", "consecutive_failures": 5}

    assert "alive" in _heartbeat_title(healthy, threshold=3)
    assert "FAILING" in _heartbeat_title(failing, threshold=3)
    assert "FAILING" not in _heartbeat_title(healthy, threshold=3)
    assert "FAILING" not in _heartbeat_title(None, threshold=3)
