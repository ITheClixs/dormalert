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

    def send(self, event) -> None:
        self.events.append(event)


class StubProfile:
    site_id = "studentvillage"
    display_name = "Student Village"
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
        ),
        browser=BrowserSettings(headless=True, slow_mo_ms=0),
        failure_alert_threshold=3,
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


def make_execution() -> DetectionExecution:
    result = DetectionResult(
        site_id="studentvillage",
        display_name="Student Village",
        state=DetectorState.OPEN,
        confidence=0.95,
        signals=("closed_banners_removed",),
        facts=("Observed open state.",),
        inferences=("The site appears open.",),
        uncertainties=(),
        anti_bot=AntiBotObservation(AntiBotSeverity.NONE),
        page_urls=("https://studentvillage.ch/en/apply/",),
        timestamp_utc=utcnow_iso(),
        fingerprint="fingerprint-1",
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
    assert event_types.count("availability_change") == 1
    assert event_types.count("manual_action_required") == 1
