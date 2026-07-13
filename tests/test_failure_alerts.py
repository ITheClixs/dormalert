from __future__ import annotations

from pathlib import Path

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
from src.diagnostics.artifacts import ArtifactManager
from src.notifier.base import NotificationDelivery
from src.orchestrator.service import DormAlertService
from src.persistence.sqlite_store import SQLiteStateStore
from src.utils.time import utcnow_iso
from src.verifier.rules import RuleBasedVerifier


class StubDetector:
    def __init__(self, execution: DetectionExecution) -> None:
        self.execution = execution

    def detect(self, profile, config) -> DetectionExecution:
        return self.execution


class StubEmailNotifier:
    def __init__(self) -> None:
        self.events = []
        self.email_succeeds = True

    def send(self, event):
        self.events.append(event)
        return (
            NotificationDelivery(
                notifier="StubEmailNotifier",
                delivery_kind="email",
                succeeded=self.email_succeeds,
                error=None if self.email_succeeds else "smtp down",
            ),
        )


class StubProfile:
    site_id = "livingscience"
    display_name = "Living Science"
    targets = ()


def make_config(base_dir: Path) -> AppConfig:
    return AppConfig(
        database_path=base_dir / "state.db",
        artifacts_dir=base_dir / "artifacts",
        log_dir=base_dir / "logs",
        log_level="INFO",
        user_agent="DormAlertTest/0.1",
        detector_only=True,
        notification=NotificationSettings(
            enable_console=False,
            webhook_url=None,
            webhook_timeout_seconds=10,
            email_enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="alerts@example.com",
            smtp_password="secret",
            smtp_starttls=True,
            email_from="alerts@example.com",
            email_to=("demirguven178@gmail.com",),
            alert_reminder_minutes=15,
        ),
        browser=BrowserSettings(headless=True, slow_mo_ms=0),
        failure_alert_threshold=3,
        closed_artifact_retention_days=7,
        sites={
            "livingscience": SiteMonitorConfig(
                site_id="livingscience",
                enabled=True,
                poll_interval_seconds=300,
                jitter_seconds=20,
                timeout_seconds=20,
                max_retries=2,
                submission_mode=SubmissionMode.DISABLED,
            )
        },
        studentvillage_applicant=None,
        studentvillage_success_phrases=(),
        studentvillage_failure_phrases=(),
    )


def make_execution(*, state: DetectorState, fingerprint: str) -> DetectionExecution:
    result = DetectionResult(
        site_id="livingscience",
        display_name="Living Science",
        state=state,
        confidence=0.0 if state is DetectorState.FAILED else 0.99,
        state_reason="probe_failure" if state is DetectorState.FAILED else "known_closed_phrase_present",
        signal_scores={
            "closed_marker_strength": 0.0,
            "open_marker_strength": 0.0,
            "drift_risk": 1.0,
        },
        state_version="test.v1",
        signals=("probe_failure",) if state is DetectorState.FAILED else ("closed_phrase_present",),
        facts=(),
        inferences=(),
        uncertainties=(),
        anti_bot=AntiBotObservation(AntiBotSeverity.NONE),
        page_urls=("https://livingscience.ch/wohnen-studieren-zuerich/?L=1",),
        timestamp_utc=utcnow_iso(),
        fingerprint=fingerprint,
        metadata={},
    )
    return DetectionExecution(result=result, probes=())


def make_service(config: AppConfig, detector, notifier) -> DormAlertService:
    return DormAlertService(
        config=config,
        profiles={"livingscience": StubProfile()},
        detector=detector,
        store=SQLiteStateStore(config.database_path),
        artifacts=ArtifactManager(config.artifacts_dir),
        notifier=notifier,
        verifier=RuleBasedVerifier(config),
    )


def _run_failures(service: DormAlertService, detector: StubDetector, count: int) -> None:
    for index in range(count):
        detector.execution = make_execution(
            state=DetectorState.FAILED, fingerprint=f"failure-fp-{index}"
        )
        service.inspect_site("livingscience")


def test_each_failure_episode_alerts_again(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    notifier = StubEmailNotifier()
    detector = StubDetector(make_execution(state=DetectorState.FAILED, fingerprint="fp"))
    service = make_service(config, detector, notifier)

    _run_failures(service, detector, 3)
    detector.execution = make_execution(state=DetectorState.CLOSED, fingerprint="recovered-fp")
    service.inspect_site("livingscience")
    _run_failures(service, detector, 3)

    failure_alerts = [event for event in notifier.events if event.event_type == "repeated_failure"]
    assert len(failure_alerts) == 2


def test_continuing_failure_does_not_spam_same_day(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    notifier = StubEmailNotifier()
    detector = StubDetector(make_execution(state=DetectorState.FAILED, fingerprint="fp"))
    service = make_service(config, detector, notifier)

    _run_failures(service, detector, 6)

    failure_alerts = [event for event in notifier.events if event.event_type == "repeated_failure"]
    assert len(failure_alerts) == 1


def test_failure_alert_retries_when_email_delivery_fails(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    notifier = StubEmailNotifier()
    notifier.email_succeeds = False
    detector = StubDetector(make_execution(state=DetectorState.FAILED, fingerprint="fp"))
    service = make_service(config, detector, notifier)

    _run_failures(service, detector, 3)
    notifier.email_succeeds = True
    _run_failures(service, detector, 1)

    failure_alerts = [event for event in notifier.events if event.event_type == "repeated_failure"]
    assert len(failure_alerts) == 2
