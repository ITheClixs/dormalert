from __future__ import annotations

from pathlib import Path

import pytest

from src.app.simulation import run_studentvillage_opening_simulation
from src.config.models import (
    AppConfig,
    BrowserSettings,
    NotificationSettings,
    SiteMonitorConfig,
    SubmissionMode,
)
from src.notifier.base import NotificationDelivery


class StubNotifier:
    def __init__(self) -> None:
        self.events = []

    def send(self, event):
        self.events.append(event)
        return (
            NotificationDelivery(
                notifier="StubEmailNotifier",
                delivery_kind="email",
                succeeded=True,
            ),
        )


def _config(base_dir: Path, *, email_enabled: bool = True) -> AppConfig:
    return AppConfig(
        database_path=base_dir / "production.db",
        artifacts_dir=base_dir / "production-artifacts",
        log_dir=base_dir / "logs",
        log_level="INFO",
        user_agent="DormAlertTest/0.1",
        detector_only=False,
        notification=NotificationSettings(
            enable_console=False,
            webhook_url=None,
            webhook_timeout_seconds=10,
            email_enabled=email_enabled,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="alerts@example.com",
            smtp_password="secret",
            smtp_starttls=True,
            email_from="alerts@example.com",
            email_to=("demirguven178@gmail.com",),
            alert_reminder_minutes=15,
        ),
        browser=BrowserSettings(headless=True, slow_mo_ms=0),
        failure_alert_threshold=3,
        closed_artifact_retention_days=7,
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
        confirmation_min_gap_seconds=60,
        open_signal_fast_path_strength=0.95,
    )


def test_studentvillage_opening_simulation_sends_opening_email(tmp_path: Path) -> None:
    notifier = StubNotifier()

    result = run_studentvillage_opening_simulation(
        config=_config(tmp_path),
        notifier=notifier,
        send_email=True,
        workspace_dir=tmp_path / "simulation",
    )

    assert result.simulated is True
    assert result.seed_state == "opening_candidate"
    assert result.final_state == "open"
    assert result.final_state_reason == "consecutive_open_confirmation_satisfied"
    assert result.opening_email_succeeded is True
    assert result.database_path == tmp_path / "simulation" / "dormalert-simulation.db"
    assert result.artifacts_dir == tmp_path / "simulation" / "artifacts"
    assert not (tmp_path / "production.db").exists()
    assert [event.event_type for event in notifier.events].count("opening_alert") == 1
    assert "SIMULATION" in notifier.events[0].title
    assert len(result.active_openings) == 1
    assert result.active_openings[0].last_notified_at is not None


def test_studentvillage_opening_simulation_requires_enabled_email(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DORMALERT_EMAIL_ENABLED"):
        run_studentvillage_opening_simulation(
            config=_config(tmp_path, email_enabled=False),
            notifier=StubNotifier(),
            send_email=True,
            workspace_dir=tmp_path / "simulation",
        )
