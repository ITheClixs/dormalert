from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from src.config.models import AppConfig
from src.detector.engine import PageStateDetector
from src.detector.models import DetectionResult, DetectorState, ProbeResult, ProbeTarget, WorkflowState
from src.detector.profile import StudentVillageProfile
from src.diagnostics.artifacts import ArtifactManager
from src.notifier.base import NotificationDelivery, NotificationEvent
from src.orchestrator.service import DormAlertService
from src.persistence.sqlite_store import OpeningEventRecord, SQLiteStateStore
from src.utils.time import parse_utc_iso, utcnow_iso
from src.verifier.rules import RuleBasedVerifier


class SendNotifier(Protocol):
    def send(self, event: NotificationEvent) -> tuple[NotificationDelivery, ...]:
        ...


@dataclass(frozen=True)
class RecordedNotification:
    event_type: str
    site_id: str
    title: str
    deliveries: tuple[NotificationDelivery, ...]


@dataclass(frozen=True)
class OpeningSimulationResult:
    site_id: str
    simulated: bool
    sent_email_requested: bool
    workspace_dir: Path
    database_path: Path
    artifacts_dir: Path
    seed_state: str
    final_state: str
    final_state_reason: str
    final_confidence: float
    opening_email_succeeded: bool
    notification_events: tuple[RecordedNotification, ...]
    active_openings: tuple[OpeningEventRecord, ...]


class RecordingNotifier:
    def __init__(self, downstream: SendNotifier) -> None:
        self.downstream = downstream
        self.records: list[RecordedNotification] = []

    def send(self, event: NotificationEvent) -> tuple[NotificationDelivery, ...]:
        deliveries = self.downstream.send(event)
        self.records.append(
            RecordedNotification(
                event_type=event.event_type,
                site_id=event.site_id,
                title=event.title,
                deliveries=deliveries,
            )
        )
        return deliveries


class SimulatedStudentVillageProfile(StudentVillageProfile):
    display_name = "Student Village SIMULATION"


class FixtureProbeClient:
    def __init__(self, fixtures: dict[str, str]) -> None:
        self.fixtures = fixtures

    def fetch(self, target: ProbeTarget, *, timeout_seconds: int, max_retries: int) -> ProbeResult:
        return ProbeResult(
            target_name=target.name,
            requested_url=target.url,
            final_url=target.url,
            status_code=200,
            headers={
                "content-type": "text/html; charset=utf-8",
                "x-dormalert-simulation": "studentvillage-opening",
            },
            text=self.fixtures[target.name],
            duration_ms=0,
            fetched_at=utcnow_iso(),
        )


def run_studentvillage_opening_simulation(
    *,
    config: AppConfig,
    notifier: SendNotifier,
    send_email: bool,
    workspace_dir: Path | None = None,
) -> OpeningSimulationResult:
    if send_email and not config.notification.email_enabled:
        raise ValueError(
            "DORMALERT_EMAIL_ENABLED must be true before running simulate-opening --send-email."
        )

    workspace = workspace_dir or Path(tempfile.mkdtemp(prefix="dormalert-sim-"))
    simulation_config = replace(
        config,
        detector_only=True,
        database_path=workspace / "dormalert-simulation.db",
        artifacts_dir=workspace / "artifacts",
    )
    profile = SimulatedStudentVillageProfile()
    detector = PageStateDetector(FixtureProbeClient(_studentvillage_open_fixtures()))
    store = SQLiteStateStore(simulation_config.database_path)
    recording_notifier = RecordingNotifier(notifier)
    service = DormAlertService(
        config=simulation_config,
        profiles={"studentvillage": profile},
        detector=detector,
        store=store,
        artifacts=ArtifactManager(simulation_config.artifacts_dir),
        notifier=recording_notifier,
        verifier=RuleBasedVerifier(simulation_config),
    )

    seed_result = _seed_prior_opening_candidate(
        detector=detector,
        profile=profile,
        config=simulation_config,
        store=store,
    )
    final_result = service.inspect_site("studentvillage")
    active_openings = service.list_openings(active_only=True)
    opening_email_succeeded = _opening_email_succeeded(recording_notifier.records)

    return OpeningSimulationResult(
        site_id="studentvillage",
        simulated=True,
        sent_email_requested=send_email,
        workspace_dir=workspace,
        database_path=simulation_config.database_path,
        artifacts_dir=simulation_config.artifacts_dir,
        seed_state=seed_result.state.value,
        final_state=final_result.state.value,
        final_state_reason=final_result.state_reason,
        final_confidence=final_result.confidence,
        opening_email_succeeded=opening_email_succeeded,
        notification_events=tuple(recording_notifier.records),
        active_openings=active_openings,
    )


def _seed_prior_opening_candidate(
    *,
    detector: PageStateDetector,
    profile: SimulatedStudentVillageProfile,
    config: AppConfig,
    store: SQLiteStateStore,
) -> DetectionResult:
    execution = detector.detect(profile, config.sites["studentvillage"])
    if execution.result.state is not DetectorState.OPEN:
        raise RuntimeError(
            "Student Village opening simulation fixture did not classify as profile-level open."
        )

    observed_at = parse_utc_iso(execution.result.timestamp_utc)
    prior_at = utcnow_iso(
        observed_at - timedelta(seconds=config.confirmation_min_gap_seconds + 5)
    )
    seed_result = replace(
        execution.result,
        state=DetectorState.OPENING_CANDIDATE,
        confidence=min(execution.result.confidence, 0.78),
        state_reason="simulation_seed_prior_opening_candidate",
        timestamp_utc=prior_at,
        inferences=execution.result.inferences
        + (
            "Simulation seeded this prior candidate so the real confirmation policy can be exercised without waiting.",
        ),
    )
    store.record_detection(seed_result)
    store.upsert_runtime(
        result=seed_result,
        workflow_state=WorkflowState.OPENING_CANDIDATE,
        consecutive_failures=0,
        transition_at=prior_at,
    )
    return seed_result


def _opening_email_succeeded(records: list[RecordedNotification]) -> bool:
    return any(
        record.event_type == "opening_alert"
        and any(delivery.delivery_kind == "email" and delivery.succeeded for delivery in record.deliveries)
        for record in records
    )


def _studentvillage_open_fixtures() -> dict[str, str]:
    filler = "\n".join(
        f"<p>Simulation filler paragraph {index}: application information is available.</p>"
        for index in range(40)
    )
    return {
        "home": f"""
            <html><body>
              <h1>Student Village</h1>
              <p>Applications for accommodation are available.</p>
              {filler}
            </body></html>
        """,
        "apply": f"""
            <html><body>
              <h1>Register</h1>
              <form id="register_form" method="post" action="/en/apply/">
                <input type="hidden" name="form_token" value="simulation-token">
                <input type="email" name="email">
                <input type="password" name="password">
                <input type="submit" value="Register" onclick="return regformhash(this.form);">
              </form>
              {filler}
            </body></html>
        """,
        "contact": f"""
            <html><body>
              <h1>Contact Student Village</h1>
              <p>Contact us for support with your application.</p>
              {filler}
            </body></html>
        """,
    }
