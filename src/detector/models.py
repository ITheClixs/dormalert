from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DetectorState(str, Enum):
    CLOSED = "closed"
    OPENING_CANDIDATE = "opening_candidate"
    OPEN = "open"
    FAILED = "failed"


class WorkflowState(str, Enum):
    CLOSED = "closed"
    OPENING_CANDIDATE = "opening_candidate"
    OPEN = "open"
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    FAILED = "failed"


class AntiBotSeverity(str, Enum):
    NONE = "none"
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


@dataclass(frozen=True)
class ProbeTarget:
    name: str
    url: str


@dataclass(frozen=True)
class ProbeResult:
    target_name: str
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    text: str
    duration_ms: int
    fetched_at: str


@dataclass(frozen=True)
class AntiBotObservation:
    severity: AntiBotSeverity
    signals: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DetectionResult:
    site_id: str
    display_name: str
    state: DetectorState
    confidence: float
    signals: tuple[str, ...]
    facts: tuple[str, ...]
    inferences: tuple[str, ...]
    uncertainties: tuple[str, ...]
    anti_bot: AntiBotObservation
    page_urls: tuple[str, ...]
    timestamp_utc: str
    fingerprint: str
    evidence_paths: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DetectionExecution:
    result: DetectionResult
    probes: tuple[ProbeResult, ...]

