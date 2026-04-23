from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from src.utils.time import utcnow_iso


class VerificationStatus(str, Enum):
    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class VerificationResult:
    site_id: str
    status: VerificationStatus
    message: str
    facts: tuple[str, ...] = ()
    inferences: tuple[str, ...] = ()
    timestamp_utc: str = field(default_factory=utcnow_iso)


class Verifier(Protocol):
    def verify(self, site_id: str, submission_result: "SubmissionResult") -> VerificationResult:
        ...

