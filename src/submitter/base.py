from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from src.config.models import AppConfig
from src.detector.models import DetectionExecution
from src.utils.time import utcnow_iso


class SubmissionStatus(str, Enum):
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"
    SUCCEEDED = "succeeded"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SubmissionResult:
    site_id: str
    status: SubmissionStatus
    mode: str
    attempted: bool
    started_at: str
    finished_at: str
    message: str
    facts: tuple[str, ...] = ()
    inferences: tuple[str, ...] = ()
    evidence_paths: tuple[str, ...] = ()
    final_url: str = ""
    final_page_text: str = ""
    fingerprint: str = ""


class Submitter(Protocol):
    def submit(self, execution: DetectionExecution, config: AppConfig) -> SubmissionResult:
        ...


def skipped_result(site_id: str, mode: str, message: str, fingerprint: str) -> SubmissionResult:
    timestamp = utcnow_iso()
    return SubmissionResult(
        site_id=site_id,
        status=SubmissionStatus.SKIPPED,
        mode=mode,
        attempted=False,
        started_at=timestamp,
        finished_at=timestamp,
        message=message,
        fingerprint=fingerprint,
    )

