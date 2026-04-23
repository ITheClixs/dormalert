from __future__ import annotations

import hashlib
import logging
from dataclasses import replace

from src.config.models import AppConfig, SubmissionMode
from src.detector.engine import PageStateDetector
from src.detector.models import DetectionExecution, DetectionResult, DetectorState, WorkflowState
from src.detector.profile import SiteProfile
from src.diagnostics.artifacts import ArtifactManager
from src.notifier.base import NotificationEvent, NotificationSeverity
from src.persistence.sqlite_store import SQLiteStateStore
from src.submitter.base import SubmissionResult, SubmissionStatus
from src.submitter.registry import build_submitter
from src.utils.time import utcnow_iso
from src.verifier.base import VerificationStatus
from src.verifier.rules import RuleBasedVerifier


class DormAlertService:
    def __init__(
        self,
        *,
        config: AppConfig,
        profiles: dict[str, SiteProfile],
        detector: PageStateDetector,
        store: SQLiteStateStore,
        artifacts: ArtifactManager,
        notifier,
        verifier: RuleBasedVerifier,
    ) -> None:
        self.config = config
        self.profiles = profiles
        self.detector = detector
        self.store = store
        self.artifacts = artifacts
        self.notifier = notifier
        self.verifier = verifier
        self.logger = logging.getLogger("dormalert.orchestrator")

    def inspect_site(self, site_id: str) -> DetectionResult:
        profile = self.profiles[site_id]
        site_config = self.config.sites[site_id]
        execution = self.detector.detect(profile, site_config)
        runtime = self.store.get_runtime(site_id)

        transition = (
            runtime is None
            or runtime.last_page_state != execution.result.state.value
            or runtime.last_fingerprint != execution.result.fingerprint
        )
        consecutive_failures = (
            (runtime.consecutive_failures if runtime else 0) + 1
            if execution.result.state is DetectorState.FAILED
            else 0
        )

        if transition or execution.result.state in {
            DetectorState.OPENING_CANDIDATE,
            DetectorState.OPEN,
            DetectorState.FAILED,
        } or execution.result.anti_bot.severity.value in {"warning", "blocking"}:
            reason = "transition" if transition else execution.result.state.value
            evidence_paths = self.artifacts.capture_detection(execution, reason)
            execution = DetectionExecution(
                result=replace(execution.result, evidence_paths=evidence_paths),
                probes=execution.probes,
            )

        workflow_state = self._derive_workflow_state(
            runtime_state=runtime.last_workflow_state if runtime else None,
            detector_result=execution.result,
            runtime_fingerprint=runtime.last_fingerprint if runtime else None,
        )
        transition_at = execution.result.timestamp_utc if transition else (runtime.last_transition_at if runtime else None)

        self.store.record_detection(execution.result)
        self.store.upsert_runtime(
            result=execution.result,
            workflow_state=workflow_state,
            consecutive_failures=consecutive_failures,
            transition_at=transition_at,
        )

        self.logger.info(
            "Detection cycle complete",
            extra={
                "event": "detection_cycle_complete",
                "site_id": site_id,
                "state": execution.result.state.value,
                "confidence": execution.result.confidence,
                "transition": transition,
                "consecutive_failures": consecutive_failures,
            },
        )

        self._handle_detection_notifications(execution.result, transition, runtime, consecutive_failures)

        if execution.result.state is DetectorState.OPEN:
            self._handle_submission(execution)

        return execution.result

    def submit_site_once(self, site_id: str, mode: SubmissionMode) -> SubmissionResult:
        profile = self.profiles[site_id]
        execution = self.detector.detect(profile, self.config.sites[site_id])
        return self._execute_submission(execution, mode=mode)

    def _handle_detection_notifications(self, result: DetectionResult, transition: bool, runtime, consecutive_failures: int) -> None:
        if transition and result.state in {DetectorState.OPENING_CANDIDATE, DetectorState.OPEN}:
            severity = (
                NotificationSeverity.CRITICAL
                if result.state is DetectorState.OPEN
                else NotificationSeverity.WARNING
            )
            self._notify(
                NotificationEvent(
                    event_type="availability_change",
                    site_id=result.site_id,
                    title=f"{result.display_name} is {result.state.value}",
                    message=(
                        f"{result.display_name} changed to {result.state.value} "
                        f"with confidence {result.confidence:.2f}."
                    ),
                    severity=severity,
                    payload={
                        "facts": result.facts,
                        "inferences": result.inferences,
                        "anti_bot": result.anti_bot.signals,
                        "evidence_paths": result.evidence_paths,
                    },
                )
            )

        previous_failures = runtime.consecutive_failures if runtime else 0
        if (
            result.state is DetectorState.FAILED
            and consecutive_failures >= self.config.failure_alert_threshold
            and previous_failures < self.config.failure_alert_threshold
        ):
            self._notify(
                NotificationEvent(
                    event_type="repeated_failure",
                    site_id=result.site_id,
                    title=f"{result.display_name} detector is failing repeatedly",
                    message=(
                        f"{result.display_name} reached {consecutive_failures} consecutive failed detection cycles."
                    ),
                    severity=NotificationSeverity.ERROR,
                    payload={
                        "facts": result.facts,
                        "uncertainties": result.uncertainties,
                        "evidence_paths": result.evidence_paths,
                    },
                )
            )

    def _handle_submission(self, execution: DetectionExecution) -> None:
        site_id = execution.result.site_id
        site_config = self.config.sites[site_id]

        if self.config.detector_only or site_config.submission_mode is SubmissionMode.DISABLED:
            reason = "detector_only" if self.config.detector_only else "submission_disabled"
            self._notify(
                NotificationEvent(
                    event_type="manual_action_required",
                    site_id=site_id,
                    title=f"{execution.result.display_name} appears open",
                    message=(
                        f"{execution.result.display_name} appears open but automatic submission is not active ({reason})."
                    ),
                    severity=NotificationSeverity.CRITICAL,
                    payload={"evidence_paths": execution.result.evidence_paths},
                )
            )
            return

        submit_key = f"submit:{site_id}:{execution.result.fingerprint}"
        if self.store.action_exists(submit_key):
            self.logger.info(
                "Skipping duplicate submission",
                extra={
                    "event": "submission_deduped",
                    "site_id": site_id,
                    "fingerprint": execution.result.fingerprint,
                },
            )
            return

        submission = self._execute_submission(execution, mode=site_config.submission_mode)
        self.store.record_submission_attempt(submission)
        if submission.attempted or submission.status in {SubmissionStatus.BLOCKED, SubmissionStatus.FAILED}:
            self.store.remember_action(
                action_key=submit_key,
                site_id=site_id,
                action_type="submit",
                details={"status": submission.status.value, "mode": submission.mode},
            )

        workflow_state = self._workflow_state_from_submission(submission)
        self.store.update_workflow_state(site_id, workflow_state)
        self._notify_submission(submission)

        verification = self.verifier.verify(site_id, submission)
        if verification.status is VerificationStatus.CONFIRMED:
            self.store.update_workflow_state(site_id, WorkflowState.VERIFIED)
        elif verification.status is VerificationStatus.FAILED:
            self.store.update_workflow_state(site_id, WorkflowState.FAILED)
        elif verification.status is VerificationStatus.AMBIGUOUS:
            self.store.update_workflow_state(site_id, WorkflowState.SUBMITTED)

        if verification.status is not VerificationStatus.NOT_APPLICABLE:
            self._notify(
                NotificationEvent(
                    event_type="verification_result",
                    site_id=site_id,
                    title=f"{site_id} verification is {verification.status.value}",
                    message=verification.message,
                    severity=(
                        NotificationSeverity.INFO
                        if verification.status is VerificationStatus.CONFIRMED
                        else NotificationSeverity.WARNING
                        if verification.status is VerificationStatus.AMBIGUOUS
                        else NotificationSeverity.ERROR
                    ),
                    payload={
                        "facts": verification.facts,
                        "inferences": verification.inferences,
                    },
                )
            )

    def _execute_submission(self, execution: DetectionExecution, mode: SubmissionMode) -> SubmissionResult:
        if execution.result.anti_bot.severity.value == "blocking":
            return SubmissionResult(
                site_id=execution.result.site_id,
                status=SubmissionStatus.BLOCKED,
                mode=mode.value,
                attempted=False,
                started_at=utcnow_iso(),
                finished_at=utcnow_iso(),
                message="Blocking anti-bot markers are present. Submission was not attempted.",
                fingerprint=execution.result.fingerprint,
            )

        try:
            submitter = build_submitter(execution.result.site_id, mode, self.artifacts)
        except ValueError as exc:
            return SubmissionResult(
                site_id=execution.result.site_id,
                status=SubmissionStatus.FAILED,
                mode=mode.value,
                attempted=False,
                started_at=utcnow_iso(),
                finished_at=utcnow_iso(),
                message=str(exc),
                fingerprint=execution.result.fingerprint,
            )

        return submitter.submit(execution, self.config)

    def _workflow_state_from_submission(self, submission: SubmissionResult) -> WorkflowState:
        if submission.status is SubmissionStatus.DRY_RUN:
            return WorkflowState.OPEN
        if submission.status in {SubmissionStatus.AMBIGUOUS, SubmissionStatus.SUCCEEDED}:
            return WorkflowState.SUBMITTED
        if submission.status in {SubmissionStatus.FAILED, SubmissionStatus.BLOCKED}:
            return WorkflowState.FAILED
        return WorkflowState.OPEN

    def _derive_workflow_state(
        self,
        *,
        runtime_state: str | None,
        detector_result: DetectionResult,
        runtime_fingerprint: str | None,
    ) -> WorkflowState:
        if detector_result.state is DetectorState.FAILED:
            return WorkflowState.FAILED
        if runtime_state in {WorkflowState.SUBMITTED.value, WorkflowState.VERIFIED.value} and runtime_fingerprint == detector_result.fingerprint:
            return WorkflowState(runtime_state)
        if detector_result.state is DetectorState.OPEN:
            return WorkflowState.OPEN
        if detector_result.state is DetectorState.OPENING_CANDIDATE:
            return WorkflowState.OPENING_CANDIDATE
        return WorkflowState.CLOSED

    def _notify_submission(self, submission: SubmissionResult) -> None:
        severity = (
            NotificationSeverity.ERROR
            if submission.status in {SubmissionStatus.FAILED, SubmissionStatus.BLOCKED}
            else NotificationSeverity.WARNING
            if submission.status is SubmissionStatus.AMBIGUOUS
            else NotificationSeverity.INFO
        )
        self._notify(
            NotificationEvent(
                event_type="submission_result",
                site_id=submission.site_id,
                title=f"{submission.site_id} submission is {submission.status.value}",
                message=submission.message,
                severity=severity,
                payload={
                    "facts": submission.facts,
                    "inferences": submission.inferences,
                    "evidence_paths": submission.evidence_paths,
                },
            )
        )

    def _notify(self, event: NotificationEvent) -> None:
        digest = hashlib.sha256(
            f"{event.event_type}|{event.site_id}|{event.title}|{event.message}".encode("utf-8")
        ).hexdigest()
        dedupe_key = f"notify:{event.event_type}:{event.site_id}:{digest}"
        if self.store.action_exists(dedupe_key):
            return
        self.notifier.send(event)
        self.store.remember_action(
            action_key=dedupe_key,
            site_id=event.site_id,
            action_type="notify",
            details={"event_type": event.event_type, "title": event.title},
        )
