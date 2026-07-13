from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from dataclasses import replace

from src.config.models import AppConfig, SubmissionMode
from src.detector.engine import PageStateDetector
from src.detector.models import DetectionExecution, DetectionResult, DetectorState, WorkflowState
from src.detector.profile import SiteProfile, WATCHED_CLOSED_TEXT_MISSING_SIGNAL
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
        self._handle_watched_closed_text_notification(execution.result)
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

    def notify_monitor_started(self, site_ids: list[str]) -> tuple[NotificationDelivery, ...]:
        if not self.config.notification.email_enabled:
            self.logger.warning(
                "Startup email was not sent because SMTP email notifications are disabled",
                extra={
                    "event": "startup_email_not_configured",
                    "email_enabled": False,
                    "email_to": self.config.notification.email_to,
                    "required_action": "Set DORMALERT_EMAIL_ENABLED=true and configure SMTP settings in .env.",
                },
            )

        poll_intervals = {
            site_id: self.config.sites[site_id].poll_interval_seconds
            for site_id in site_ids
        }
        deliveries = self._send_notification(
            NotificationEvent(
                event_type="monitor_started",
                site_id="system",
                title="DormAlert monitor is running",
                message=(
                    "DormAlert continuous monitoring has started. This startup email confirms "
                    "the process is running and the notification channel is configured."
                ),
                severity=NotificationSeverity.INFO,
                payload={
                    "monitored_sites": tuple(site_ids),
                    "detector_only": self.config.detector_only,
                    "poll_intervals_seconds": poll_intervals,
                },
            )
        )
        self.logger.info(
            "Monitor startup notification sent",
            extra={
                "event": "monitor_start_notification",
                "site_ids": site_ids,
                "delivery_count": len(deliveries),
                "email_enabled": self.config.notification.email_enabled,
                "email_succeeded": any(
                    delivery.delivery_kind == "email" and delivery.succeeded
                    for delivery in deliveries
                ),
            },
        )
        return deliveries

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
        if open_strength >= self.config.open_signal_fast_path_strength:
            return execution

        same_fingerprint = runtime is not None and runtime.last_fingerprint == result.fingerprint
        prior_positive = runtime is not None and runtime.last_page_state in {
            DetectorState.OPEN.value,
            DetectorState.OPENING_CANDIDATE.value,
        }
        gap_satisfied = False
        if runtime is not None and runtime.last_transition_at is not None:
            gap_seconds = (
                parse_utc_iso(result.timestamp_utc) - parse_utc_iso(runtime.last_transition_at)
            ).total_seconds()
            gap_satisfied = gap_seconds >= self.config.confirmation_min_gap_seconds

        if same_fingerprint and prior_positive and gap_satisfied:
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
                ),
                dedupe_key=f"availability_change:{result.site_id}:{result.fingerprint}",
            )

        previous_failures = runtime.consecutive_failures if runtime else 0
        threshold = self.config.failure_alert_threshold
        if result.state is DetectorState.FAILED and consecutive_failures >= threshold:
            crossing = previous_failures < threshold
            failure_day = result.timestamp_utc[:10]
            day_key = f"repeated_failure:{result.site_id}:{failure_day}"
            if crossing or not self.store.action_exists(day_key):
                self._notify_once(
                    NotificationEvent(
                        event_type="repeated_failure",
                        site_id=result.site_id,
                        title=f"{result.display_name} detector is failing repeatedly",
                        message=(
                            f"{result.display_name} reached {consecutive_failures} consecutive failed detection "
                            "cycles. The monitor may be blind to a real opening until this is fixed."
                        ),
                        severity=NotificationSeverity.ERROR,
                        payload={
                            "facts": result.facts,
                            "uncertainties": result.uncertainties,
                            "evidence_paths": result.evidence_paths,
                        },
                    ),
                    dedupe_key=day_key,
                    force_send=crossing,
                )

    def _handle_watched_closed_text_notification(self, result: DetectionResult) -> None:
        if WATCHED_CLOSED_TEXT_MISSING_SIGNAL not in result.signals:
            return

        expected_text = str(result.metadata.get("watched_closed_text") or "")
        status = str(result.metadata.get("watched_closed_text_status") or "missing")
        title = f"DormAlert: {result.display_name} monitored closed text disappeared"
        message = (
            f"DormAlert no longer observes the monitored closed/waitlist text for {result.display_name}. "
            "This does not prove the waitlist is open; it means the exact watched text changed or disappeared. "
            f"Current detector state is {result.state.value} with confidence {result.confidence:.2f}."
        )
        event = NotificationEvent(
            event_type="closed_text_missing_alert",
            site_id=result.site_id,
            title=title,
            message=message,
            severity=NotificationSeverity.CRITICAL,
            payload={
                "observed_status": status,
                "expected_text": expected_text,
                "state_reason": result.state_reason,
                "confidence": result.confidence,
                "signals": result.signals,
                "facts": result.facts,
                "inferences": result.inferences,
                "page_urls": result.page_urls,
                "evidence_paths": result.evidence_paths,
            },
        )
        dedupe_key = f"watched_closed_text_missing:{result.site_id}:{result.fingerprint}"
        if self.store.action_exists(dedupe_key):
            return

        deliveries = self._send_notification(event)
        if self.config.notification.email_enabled and not any(
            delivery.delivery_kind == "email" and delivery.succeeded
            for delivery in deliveries
        ):
            self.logger.warning(
                "Watched closed text alert did not reach email",
                extra={
                    "event": "watched_closed_text_notification_failed",
                    "site_id": result.site_id,
                    "fingerprint": result.fingerprint,
                },
            )
            return

        self.store.remember_action(
            action_key=dedupe_key,
            site_id=result.site_id,
            action_type="notify",
            details={
                "event_type": event.event_type,
                "title": event.title,
                "watched_closed_text_status": status,
            },
        )

    def _is_alertable_opening(self, execution: DetectionExecution) -> bool:
        if execution.result.state is DetectorState.OPEN:
            return True
        profile = self.profiles.get(execution.result.site_id)
        return (
            execution.result.state is DetectorState.OPENING_CANDIDATE
            and bool(getattr(profile, "candidate_open_alerts", False))
            and WATCHED_CLOSED_TEXT_MISSING_SIGNAL in execution.result.signals
        )

    def _reconcile_opening_event(self, execution: DetectionExecution) -> None:
        site_id = execution.result.site_id
        current_opening = self.store.get_current_opening(site_id)

        if self._is_alertable_opening(execution):
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
        is_candidate = result.state is DetectorState.OPENING_CANDIDATE
        if is_initial:
            event_type = "opening_alert"
            if is_candidate:
                title = f"DormAlert: {result.display_name} waitlist may be open - check now"
                message = (
                    f"The monitored closed-state text for {result.display_name} disappeared and the page "
                    f"no longer looks closed (confidence {result.confidence:.2f}). Treat this as a live "
                    f"opening: open the site and register manually right away. "
                    f"Event #{opening.event_id} was created."
                )
            else:
                title = f"DormAlert: {result.display_name} waitlist is open"
                message = (
                    f"{result.display_name} reached the confirmed open state with confidence {result.confidence:.2f}. "
                    f"Event #{opening.event_id} was created."
                )
        else:
            reminder_number = max(1, opening.reminder_count)
            event_type = "opening_reminder"
            if is_candidate:
                title = (
                    f"DormAlert reminder {reminder_number}: {result.display_name} still looks open (unverified)"
                )
                message = (
                    f"The monitored closed-state text for {result.display_name} is still missing "
                    f"(confidence {result.confidence:.2f}). If you have not registered yet, do it now. "
                    f"Event #{opening.event_id} remains active."
                )
            else:
                title = f"DormAlert reminder {reminder_number}: {result.display_name} waitlist is still open"
                message = (
                    f"{result.display_name} still has a confirmed open state with confidence {result.confidence:.2f}. "
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
                "state_reason": result.state_reason,
                "signals": result.signals,
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
        force_send: bool = False,
    ) -> tuple[NotificationDelivery, ...]:
        key = dedupe_key
        if key is None:
            digest = hashlib.sha256(
                f"{event.event_type}|{event.site_id}|{event.title}|{event.message}".encode("utf-8")
            ).hexdigest()
            key = f"notify:{event.event_type}:{event.site_id}:{digest}"
        if not force_send and self.store.action_exists(key):
            return ()
        deliveries = self._send_notification(event)
        if not self._opening_delivery_succeeded(deliveries):
            self.logger.warning(
                "Notification did not reach the required delivery channel; it will be retried",
                extra={
                    "event": "notification_delivery_failed",
                    "notification_type": event.event_type,
                    "site_id": event.site_id,
                    "dedupe_key": key,
                },
            )
            return deliveries
        self.store.remember_action(
            action_key=key,
            site_id=event.site_id,
            action_type="notify",
            details={"event_type": event.event_type, "title": event.title},
        )
        return deliveries
