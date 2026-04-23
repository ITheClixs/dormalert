from __future__ import annotations

from src.detector.antibot import inspect_probe
from src.detector.models import AntiBotSeverity, ProbeResult


def test_antibot_detects_form_token_and_session_cookie() -> None:
    probe = ProbeResult(
        target_name="apply",
        requested_url="https://studentvillage.ch/en/apply/",
        final_url="https://studentvillage.ch/en/apply/",
        status_code=200,
        headers={"set-cookie": "PHPSESSID=abc123; path=/"},
        text='<form><input type="hidden" name="form_token" value="abc"></form>',
        duration_ms=50,
        fetched_at="2026-04-23T18:00:00Z",
    )
    observation = inspect_probe(probe)
    assert observation.severity is AntiBotSeverity.INFO
    assert "tokenized_form_present" in observation.signals
    assert "session_cookie_present" in observation.signals


def test_antibot_detects_blocking_captcha_markers() -> None:
    probe = ProbeResult(
        target_name="apply",
        requested_url="https://example.invalid/",
        final_url="https://example.invalid/",
        status_code=200,
        headers={"content-type": "text/html"},
        text='<script src="https://www.google.com/recaptcha/api.js"></script>',
        duration_ms=50,
        fetched_at="2026-04-23T18:00:00Z",
    )
    observation = inspect_probe(probe)
    assert observation.severity is AntiBotSeverity.BLOCKING
    assert "visible_recaptcha" in observation.signals

