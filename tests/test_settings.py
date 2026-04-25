from __future__ import annotations

from src.config.settings import load_settings


def test_default_alert_recipient_is_configured(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DORMALERT_EMAIL_TO", "")
    monkeypatch.setenv("LIVINGSCIENCE_SUBMISSION_MODE", "disabled")
    monkeypatch.setenv("STUDENTVILLAGE_SUBMISSION_MODE", "dry_run")

    config = load_settings()

    assert config.notification.email_to == ("demirguven178@gmail.com",)
