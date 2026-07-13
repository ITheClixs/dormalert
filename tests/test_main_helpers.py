from __future__ import annotations

from src.main import _heartbeat_title, _sites_crossing_failure_threshold
from src.persistence.sqlite_store import SiteRuntimeRecord


def _runtime(site_id: str, consecutive_failures: int, state: str = "closed") -> SiteRuntimeRecord:
    return SiteRuntimeRecord(
        site_id=site_id,
        display_name=site_id.title(),
        last_page_state=state,
        last_workflow_state=state,
        last_confidence=0.99,
        last_fingerprint="fp",
        last_checked_at="2026-07-13T12:00:00Z",
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


def test_heartbeat_title_flags_failing_detector() -> None:
    healthy = {"last_page_state": "closed", "consecutive_failures": 0}
    failing = {"last_page_state": "failed", "consecutive_failures": 5}

    assert "alive" in _heartbeat_title(healthy, threshold=3)
    assert "FAILING" in _heartbeat_title(failing, threshold=3)
    assert "FAILING" not in _heartbeat_title(healthy, threshold=3)
    assert "FAILING" not in _heartbeat_title(None, threshold=3)
