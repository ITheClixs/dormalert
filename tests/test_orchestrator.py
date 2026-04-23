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


class StubNotifier:
    def __init__(self) -> None:
        self.events = []

    def send(self, event):
        self.events.append(event)
        return (
            NotificationDelivery(
                notifier="StubNotifier",
                delivery_kind="console",
                succeeded=True,
            ),
        )


class StubProfile:
    site_id = "studentvillage"
    display_name = "Student Village"
    targets = ()


def make_config(
    base_dir: Path,
    *,
    detector_only: bool = True,
    reminder_minutes: int = 15,
) -> AppConfig:
    return AppConfig(
        database_path=base_dir / "state.db",
        artifacts_dir=base_dir / "artifacts",
        log_dir=base_dir / "logs",
        log_level="INFO",
        user_agent="DormAlertTest/0.1",
        detector_only=detector_only,
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
            alert_reminder_minutes=reminder_minutes,
        ),
        browser=BrowserSettings(headless=True, slow_mo_ms=0),
        failure_alert_threshold=3,
        closed_artifact_retention_days=7,
        sites={
            "studentvillage": SiteMonitorConfig(
                site_id="studentvillage",
                enabled=True,
                poll_interval_seconds=180,
                jitter_seconds=20,
                timeout_seconds=20,
                max_retries=2,
                submission_mode=SubmissionMode.DRY_RUN,
            )
        },
        studentvillage_applicant=None,
        studentvillage_success_phrases=(),
        studentvillage_failure_phrases=(),
    )


def make_execution(
    *,
    state: DetectorState = DetectorState.OPEN,
    fingerprint: str = "fingerprint-1",
) -> DetectionExecution:
    result = DetectionResult(
        site_id="studentvillage",
        display_name="Student Village",
        state=state,
        confidence=0.95,
        state_reason="test_state",
        signal_scores={
            "closed_marker_strength": 0.0,
            "open_marker_strength": 1.0 if state is DetectorState.OPEN else 0.0,
            "drift_risk": 0.0,
        },
        state_version="test.v1",
        signals=("closed_banners_removed",),
        facts=("Observed open state.",),
        inferences=("The site appears open.",),
        uncertainties=(),
        anti_bot=AntiBotObservation(AntiBotSeverity.NONE),
        page_urls=("https://studentvillage.ch/en/apply/",),
        timestamp_utc=utcnow_iso(),
        fingerprint=fingerprint,
    )
    return DetectionExecution(result=result, probes=())


def test_manual_open_notifications_are_deduped(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    notifier = StubNotifier()
    store = SQLiteStateStore(config.database_path)
    service = DormAlertService(
        config=config,
        profiles={"studentvillage": StubProfile()},
        detector=StubDetector(make_execution()),
        store=store,
        artifacts=ArtifactManager(config.artifacts_dir),
        notifier=notifier,
        verifier=RuleBasedVerifier(config),
    )

    service.inspect_site("studentvillage")
    service.inspect_site("studentvillage")

    event_types = [event.event_type for event in notifier.events]
    assert event_types.count("opening_alert") == 1
    assert event_types.count("manual_action_required") == 1


def test_opening_event_reminders_and_acknowledgement(tmp_path: Path) -> None:
    config = make_config(tmp_path, detector_only=True, reminder_minutes=0)
    notifier = StubNotifier()
    store = SQLiteStateStore(config.database_path)
    service = DormAlertService(
        config=config,
        profiles={"studentvillage": StubProfile()},
        detector=StubDetector(make_execution()),
        store=store,
        artifacts=ArtifactManager(config.artifacts_dir),
        notifier=notifier,
        verifier=RuleBasedVerifier(config),
    )

    service.inspect_site("studentvillage")
    openings = service.list_openings(active_only=True)
    assert len(openings) == 1
    event_id = openings[0].event_id

    service.inspect_site("studentvillage")
    assert service.acknowledge_opening(event_id) is True
    service.inspect_site("studentvillage")

    event_types = [event.event_type for event in notifier.events]
    assert event_types.count("opening_alert") == 1
    assert event_types.count("opening_reminder") == 1


def test_closed_state_closes_active_opening(tmp_path: Path) -> None:
    config = make_config(tmp_path, detector_only=True, reminder_minutes=15)
    notifier = StubNotifier()
    store = SQLiteStateStore(config.database_path)
    detector = StubDetector(make_execution())
    service = DormAlertService(
        config=config,
        profiles={"studentvillage": StubProfile()},
        detector=detector,
        store=store,
        artifacts=ArtifactManager(config.artifacts_dir),
        notifier=notifier,
        verifier=RuleBasedVerifier(config),
    )

    service.inspect_site("studentvillage")
    detector.execution = make_execution(state=DetectorState.CLOSED, fingerprint="fingerprint-closed")
    service.inspect_site("studentvillage")

    assert service.list_openings(active_only=True) == ()
