from __future__ import annotations

from src.config.models import AppConfig
from src.detector.models import DetectionExecution
from src.diagnostics.artifacts import ArtifactManager
from src.submitter.base import SubmissionResult, SubmissionStatus
from src.utils.time import utcnow_iso


class DryRunSubmitter:
    def __init__(self, artifacts: ArtifactManager) -> None:
        self.artifacts = artifacts

    def submit(self, execution: DetectionExecution, config: AppConfig) -> SubmissionResult:
        started = utcnow_iso()
        metadata = {
            "mode": "dry_run",
            "site_id": execution.result.site_id,
            "detection_state": execution.result.state.value,
            "detection_confidence": execution.result.confidence,
            "detection_fingerprint": execution.result.fingerprint,
            "facts": execution.result.facts,
            "anti_bot": execution.result.anti_bot.signals,
            "studentvillage_applicant": (
                config.studentvillage_applicant.redacted_summary()
                if config.studentvillage_applicant
                else None
            ),
        }
        evidence_paths = self.artifacts.capture_submission(
            site_id=execution.result.site_id,
            reason="dry_run",
            metadata=metadata,
        )
        finished = utcnow_iso()
        return SubmissionResult(
            site_id=execution.result.site_id,
            status=SubmissionStatus.DRY_RUN,
            mode="dry_run",
            attempted=True,
            started_at=started,
            finished_at=finished,
            message="Dry run completed. No live form submission was sent.",
            facts=("Dry-run evidence bundle created.",),
            inferences=(
                "Use the saved artifacts to confirm configuration before enabling live mode.",
            ),
            evidence_paths=evidence_paths,
            fingerprint=execution.result.fingerprint,
        )

