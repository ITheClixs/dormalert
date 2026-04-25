from __future__ import annotations

import os
from pathlib import Path

from src.config.models import (
    AppConfig,
    BrowserSettings,
    NotificationSettings,
    SiteMonitorConfig,
    StudentVillageApplicant,
    SubmissionMode,
)
from src.utils.env import get_bool, get_csv, get_float, get_int, load_dotenv


def _site_config(prefix: str, *, default_interval: int, default_mode: SubmissionMode) -> SiteMonitorConfig:
    return SiteMonitorConfig(
        site_id=prefix.lower(),
        enabled=get_bool(f"{prefix}_ENABLED", True),
        poll_interval_seconds=get_int(f"{prefix}_POLL_INTERVAL_SECONDS", default_interval),
        jitter_seconds=get_float(f"{prefix}_JITTER_SECONDS", 30.0),
        timeout_seconds=get_int(f"{prefix}_TIMEOUT_SECONDS", 20),
        max_retries=get_int(f"{prefix}_MAX_RETRIES", 2),
        submission_mode=SubmissionMode(
            os.environ.get(f"{prefix}_SUBMISSION_MODE", default_mode.value).strip().lower()
        ),
    )


def _studentvillage_applicant() -> StudentVillageApplicant | None:
    if not any(key.startswith("STUDENTVILLAGE_APPLICANT_") for key in os.environ):
        return None
    return StudentVillageApplicant(
        firstname=os.environ.get("STUDENTVILLAGE_APPLICANT_FIRSTNAME", "").strip(),
        lastname=os.environ.get("STUDENTVILLAGE_APPLICANT_LASTNAME", "").strip(),
        email=os.environ.get("STUDENTVILLAGE_APPLICANT_EMAIL", "").strip(),
        second_email=os.environ.get("STUDENTVILLAGE_APPLICANT_SECOND_EMAIL", "").strip(),
        address=os.environ.get("STUDENTVILLAGE_APPLICANT_ADDRESS", "").strip(),
        zipcode=os.environ.get("STUDENTVILLAGE_APPLICANT_ZIPCODE", "").strip(),
        city=os.environ.get("STUDENTVILLAGE_APPLICANT_CITY", "").strip(),
        country=os.environ.get("STUDENTVILLAGE_APPLICANT_COUNTRY", "").strip(),
        phonenumber=os.environ.get("STUDENTVILLAGE_APPLICANT_PHONE", "").strip(),
        dob=os.environ.get("STUDENTVILLAGE_APPLICANT_DOB", "").strip(),
        roomnumberonregister=os.environ.get("STUDENTVILLAGE_APPLICANT_ROOMNUMBER", "").strip(),
        parents=os.environ.get("STUDENTVILLAGE_APPLICANT_PARENTS", "").strip(),
        nationality=os.environ.get("STUDENTVILLAGE_APPLICANT_NATIONALITY", "").strip(),
        studentfaculty=os.environ.get("STUDENTVILLAGE_APPLICANT_STUDENTFACULTY", "").strip(),
        spokenlanguage=os.environ.get("STUDENTVILLAGE_APPLICANT_SPOKEN_LANGUAGE", "en").strip(),
        gender=os.environ.get("STUDENTVILLAGE_APPLICANT_GENDER", "anonymous").strip(),
        username=os.environ.get("STUDENTVILLAGE_APPLICANT_USERNAME", "").strip(),
        password=os.environ.get("STUDENTVILLAGE_APPLICANT_PASSWORD", "").strip(),
        request_rentaldate=os.environ.get("STUDENTVILLAGE_APPLICANT_REQUEST_RENTALDATE", "--").strip(),
        comments=os.environ.get("STUDENTVILLAGE_APPLICANT_COMMENTS", "").strip(),
    )


