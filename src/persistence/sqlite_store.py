from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.detector.models import DetectionResult, WorkflowState
from src.submitter.base import SubmissionResult
from src.utils.serialization import to_jsonable
from src.utils.time import utcnow_iso


@dataclass(frozen=True)
class SiteRuntimeRecord:
    site_id: str
    display_name: str
    last_page_state: str
    last_workflow_state: str
    last_confidence: float
    last_fingerprint: str
    last_checked_at: str
    consecutive_failures: int
    last_transition_at: str | None
    updated_at: str


@dataclass(frozen=True)
class OpeningEventRecord:
    event_id: int
    site_id: str
    opening_fingerprint: str
    status: str
    first_opened_at: str
    last_seen_open_at: str
    last_notified_at: str | None
    next_reminder_at: str | None
    reminder_count: int
    acknowledged_at: str | None
    closed_at: str | None
    last_detection_payload_json: str
    created_at: str
    updated_at: str


class SQLiteStateStore:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.initialize()

    def initialize(self) -> None:
        cursor = self.connection.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS site_runtime (
                site_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                last_page_state TEXT NOT NULL,
                last_workflow_state TEXT NOT NULL,
                last_confidence REAL NOT NULL,
                last_fingerprint TEXT NOT NULL,
                last_checked_at TEXT NOT NULL,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_transition_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS detection_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT NOT NULL,
                state TEXT NOT NULL,
                confidence REAL NOT NULL,
                fingerprint TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS action_dedupe (
                action_key TEXT PRIMARY KEY,
                site_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                details_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS submission_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS opening_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT NOT NULL,
                opening_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                first_opened_at TEXT NOT NULL,
                last_seen_open_at TEXT NOT NULL,
                last_notified_at TEXT,
                next_reminder_at TEXT,
                reminder_count INTEGER NOT NULL DEFAULT 0,
                acknowledged_at TEXT,
                closed_at TEXT,
                last_detection_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def get_runtime(self, site_id: str) -> SiteRuntimeRecord | None:
        cursor = self.connection.execute(
            "SELECT * FROM site_runtime WHERE site_id = ?",
            (site_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return SiteRuntimeRecord(
            site_id=row["site_id"],
            display_name=row["display_name"],
            last_page_state=row["last_page_state"],
            last_workflow_state=row["last_workflow_state"],
            last_confidence=row["last_confidence"],
            last_fingerprint=row["last_fingerprint"],
            last_checked_at=row["last_checked_at"],
            consecutive_failures=row["consecutive_failures"],
            last_transition_at=row["last_transition_at"],
            updated_at=row["updated_at"],
        )

    def upsert_runtime(
        self,
        *,
        result: DetectionResult,
        workflow_state: WorkflowState,
        consecutive_failures: int,
        transition_at: str | None,
    ) -> None:
        now = utcnow_iso()
        self.connection.execute(
            """
            INSERT INTO site_runtime (
                site_id,
                display_name,
                last_page_state,
                last_workflow_state,
                last_confidence,
                last_fingerprint,
                last_checked_at,
                consecutive_failures,
                last_transition_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(site_id) DO UPDATE SET
                display_name=excluded.display_name,
                last_page_state=excluded.last_page_state,
                last_workflow_state=excluded.last_workflow_state,
                last_confidence=excluded.last_confidence,
                last_fingerprint=excluded.last_fingerprint,
                last_checked_at=excluded.last_checked_at,
                consecutive_failures=excluded.consecutive_failures,
                last_transition_at=excluded.last_transition_at,
                updated_at=excluded.updated_at
            """,
            (
                result.site_id,
                result.display_name,
                result.state.value,
                workflow_state.value,
                result.confidence,
                result.fingerprint,
                result.timestamp_utc,
                consecutive_failures,
                transition_at,
                now,
            ),
        )
        self.connection.commit()

    def update_workflow_state(self, site_id: str, workflow_state: WorkflowState) -> None:
        self.connection.execute(
            """
            UPDATE site_runtime
            SET last_workflow_state = ?, updated_at = ?
            WHERE site_id = ?
            """,
            (workflow_state.value, utcnow_iso(), site_id),
        )
        self.connection.commit()

    def record_detection(self, result: DetectionResult) -> None:
        self.connection.execute(
            """
            INSERT INTO detection_events (
                site_id,
                state,
                confidence,
                fingerprint,
                observed_at,
                payload_json,
                evidence_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.site_id,
                result.state.value,
                result.confidence,
                result.fingerprint,
                result.timestamp_utc,
                json.dumps(to_jsonable(result)),
                json.dumps(to_jsonable(result.evidence_paths)),
            ),
        )
        self.connection.commit()

    def action_exists(self, action_key: str) -> bool:
        cursor = self.connection.execute(
            "SELECT 1 FROM action_dedupe WHERE action_key = ?",
            (action_key,),
        )
        return cursor.fetchone() is not None

    def remember_action(
        self,
        *,
        action_key: str,
        site_id: str,
        action_type: str,
        details: dict[str, Any],
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO action_dedupe (
                action_key,
                site_id,
                action_type,
                created_at,
                details_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                action_key,
                site_id,
                action_type,
                utcnow_iso(),
                json.dumps(to_jsonable(details)),
            ),
        )
        self.connection.commit()

    def record_submission_attempt(self, result: SubmissionResult) -> None:
        self.connection.execute(
            """
            INSERT INTO submission_attempts (
                site_id,
                fingerprint,
                status,
                mode,
                started_at,
                finished_at,
                payload_json,
                evidence_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.site_id,
                result.fingerprint,
                result.status.value,
                result.mode,
                result.started_at,
                result.finished_at,
                json.dumps(to_jsonable(result)),
                json.dumps(to_jsonable(result.evidence_paths)),
            ),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def list_runtime_records(self) -> tuple[SiteRuntimeRecord, ...]:
        cursor = self.connection.execute("SELECT * FROM site_runtime ORDER BY site_id")
        rows = cursor.fetchall()
        return tuple(
            SiteRuntimeRecord(
                site_id=row["site_id"],
                display_name=row["display_name"],
                last_page_state=row["last_page_state"],
                last_workflow_state=row["last_workflow_state"],
                last_confidence=row["last_confidence"],
                last_fingerprint=row["last_fingerprint"],
                last_checked_at=row["last_checked_at"],
                consecutive_failures=row["consecutive_failures"],
                last_transition_at=row["last_transition_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        )

    def _row_to_opening_event(self, row: sqlite3.Row) -> OpeningEventRecord:
        return OpeningEventRecord(
            event_id=row["id"],
            site_id=row["site_id"],
            opening_fingerprint=row["opening_fingerprint"],
            status=row["status"],
            first_opened_at=row["first_opened_at"],
            last_seen_open_at=row["last_seen_open_at"],
            last_notified_at=row["last_notified_at"],
            next_reminder_at=row["next_reminder_at"],
            reminder_count=row["reminder_count"],
            acknowledged_at=row["acknowledged_at"],
            closed_at=row["closed_at"],
            last_detection_payload_json=row["last_detection_payload_json"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_active_opening(self, site_id: str) -> OpeningEventRecord | None:
        cursor = self.connection.execute(
            """
            SELECT * FROM opening_events
            WHERE site_id = ? AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """,
            (site_id,),
        )
        row = cursor.fetchone()
        return self._row_to_opening_event(row) if row else None

    def get_current_opening(self, site_id: str) -> OpeningEventRecord | None:
        cursor = self.connection.execute(
            """
            SELECT * FROM opening_events
            WHERE site_id = ? AND status IN ('active', 'acknowledged')
            ORDER BY id DESC
            LIMIT 1
            """,
            (site_id,),
        )
        row = cursor.fetchone()
        return self._row_to_opening_event(row) if row else None

    def get_opening_event(self, event_id: int) -> OpeningEventRecord | None:
        cursor = self.connection.execute(
            "SELECT * FROM opening_events WHERE id = ?",
            (event_id,),
        )
        row = cursor.fetchone()
        return self._row_to_opening_event(row) if row else None

    def list_opening_events(self, active_only: bool = False) -> tuple[OpeningEventRecord, ...]:
        if active_only:
            cursor = self.connection.execute(
                "SELECT * FROM opening_events WHERE status = 'active' ORDER BY id DESC"
            )
        else:
            cursor = self.connection.execute("SELECT * FROM opening_events ORDER BY id DESC")
        rows = cursor.fetchall()
        return tuple(self._row_to_opening_event(row) for row in rows)

    def create_opening_event(self, result: DetectionResult) -> OpeningEventRecord:
        now = utcnow_iso()
        cursor = self.connection.execute(
            """
            INSERT INTO opening_events (
                site_id,
                opening_fingerprint,
                status,
                first_opened_at,
                last_seen_open_at,
                last_notified_at,
                next_reminder_at,
                reminder_count,
                acknowledged_at,
                closed_at,
                last_detection_payload_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, 'active', ?, ?, NULL, NULL, 0, NULL, NULL, ?, ?, ?)
            """,
            (
                result.site_id,
                result.fingerprint,
                result.timestamp_utc,
                result.timestamp_utc,
                json.dumps(to_jsonable(result)),
                now,
                now,
            ),
        )
        self.connection.commit()
        return self.get_opening_event(int(cursor.lastrowid))  # type: ignore[arg-type]

    def refresh_opening_event(self, event_id: int, result: DetectionResult) -> OpeningEventRecord | None:
        self.connection.execute(
            """
            UPDATE opening_events
            SET last_seen_open_at = ?, last_detection_payload_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                result.timestamp_utc,
                json.dumps(to_jsonable(result)),
                utcnow_iso(),
                event_id,
            ),
        )
        self.connection.commit()
        return self.get_opening_event(event_id)

    def mark_opening_notified(
        self,
        *,
        event_id: int,
        last_notified_at: str,
        next_reminder_at: str,
        reminder_count: int,
    ) -> OpeningEventRecord | None:
        self.connection.execute(
            """
            UPDATE opening_events
            SET last_notified_at = ?, next_reminder_at = ?, reminder_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                last_notified_at,
                next_reminder_at,
                reminder_count,
                utcnow_iso(),
                event_id,
            ),
        )
        self.connection.commit()
        return self.get_opening_event(event_id)

    def acknowledge_opening(self, event_id: int) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE opening_events
            SET status = 'acknowledged',
                acknowledged_at = ?,
                next_reminder_at = NULL,
                updated_at = ?
            WHERE id = ? AND status = 'active'
            """,
            (utcnow_iso(), utcnow_iso(), event_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def close_opening_event(self, event_id: int) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE opening_events
            SET status = 'closed',
                closed_at = ?,
                next_reminder_at = NULL,
                updated_at = ?
            WHERE id = ? AND status IN ('active', 'acknowledged')
            """,
            (utcnow_iso(), utcnow_iso(), event_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0
