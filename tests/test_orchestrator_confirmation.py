from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from src.config.models import (
    AppConfig,
    BrowserSettings,
    NotificationSettings,
    SiteMonitorConfig,
    SubmissionMode,
)
from src.detector.models import (
    AntiBotObservation,
    AntiBotSeverity,
    DetectionExecution,
    DetectionResult,
    DetectorState,
)
from src.orchestrator.service import DormAlertService
from src.persistence.sqlite_store import SiteRuntimeRecord


def _config(tmp_path: Path) -> AppConfig:
    site = SiteMonitorConfig(
        site_id="studentvillage",
        enabled=True,
        poll_interval_seconds=15,
        jitter_seconds=0.0,
        timeout_seconds=5,
        max_retries=0,
        submission_mode=SubmissionMode.DRY_RUN,
    )
    return AppConfig(
        database_path=tmp_path / "db.sqlite",
        artifacts_dir=tmp_path / "artifacts",
        log_dir=tmp_path / "logs",
        log_level="INFO",
        user_agent="test",
        detector_only=True,
        notification=NotificationSettings(
            enable_console=False,
            webhook_url=None,
            webhook_timeout_seconds=10,
            email_enabled=False,
            smtp_host=None,
            smtp_port=587,
            smtp_username=None,
            smtp_password=None,
            smtp_starttls=True,
            email_from=None,
            email_to=(),
            alert_reminder_minutes=15,
        ),
        browser=BrowserSettings(headless=True, slow_mo_ms=0),
        failure_alert_threshold=3,
        closed_artifact_retention_days=7,
        sites={"studentvillage": site},
        studentvillage_applicant=None,
        studentvillage_success_phrases=(),
        studentvillage_failure_phrases=(),
        confirmation_min_gap_seconds=60,
        open_signal_fast_path_strength=0.95,
    )


def _open_result(fingerprint: str, open_strength: float, timestamp: str) -> DetectionResult:
    return DetectionResult(
        site_id="studentvillage",
        display_name="Student Village",
        state=DetectorState.OPEN,
        confidence=0.9,
        state_reason="closed_banners_removed_and_register_form_present",
        signal_scores={"closed_marker_strength": 0.0, "open_marker_strength": open_strength, "drift_risk": 0.15},
        state_version="test",
        signals=("closed_banners_removed",),
        facts=(), inferences=(), uncertainties=(),
        anti_bot=AntiBotObservation(AntiBotSeverity.NONE),
        page_urls=(),
        timestamp_utc=timestamp,
        fingerprint=fingerprint,
    )


def _runtime(fingerprint: str, transition_at: str) -> SiteRuntimeRecord:
    return SiteRuntimeRecord(
        site_id="studentvillage",
        display_name="Student Village",
        last_page_state=DetectorState.OPEN.value,
        last_workflow_state="open",
        last_confidence=0.9,
        last_fingerprint=fingerprint,
        last_checked_at=transition_at,
        consecutive_failures=0,
        last_transition_at=transition_at,
        updated_at=transition_at,
    )


def _service(tmp_path: Path) -> DormAlertService:
    config = _config(tmp_path)
    return DormAlertService(
        config=config,
        profiles={},
        detector=MagicMock(),
        store=MagicMock(),
        artifacts=MagicMock(),
        notifier=MagicMock(),
        verifier=MagicMock(),
    )


def test_confirmation_downgrades_if_consecutive_observations_are_too_close(tmp_path: Path) -> None:
    service = _service(tmp_path)
    execution = DetectionExecution(
        result=_open_result(fingerprint="fp1", open_strength=0.9, timestamp="2026-04-23T18:00:05Z"),
        probes=(),
    )
    runtime = _runtime(fingerprint="fp1", transition_at="2026-04-23T18:00:00Z")  # 5 seconds ago

    result = service._apply_confirmation_policy(execution, runtime).result

    assert result.state is DetectorState.OPENING_CANDIDATE
    assert result.state_reason == "awaiting_consecutive_open_confirmation"


def test_confirmation_promotes_when_gap_is_wide_enough(tmp_path: Path) -> None:
    service = _service(tmp_path)
    execution = DetectionExecution(
        result=_open_result(fingerprint="fp1", open_strength=0.9, timestamp="2026-04-23T18:02:00Z"),
        probes=(),
    )
    runtime = _runtime(fingerprint="fp1", transition_at="2026-04-23T18:00:00Z")  # 120 seconds ago

    result = service._apply_confirmation_policy(execution, runtime).result

    assert result.state is DetectorState.OPEN
    assert result.state_reason == "consecutive_open_confirmation_satisfied"


def test_high_strength_open_still_fast_paths_without_gap(tmp_path: Path) -> None:
    service = _service(tmp_path)
    execution = DetectionExecution(
        result=_open_result(fingerprint="fp1", open_strength=0.99, timestamp="2026-04-23T18:00:05Z"),
        probes=(),
    )
    runtime = _runtime(fingerprint="fp1", transition_at="2026-04-23T18:00:00Z")

    result = service._apply_confirmation_policy(execution, runtime).result

    assert result.state is DetectorState.OPEN