def load_settings(env_file: str | Path = ".env") -> AppConfig:
    load_dotenv(env_file)

    artifacts_dir = Path(os.environ.get("DORMALERT_ARTIFACTS_DIR", "./artifacts")).resolve()
    log_dir = Path(os.environ.get("DORMALERT_LOG_DIR", "./logs")).resolve()
    database_path = Path(os.environ.get("DORMALERT_DATABASE_PATH", "./artifacts/dormalert.db")).resolve()

    sites = {
        "livingscience": _site_config(
            "LIVINGSCIENCE",
            default_interval=300,
            default_mode=SubmissionMode.DISABLED,
        ),
        "studentvillage": _site_config(
            "STUDENTVILLAGE",
            default_interval=180,
            default_mode=SubmissionMode.DRY_RUN,
        ),
    }

    if sites["livingscience"].submission_mode is SubmissionMode.LIVE:
        raise ValueError("Live submission is not supported for livingscience until the real form is mapped.")

    studentvillage_applicant = _studentvillage_applicant()
    if sites["studentvillage"].submission_mode is SubmissionMode.LIVE:
        if studentvillage_applicant is None:
            raise ValueError("Student Village live mode requires applicant configuration.")
        missing = studentvillage_applicant.missing_required_fields()
        if missing:
            raise ValueError(
                "Student Village live mode is missing applicant fields: "
                + ", ".join(missing)
            )

    return AppConfig(
        database_path=database_path,
        artifacts_dir=artifacts_dir,
        log_dir=log_dir,
        log_level=os.environ.get("DORMALERT_LOG_LEVEL", "INFO").upper(),
        user_agent=os.environ.get(
            "DORMALERT_USER_AGENT",
            "DormAlert/0.1 (+https://example.invalid/dormalert)",
        ).strip(),
        detector_only=get_bool("DORMALERT_DETECTOR_ONLY", False),
        notification=NotificationSettings(
            enable_console=get_bool("DORMALERT_ENABLE_CONSOLE_NOTIFIER", True),
            webhook_url=os.environ.get("DORMALERT_WEBHOOK_URL") or None,
            webhook_timeout_seconds=get_int("DORMALERT_WEBHOOK_TIMEOUT_SECONDS", 10),
            email_enabled=get_bool("DORMALERT_EMAIL_ENABLED", False),
            smtp_host=os.environ.get("DORMALERT_SMTP_HOST") or None,
            smtp_port=get_int("DORMALERT_SMTP_PORT", 587),
            smtp_username=os.environ.get("DORMALERT_SMTP_USERNAME") or None,
            smtp_password=os.environ.get("DORMALERT_SMTP_PASSWORD") or None,
            smtp_starttls=get_bool("DORMALERT_SMTP_STARTTLS", True),
            email_from=os.environ.get("DORMALERT_EMAIL_FROM") or None,
            email_to=get_csv("DORMALERT_EMAIL_TO"),
            alert_reminder_minutes=get_int("DORMALERT_ALERT_REMINDER_MINUTES", 15),
        ),
        browser=BrowserSettings(
            headless=get_bool("DORMALERT_BROWSER_HEADLESS", True),
            slow_mo_ms=get_int("DORMALERT_BROWSER_SLOW_MO_MS", 0),
        ),
        failure_alert_threshold=get_int("DORMALERT_FAILURE_ALERT_THRESHOLD", 3),
        closed_artifact_retention_days=get_int("DORMALERT_CLOSED_ARTIFACT_RETENTION_DAYS", 7),
        sites=sites,
        studentvillage_applicant=studentvillage_applicant,
        studentvillage_success_phrases=get_csv("STUDENTVILLAGE_SUCCESS_PHRASES"),
        studentvillage_failure_phrases=get_csv("STUDENTVILLAGE_FAILURE_PHRASES"),
        confirmation_min_gap_seconds=get_int("DORMALERT_CONFIRMATION_MIN_GAP_SECONDS", 60),
        open_signal_fast_path_strength=get_float("DORMALERT_OPEN_SIGNAL_FAST_PATH_STRENGTH", 0.95),
    )
