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
from src.detector.profile import WATCHED_CLOSED_TEXT_MISSING_SIGNAL
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


class CandidateAlertProfile:
    site_id = "livingscience"
    display_name = "Living Science"
    targets = ()
    candidate_open_alerts = True


class NoCandidateAlertProfile:
    site_id = "livingscience"
    display_name = "Living Science"
    targets = ()
    candidate_open_alerts = False


def make_config(base_dir: Path, *, reminder_minutes: int = 0) -> AppConfig:
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
            alert_reminder_minutes=reminder_minutes,
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


def make_candidate_execution(
    *,
    state: DetectorState = DetectorState.OPENING_CANDIDATE,
    fingerprint: str = "candidate-fp-1",
    signals: tuple[str, ...] | None = None,
) -> DetectionExecution:
    if signals is None:
        signals = (WATCHED_CLOSED_TEXT_MISSING_SIGNAL, "closed_phrase_absent")
    result = DetectionResult(
        site_id="livingscience",
        display_name="Living Science",
        state=state,
        confidence=0.6,
        state_reason="closed_phrase_absent_pending_operator_verification",
        signal_scores={
            "closed_marker_strength": 0.0,
            "open_marker_strength": 0.35,
            "drift_risk": 0.65,
        },
        state_version="test.v1",
        signals=signals,
        facts=("The monitored LivingScience English waitlist text is absent or changed.",),
        inferences=(),
        uncertainties=(),
        anti_bot=AntiBotObservation(AntiBotSeverity.NONE),
        page_urls=("https://livingscience.ch/wohnen-studieren-zuerich/?L=1",),
        timestamp_utc=utcnow_iso(),
        fingerprint=fingerprint,
        metadata={
            "watched_closed_text": "Our waiting lists for rooms and studios are currently full.",
            "watched_closed_text_status": "missing",
        },
    )
    return DetectionExecution(result=result, probes=())


def make_service(config: AppConfig, profile, detector, notifier) -> DormAlertService:
    return DormAlertService(
        config=config,
        profiles={"livingscience": profile},
        detector=detector,
        store=SQLiteStateStore(config.database_path),
        artifacts=ArtifactManager(config.artifacts_dir),
        notifier=notifier,
        verifier=RuleBasedVerifier(config),
    )


def test_candidate_opening_creates_event_and_reminds(tmp_path: Path) -> None:
    config = make_config(tmp_path, reminder_minutes=0)
    notifier = StubEmailNotifier()
    detector = StubDetector(make_candidate_execution())
    service = make_service(config, CandidateAlertProfile(), detector, notifier)

    service.inspect_site("livingscience")
    openings = service.list_openings(active_only=True)
    assert len(openings) == 1

    service.inspect_site("livingscience")

    event_types = [event.event_type for event in notifier.events]
    assert event_types.count("opening_alert") == 1
    assert event_types.count("opening_reminder") == 1


def test_candidate_opening_alert_says_unverified(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    notifier = StubEmailNotifier()
    detector = StubDetector(make_candidate_execution())
    service = make_service(config, CandidateAlertProfile(), detector, notifier)

    service.inspect_site("livingscience")

    alerts = [event for event in notifier.events if event.event_type == "opening_alert"]
    assert len(alerts) == 1
    assert "may be open" in alerts[0].title
    assert "register" in alerts[0].message.lower()


def test_candidate_opening_requires_profile_opt_in(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    notifier = StubEmailNotifier()
    detector = StubDetector(make_candidate_execution())
    service = make_service(config, NoCandidateAlertProfile(), detector, notifier)

    service.inspect_site("livingscience")

    assert service.list_openings(active_only=True) == ()
    assert not [event for event in notifier.events if event.event_type == "opening_alert"]


def test_candidate_opening_requires_watched_text_missing_signal(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    notifier = StubEmailNotifier()
    detector = StubDetector(make_candidate_execution(signals=("closed_phrase_absent",)))
    service = make_service(config, CandidateAlertProfile(), detector, notifier)

    service.inspect_site("livingscience")

    assert service.list_openings(active_only=True) == ()


def test_candidate_opening_closes_when_site_closes_again(tmp_path: Path) -> None:
    config = make_config(tmp_path, reminder_minutes=15)
    notifier = StubEmailNotifier()
    detector = StubDetector(make_candidate_execution())
    service = make_service(config, CandidateAlertProfile(), detector, notifier)

    service.inspect_site("livingscience")
    assert len(service.list_openings(active_only=True)) == 1

    detector.execution = make_candidate_execution(
        state=DetectorState.CLOSED,
        fingerprint="closed-again-fp",
        signals=("watched_closed_text_present", "closed_phrase_present"),
    )
    service.inspect_site("livingscience")

    assert service.list_openings(active_only=True) == ()


def test_candidate_opening_alert_retries_until_email_succeeds(tmp_path: Path) -> None:
    config = make_config(tmp_path, reminder_minutes=15)
    notifier = StubEmailNotifier()
    notifier.email_succeeds = False
    detector = StubDetector(make_candidate_execution())
    service = make_service(config, CandidateAlertProfile(), detector, notifier)

    service.inspect_site("livingscience")
    openings = service.list_openings(active_only=True)
    assert len(openings) == 1
    assert openings[0].last_notified_at is None

    notifier.email_succeeds = True
    service.inspect_site("livingscience")

    openings = service.list_openings(active_only=True)
    assert openings[0].last_notified_at is not None
    initial_alerts = [event for event in notifier.events if event.event_type == "opening_alert"]
    assert len(initial_alerts) == 2
