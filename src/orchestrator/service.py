from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from dataclasses import replace

from src.config.models import AppConfig, SubmissionMode
from src.detector.engine import PageStateDetector
from src.detector.models import DetectionExecution, DetectionResult, DetectorState, WorkflowState
from src.detector.profile import SiteProfile
from src.diagnostics.artifacts import ArtifactManager
from src.notifier.base import NotificationDelivery, NotificationEvent, NotificationSeverity
from src.persistence.sqlite_store import OpeningEventRecord, SQLiteStateStore
from src.submitter.base import SubmissionResult, SubmissionStatus
from src.submitter.registry import build_submitter
from src.utils.time import add_minutes, parse_utc_iso, utcnow_iso
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
        runtime = self.store.get_runtime(site_id)

        execution = self.detector.detect(profile, site_config)
        execution = self._apply_confirmation_policy(execution, runtime)

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
                "state_reason": execution.result.state_reason,
                "confidence": execution.result.confidence,
                "transition": transition,
                "consecutive_failures": consecutive_failures,
            },
        )

        self._handle_detection_notifications(execution.result, transition, runtime, consecutive_failures)
        self._reconcile_opening_event(execution)

        if execution.result.state is DetectorState.OPEN:
            self._handle_submission(execution)

        return execution.result

    def submit_site_once(self, site_id: str, mode: SubmissionMode) -> SubmissionResult:
        profile = self.profiles[site_id]
        execution = self.detector.detect(profile, self.config.sites[site_id])
        execution = self._apply_confirmation_policy(execution, self.store.get_runtime(site_id))
        return self._execute_submission(execution, mode=mode)

    def list_openings(self, active_only: bool = False) -> tuple[OpeningEventRecord, ...]:
        return self.store.list_opening_events(active_only=active_only)

    def acknowledge_opening(self, event_id: int) -> bool:
        return self.store.acknowledge_opening(event_id)

    def status_snapshot(self) -> dict[str, object]:
        runtimes = self.store.list_runtime_records()
        openings = self.store.list_opening_events(active_only=True)
        return {
            "sites": [
                {
                    "site_id": runtime.site_id,
                    "display_name": runtime.display_name,
                    "last_page_state": runtime.last_page_state,
                    "last_workflow_state": runtime.last_workflow_state,
                    "last_confidence": runtime.last_confidence,
                    "last_checked_at": runtime.last_checked_at,
                    "consecutive_failures": runtime.consecutive_failures,
                }
                for runtime in runtimes
            ],
            "active_openings": [
                {
                    "event_id": opening.event_id,
                    "site_id": opening.site_id,
                    "first_opened_at": opening.first_opened_at,
                    "last_seen_open_at": opening.last_seen_open_at,
                    "last_notified_at": opening.last_notified_at,
                    "next_reminder_at": opening.next_reminder_at,
                    "reminder_count": opening.reminder_count,
                    "status": opening.status,
                }
                for opening in openings
            ],
        }

    def log_heartbeat(self, processed_sites: list[str]) -> None:
        active_openings = self.store.list_opening_events(active_only=True)
        runtimes = self.store.list_runtime_records()
        self.logger.info(
            "Heartbeat",
            extra={
                "event": "heartbeat",
                "processed_sites": processed_sites,
                "active_opening_count": len(active_openings),
                "site_count": len(runtimes),
                "failure_counts": {
                    runtime.site_id: runtime.consecutive_failures
                    for runtime in runtimes
                },
            },
        )

    def log_scheduler_wait(self, next_run: dict[str, datetime]) -> None:
        now = parse_utc_iso(utcnow_iso())
        next_checks_seconds = {
            site_id: max(0, int((due_at - now).total_seconds()))
            for site_id, due_at in sorted(next_run.items())
        }
        runtimes = self.store.list_runtime_records()
        site_states = {runtime.site_id: runtime.last_page_state for runtime in runtimes}
        active_open_sites = sorted(
            site_id for site_id, state in site_states.items() if state == "open"
        )
        opening_candidate_sites = sorted(
            site_id for site_id, state in site_states.items() if state == "opening_candidate"
        )

        if active_open_sites:
            availability = f"OPEN:{','.join(active_open_sites)}"
        elif opening_candidate_sites:
            availability = f"OPENING_CANDIDATE:{','.join(opening_candidate_sites)}"
        else:
            availability = "NO_OPENINGS"

        self.logger.info(
            "Monitor is running; waiting for next scheduled checks",
            extra={
                "event": "scheduler_wait",
                "next_checks_seconds": next_checks_seconds,
                "site_states": site_states,
                "availability": availability,
            },
        )

    def prune_old_artifacts(self) -> int:
        removed = self.artifacts.prune_closed_detection_artifacts(
            self.config.closed_artifact_retention_days
        )
        if removed:
            self.logger.info(
                "Pruned old closed-state artifacts",
                extra={"event": "artifact_prune", "removed_count": removed},
            )
        return removed

    def _apply_confirmation_policy(
        self,
        execution: DetectionExecution,
        runtime,
    ) -> DetectionExecution:
        result = execution.result
        if result.state is not DetectorState.OPEN:
            return execution

        open_strength = result.signal_scores.get("open_marker_strength", 0.0)
        if open_strength >= 0.95:
            return execution

        same_fingerprint = runtime is not None and runtime.last_fingerprint == result.fingerprint
        prior_positive = runtime is not None and runtime.last_page_state in {
            DetectorState.OPEN.value,
            DetectorState.OPENING_CANDIDATE.value,
        }

        if same_fingerprint and prior_positive:
            confirmed = replace(
                result,
                state=DetectorState.OPEN,
                confidence=max(result.confidence, 0.94),
                state_reason="consecutive_open_confirmation_satisfied",
                inferences=result.inferences
                + ("A second consecutive matching positive detection confirmed the open state.",),
            )
            return DetectionExecution(result=confirmed, probes=execution.probes)

        downgraded = replace(
            result,
            state=DetectorState.OPENING_CANDIDATE,
            confidence=min(result.confidence, 0.78),
            state_reason="awaiting_consecutive_open_confirmation",
            signal_scores={**result.signal_scores, "confirmation_strength": 0.5},
            inferences=result.inferences
            + ("A second consecutive matching positive detection is required before declaring open.",),
        )
        return DetectionExecution(result=downgraded, probes=execution.probes)

    def _handle_detection_notifications(self, result: DetectionResult, transition: bool, runtime, consecutive_failures: int) -> None:
        if transition and result.state is DetectorState.OPENING_CANDIDATE:
            self._notify_once(
                NotificationEvent(
                    event_type="availability_change",
                    site_id=result.site_id,
                    title=f"{result.display_name} is opening_candidate",
                    message=(
                        f"{result.display_name} changed to opening_candidate "
                        f"with confidence {result.confidence:.2f}."
                    ),
                    severity=NotificationSeverity.WARNING,
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
            self._notify_once(
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

    def _reconcile_opening_event(self, execution: DetectionExecution) -> None:
        site_id = execution.result.site_id
        current_opening = self.store.get_current_opening(site_id)

        if execution.result.state is DetectorState.OPEN:
            if current_opening is None:
                current_opening = self.store.create_opening_event(execution.result)
            elif current_opening.opening_fingerprint != execution.result.fingerprint:
                self.store.close_opening_event(current_opening.event_id)
                current_opening = self.store.create_opening_event(execution.result)
            else:
                current_opening = self.store.refresh_opening_event(current_opening.event_id, execution.result)

            if current_opening and current_opening.status == "active":
                self._notify_opening_event(current_opening, execution.result)
            return

        if execution.result.state is DetectorState.CLOSED and current_opening is not None:
            self.store.close_opening_event(current_opening.event_id)

    def _notify_opening_event(self, opening: OpeningEventRecord, result: DetectionResult) -> None:
        should_send = opening.last_notified_at is None
        if not should_send and opening.next_reminder_at is not None:
            should_send = parse_utc_iso(opening.next_reminder_at) <= parse_utc_iso(result.timestamp_utc)

        if not should_send:
            return

        deliveries = self._send_notification(self._opening_notification(opening, result))
        if not self._opening_delivery_succeeded(deliveries):
            self.logger.warning(
                "Opening notification did not reach the required delivery channel",
                extra={
                    "event": "opening_notification_failed",
                    "site_id": result.site_id,
                    "event_id": opening.event_id,
                },
            )
            return

        next_reminder_at = add_minutes(
            result.timestamp_utc,
            self.config.notification.alert_reminder_minutes,
        )
        self.store.mark_opening_notified(
            event_id=opening.event_id,
            last_notified_at=result.timestamp_utc,
            next_reminder_at=next_reminder_at,
            reminder_count=opening.reminder_count + 1,
        )

    def _opening_notification(self, opening: OpeningEventRecord, result: DetectionResult) -> NotificationEvent:
        is_initial = opening.last_notified_at is None
        if is_initial:
            event_type = "opening_alert"
            title = f"[DormAlert][OPEN] {result.display_name} appears open"
            message = (
                f"{result.display_name} appears open with confidence {result.confidence:.2f}. "
                f"Event #{opening.event_id} was created."
            )
        else:
            reminder_number = max(1, opening.reminder_count)
            event_type = "opening_reminder"
            title = f"[DormAlert][REMINDER {reminder_number}] {result.display_name} still appears open"
            message = (
                f"{result.display_name} still appears open with confidence {result.confidence:.2f}. "
                f"Event #{opening.event_id} remains active."
            )

        return NotificationEvent(
            event_type=event_type,
            site_id=result.site_id,
            title=title,
            message=message,
            severity=NotificationSeverity.CRITICAL,
            payload={
                "event_id": opening.event_id,
                "confidence": result.confidence,
                "facts": result.facts,
                "anti_bot": result.anti_bot.signals,
                "page_urls": result.page_urls,
                "evidence_paths": result.evidence_paths,
            },
        )

    def _opening_delivery_succeeded(self, deliveries: tuple[NotificationDelivery, ...]) -> bool:
        if self.config.notification.email_enabled:
            return any(
                delivery.delivery_kind == "email" and delivery.succeeded
                for delivery in deliveries
            )
        return any(delivery.succeeded for delivery in deliveries)

    def _handle_submission(self, execution: DetectionExecution) -> None:
        site_id = execution.result.site_id
        site_config = self.config.sites[site_id]

        if self.config.detector_only or site_config.submission_mode is SubmissionMode.DISABLED:
            reason = "detector_only" if self.config.detector_only else "submission_disabled"
            self._notify_once(
                NotificationEvent(
                    event_type="manual_action_required",
                    site_id=site_id,
                    title=f"{execution.result.display_name} appears open",
                    message=(
                        f"{execution.result.display_name} appears open but automatic submission is not active ({reason})."
                    ),
                    severity=NotificationSeverity.CRITICAL,
                    payload={"evidence_paths": execution.result.evidence_paths},
                ),
                dedupe_key=f"manual_action_required:{site_id}:{execution.result.fingerprint}",
            )
            return

        if site_config.submission_mode is SubmissionMode.LIVE and execution.result.anti_bot.severity.value == "warning":
            self._notify_once(
                NotificationEvent(
                    event_type="manual_action_required",
                    site_id=site_id,
                    title=f"{execution.result.display_name} requires manual review",
                    message=(
                        f"{execution.result.display_name} appears open but live submission was skipped because "
                        "anti-bot warning signals were detected."
                    ),
                    severity=NotificationSeverity.CRITICAL,
                    payload={
                        "anti_bot": execution.result.anti_bot.signals,
                        "evidence_paths": execution.result.evidence_paths,
                    },
                ),
                dedupe_key=f"manual_action_required:{site_id}:{execution.result.fingerprint}:antibot_warning",
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
            self._notify_once(
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
                ),
                dedupe_key=f"verification:{site_id}:{submission.fingerprint}:{verification.status.value}",
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
        self._notify_once(
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
            ),
            dedupe_key=f"submission:{submission.site_id}:{submission.fingerprint}:{submission.status.value}",
        )

    def _send_notification(self, event: NotificationEvent) -> tuple[NotificationDelivery, ...]:
        return self.notifier.send(event)

    def _notify_once(
        self,
        event: NotificationEvent,
        dedupe_key: str | None = None,
    ) -> tuple[NotificationDelivery, ...]:
        key = dedupe_key
        if key is None:
            digest = hashlib.sha256(
                f"{event.event_type}|{event.site_id}|{event.title}|{event.message}".encode("utf-8")
            ).hexdigest()
            key = f"notify:{event.event_type}:{event.site_id}:{digest}"
        if self.store.action_exists(key):
            return ()
        deliveries = self._send_notification(event)
        self.store.remember_action(
            action_key=key,
            site_id=event.site_id,
            action_type="notify",
            details={"event_type": event.event_type, "title": event.title},
        )
        return deliveries
