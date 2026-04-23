from __future__ import annotations

from unittest.mock import MagicMock

from src.config.models import SiteMonitorConfig, SubmissionMode
from src.detector.engine import PageStateDetector
from src.detector.models import DetectorState, ProbeResult, ProbeTarget
from src.detector.profile import LivingScienceProfile


def _make_probe(status_code: int, text: str, content_type: str = "text/html") -> ProbeResult:
    return ProbeResult(
        target_name="home",
        requested_url="https://livingscience.ch/wohnen-studieren-zuerich/?L=0",
        final_url="https://livingscience.ch/wohnen-studieren-zuerich/?L=0",
        status_code=status_code,
        headers={"content-type": content_type},
        text=text,
        duration_ms=10,
        fetched_at="2026-04-23T18:00:00Z",
    )


def _site_config() -> SiteMonitorConfig:
    return SiteMonitorConfig(
        site_id="livingscience",
        enabled=True,
        poll_interval_seconds=300,
        jitter_seconds=0.0,
        timeout_seconds=5,
        max_retries=0,
        submission_mode=SubmissionMode.DISABLED,
    )


def test_non_2xx_response_forces_failed_state() -> None:
    client = MagicMock()
    client.fetch.return_value = _make_probe(502, "<html>Bad gateway</html>")
    detector = PageStateDetector(client)

    execution = detector.detect(LivingScienceProfile(), _site_config())

    assert execution.result.state is DetectorState.FAILED
    assert "implausible_response" in execution.result.signals


def test_short_body_forces_failed_state() -> None:
    client = MagicMock()
    client.fetch.return_value = _make_probe(200, "ok")
    detector = PageStateDetector(client)

    execution = detector.detect(LivingScienceProfile(), _site_config())

    assert execution.result.state is DetectorState.FAILED
    assert "implausible_response" in execution.result.signals


def test_non_html_content_type_forces_failed_state() -> None:
    client = MagicMock()
    client.fetch.return_value = _make_probe(
        200,
        '{"status":"maintenance"}',
        content_type="application/json",
    )
    detector = PageStateDetector(client)

    execution = detector.detect(LivingScienceProfile(), _site_config())

    assert execution.result.state is DetectorState.FAILED
    assert "implausible_response" in execution.result.signals
